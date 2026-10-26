"""Authenticated reference mapping, shared by REST and RPC. No submitted sequence is accepted."""
import asyncio
import hashlib
import re
from time import monotonic
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import Field

from .context import ServiceContext, context_of
from .identity import CurrentIdentity
from .sifts import MappingRecord, PDB_PATTERN, ResidueMapping, SiftsError, SourceLimits, fetch_mapping
from .uniprot import ACCESSION_PATTERN, fetch_entry

MAPPING_TIMEOUT_SECONDS = 20.0
MAPPING_RESPONSE_MAX_BYTES = 2_000_000


class ResidueMappingResponse(MappingRecord):
    mapping: ResidueMapping
    reference_sequence_version: int = Field(strict=True, gt=0, le=2_147_483_647)
    reference_sequence_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


SOURCE_STATUS = {
    'sifts-invalid-request': 422, 'sifts-not-found': 404,
    'sifts-provider-rate-limit': 429, 'sifts-source-too-large': 502,
    'sifts-timeout': 504,
}


async def get_residue_mapping(context: ServiceContext, pdb_id: str, accession: str,
                              sequence_version: int, requester: str) -> ResidueMappingResponse:
    if (not re.fullmatch(PDB_PATTERN, pdb_id) or not re.fullmatch(ACCESSION_PATTERN, accession)
            or type(sequence_version) is not int or not 0 < sequence_version <= 2_147_483_647):
        raise HTTPException(422, 'sifts-invalid-request')
    deadline = monotonic() + MAPPING_TIMEOUT_SECONDS
    try:
        async with asyncio.timeout(MAPPING_TIMEOUT_SECONDS):
            # Reuse the reference budget even on cache hits. UniProt is checked afresh:
            # cached SIFTS cannot vouch for the current full reference or version.
            entry = await fetch_entry(context, accession, requester)
            if entry.sequence_version != sequence_version:
                raise HTTPException(409, 'sifts-reference-version')
            mapping = context.sifts_cache.get(pdb_id)
            if mapping is None:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise HTTPException(504, 'sifts-timeout')
                mapping = await fetch_mapping(context.http_client, pdb_id,
                                              limits=SourceLimits(timeout_seconds=remaining))
                context.sifts_cache.put(mapping)
            rows = tuple(row for row in mapping.rows if row.accession == accession)
            if any(row.position > len(entry.sequence) or entry.sequence[row.position - 1] != row.residue for row in rows):
                raise HTTPException(409, 'sifts-reference-mismatch')
            result = ResidueMappingResponse(
                mapping=mapping.model_copy(update={'rows': rows}),
                reference_sequence_version=entry.sequence_version,
                reference_sequence_sha256=hashlib.sha256(entry.sequence.encode('ascii')).hexdigest())
            if len(result.model_dump_json(by_alias=True).encode('utf-8')) > MAPPING_RESPONSE_MAX_BYTES:
                raise HTTPException(413, 'sifts-response-too-large')
            if monotonic() >= deadline:
                raise HTTPException(504, 'sifts-timeout')
            return result
    except SiftsError as error:
        raise HTTPException(SOURCE_STATUS.get(error.code, 502), error.code) from error
    except TimeoutError as error:
        raise HTTPException(504, 'sifts-timeout') from error


router = APIRouter(prefix='/pdb', tags=['public reference data'])


@router.get('/{pdb_id}/residue-mapping', response_model=ResidueMappingResponse)
async def detail(request: Request, identity: CurrentIdentity,
                 pdb_id: Annotated[str, Path(pattern=f'^{PDB_PATTERN}$')],
                 accession: Annotated[str, Query(pattern=f'^{ACCESSION_PATTERN}$')],
                 sequence_version: Annotated[int, Query(gt=0, le=2_147_483_647)]):
    return await get_residue_mapping(context_of(request), pdb_id, accession, sequence_version, identity.id)
