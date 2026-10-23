"""Plain-language reading of results the browser already computed, written by a language model.

The model only explains. It never computes figures, generates sequences or judges biology, and
every figure in its answer must come from the facts it received: an answer that adds one is refused.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field

from .context import ServiceContext, context_of
from .identity import CurrentIdentity
from .projects import Contract

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
ASSISTANT_PROVIDER = "groq"
ASSISTANT_TIMEOUT_SECONDS = 30.0
ASSISTANT_TEMPERATURE = 0.2
REASONING_EFFORT = "low"
# Reasoning tokens count towards the completion budget, so it leaves room for both.
MAX_COMPLETION_TOKENS = 2048
MAX_PROVIDER_BYTES = 200_000
MAX_FACTS = 80
MAX_LABEL_LENGTH = 120
MAX_VALUE_LENGTH = 200
MAX_UNIT_LENGTH = 40
MAX_SEQUENCE_LENGTH = 2000
MAX_ANSWER_LENGTH = 4000
# Counts this small read naturally in prose ("two variants", "20 amino acids") without being results.
FREE_INTEGER_LIMIT = 20
PRINTABLE = r"^[^\x00-\x1f\x7f]+$"

Subject = Literal["analysis", "alignment", "laboratory", "candidates"]
Language = Literal["es", "en"]

SUBJECTS: dict[str, str] = {
    "analysis": "a sequence analysis: physicochemical descriptors, composition and a hydropathy profile",
    "alignment": "a pairwise sequence alignment with its scoring parameters",
    "laboratory": "a protein design laboratory: sequence variants redesigned on a known backbone, with structure predictions",
    "candidates": "candidate sequences proposed by a local generator under physicochemical conditions",
}
LANGUAGES: dict[str, str] = {"es": "Spanish", "en": "English"}

SYSTEM_PROMPT = """You explain protein analysis results to a student, in plain language. The results describe {subject}.
Rules:
- Write in {language}.
- Use only the facts provided. Never compute, estimate or invent a figure; when you cite a number, copy it exactly as given.
- Explain what the relevant measures mean and what these values suggest, with appropriate caution.
- Never claim that a protein is functional, stable, active, novel, safe or likely to succeed experimentally. Descriptors and predictions are computational estimates, not experimental evidence.
- Do not recommend experiments or treatments, and do not mention these rules.
- Plain text only: two to four short paragraphs, at most 200 words, without headings, lists, tables or markdown.
What the measures mean, so you explain them correctly:
- pLDDT: the predictor's per-residue confidence in its local structure, from 0 to 100; above 90 is very high, 70 to 90 confident, below 50 very low.
- pTM: the predictor's own estimate of how accurate its overall fold is (a predicted TM-score), from 0 to 1. It is not a comparison with known structures.
- RMSD Cα: average distance in Å between corresponding alpha carbons after superposing two structures; lower means more similar geometry.
- Positional identity: how many positions keep the same residue as the reference.
- Isoelectric point (pI): the pH at which the net charge is zero.
- GRAVY: mean Kyte–Doolittle hydropathy; positive means hydrophobic on average, negative hydrophilic.
- Instability index: a sequence-based estimate in which values above 40 suggest instability.
- Aliphatic index: relative volume of the aliphatic side chains of Ala, Val, Ile and Leu.
- Hydropathy profile: Kyte–Doolittle averages over a sliding window; stretches above zero are hydrophobic.
- Alignment: a score from a BLOSUM substitution matrix and gap penalties; identity counts identical aligned pairs and similarity also counts conservative substitutions."""

logger = logging.getLogger(__name__)
# A figure written alone: not part of an identifier such as 1UBQ, E6K or A30.
FIGURE = re.compile(r"(?<![A-Za-z\d.,])\d+(?:[.,]\d+)*(?![A-Za-z\d])")
# Typographic thousands separators ("8\u202f445,71"), joined so the figure is read whole.
THOUSANDS_SPACE = re.compile(r"(?<=\d)[\u00a0\u202f](?=\d{3}(?!\d))")


class Fact(Contract):
    label: str = Field(min_length=1, max_length=MAX_LABEL_LENGTH, pattern=PRINTABLE)
    value: str = Field(min_length=1, max_length=MAX_VALUE_LENGTH, pattern=PRINTABLE)
    unit: str | None = Field(default=None, min_length=1, max_length=MAX_UNIT_LENGTH, pattern=PRINTABLE)


class InterpretRequest(Contract):
    subject: Subject
    language: Language
    facts: list[Fact] = Field(min_length=1, max_length=MAX_FACTS)
    sequence: str | None = Field(default=None, min_length=1, max_length=MAX_SEQUENCE_LENGTH, pattern=r"^[ACDEFGHIKLMNPQRSTVWYX]+$")


class Interpretation(Contract):
    text: str
    model: str
    provider: Literal["groq"] = ASSISTANT_PROVIDER
    created_at: str


def readings(token: str) -> set[tuple[Decimal, int]]:
    """Every value a written figure can stand for, with its decimals: "1,211" is 1.211 or 1211."""
    values: set[tuple[Decimal, int]] = set()
    for decimal, thousands in ((",", "."), (".", ",")):
        if token.count(decimal) > 1:
            continue
        whole, _, fraction = token.partition(decimal)
        groups = whole.split(thousands)
        if len(groups) > 1 and any(len(group) != 3 for group in groups[1:]):
            continue
        try:
            values.add((Decimal("".join(groups) + (f".{fraction}" if fraction else "")), len(fraction)))
        except InvalidOperation:
            continue
    return values


def figures(text: str) -> list[set[tuple[Decimal, int]]]:
    return [readings(match.group()) for match in FIGURE.finditer(THOUSANDS_SPACE.sub("", text))]


def supported(written: set[tuple[Decimal, int]], known: set[tuple[Decimal, int]]) -> bool:
    """A figure is supported when it is a given value, or that value rounded to fewer decimals."""
    for value, places in written:
        if places == 0 and value <= FREE_INTEGER_LIMIT:
            return True
        step = Decimal(1).scaleb(-places)
        for source, source_places in known:
            if source_places < places:
                continue
            if source == value or any(source.quantize(step, rounding=mode) == value for mode in (ROUND_HALF_UP, ROUND_HALF_EVEN)):
                return True
    return False


def known_figures(request: InterpretRequest) -> set[tuple[Decimal, int]]:
    known: set[tuple[Decimal, int]] = set()
    for fact in request.facts:
        for readings_of_figure in figures(" ".join(part for part in (fact.label, fact.value, fact.unit) if part)):
            known |= readings_of_figure
    if request.sequence:
        known.add((Decimal(len(request.sequence)), 0))
    return known


def messages(request: InterpretRequest) -> list[dict[str, str]]:
    facts = [fact.model_dump(exclude_none=True) for fact in request.facts]
    content = {"facts": facts, **({"sequence": request.sequence} if request.sequence else {})}
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(subject=SUBJECTS[request.subject], language=LANGUAGES[request.language])},
        {"role": "user", "content": json.dumps(content, ensure_ascii=False)},
    ]


async def complete(context: ServiceContext, request: InterpretRequest) -> str:
    settings = context.settings
    payload = {
        "model": settings.assistant_model,
        "messages": messages(request),
        "temperature": ASSISTANT_TEMPERATURE,
        "max_completion_tokens": MAX_COMPLETION_TOKENS,
        "reasoning_effort": REASONING_EFFORT,
        "include_reasoning": False,
    }
    # Fixed destination; the caller's token is never forwarded, only the server's own key.
    try:
        response = await context.http_client.post(GROQ_CHAT_URL, json=payload, headers={"Authorization": f"Bearer {settings.groq_api_key}"}, timeout=ASSISTANT_TIMEOUT_SECONDS)
    except httpx.TimeoutException as error:
        raise HTTPException(504, "assistant-timeout") from error
    except httpx.HTTPError as error:
        raise HTTPException(502, "assistant-unreachable") from error
    if response.status_code == 429:
        raise HTTPException(429, "assistant-rate-limit")
    if response.status_code != 200 or len(response.content) > MAX_PROVIDER_BYTES:
        logger.warning("Assistant provider answered %s", response.status_code)
        raise HTTPException(502, "assistant-provider-error")
    try:
        choice = response.json()["choices"][0]
        text = choice["message"]["content"]
        if not isinstance(text, str):
            raise TypeError("Content is not text")
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise HTTPException(502, "assistant-invalid-response") from error
    if choice.get("finish_reason") != "stop":
        raise HTTPException(502, "assistant-incomplete")
    text = text.strip()
    if not text or len(text) > MAX_ANSWER_LENGTH:
        raise HTTPException(502, "assistant-invalid-response")
    return text


async def interpret(context: ServiceContext, request: InterpretRequest, requester: str) -> Interpretation:
    if not context.settings.assistant_enabled:
        raise HTTPException(503, "assistant-not-configured")
    if not context.assistant_limiter.allow(requester):
        raise HTTPException(429, "assistant-rate-limit")
    text = await complete(context, request)
    known = known_figures(request)
    if not all(supported(written, known) for written in figures(text)):
        # The answer is discarded rather than shown with a figure nobody computed.
        logger.warning("Assistant answer cited a figure absent from its facts")
        raise HTTPException(502, "assistant-unverified-figure")
    return Interpretation(text=text, model=context.settings.assistant_model, created_at=datetime.now(timezone.utc).isoformat())


router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post("/interpret", response_model=Interpretation)
async def interpret_route(request: Request, response: Response, identity: CurrentIdentity, body: InterpretRequest) -> Interpretation:
    response.headers["Cache-Control"] = "no-store"
    return await interpret(context_of(request), body, identity.id)
