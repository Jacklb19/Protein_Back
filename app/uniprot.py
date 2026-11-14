"""Bounded, authenticated access to public UniProt annotations. Never forwards a user's JWT."""
from datetime import datetime, timezone
import re
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import Field

from .context import ServiceContext, context_of
from .identity import CurrentIdentity, bearer_token
from .projects import Contract
from .spend import Budget, spend
from .spend_config import MINUTE_SECONDS, SCOPE_UNIPROT

UNIPROT_BASE_URL = "https://rest.uniprot.org/uniprotkb"
UNIPROT_TIMEOUT_SECONDS = 12.0
UNIPROT_SEARCH_SIZE = 10
MAX_UNIPROT_BYTES = 8_000_000
MIN_QUERY_LENGTH = 2
MAX_QUERY_LENGTH = 200
ACCESSION_PATTERN = r"(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})"
# Positional annotations an entry may carry, by UniProt feature type, in the order they are kept when an
# entry has more than the limits allow: what the protein does first, then how it is built, then variants.
FEATURE_KINDS = {
    "Active site": "active-site", "Binding site": "binding-site", "Site": "site", "DNA binding": "dna-binding",
    "Zinc finger": "zinc-finger", "Motif": "motif", "Domain": "domain", "Region": "region", "Transmembrane": "transmembrane",
    "Signal": "signal", "Transit peptide": "transit-peptide", "Propeptide": "propeptide", "Chain": "chain", "Peptide": "peptide",
    "Disulfide bond": "disulfide", "Modified residue": "modified-residue", "Glycosylation": "glycosylation",
    "Natural variant": "variant",
}
# A disulfide bond joins its two ends; every other kind covers the whole span between them.
PAIRED_FEATURE_KINDS = frozenset({"disulfide"})
MAX_FEATURES = 300
MAX_FEATURES_PER_KIND = 60
MAX_FEATURE_DESCRIPTION = 400


class EntrySummary(Contract):
    accession: str = Field(pattern=f"^{ACCESSION_PATTERN}$")
    name: str = Field(min_length=1, max_length=2000)
    organism: str = Field(min_length=1, max_length=500)
    reviewed: bool
    length: int = Field(gt=0, le=100_000)


class PdbReference(Contract):
    id: str = Field(pattern=r"^[0-9][A-Z0-9]{3}$")
    method: str
    resolution: str | None
    chains: str | None


class ProteinFeature(Contract):
    kind: str = Field(pattern=r"^[a-z]+(?:-[a-z]+)*$", max_length=32)
    # Source-language text from UniProt; empty when the annotation has none.
    description: str = Field(max_length=MAX_FEATURE_DESCRIPTION)
    begin: int = Field(gt=0, le=100_000)
    end: int = Field(gt=0, le=100_000)
    # True when the annotation names two residues (a bond) rather than every residue between them.
    paired: bool


class UniProtEntry(EntrySummary):
    sequence: str = Field(pattern=r"^[ACDEFGHIKLMNPQRSTVWYUOBZX]+$", max_length=100_000)
    entry_version: int = Field(gt=0)
    sequence_version: int = Field(gt=0)
    retrieved_at: str
    function: str | None
    structures: list[PdbReference]
    features: list[ProteinFeature] = Field(default_factory=list, max_length=MAX_FEATURES)
    # True when the entry had more usable annotations than the limits keep.
    features_truncated: bool = False


class SearchResult(Contract):
    items: list[EntrySummary]
    limit: int = UNIPROT_SEARCH_SIZE
    truncated: bool
    retrieved_at: str


def summary(payload: dict) -> EntrySummary:
    description = payload.get("proteinDescription", {})
    name = description.get("recommendedName", {}).get("fullName", {}).get("value")
    if not name:
        submitted = description.get("submissionNames", [])
        name = submitted[0].get("fullName", {}).get("value") if submitted else payload.get("uniProtkbId")
    return EntrySummary(accession=payload["primaryAccession"], name=name, organism=payload["organism"]["scientificName"], reviewed=payload["entryType"] == "UniProtKB reviewed (Swiss-Prot)", length=payload["sequence"]["length"])


def exact_position(endpoint) -> int | None:
    if not isinstance(endpoint, dict) or endpoint.get("modifier") != "EXACT":
        return None
    value = endpoint.get("value")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def feature_description(feature: dict) -> str:
    parts = []
    alternative = feature.get("alternativeSequence")
    if isinstance(alternative, dict):
        original, replacements = alternative.get("originalSequence"), alternative.get("alternativeSequences")
        if isinstance(original, str) and isinstance(replacements, list) and replacements and all(isinstance(item, str) for item in replacements):
            parts.append(f"{original} > {'/'.join(replacements)}")
    ligand = feature.get("ligand")
    if isinstance(ligand, dict) and isinstance(ligand.get("name"), str):
        parts.append(ligand["name"])
    if isinstance(feature.get("description"), str) and feature["description"].strip():
        parts.append(feature["description"].strip())
    return "; ".join(parts)[:MAX_FEATURE_DESCRIPTION]


def features(payload: dict, length: int) -> tuple[list[ProteinFeature], bool]:
    """Positional annotations with exact ends inside the sequence; anything uncertain is left out, never guessed."""
    kept: dict[str, list[ProteinFeature]] = {kind: [] for kind in FEATURE_KINDS.values()}
    truncated = False
    source = payload.get("features", [])
    for feature in source if isinstance(source, list) else []:
        if not isinstance(feature, dict):
            continue
        kind = FEATURE_KINDS.get(feature.get("type"))
        location = feature.get("location")
        if kind is None or not isinstance(location, dict):
            continue
        begin, end = exact_position(location.get("start")), exact_position(location.get("end"))
        if begin is None or end is None or not 1 <= begin <= end <= length:
            continue
        if len(kept[kind]) >= MAX_FEATURES_PER_KIND:
            truncated = True
            continue
        kept[kind].append(ProteinFeature(kind=kind, description=feature_description(feature), begin=begin, end=end, paired=kind in PAIRED_FEATURE_KINDS and begin != end))
    ordered = [item for group in kept.values() for item in sorted(group, key=lambda item: (item.begin, item.end))]
    return ordered[:MAX_FEATURES], truncated or len(ordered) > MAX_FEATURES


def entry(payload: dict) -> UniProtEntry:
    record = summary(payload)
    sequence = payload["sequence"]["value"]
    if len(sequence) != record.length:
        raise ValueError("Sequence length mismatch")
    structures = []
    for crossref in payload.get("uniProtKBCrossReferences", []):
        if crossref.get("database") != "PDB" or not re.fullmatch(r"[0-9][A-Z0-9]{3}", crossref.get("id", "")):
            continue
        properties = {item["key"]: item["value"] for item in crossref.get("properties", [])}
        structures.append(PdbReference(id=crossref["id"], method=properties.get("Method", "Unspecified"), resolution=properties.get("Resolution"), chains=properties.get("Chains")))
    functions = [text["value"] for comment in payload.get("comments", []) if comment.get("commentType") == "FUNCTION" for text in comment.get("texts", [])]
    audit = payload["entryAudit"]
    annotated, truncated = features(payload, record.length)
    return UniProtEntry(**record.model_dump(), sequence=sequence, features=annotated, features_truncated=truncated, entry_version=audit["entryVersion"], sequence_version=audit["sequenceVersion"], retrieved_at=datetime.now(timezone.utc).isoformat(), function="\n".join(functions) or None, structures=structures)


async def retrieve(context: ServiceContext, path: str, params: dict | None = None):
    # A fresh request does not inherit identity headers, auth or cookies from the shared client.
    try:
        request = httpx.Request('GET', f'{UNIPROT_BASE_URL}/{path}', params=params,
                                headers={'Accept': 'application/json', 'Accept-Encoding': 'identity'},
                                extensions={'timeout': httpx.Timeout(UNIPROT_TIMEOUT_SECONDS).as_dict()})
        response = await context.http_client.send(request, stream=True, auth=None, follow_redirects=False)
        try:
            if response.status_code == 404:
                raise HTTPException(404, 'uniprot-not-found')
            if response.status_code == 400:
                raise HTTPException(422, 'uniprot-query')
            if response.status_code == 429:
                raise HTTPException(429, 'uniprot-rate-limit')
            if response.status_code != 200:
                raise HTTPException(502, 'uniprot-provider-error')
            length = response.headers.get('Content-Length')
            if length is not None and (not length.isascii() or not length.isdecimal()
                                       or len(length) > 9 or int(length) > MAX_UNIPROT_BYTES):
                raise HTTPException(502, 'uniprot-provider-error')
            if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                raise HTTPException(502, 'uniprot-invalid-response')
            output = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=65_536):
                if len(output) + len(chunk) > MAX_UNIPROT_BYTES:
                    raise HTTPException(502, 'uniprot-provider-error')
                output.extend(chunk)
            try:
                return httpx.Response(200, content=bytes(output)).json()
            except (ValueError, RecursionError) as error:
                raise HTTPException(502, 'uniprot-invalid-response') from error
        finally:
            await response.aclose()
    except httpx.TimeoutException as error:
        raise HTTPException(504, "uniprot-timeout") from error
    except httpx.HTTPError as error:
        raise HTTPException(502, "uniprot-unreachable") from error


router = APIRouter(prefix="/uniprot", tags=["public reference data"])


async def within_budget(context: ServiceContext, requester: str, token: str) -> None:
    """One caller cannot turn this API into unbounded load on a public provider."""
    budget = Budget(SCOPE_UNIPROT, context.settings.uniprot_requests_per_minute, MINUTE_SECONDS)
    if not (await spend(context, token, requester, budget, context.uniprot_limiter)).allowed:
        raise HTTPException(429, "uniprot-rate-limit")


async def search_entries(context: ServiceContext, query: str, requester: str, token: str) -> SearchResult:
    # Shape first: a query that never reaches the provider does not spend the caller's budget.
    if not MIN_QUERY_LENGTH <= len(query.strip()) <= MAX_QUERY_LENGTH:
        raise HTTPException(422, "uniprot-query")
    await within_budget(context, requester, token)
    payload = await retrieve(context, "search", {"query": query.strip(), "format": "json", "size": UNIPROT_SEARCH_SIZE + 1, "fields": "accession,id,protein_name,organism_name,length,reviewed"})
    try:
        results = payload["results"]
        if not isinstance(results, list):
            raise ValueError("Invalid results")
        items = [summary(result) for result in results[:UNIPROT_SEARCH_SIZE]]
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise HTTPException(502, "uniprot-invalid-response") from error
    return SearchResult(items=items, truncated=len(results) > UNIPROT_SEARCH_SIZE, retrieved_at=datetime.now(timezone.utc).isoformat())


async def fetch_entry(context: ServiceContext, accession: str, requester: str, token: str) -> UniProtEntry:
    if not re.fullmatch(ACCESSION_PATTERN, accession):
        raise HTTPException(422, "uniprot-accession")
    await within_budget(context, requester, token)
    payload = await retrieve(context, f"{accession}.json")
    try:
        result = entry(payload)
        if result.accession != accession:
            raise ValueError("Accession mismatch")
        return result
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise HTTPException(502, "uniprot-invalid-response") from error


@router.get("/search", response_model=SearchResult)
async def search(request: Request, identity: CurrentIdentity, query: str = Query(min_length=MIN_QUERY_LENGTH, max_length=MAX_QUERY_LENGTH)):
    return await search_entries(context_of(request), query, identity.id, bearer_token(request))


@router.get("/{accession}", response_model=UniProtEntry)
async def detail(request: Request, identity: CurrentIdentity, accession: Annotated[str, Path(pattern=f"^{ACCESSION_PATTERN}$")]):
    return await fetch_entry(context_of(request), accession, identity.id, bearer_token(request))

