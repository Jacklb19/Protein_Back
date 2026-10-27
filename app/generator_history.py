"""Bounded generator history envelopes. Candidates are checked for shape, never recalculated."""
import hashlib
import json
import math
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

MAX_HISTORY_BYTES = 100_000
MAX_RUN_BYTES = 10_000
MAX_RUNS = 10
RUN_ORIGIN = "local-generator"
# Values of the frontend generator (features/generator/config.ts) that a stored run must respect.
METHOD_VERSIONS = frozenset({"proteinforge-candidates-v1"})
MIN_LENGTH, MAX_LENGTH = 5, 300
MIN_BUDGET, MAX_BUDGET = 100, 5_000
MAX_SEED = 0xFFFFFFFF
RETAINED = 5
MAX_REFERENCES = 10
MAX_REFERENCE_LENGTH = 500
ARMS = ("guided", "reference")
APP_BUILD = re.compile(r"[0-9a-f]{7,40}")
SHA256 = re.compile(r"[a-f0-9]{64}")
CANDIDATE_SEQUENCE = re.compile(r"[ACDEFGHIKLMNPQRSTVWY]+")
DESCRIPTOR_FIELDS = {"massDa", "isoelectricPoint", "instabilityIndex", "aliphaticIndex", "gravy", "length"}


def _strict_json(text: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate field")
            result[key] = value
        return result

    def reject_constant(_value):
        raise ValueError("Invalid number")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=reject_constant)


def _integer(value, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError()
    return value


def _number(value) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError()
    return value


def _interval(value, low: float, high: float, exclusive_low: bool = False) -> bool:
    if not isinstance(value, dict) or set(value) != {"enabled", "minimum", "maximum"} or type(value["enabled"]) is not bool:
        raise ValueError()
    minimum, maximum = _number(value["minimum"]), _number(value["maximum"])
    if minimum > maximum or maximum > high or (minimum <= low if exclusive_low else minimum < low):
        raise ValueError()
    return value["enabled"]


def _arm(value, parameters: dict) -> int:
    if not isinstance(value, dict) or set(value) != {"evaluations", "matchingEvaluations", "bestLoss", "candidates", "minimumPairDistance"}:
        raise ValueError()
    evaluations = _integer(value["evaluations"], 1, parameters["budget"])
    _integer(value["matchingEvaluations"], 0, evaluations)
    if _number(value["bestLoss"]) < 0:
        raise ValueError()
    candidates = value["candidates"]
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= RETAINED:
        raise ValueError()
    for candidate in candidates:
        if not isinstance(candidate, dict) or set(candidate) != {"sequence", "descriptors", "loss", "satisfies"}:
            raise ValueError()
        sequence, descriptors = candidate["sequence"], candidate["descriptors"]
        if not isinstance(sequence, str) or not CANDIDATE_SEQUENCE.fullmatch(sequence) or len(sequence) != parameters["length"]:
            raise ValueError()
        if not isinstance(descriptors, dict) or set(descriptors) != DESCRIPTOR_FIELDS or _integer(descriptors["length"], 1, MAX_LENGTH) != len(sequence):
            raise ValueError()
        for field in DESCRIPTOR_FIELDS - {"length"}:
            _number(descriptors[field])
        if _number(candidate["loss"]) < 0 or type(candidate["satisfies"]) is not bool:
            raise ValueError()
    if len({candidate["sequence"] for candidate in candidates}) != len(candidates):
        raise ValueError()
    distance = value["minimumPairDistance"]
    if (distance is None) != (len(candidates) < 2) or (distance is not None and not 0 <= _number(distance) <= 1):
        raise ValueError()
    return len(candidates)


def generator_result(text: str) -> dict[str, int]:
    """Validate the exact exported search result; returns the candidate count of each arm."""
    if len(text.encode("utf-8")) > MAX_RUN_BYTES:
        raise ValueError("Generator run size")
    result = _strict_json(text)
    if not isinstance(result, dict) or set(result) != {"schemaVersion", "methodVersion", "parameters", "guided", "reference", "references"}:
        raise ValueError()
    if type(result["schemaVersion"]) is not int or result["schemaVersion"] != 1 or result["methodVersion"] not in METHOD_VERSIONS:
        raise ValueError()
    parameters = result["parameters"]
    if not isinstance(parameters, dict) or set(parameters) != {"length", "budget", "seed", "gravy", "massDa"}:
        raise ValueError()
    _integer(parameters["length"], MIN_LENGTH, MAX_LENGTH)
    _integer(parameters["budget"], MIN_BUDGET, MAX_BUDGET)
    _integer(parameters["seed"], 1, MAX_SEED)
    if not (_interval(parameters["gravy"], -4.5, 4.5) | _interval(parameters["massDa"], 0, 100_000, exclusive_low=True)):
        raise ValueError()
    references = result["references"]
    if not isinstance(references, list) or len(references) > MAX_REFERENCES or any(not isinstance(item, str) or not 0 < len(item) <= MAX_REFERENCE_LENGTH for item in references):
        raise ValueError()
    return {arm: _arm(result[arm], parameters) for arm in ARMS}


def generator_history_document(text: str) -> dict:
    """Validate structural inputs only; the browser Worker revalidates and may reproduce the search."""
    if len(text.encode("utf-8")) > MAX_HISTORY_BYTES:
        raise ValueError("Generator history size")
    try:
        doc = _strict_json(text)
        if not isinstance(doc, dict) or set(doc) != {"kind", "schemaVersion", "runs"} or doc["kind"] != "proteinforge-generator-history":
            raise ValueError()
        if type(doc["schemaVersion"]) is not int or doc["schemaVersion"] != 1:
            raise ValueError()
        runs = doc["runs"]
        if not isinstance(runs, list) or not 1 <= len(runs) <= MAX_RUNS:
            raise ValueError()
        for run in runs:
            if not isinstance(run, dict) or set(run) != {"id", "origin", "recordedAt", "appBuild", "resultJson", "selection"}:
                raise ValueError()
            if run["origin"] != RUN_ORIGIN or not isinstance(run["resultJson"], str) or not isinstance(run["id"], str) or not SHA256.fullmatch(run["id"]):
                raise ValueError()
            if hashlib.sha256(run["resultJson"].encode("utf-8")).hexdigest() != run["id"]:
                raise ValueError()
            if not isinstance(run["recordedAt"], str) or len(run["recordedAt"]) > 64 or datetime.fromisoformat(run["recordedAt"]).tzinfo is None:
                raise ValueError()
            if run["appBuild"] is not None and (not isinstance(run["appBuild"], str) or not APP_BUILD.fullmatch(run["appBuild"])):
                raise ValueError()
            counts = generator_result(run["resultJson"])
            selection = run["selection"]
            if selection is not None:
                if not isinstance(selection, dict) or set(selection) != {"arm", "index"} or selection["arm"] not in ARMS:
                    raise ValueError()
                _integer(selection["index"], 0, counts[selection["arm"]] - 1)
        if len({run["id"] for run in runs}) != len(runs):
            raise ValueError()
        return doc
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        raise ValueError("Invalid generator history document") from error


class GeneratorHistorySnapshot(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")
    schema_version: Literal[1]
    document_json: str = Field(max_length=MAX_HISTORY_BYTES)
    document_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_document(self):
        if hashlib.sha256(self.document_json.encode("utf-8")).hexdigest() != self.document_sha256:
            raise ValueError("Generator history hash")
        generator_history_document(self.document_json)
        return self

    def run_count(self) -> int:
        return len(json.loads(self.document_json)["runs"])
