"""Plain-language reading of results the browser already computed, written by a language model.

The model only explains. It never computes figures, generates sequences or judges biology, and
every figure in its answer must come from the facts it received: an answer that adds one is refused.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field

from .config import ASSISTANT_REMAINING_HEADER
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
# Bounds of the scales the glossary below teaches, which an explanation must be able to quote.
# They describe the measures, never this protein; keep them in step with SYSTEM_PROMPT.
SCALE_FIGURES = (40, 50, 70, 90, 100)
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
- Report fractions and positional identity as the supplied numerator and denominator. Do not convert them to percentages or describe them as more/less than half, a majority/minority, most/few, or a proportion; those are additional calculations, even when written without digits.
- For positional identity, quote the supplied value without further discussion. Do not discuss the number or proportion of substitutions. Do not infer a distribution of residue confidence from its mean: a mean is not a count of residues in any confidence category.
- Explain what the relevant measures mean and what each measure reports for these values, with appropriate caution.
- Cite no threshold, range or reference value other than those in the glossary below; if the glossary gives none for a measure, give none.
- Never say that all, every, none, or any share of the residues reach a level because of a mean: a mean says nothing about individual residues.
- Describe a mean local confidence only as one average over the whole chain that falls in a band of the scale. Do not attribute that confidence to residues, regions or parts of the chain, in any amount.
- Explain only the measures present in the facts. The glossary also covers measures that may be absent: skip those, and never state a value for them, not even zero or none.
- Do not compare with variants, structures, references or results that are not in the facts.
- Never claim that the protein folds, folds correctly, is stable or has a function: the pTM and pLDDT describe the predictor's confidence in its own model, not the protein.
- Never claim that a protein is functional, stable, active, novel, safe or likely to succeed experimentally. Descriptors and predictions are computational estimates, not experimental evidence.
- Say what each measure reports, not what the protein is: "the instability index is below the threshold it uses", never "the protein is stable". Never extend a figure to solubility, aggregation, thermostability, expression, folding or behaviour in a cell, because none of these is measured here.
- Do not recommend experiments or treatments, and do not mention these rules.
- Open with one sentence that says what these results are, in plain words and with no figure in it.
- Then write three or four paragraphs of two or three sentences each. Keep each paragraph to a single idea, and do not crowd more than two figures into one sentence.
- Plain text only: at most 180 words in total, without headings, lists, tables or markdown.
What the measures mean, so you explain them correctly:
- pLDDT: the predictor's per-residue confidence in its local structure, from 0 to 100; above 90 is very high, 70 to 90 confident, below 50 very low. A mean pLDDT is one average for the chain, not a count of confident residues.
- pTM: the predictor's own estimate of how accurate its overall fold is (a predicted TM-score), from 0 to 1. It is not a comparison with known structures.
- RMSD Cα: average distance in Å between corresponding alpha carbons after superposing two structures; lower means more similar geometry.
- Positional identity: how many positions keep the same residue as the reference.
- Protected positions: positions the user chose to keep fixed in a design. A count of zero means no positions are protected in that draft; it is not an alignment result or a detected structural feature.
- Isoelectric point (pI): the pH at which the net charge is zero.
- GRAVY: mean Kyte–Doolittle hydropathy; positive means hydrophobic on average, negative hydrophilic.
- Instability index: a sequence-based estimate in which values above 40 suggest instability.
- Aliphatic index: relative volume of the aliphatic side chains of Ala, Val, Ile and Leu.
- Hydropathy profile: Kyte–Doolittle averages over a sliding window; stretches above zero are hydrophobic.
- Alignment: a score from a BLOSUM substitution matrix and gap penalties; identity counts identical aligned pairs and similarity also counts conservative substitutions."""


def prompt_version(template: str = SYSTEM_PROMPT) -> str:
    """Fingerprint of everything that shapes a reading besides the facts, so editing any of it retires cached readings."""
    contract = {"prompt": template, "subjects": SUBJECTS, "languages": LANGUAGES, "temperature": ASSISTANT_TEMPERATURE, "reasoning": REASONING_EFFORT, "tokens": MAX_COMPLETION_TOKENS}
    return hashlib.sha256(json.dumps(contract, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


PROMPT_VERSION = prompt_version()

logger = logging.getLogger(__name__)
# A figure written alone: not part of an identifier such as 1UBQ, E6K or A30.
FIGURE = re.compile(r"(?<![A-Za-z\d.,])[+\-−]?\d+(?:[.,]\d+)*(?![A-Za-z\d])")
# Typographic thousands separators ("8\u202f445,71"), joined so the figure is read whole.
THOUSANDS_SPACE = re.compile(r"(?<=\d)[\u00a0\u202f](?=\d{3}(?!\d))")
# These verbal quantities can invent a calculation without introducing a new digit.
# Permit them only when the caller supplied that wording, just as with written figures.
VERBAL_PROPORTION = re.compile(r"\b(?:(?:more|less|fewer)\s+than\s+(?:a\s+)?half|(?:m[aá]s|menos)\s+de\s+la\s+mitad|half|majority|minority|most|few|quarter|third|mitad|mayor[ií]a|minor[ií]a|tercio|cuarta\s+parte)\b", re.IGNORECASE)
# English phrases that share those words without stating a share of anything: an upper bound
# ("at most") and the ordinal of a supplied item ("the third variant"). Only these exact phrases
# are set aside; any proportion word next to them is still checked.
NOT_A_PROPORTION = re.compile(r"\bat\s+most\b|\bthe\s+third\s+(?:variant|sample|design|candidate)s?\b", re.IGNORECASE)
PERCENT_AFTER = re.compile(r"\s*(?:%|por\s+ciento\b|percent\b|per\s+cent\b)", re.IGNORECASE)


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
    token = token.replace("−", "-")
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
        if places == 0 and 0 <= value <= FREE_INTEGER_LIMIT:
            return True
        step = Decimal(1).scaleb(-places)
        for source, source_places in known:
            if source_places < places:
                continue
            if source == value:
                return True
            try:
                if any(source.quantize(step, rounding=mode) == value for mode in (ROUND_HALF_UP, ROUND_HALF_EVEN)):
                    return True
            except InvalidOperation:
                # An oversized numeric fact must not crash verification of an unrelated figure.
                continue
    return False


def known_figures(request: InterpretRequest) -> set[tuple[Decimal, int]]:
    known: set[tuple[Decimal, int]] = set()
    for fact in request.facts:
        for readings_of_figure in figures(" ".join(part for part in (fact.label, fact.value, fact.unit) if part)):
            known |= readings_of_figure
    if request.sequence:
        known.add((Decimal(len(request.sequence)), 0))
    return known | {(Decimal(value), 0) for value in SCALE_FIGURES}


def verbal_term(match: re.Match[str]) -> str:
    return " ".join(match.group().split()).casefold()


def unsupported_proportions(text: str, request: InterpretRequest) -> list[str]:
    """Verbal quantities absent from fact values; this is not a full semantic verifier."""
    given = {verbal_term(match) for fact in request.facts for match in VERBAL_PROPORTION.finditer(fact.value)}
    checked = NOT_A_PROPORTION.sub(" ", text)
    return [term for term in map(verbal_term, VERBAL_PROPORTION.finditer(checked)) if term not in given]


def supported_proportions(text: str, request: InterpretRequest) -> bool:
    return not unsupported_proportions(text, request)


def figure_kind(text: str, match: re.Match[str], known: set[tuple[Decimal, int]]) -> str:
    """A coarse, data-free class of a refused figure, so a refusal can be diagnosed from logs."""
    if PERCENT_AFTER.match(text, match.end()):
        return "percent"
    written = readings(match.group())
    for value, places in written:
        unit = Decimal(1).scaleb(-places)
        # A supplied value or allowed bound, truncated or misrounded at the precision written (93,3 for 93,39).
        if any(source_places >= places and abs(source - value) <= unit for source, source_places in known):
            return "near-known"
    return "integer" if all(places == 0 for _, places in written) else "decimal"


def refusal(text: str, request: InterpretRequest) -> dict[str, int]:
    """Counts of why an answer cannot be shown, by kind only: never the figures or the prose."""
    known = known_figures(request)
    checked = THOUSANDS_SPACE.sub("", text)
    reasons: dict[str, int] = {}
    for match in FIGURE.finditer(checked):
        if not supported(readings(match.group()), known):
            kind = f"figure-{figure_kind(checked, match, known)}"
            reasons[kind] = reasons.get(kind, 0) + 1
    for term in unsupported_proportions(text, request):
        # ASCII keys keep the log readable on any console: "más de la mitad" is verbal-mas-de-la-mitad.
        kind = "verbal-" + unicodedata.normalize("NFKD", term).encode("ascii", "ignore").decode().replace(" ", "-")
        reasons[kind] = reasons.get(kind, 0) + 1
    return reasons


def messages(request: InterpretRequest) -> list[dict[str, str]]:
    facts = [fact.model_dump(exclude_none=True) for fact in request.facts]
    content = {"facts": facts, **({"sequence": request.sequence} if request.sequence else {})}
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(subject=SUBJECTS[request.subject], language=LANGUAGES[request.language])},
        {"role": "user", "content": json.dumps(content, ensure_ascii=False)},
    ]


def cache_key(request: InterpretRequest, requester: str, model: str, version: str) -> str:
    """Opaque key of one exact context. The caller is part of it: facts can come from private
    sequences, so a reading is only ever served back to whoever asked for it."""
    context = {
        "version": version,
        "model": model,
        "requester": requester,
        "subject": request.subject,
        "language": request.language,
        "facts": [fact.model_dump(exclude_none=True) for fact in request.facts],
        "sequence": request.sequence,
    }
    return hashlib.sha256(json.dumps(context, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


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
    key = cache_key(request, requester, context.settings.assistant_model, PROMPT_VERSION)
    cached = context.assistant_cache.get(key)
    if cached is not None:
        # The budget bounds what this process forwards to the provider; a reading served again forwards nothing.
        return cached
    if not context.assistant_limiter.allow(requester):
        raise HTTPException(429, "assistant-rate-limit")
    text = await complete(context, request)
    reasons = refusal(text, request)
    if reasons:
        # The answer is discarded rather than shown with a figure nobody computed. The log names
        # only kinds and counts (verbal terms come from the fixed list above), never figures or prose.
        logger.warning(
            "Assistant answer cited a figure or verbal proportion absent from its facts: subject=%s language=%s %s",
            request.subject, request.language, " ".join(f"{kind}={count}" for kind, count in sorted(reasons.items())),
        )
        raise HTTPException(502, "assistant-unverified-figure")
    interpretation = Interpretation(text=text, model=context.settings.assistant_model, created_at=datetime.now(timezone.utc).isoformat())
    # Stored only after the verifier accepted it: refusals and provider errors raised above never reach here.
    context.assistant_cache.put(key, interpretation)
    return interpretation


def remaining_header(context: ServiceContext, requester: str) -> dict[str, str]:
    """What the caller may still ask for this minute, as a response header; empty without a budget."""
    remaining = context.assistant_limiter.remaining(requester)
    return {} if remaining is None else {ASSISTANT_REMAINING_HEADER: str(remaining)}


router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post("/interpret", response_model=Interpretation)
async def interpret_route(request: Request, response: Response, identity: CurrentIdentity, body: InterpretRequest) -> Interpretation:
    response.headers["Cache-Control"] = "no-store"
    context = context_of(request)
    interpretation = await interpret(context, body, identity.id)
    response.headers.update(remaining_header(context, identity.id))
    return interpretation
