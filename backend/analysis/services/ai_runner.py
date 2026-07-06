"""AI reviewer tool — runs an LLM review instead of a container CLI.

This is the ``kind == "ai"`` execution path. It calls OpenRouter (reusing the
generation client + credential resolver) and parses a structured JSON list of
findings out of the model response.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING
from typing import Any

from backend.analysis.services.base import AnalyzerOutput
from backend.analysis.services.base import FindingData

if TYPE_CHECKING:
    from backend.analysis.models import AnalyzerTool
    from backend.users.models import User

logger = logging.getLogger(__name__)

# Default to the latest Claude model for the built-in reviewer.
DEFAULT_MODEL = "anthropic/claude-opus-4-8"

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
    from backend.credentials.services.resolver import MissingApiKeyError
    from backend.credentials.services.resolver import get_openrouter_key
    from backend.generation.services.openrouter_client import OpenRouterClient
    from backend.generation.services.openrouter_client import OpenRouterError

    config = config or {}
    model = config.get("model") or tool.default_config.get("model") or DEFAULT_MODEL

    try:
        api_key = get_openrouter_key(user)
    except MissingApiKeyError as exc:
        return AnalyzerOutput(error=str(exc))

    code_blob = _format_code(code)
    if not code_blob.strip():
        return AnalyzerOutput(summary={"message": "No code to review"})

    max_tokens = int(config.get("max_tokens") or tool.default_config.get("max_tokens") or 4000)

    client = OpenRouterClient(api_key=api_key)
    try:
        response = client.chat_completion(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": code_blob},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
        )
    except OpenRouterError as exc:
        logger.warning("AI review failed: %s", exc)
        return AnalyzerOutput(error=str(exc))

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
        summary={"model": model, "finding_count": len(findings), "truncated": truncated},
    )


def _format_code(code: dict[str, str]) -> str:
    parts: list[str] = []
    for name, content in code.items():
        if content and content.strip():
            parts.append(f"# ===== {name} =====\n{content}")
    return "\n\n".join(parts)[:60000]


def _parse_findings(content: str) -> list[FindingData]:
    payload = _extract_json_array(content)
    if payload is None:
        return []
    findings: list[FindingData] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        findings.append(
            FindingData(
                severity=str(item.get("severity", "info")).lower(),
                category=str(item.get("category", "quality")),
                title=str(item.get("title", "AI finding"))[:500],
                description=str(item.get("description", "")),
                suggestion=str(item.get("suggestion", "")),
                file_path=str(item.get("file_path", "")),
                line_number=_as_int(item.get("line_number")),
                rule_id=str(item.get("rule_id", "")),
                confidence="medium",
            ),
        )
    return findings


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
