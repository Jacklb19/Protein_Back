"""Internal, bounded acquisition of public residue mappings. No route or user data."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import re
from time import monotonic
from typing import Annotated, Literal
from xml.etree import ElementTree as ET
import zlib

import httpx
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pydantic.alias_generators import to_camel

SIFTS_NAMESPACE = "http://www.ebi.ac.uk/pdbe/docs/sifts/eFamily.xsd"
PDB_PATTERN = r"[0-9][A-Z0-9]{3}"
ACCESSION_PATTERN = r"(?:[A-Z0-9]{6}|[A-Z0-9]{10})(?:-[1-9][0-9]*)?"
RESIDUE_PATTERN = r"[ACDEFGHIKLMNPQRSTVWYUOBZX]"
MAX_RESIDUE_NUMBER = 100_000
Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128)]
Positive = Annotated[int, Field(strict=True, gt=0, le=100_000)]


@dataclass(frozen=True)
class SourceLimits:
    compressed_bytes: int = 2_000_000
    xml_bytes: int = 10_000_000
    json_bytes: int = 10_000_000
    residues: int = 100_000
    xml_elements: int = 750_000
    xml_depth: int = 32
    timeout_seconds: float = 20.0

    def __post_init__(self):
        for value in (self.compressed_bytes, self.xml_bytes, self.json_bytes,
                      self.residues, self.xml_elements, self.xml_depth):
            if type(value) is not int or value <= 0:
                raise ValueError("Invalid source limit")
        if not 0 < self.timeout_seconds <= 20:
            raise ValueError("Invalid source timeout")


DEFAULT_LIMITS = SourceLimits()


class SiftsError(ValueError):
    """Stable category only; provider bodies, identifiers and traces are never exposed."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _check_deadline(deadline: float | None):
    if deadline is not None and monotonic() >= deadline:
        raise SiftsError("sifts-timeout")


class MappingRecord(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True,
                              extra="forbid", strict=True, frozen=True)


class ResidueRow(MappingRecord):
    entity_id: Identifier
    label_asym_id: Identifier
    auth_asym_id: Identifier
    label_seq_id: Positive
    auth_seq_id: Identifier | None
    insertion_code: str = Field(max_length=16)
    segment_id: Identifier
    accession: str = Field(pattern=f"^{ACCESSION_PATTERN}$")
    position: Positive
    residue: str = Field(pattern=f"^{RESIDUE_PATTERN}$")
    not_observed: bool


class MappingProvenance(MappingRecord):
    provider: Literal["PDBe/SIFTS"] = "PDBe/SIFTS"
    xml_url: str
    residue_listing_url: str
    retrieved_at: str
    release_date: str


class ResidueMapping(MappingRecord):
    version: Literal[1] = 1
    pdb_id: str = Field(pattern=f"^{PDB_PATTERN}$")
    provenance: MappingProvenance
    rows: tuple[ResidueRow, ...] = Field(max_length=100_000)


def source_urls(pdb_id: str) -> tuple[str, str]:
    if not isinstance(pdb_id, str) or not re.fullmatch(PDB_PATTERN, pdb_id):
        raise SiftsError("sifts-invalid-request")
    key = pdb_id.lower()
    return (f"https://ftp.ebi.ac.uk/pub/databases/msd/sifts/xml/{key}.xml.gz",
            f"https://www.ebi.ac.uk/pdbe/api/pdb/entry/residue_listing/{key}")


class _BoundedTree(ET.TreeBuilder):
    def __init__(self, limits: SourceLimits, deadline: float | None):
        super().__init__()
        self.limits, self.depth, self.count = limits, 0, 0
        self.deadline = deadline

    def start(self, tag, attrs):
        self.depth += 1
        self.count += 1
        if self.count % 256 == 0:
            _check_deadline(self.deadline)
        if self.depth > self.limits.xml_depth or self.count > self.limits.xml_elements:
            raise SiftsError("sifts-source-too-large")
        return super().start(tag, attrs)

    def end(self, tag):
        self.depth -= 1
        return super().end(tag)

    def doctype(self, name, pubid, system):
        # Reject declarations in the parser itself, including UTF-16 encoded DTDs.
        raise SiftsError("sifts-invalid-response")


def _text(value, maximum=128):
    if not isinstance(value, str) or not 0 < len(value) <= maximum:
        raise SiftsError("sifts-invalid-response")
    return value


def _positive(value, maximum):
    if type(value) is not int or not 0 < value <= maximum:
        raise SiftsError("sifts-invalid-response")
    return value


def _array(value, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        raise SiftsError("sifts-invalid-response")
    return value


def _object(value):
    if not isinstance(value, dict):
        raise SiftsError("sifts-invalid-response")
    return value


def _listing_index(listing, pdb_id: str, limits: SourceLimits, deadline: float | None):
    molecules = _array(_object(_object(listing).get(pdb_id.lower())).get("molecules"), limits.residues)
    # Full corroborated identity makes each join constant-time even for duplicate input rows.
    lookup: dict[tuple[str, int, str, str], tuple[str, str, str | None, str] | None] = {}
    chain_identities: dict[str, tuple[str, str]] = {}
    residue_identities: dict[tuple[str, int], tuple[str | None, str, str]] = {}
    count = chains = 0
    for molecule in molecules:
        _check_deadline(deadline)
        entity = str(_positive(_object(molecule).get("entity_id"), MAX_RESIDUE_NUMBER))
        for chain in _array(molecule.get("chains"), limits.residues):
            chains += 1
            if chains % 256 == 0:
                _check_deadline(deadline)
            if chains > limits.residues:
                raise SiftsError("sifts-source-too-large")
            auth_chain = _text(_object(chain).get("chain_id"))
            label_chain = _text(chain.get("struct_asym_id"))
            chain_identity = (entity, auth_chain)
            if label_chain in chain_identities and chain_identities[label_chain] != chain_identity:
                raise SiftsError("sifts-invalid-response")
            chain_identities[label_chain] = chain_identity
            for item in _array(chain.get("residues"), limits.residues):
                count += 1
                if count % 256 == 0:
                    _check_deadline(deadline)
                if count > limits.residues:
                    raise SiftsError("sifts-source-too-large")
                label = _positive(_object(item).get("residue_number"), MAX_RESIDUE_NUMBER)
                author = item.get("author_residue_number")
                if author is not None and type(author) is not int and not isinstance(author, str):
                    raise SiftsError("sifts-invalid-response")
                author = None if author is None else _text(str(author))
                insertion = item.get("author_insertion_code")
                if not isinstance(insertion, str) or len(insertion) > 16:
                    raise SiftsError("sifts-invalid-response")
                author_number = "null" if author is None else author + insertion
                name = _text(item.get("residue_name"))
                residue_key = (label_chain, label)
                residue_identity = (author, insertion, name)
                if residue_key in residue_identities and residue_identities[residue_key] != residue_identity:
                    raise SiftsError("sifts-invalid-response")
                residue_identities[residue_key] = residue_identity
                key = (auth_chain, label, author_number, name)
                row = (entity, label_chain, author, insertion)
                # Keep ambiguity explicit without accumulating an unbounded candidate list.
                lookup[key] = None if key in lookup else row
    return lookup


def _attribute(element: ET.Element, name: str) -> str:
    return _text(element.get(name))


def _integer(element: ET.Element, name: str, maximum: int) -> int:
    raw = _attribute(element, name)
    if not re.fullmatch(r"[0-9]+", raw):
        raise SiftsError("sifts-invalid-response")
    return _positive(int(raw), maximum)


def normalise_mapping(xml: bytes, listing: object, pdb_id: str, *,
                      retrieved_at: datetime | None = None,
                      limits: SourceLimits = DEFAULT_LIMITS,
                      _deadline: float | None = None) -> ResidueMapping:
    """Join explicit residue identities, never segment endpoints or sequence offsets."""
    xml_url, listing_url = source_urls(pdb_id)
    if not xml or len(xml) > limits.xml_bytes:
        raise SiftsError("sifts-source-too-large")
    _check_deadline(_deadline)
    try:
        root = ET.fromstring(xml, parser=ET.XMLParser(target=_BoundedTree(limits, _deadline)))
        ns = f"{{{SIFTS_NAMESPACE}}}"
        if root.tag != ns + "entry" or _attribute(root, "dbAccessionId").upper() != pdb_id:
            raise SiftsError("sifts-invalid-response")
        release = _attribute(root, "date")
        if date.fromisoformat(release).isoformat() != release:
            raise SiftsError("sifts-invalid-response")
        lookup = _listing_index(listing, pdb_id, limits, _deadline)
        rows: list[ResidueRow] = []
        seen = {}
        observations: dict[tuple[str, int], bool] = {}
        reference_residues: dict[tuple[str, int], str] = {}
        residue_count = 0
        for entity in root.findall(ns + "entity"):
            for segment in entity.findall(ns + "segment"):
                segment_id = _attribute(segment, "segId")
                for group in segment.findall(ns + "listResidue"):
                    for residue in group.findall(ns + "residue"):
                        residue_count += 1
                        if residue_count % 256 == 0:
                            _check_deadline(_deadline)
                        if residue_count > limits.residues:
                            raise SiftsError("sifts-source-too-large")
                        refs = residue.findall(ns + "crossRefDb")
                        uniprot = [ref for ref in refs if ref.get("dbSource") == "UniProt"]
                        if not uniprot:
                            continue
                        if residue.get("dbSource") != "PDBe" or residue.get("dbCoordSys") != "PDBe":
                            raise SiftsError("sifts-invalid-response")
                        pdb_refs = [ref for ref in refs if ref.get("dbSource") == "PDB"]
                        if len(pdb_refs) != 1:
                            raise SiftsError("sifts-invalid-response")
                        pdb = pdb_refs[0]
                        if _attribute(pdb, "dbAccessionId").upper() != pdb_id or pdb.get("dbCoordSys") != "PDBresnum":
                            raise SiftsError("sifts-invalid-response")
                        auth_chain = _attribute(pdb, "dbChainId")
                        label = _integer(residue, "dbResNum", MAX_RESIDUE_NUMBER)
                        author_number, name = _attribute(pdb, "dbResNum"), _attribute(pdb, "dbResName")
                        candidate = lookup.get((auth_chain, label, author_number, name))
                        if candidate is None:
                            raise SiftsError("sifts-invalid-response")
                        entity_id, label_chain, author, insertion = candidate
                        absent = any(item.get("dbSource") == "PDBe" and item.get("property") == "Annotation"
                                     and (item.text or "").strip().lower() == "not_observed"
                                     for item in residue.findall(ns + "residueDetail"))
                        coordinate_key = (label_chain, label)
                        if coordinate_key in observations and observations[coordinate_key] != absent:
                            raise SiftsError("sifts-invalid-response")
                        observations[coordinate_key] = absent
                        for ref in uniprot:
                            if len(rows) >= limits.residues or ref.get("dbCoordSys") != "UniProt":
                                raise SiftsError("sifts-invalid-response")
                            row = ResidueRow(entity_id=entity_id, label_asym_id=label_chain,
                                             auth_asym_id=auth_chain, label_seq_id=label, auth_seq_id=author,
                                             insertion_code=insertion, segment_id=segment_id,
                                             accession=_attribute(ref, "dbAccessionId"),
                                             position=_integer(ref, "dbResNum", MAX_RESIDUE_NUMBER),
                                             residue=_attribute(ref, "dbResName"), not_observed=absent)
                            reference_key = (row.accession, row.position)
                            if reference_key in reference_residues and reference_residues[reference_key] != row.residue:
                                raise SiftsError("sifts-invalid-response")
                            reference_residues[reference_key] = row.residue
                            identity = (entity_id, label_chain, auth_chain, label, author, insertion, row.accession, row.position)
                            annotation = (row.residue, row.not_observed)
                            if identity in seen and seen[identity] != annotation:
                                raise SiftsError("sifts-invalid-response")
                            seen[identity] = annotation
                            rows.append(row)
        # A UniProt cross-reference in an unsupported XML layout must not become "no mapping".
        references = 0
        for index, item in enumerate(root.iter()):
            if index % 256 == 0:
                _check_deadline(_deadline)
            if item.tag.rsplit("}", 1)[-1] == "crossRefDb" and item.get("dbSource") == "UniProt":
                if item.tag != ns + "crossRefDb":
                    raise SiftsError("sifts-invalid-response")
                references += 1
        if references != len(rows):
            raise SiftsError("sifts-invalid-response")
        stamp = retrieved_at or datetime.now(timezone.utc)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise SiftsError("sifts-invalid-request")
        _check_deadline(_deadline)
        return ResidueMapping(pdb_id=pdb_id, rows=tuple(rows), provenance=MappingProvenance(
            xml_url=xml_url, residue_listing_url=listing_url, retrieved_at=stamp.isoformat(), release_date=release))
    except SiftsError:
        raise
    except (ET.ParseError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        raise SiftsError("sifts-invalid-response") from error


async def _download(client: httpx.AsyncClient, url: str, limit: int, timeout: float) -> bytes:
    # Construct a fresh request: client defaults, cookies, API keys and JWTs are not inherited.
    request = httpx.Request("GET", url, headers={"Accept-Encoding": "identity"},
                            extensions={"timeout": httpx.Timeout(timeout).as_dict()})
    response = await client.send(request, stream=True, auth=None, follow_redirects=False)
    try:
        if response.status_code == 404:
            raise SiftsError("sifts-not-found")
        if response.status_code == 429:
            raise SiftsError("sifts-provider-rate-limit")
        if response.status_code != 200:
            raise SiftsError("sifts-provider-error")
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise SiftsError("sifts-invalid-response")
        length = response.headers.get("Content-Length")
        if length is not None and (not length.isascii() or not length.isdecimal() or int(length) > limit):
            raise SiftsError("sifts-source-too-large")
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_raw(chunk_size=65_536):
            size += len(chunk)
            if size > limit:
                raise SiftsError("sifts-source-too-large")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        await response.aclose()


def _gunzip(data: bytes, limit: int) -> bytes:
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    output = bytearray()
    try:
        for offset in range(0, len(data), 65_536):
            output.extend(decoder.decompress(data[offset:offset + 65_536], limit - len(output) + 1))
            if len(output) > limit:
                raise SiftsError("sifts-source-too-large")
            if decoder.unused_data:
                raise SiftsError("sifts-invalid-response")
        if not decoder.eof:
            raise SiftsError("sifts-invalid-response")
    except zlib.error as error:
        raise SiftsError("sifts-invalid-response") from error
    return bytes(output)


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SiftsError("sifts-invalid-response")
        result[key] = value
    return result


def _invalid_constant(_):
    raise SiftsError("sifts-invalid-response")


async def fetch_mapping(client: httpx.AsyncClient, pdb_id: str, *,
                        limits: SourceLimits = DEFAULT_LIMITS) -> ResidueMapping:
    """Preparation for a future authenticated service; not mounted by the application."""
    xml_url, listing_url = source_urls(pdb_id)
    deadline = monotonic() + limits.timeout_seconds
    try:
        async with asyncio.timeout(limits.timeout_seconds):
            compressed = await _download(client, xml_url, limits.compressed_bytes, limits.timeout_seconds)
            xml = _gunzip(compressed, limits.xml_bytes)
            raw_listing = await _download(client, listing_url, limits.json_bytes, limits.timeout_seconds)
            listing = json.loads(raw_listing.decode("utf-8"), object_pairs_hook=_json_object,
                                 parse_constant=_invalid_constant)
            return normalise_mapping(xml, listing, pdb_id, limits=limits, _deadline=deadline)
    except SiftsError:
        raise
    except (TimeoutError, httpx.TimeoutException) as error:
        raise SiftsError("sifts-timeout") from error
    except httpx.HTTPError as error:
        raise SiftsError("sifts-unreachable") from error
    except (ValueError, UnicodeError, RecursionError) as error:
        raise SiftsError("sifts-invalid-response") from error
