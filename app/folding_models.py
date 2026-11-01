"""Exact-sequence PDB envelopes. Missing provider metrics are never inferred."""

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
from pydantic.alias_generators import to_camel

from .folding_config import (
    FOLDING_MODEL, FOLDING_PROVIDER, FOLDING_URL, MAX_ATOMS_PER_RESIDUE,
    MAX_COORDINATE_MAGNITUDE, MAX_FOLDING_BYTES, MAX_FOLDING_RESIDUES, MIN_FOLDING_RESIDUES,
)

AMINO_ACIDS = dict(zip(
    "ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL".split(),
    "ARNDCQEGHILKMFPSTWYV",
))
BACKBONE_ATOMS = {"N", "CA", "C", "O"}
PDB_RECORDS = {"HEADER", "TITLE", "REMARK", "COMPND", "SOURCE", "KEYWDS", "EXPDTA", "AUTHOR", "REVDAT", "JRNL", "DBREF", "SEQRES", "CRYST1", "ORIGX1", "ORIGX2", "ORIGX3", "SCALE1", "SCALE2", "SCALE3", "MODEL", "ATOM", "TER", "ENDMDL", "END", "MASTER"}


def sequence_hash(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("ascii")).hexdigest()


def validate_pdb(pdb: str, expected_sequence: str) -> None:
    """One explicit chain, exact residues, complete finite backbone; no code is executed."""
    if len(pdb.encode("utf-8")) > MAX_FOLDING_BYTES or "\x00" in pdb:
        raise ValueError("PDB size or encoding")
    residues: dict[tuple[int, str], tuple[str, set[str]]] = {}
    model_count = 0
    ended = False
    atoms = 0
    for line in pdb.splitlines():
        if not line.strip():
            continue
        record = line[:6].strip()
        if record not in PDB_RECORDS or len(line) > 200:
            raise ValueError("Invalid PDB record")
        if record == "MODEL":
            model_count += 1
            if model_count > 1 or ended:
                raise ValueError("Multiple PDB models")
        if record in ("ENDMDL", "END"):
            ended = True
        if record != "ATOM":
            continue
        if ended or len(line) < 66 or line[21] != "A" or line[16] not in (" ", "A"):
            raise ValueError("Ambiguous PDB chain or atom")
        name = line[17:20]
        atom = line[12:16].strip()
        insertion = line[26].strip()
        if name not in AMINO_ACIDS or not atom or not re.fullmatch(r"[A-Za-z]?", insertion):
            raise ValueError("Invalid PDB residue")
        key = (int(line[22:26]), insertion)
        coordinates = [float(line[start:end]) for start, end in ((30, 38), (38, 46), (46, 54))]
        occupancy = float(line[54:60])
        b_factor = float(line[60:66])
        if not all(math.isfinite(value) for value in (*coordinates, occupancy, b_factor)) or occupancy <= 0:
            raise ValueError("Non-finite PDB coordinates")
        if any(abs(value) >= MAX_COORDINATE_MAGNITUDE for value in coordinates):
            raise ValueError("PDB coordinate bounds")
        if key not in residues:
            if residues and key <= next(reversed(residues)):
                raise ValueError("Unordered PDB residues")
            residues[key] = (name, set())
        elif key != next(reversed(residues)):
            raise ValueError("Interleaved PDB residues")
        residue_name, present = residues[key]
        if residue_name != name or atom in present or len(present) >= MAX_ATOMS_PER_RESIDUE:
            raise ValueError("Duplicate or ambiguous PDB atoms")
        present.add(atom)
        atoms += 1
        if len(residues) > MAX_FOLDING_RESIDUES or atoms > MAX_FOLDING_RESIDUES * MAX_ATOMS_PER_RESIDUE:
            raise ValueError("PDB bounds")
    if not residues or "".join(AMINO_ACIDS[name] for name, _ in residues.values()) != expected_sequence:
        raise ValueError("PDB sequence mismatch")
    if any(not BACKBONE_ATOMS <= present for _, present in residues.values()):
        raise ValueError("Incomplete PDB backbone")


class FoldingContract(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid", allow_inf_nan=False)


class FoldRequest(FoldingContract):
    sequence: str = Field(min_length=MIN_FOLDING_RESIDUES, max_length=MAX_FOLDING_RESIDUES, pattern=r"^[ACDEFGHIKLMNPQRSTVWY]+$")
    consent_to_send_sequence: StrictBool


class FoldResult(FoldingContract):
    schema_version: Literal[1] = 1
    sequence: str = Field(min_length=MIN_FOLDING_RESIDUES, max_length=MAX_FOLDING_RESIDUES, pattern=r"^[ACDEFGHIKLMNPQRSTVWY]+$")
    sequence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    pdb: str = Field(min_length=1, max_length=MAX_FOLDING_BYTES)
    pdb_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider: Literal[FOLDING_PROVIDER] = FOLDING_PROVIDER
    model: Literal[FOLDING_MODEL] = FOLDING_MODEL
    model_revision: None = None
    retrieved_at: str = Field(max_length=64)
    source_url: Literal[FOLDING_URL] = FOLDING_URL
    ptm: None = None
    pae: None = None
    confidence: None = None

    @field_validator("retrieved_at")
    @classmethod
    def require_timezone(cls, value: str) -> str:
        if datetime.fromisoformat(value).tzinfo is None:
            raise ValueError("Timezone required")
        return value

    @model_validator(mode="after")
    def exact_document(self):
        if self.sequence_sha256 != sequence_hash(self.sequence) or self.pdb_sha256 != hashlib.sha256(self.pdb.encode("utf-8")).hexdigest():
            raise ValueError("Prediction hash")
        validate_pdb(self.pdb, self.sequence)
        return self
