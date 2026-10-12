"""Bounded, authenticated access to public UniProt annotations. Never forwards a user's JWT."""
from datetime import datetime, timezone
import re
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import Field

from .identity import CurrentIdentity
from .projects import Contract

UNIPROT_BASE_URL = "https://rest.uniprot.org/uniprotkb"
UNIPROT_TIMEOUT_SECONDS = 12.0
UNIPROT_SEARCH_SIZE = 10
MAX_UNIPROT_BYTES = 8_000_000
ACCESSION_PATTERN = r"(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})"


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


class UniProtEntry(EntrySummary):
    sequence: str = Field(pattern=r"^[ACDEFGHIKLMNPQRSTVWYUOBZX]+$", max_length=100_000)
    entry_version: int = Field(gt=0)
    sequence_version: int = Field(gt=0)
    retrieved_at: str
    function: str | None
    structures: list[PdbReference]


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
    return UniProtEntry(**record.model_dump(), sequence=sequence, entry_version=audit["entryVersion"], sequence_version=audit["sequenceVersion"], retrieved_at=datetime.now(timezone.utc).isoformat(), function="\n".join(functions) or None, structures=structures)


async def retrieve(request: Request, path: str, params: dict | None = None):
    # Fixed destination and separately constructed headers: private identity is never sent to UniProt.
    try:
        response = await request.app.state.http_client.get(f"{UNIPROT_BASE_URL}/{path}", params=params, headers={"Accept": "application/json"}, timeout=UNIPROT_TIMEOUT_SECONDS)
    except httpx.TimeoutException as error:
        raise HTTPException(504, "uniprot-timeout") from error
    except httpx.HTTPError as error:
        raise HTTPException(502, "uniprot-unreachable") from error
    if response.status_code == 404:
        raise HTTPException(404, "uniprot-not-found")
    if response.status_code == 400:
        raise HTTPException(422, "uniprot-query")
    if response.status_code == 429:
        raise HTTPException(429, "uniprot-rate-limit")
    if response.status_code != 200 or len(response.content) > MAX_UNIPROT_BYTES:
        raise HTTPException(502, "uniprot-provider-error")
    try:
        return response.json()
    except ValueError as error:
        raise HTTPException(502, "uniprot-invalid-response") from error


router = APIRouter(prefix="/uniprot", tags=["public reference data"])


@router.get("/search", response_model=SearchResult)
async def search(request: Request, _identity: CurrentIdentity, query: str = Query(min_length=2, max_length=200)):
    if len(query.strip()) < 2:
        raise HTTPException(422, "uniprot-query")
    payload = await retrieve(request, "search", {"query": query.strip(), "format": "json", "size": UNIPROT_SEARCH_SIZE + 1, "fields": "accession,id,protein_name,organism_name,length,reviewed"})
    try:
        results = payload["results"]
        if not isinstance(results, list):
            raise ValueError("Invalid results")
        items = [summary(result) for result in results[:UNIPROT_SEARCH_SIZE]]
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise HTTPException(502, "uniprot-invalid-response") from error
    return SearchResult(items=items, truncated=len(results) > UNIPROT_SEARCH_SIZE, retrieved_at=datetime.now(timezone.utc).isoformat())


@router.get("/{accession}", response_model=UniProtEntry)
async def detail(request: Request, _identity: CurrentIdentity, accession: Annotated[str, Path(pattern=f"^{ACCESSION_PATTERN}$")]):
    payload = await retrieve(request, f"{accession}.json")
    try:
        result = entry(payload)
        if result.accession != accession:
            raise ValueError("Accession mismatch")
        return result
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise HTTPException(502, "uniprot-invalid-response") from error
