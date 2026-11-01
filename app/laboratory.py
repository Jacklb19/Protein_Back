"""Bounded portable laboratory envelopes. Scientific validation stays in the browser Worker."""
import base64
import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel
from .folding_models import FoldResult

MAX_LABORATORY_BYTES = 1_000_000


def laboratory_document(text: str) -> dict:
    """Validate structural inputs only; never extract ZIPs or execute their contents."""
    if len(text.encode("utf-8")) > MAX_LABORATORY_BYTES:
        raise ValueError("Laboratory size")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate laboratory field")
            result[key] = value
        return result
    def reject_constant(_value):
        raise ValueError("Invalid number")
    try:
        doc = json.loads(text, object_pairs_hook=pairs, parse_constant=reject_constant)
        version = doc.get("schemaVersion")
        expected_keys = {"kind", "schemaVersion", "source", "state"} | ({"predictions"} if version == 2 else set())
        if set(doc) != expected_keys or doc["kind"] != "proteinforge-laboratory" or type(version) is not int or version not in (1, 2):
            raise ValueError()
        source, state = doc["source"], doc["state"]
        if source.get("type") == "example":
            if source != {"type": "example", "id": "1ubq-colab-20261021-v1"}:
                raise ValueError()
        elif source.get("type") == "archive":
            if set(source) != {"type", "sha256", "base64"} or not re.fullmatch(r"[a-f0-9]{64}", source["sha256"]):
                raise ValueError()
            raw = base64.b64decode(source["base64"], validate=True)
            if not raw or base64.b64encode(raw).decode() != source["base64"] or hashlib.sha256(raw).hexdigest() != source["sha256"]:
                raise ValueError()
        else:
            raise ValueError()
        if set(state) != {"activeId", "selected", "drafts"} or not 1 <= len(state["drafts"]) <= 3 or state["activeId"] not in state["drafts"] or type(state["selected"]) is not int:
            raise ValueError()
        for key, draft in state["drafts"].items():
            if not re.fullmatch(r"[a-f0-9]{64}", key) or set(draft) != {"sequence", "protectedPositions"} or not re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWY]{3,120}", draft["sequence"]):
                raise ValueError()
            positions = draft["protectedPositions"]
            if not isinstance(positions, list) or any(type(i) is not int or not 0 <= i < len(draft["sequence"]) for i in positions) or len(set(positions)) != len(positions):
                raise ValueError()
        if not 0 <= state["selected"] < len(state["drafts"][state["activeId"]]["sequence"]):
            raise ValueError()
        if version == 2:
            predictions = doc["predictions"]
            if not isinstance(predictions, dict) or len(predictions) > 3:
                raise ValueError()
            for variant_id, prediction in predictions.items():
                if variant_id not in state["drafts"] or not isinstance(prediction, dict) or type(prediction.get("schemaVersion")) is not int:
                    raise ValueError()
                if set(prediction) != {field.alias for field in FoldResult.model_fields.values()}:
                    raise ValueError()
                result = FoldResult.model_validate(prediction)
                if result.sequence != state["drafts"][variant_id]["sequence"]:
                    raise ValueError()
        return doc
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        raise ValueError("Invalid laboratory document") from error


class LaboratorySnapshot(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")
    schema_version: Literal[1]
    document_json: str = Field(max_length=MAX_LABORATORY_BYTES)
    document_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_document(self):
        if hashlib.sha256(self.document_json.encode("utf-8")).hexdigest() != self.document_sha256:
            raise ValueError("Laboratory hash")
        laboratory_document(self.document_json)
        return self
