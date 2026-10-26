"""Shared parsing of a review payload (both engines use it; owner: reviewer stage).

Validates the payload against the findings schema, grounds every finding
against the sample and maps it onto NormalizedFinding (via
``normalize.normalize(AI_TOOL_SLUG, ...)``). ``counted`` starts False: only
consensus (``consensus.apply_consensus``) may count an AI finding.

How a model finding becomes a :class:`RawFinding` (the measure stage's
``ai_review`` mapping reads these fields):

* ``rule_id`` = the model's category (``security``, ``bug``, ...), also in
  ``extra["category"]``; ``cwe`` = ``["CWE-n"]`` or ``[]``;
* ``native_severity`` = the model's severity token; ``native_confidence`` =
  its 0..1 confidence formatted ``"0.85"`` (float in ``extra["confidence"]``);
* ``title``/``message`` = title, ``description`` = rationale,
  ``suggestion`` = fix, ``snippet`` = evidence;
* ``line``/``end_line`` = the grounded location (moved when relocated; the
  cited one is kept in ``extra["cited_lines"]``); ``extra["evidence_at"]`` =
  ``[first, last]`` lines the evidence occupies (grounded findings only), the
  point consensus clusters on.
"""

from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft202012Validator

from backend.analysis.engine.reviewer import grounding
from backend.analysis.engine.reviewer import prompt
from backend.analysis.engine.types import SEVERITIES
from backend.analysis.engine.types import NormalizedFinding
from backend.analysis.engine.types import RawFinding
from backend.analysis.engine.types import Sample

MAX_EVIDENCE_LINES = 3
# Finding.title is a CharField(500): never let a verbose model crash persistence.
MAX_TITLE_CHARS = 500
MAX_REPORTED_ERRORS = 12

_FENCE_RE = re.compile(r"^```[A-Za-z0-9_-]*\s*\n(.*)\n```\s*$", re.DOTALL)


class InvalidReview(Exception):  # noqa: N818 - domain wording
    """The payload is not a valid findings document (schema/JSON error)."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or [message]


def load_document(payload: str | bytes | dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Parse a payload into a JSON object -> (document, lenient).

    Accepts a bare JSON object, one wrapped in a Markdown code fence, or one
    surrounded by prose (``lenient`` = True for the last two). Never repairs
    a truncated or malformed document: that is :class:`InvalidReview`.
    """
    if isinstance(payload, dict):
        return payload, False
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    text = (payload or "").strip()
    if not text:
        msg = "empty answer"
        raise InvalidReview(msg)
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        doc = _lenient_object(text)
        if doc is None:
            msg = f"answer is not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}"
            raise InvalidReview(msg) from None
        return doc, True
    if not isinstance(doc, dict):
        msg = f"answer must be a JSON object, got {type(doc).__name__}"
        raise InvalidReview(msg)
    return doc, False


def _lenient_object(text: str) -> dict[str, Any] | None:
    fenced = _FENCE_RE.match(text)
    candidates = [fenced.group(1)] if fenced else []
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        candidates.append(text[first : last + 1])
    for candidate in candidates:
        try:
            doc = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(doc, dict):
            return doc
    return None


def schema_errors(doc: dict[str, Any], version: str = prompt.SCHEMA_VERSION) -> list[str]:
    """Human-readable schema violations (deterministic order, capped), [] when valid."""
    validator = Draft202012Validator(prompt.findings_schema(None, version))
    errors = sorted(validator.iter_errors(doc), key=lambda e: (list(map(str, e.absolute_path)), e.message))
    out = []
    for err in errors[:MAX_REPORTED_ERRORS]:
        where = "/".join(str(p) for p in err.absolute_path) or "(root)"
        out.append(f"{where}: {err.message[:300]}")
    if len(errors) > MAX_REPORTED_ERRORS:
        out.append(f"... and {len(errors) - MAX_REPORTED_ERRORS} more")
    return out


def validate_document(payload: str | bytes | dict[str, Any], version: str = prompt.SCHEMA_VERSION) -> dict[str, Any]:
    """Parse + schema-validate; returns the document or raises :class:`InvalidReview`."""
    doc, _ = load_document(payload)
    errors = schema_errors(doc, version)
    if errors:
        msg = f"answer does not match {version}: {errors[0]}"
        raise InvalidReview(msg, errors)
    return doc


def _bucket(confidence: float) -> str:
    if confidence >= 0.8:
        return "high"
    if confidence >= 0.5:
        return "medium"
    return "low"


def parse_payload(
    payload: str | dict[str, Any],
    *,
    sample: Sample,
    frozen: dict[str, Any],
    index: int,
    allowed_files: list[str] | set[str] | None = None,
    scope: dict[str, Any] | None = None,
    path_root: str | None = None,
) -> tuple[list[NormalizedFinding], dict[str, int], dict[str, Any]]:
    """(findings, grounding_counts, info{review_complete, files_reviewed}); raises InvalidReview.

    *allowed_files*: paths the model was shown (single-shot chunk); a cited
    file outside them is ``invalid_file`` even when the sample has it (the
    model cannot have read it). None = any file of the sample. *scope*: the
    plan's scope for ``normalize`` (default ``frozen["scope"]``, then
    ``sample.DEFAULT_SCOPE``). *path_root*: the absolute directory the
    reviewer saw the app in (agent engine: ``/workspace``); cited and
    reviewed paths under it are read relative to it, as the brief defines
    them, instead of grading the agent's own absolute paths ``invalid_file``.
    """
    from backend.analysis.engine import normalize
    from backend.analysis.engine.catalog import AI_TOOL_SLUG
    from backend.analysis.models import FindingCategory

    version = frozen.get("schema_version") or prompt.SCHEMA_VERSION
    doc, lenient = load_document(payload)
    errors = schema_errors(doc, version)
    if errors:
        msg = f"answer does not match {version}: {errors[0]}"
        raise InvalidReview(msg, errors)

    if scope is None:
        scope = frozen.get("scope")
    if scope is None:
        from backend.analysis.engine.sample import DEFAULT_SCOPE

        scope = DEFAULT_SCOPE
    shown = {grounding.normalize_path(p, path_root) for p in allowed_files} if allowed_files is not None else None
    window = int((frozen.get("consensus") or {}).get("grounding_window") or 3)
    reviewer = {
        "engine": frozen.get("engine", ""),
        "harness": frozen.get("harness") or "",
        "model": (frozen.get("model") or {}).get("canonical_slug", ""),
    }

    counts = grounding.empty_counts()
    findings: list[NormalizedFinding] = []
    evidence_over_limit = 0
    for item in doc["findings"]:
        cited_path = item["file"]
        path = grounding.normalize_path(cited_path, path_root)
        start, end = item["start_line"], item["end_line"]
        if shown is not None and path not in shown:
            located = grounding.Grounding("invalid_file", start, end)
        else:
            located = grounding.locate(
                file_path=path,
                start_line=start,
                end_line=end,
                evidence=item["evidence"],
                sample=sample,
                window=window,
            )
        status, line, end_line = located.status, located.line, located.end_line
        evidence_at = [located.evidence_line, located.evidence_end_line] if located.evidence_line is not None else None
        counts[status] += 1
        evidence_n = len(grounding.evidence_lines(item["evidence"]))
        if evidence_n > MAX_EVIDENCE_LINES:
            evidence_over_limit += 1
        confidence = float(item["confidence"])
        category = item["category"]
        title = (item["title"] or "").strip()[:MAX_TITLE_CHARS] or f"AI review: {category}"
        raw = RawFinding(
            rule_id=category,
            message=title,
            file_path=path,
            line=line,
            end_line=end_line,
            native_severity=item["severity"],
            native_confidence=f"{confidence:.2f}",
            cwe=[item["cwe"]] if item["cwe"] else [],
            title=title,
            description=item["rationale"] or "",
            suggestion=item["fix"] or "",
            snippet=item["evidence"] or "",
            extra={
                "category": category,
                "confidence": confidence,
                "grounding": status,
                "cited_file": cited_path,
                "cited_lines": [start, end],
                "evidence_at": evidence_at,
                "evidence_lines": evidence_n,
                "reviewer": reviewer,
            },
        )
        nf = normalize.normalize(AI_TOOL_SLUG, raw, sample, scope)
        # AI invariants hold whatever the mapping tables say: an opinion is
        # never counted before consensus, and keeps its grounding/provenance.
        nf.tool_slug = AI_TOOL_SLUG
        nf.is_ai = True
        nf.grounding = status
        nf.extra = {**(nf.extra or {}), "evidence_at": evidence_at}
        nf.sample_index = index
        nf.counted = False
        nf.is_consensus = False
        if nf.severity not in SEVERITIES:
            nf.severity = item["severity"]
        if nf.category not in FindingCategory.values:
            nf.category = category
        if nf.confidence not in {"high", "medium", "low"}:
            nf.confidence = _bucket(confidence)
        if not nf.cwe and item["cwe"]:
            nf.cwe = [item["cwe"]]
        findings.append(nf)

    files_reviewed = sorted({grounding.normalize_path(p, path_root) for p in doc["files_reviewed"] if p})
    info = {
        "review_complete": bool(doc["review_complete"]),
        "files_reviewed": files_reviewed,
        "findings": len(findings),
        "evidence_over_limit": evidence_over_limit,
        "lenient_json": lenient,
    }
    return findings, counts, info
