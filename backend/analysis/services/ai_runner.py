"""AI reviewer tool — runs an LLM review instead of a container CLI.

This is the ``kind == "ai"`` execution path. It resolves the model through
``inference.access`` (OpenRouter with the user's key, or a local endpoint),
calls it through ``inference.chat`` (``source="analysis"``) and parses a
structured JSON list of findings out of the model response.
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING
from typing import Any

from backend.analysis.services.base import SEVERITIES
from backend.analysis.services.base import AnalyzerOutput
from backend.analysis.services.base import FindingData
from backend.analysis.services.parsers import _label

if TYPE_CHECKING:
    from backend.analysis.models import AnalyzerTool
    from backend.users.models import User

logger = logging.getLogger(__name__)

# Default to the latest Claude model for the built-in reviewer.
DEFAULT_MODEL = "anthropic/claude-opus-5.5"
# Upper bound for a local-model call made inside a web request (tool "test").
INLINE_LOCAL_TIMEOUT_S = 180

_SYSTEM_PROMPT = (
    "You are a meticulous senior code reviewer. Analyze the provided code for "
    "security vulnerabilities, bugs, and quality issues. The code is given as "
    "one or more files, each introduced by a '# ===== <filename> =====' marker. "
    "Respond with ONLY a JSON array of findings. Each finding is an object "
    "with keys: severity (one of critical, high, medium, low, info), category "
    "(security, quality, performance, style, best_practice), title, "
    "description, suggestion, file_path, line_number, rule_id. file_path must "
    "be exactly one of the provided filenames; line_number is the 1-based line "
    "within that file, counting from the line after its marker. "
    "Return [] if there are no issues."
)


def run(
    tool: AnalyzerTool,
    code: dict[str, str],
    config: dict[str, Any] | None,
    user: User | None,
) -> AnalyzerOutput:
    from backend.generation.services.openrouter_client import OpenRouterError

    config = config or {}
    model = config.get("model") or tool.default_config.get("model") or DEFAULT_MODEL

    code_blob, omitted = _format_code(code)
    max_tokens = int(config.get("max_tokens") or tool.default_config.get("max_tokens") or 4000)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": code_blob},
    ]
    try:
        access = _resolve(model, user)
    except OpenRouterError as exc:
        return AnalyzerOutput(error=str(exc))
    if not code_blob.strip():
        return AnalyzerOutput(summary={"message": "No code to review"})

    try:
        response = _chat(access, messages, max_tokens)
    except OpenRouterError as exc:
        logger.warning("AI review failed: %s", exc)
        return AnalyzerOutput(error=str(exc))

    from backend.generation.services.openrouter_client import OpenRouterClient

    content = OpenRouterClient.extract_content(response)
    truncated = OpenRouterClient.is_truncated(response)
    findings = _parse_findings(content)
    if not findings and content.strip() and _extract_json_array(content) is None:
        # The model answered but nothing parseable came out — report a failure
        # instead of a clean zero-finding result the user would trust.
        reason = "response hit the max_tokens limit" if truncated else "response was not a JSON findings array"
        return AnalyzerOutput(
            error=f"AI review unparseable: {reason}",
            raw_output={"model": model, "truncated": truncated, "response": content[:8000]},
        )
    return AnalyzerOutput(
        findings=findings,
        raw_output={"model": model, "truncated": truncated, "response": content[:8000]},
        summary={
            "model": model,
            "finding_count": len(findings),
            "truncated": truncated,
            **({"files_not_reviewed": omitted} if omitted else {}),
        },
        warnings=[f"Not reviewed (prompt size limit): {', '.join(omitted)}"] if omitted else [],
    )


def _resolve(model_id: str, user: User | None):
    """The :class:`~backend.inference.access.Access` for ``model_id``.

    A model visible to ``user`` resolves through ``inference.resolve_access``
    (local endpoints included); an id that is not in the catalog is treated
    as an OpenRouter id with the user's key, as before.
    """
    from backend.inference.access import LLMAccessError
    from backend.inference.access import resolve_access
    from backend.inference.access import system_openrouter
    from backend.llm_models.models import LLMModel

    row = LLMModel.objects.visible_to(user).select_related("endpoint").filter(model_id=model_id).first()
    if row is not None:
        return resolve_access(user, row)
    from backend.credentials.services.resolver import MissingApiKeyError
    from backend.credentials.services.resolver import get_openrouter_key
    from backend.inference.access import Access

    try:
        api_key = get_openrouter_key(user)
    except MissingApiKeyError as exc:
        raise LLMAccessError(
            code="missing_api_key",
            message=str(exc),
            retryable=False,
            remediation=exc.remediation,
        ) from exc
    endpoint = system_openrouter()
    return Access(
        endpoint=endpoint,
        kind=endpoint.kind,
        connection=endpoint.connection,
        api_flavor=endpoint.api_flavor,
        served_model_name=model_id,
        digest="",
        num_ctx=None,
        keep_alive="",
        base_url=endpoint.base_url,
        api_key=api_key,
        request_timeout_s=endpoint.request_timeout_s,
    )


def _chat(access, messages: list[dict[str, Any]], max_tokens: int) -> dict[str, Any]:
    """One review call; local models queue behind experiment calls (``analysis`` claims last).

    A local call is a queued ``LLMRequest`` that a relay or pump can only
    claim once it is committed. Inside a request transaction (the tool's
    inline "test" action under ``ATOMIC_REQUESTS``) the call therefore runs on
    its own autocommit connection in a helper thread, bounded by
    :data:`INLINE_LOCAL_TIMEOUT_S` instead of the request's long deadline.
    """
    from django.db import connection

    from backend.inference.chat import chat

    params = {"temperature": 0.2, "max_tokens": max_tokens}
    if access.is_openrouter or not connection.in_atomic_block:
        return chat(access, messages=messages, params=params, seed=None, source="analysis").response

    from backend.inference.access import LLMAccessError
    from backend.inference.db import close_db

    deadline = time.monotonic() + INLINE_LOCAL_TIMEOUT_S

    def call() -> dict[str, Any]:
        try:
            return chat(
                access,
                messages=messages,
                params=params,
                seed=None,
                source="analysis",
                cancel_check=lambda: time.monotonic() > deadline,
            ).response
        except LLMAccessError as exc:
            if exc.code == "cancelled" and time.monotonic() > deadline:
                msg = f"{access.endpoint.slug} did not answer within {INLINE_LOCAL_TIMEOUT_S} s"
                raise LLMAccessError(code="timeout", message=msg) from exc
            raise
        finally:
            close_db()

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="ai-review") as pool:
        return pool.submit(call).result()


# Prompt budget for the code bundle (characters).
MAX_CODE_CHARS = 60000
# Generated, not written by the model: hashes and resolved versions only.
_LOCKFILES = frozenset({"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "pipfile.lock"})


def _review_order(name: str) -> tuple[int, str]:
    from backend.reports.services.loc import is_source_file

    # Source first, then manifests/config/docs: when the budget runs out it
    # is the least informative files that are left out, never the app code.
    return (0 if is_source_file(name) else 1, name)


def _format_code(code: dict[str, str]) -> tuple[str, list[str]]:
    """The review prompt's code bundle and the files that did not fit in it.

    Whole files only: a file cut mid-way would make the model report the
    cut as a syntax error.
    """
    parts: list[str] = []
    omitted: list[str] = []
    used = 0
    for name in sorted(code, key=_review_order):
        content = code[name]
        base = name.rsplit("/", 1)[-1].lower()
        if not content or not content.strip() or base in _LOCKFILES or ".min." in base:
            continue
        block = f"# ===== {name} =====\n{content}"
        if used + len(block) > MAX_CODE_CHARS:
            omitted.append(name)
            continue
        parts.append(block)
        used += len(block) + 2
    return "\n\n".join(parts), omitted


def _parse_findings(content: str) -> list[FindingData]:
    payload = _extract_json_array(content)
    if payload is None:
        return []
    findings: list[FindingData] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity", "info")).strip().lower()
        category = str(item.get("category", "quality")).lower()
        tool_confidence = _label(item.get("confidence"))
        findings.append(
            FindingData(
                # Models drift off the requested vocabulary ("moderate",
                # "Security "); keep severities/categories comparable.
                severity=_SEVERITY_ALIASES.get(severity, severity if severity in SEVERITIES else "info"),
                category=category.strip() if category.strip() in _CATEGORIES else "quality",
                title=str(item.get("title", "AI finding"))[:500],
                description=str(item.get("description", "")),
                suggestion=str(item.get("suggestion", "")),
                file_path=str(item.get("file_path", "")),
                line_number=_as_int(item.get("line_number")),
                rule_id=str(item.get("rule_id", "")),
                confidence="medium",
                # The label exactly as the model wrote it ("Moderate"); empty
                # when it gave none.
                tool_severity=_label(item.get("severity")),
                tool_specific_data={"tool_confidence": tool_confidence} if tool_confidence else {},
            ),
        )
    return findings


_SEVERITY_ALIASES = {"moderate": "medium", "warning": "medium", "error": "high", "minor": "low", "major": "high"}
_CATEGORIES = frozenset({"security", "quality", "performance", "style", "best_practice"})


def _extract_json_array(content: str) -> list | None:
    content = content.strip()
    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", content, re.DOTALL)
    if fence:
        content = fence.group(1)
    start = content.find("[")
    if start == -1:
        return None
    end = content.rfind("]")
    if end > start:
        try:
            data = json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            pass
        else:
            return data if isinstance(data, list) else None
    return _salvage_truncated_array(content[start:])


def _salvage_truncated_array(fragment: str) -> list | None:
    """Recover complete findings from an array cut off mid-object.

    Responses that hit the max_tokens limit stop mid-JSON; the complete
    objects before the cut are still good findings. Walk back through the
    object closers and take the longest prefix that parses.
    """
    for end in range(len(fragment) - 1, 0, -1):
        if fragment[end] != "}":
            continue
        try:
            data = json.loads(fragment[: end + 1] + "]")
        except json.JSONDecodeError:
            continue
        if isinstance(data, list) and data:
            logger.warning("AI review JSON was truncated; salvaged %d findings", len(data))
            return data
        return None
    return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
