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

from llm_lab.analysis.services.base import AnalyzerOutput
from llm_lab.analysis.services.base import FindingData

if TYPE_CHECKING:
    from llm_lab.analysis.models import AnalyzerTool
    from llm_lab.users.models import User

logger = logging.getLogger(__name__)

# Default to the latest Claude model for the built-in reviewer.
DEFAULT_MODEL = "anthropic/claude-opus-4-8"

_SYSTEM_PROMPT = (
    "You are a meticulous senior code reviewer. Analyze the provided code for "
    "security vulnerabilities, bugs, and quality issues. Respond with ONLY a "
    "JSON array of findings. Each finding is an object with keys: severity "
    "(one of critical, high, medium, low, info), category (security, quality, "
    "performance, style, best_practice), title, description, suggestion, "
    "file_path, line_number, rule_id. Return [] if there are no issues."
)


def run(
    tool: AnalyzerTool,
    code: dict[str, str],
    config: dict[str, Any] | None,
    user: User | None,
) -> AnalyzerOutput:
    from llm_lab.credentials.services.resolver import MissingApiKeyError
    from llm_lab.credentials.services.resolver import get_openrouter_key
    from llm_lab.generation.services.openrouter_client import OpenRouterClient
    from llm_lab.generation.services.openrouter_client import OpenRouterError

    config = config or {}
    model = config.get("model") or tool.default_config.get("model") or DEFAULT_MODEL

    try:
        api_key = get_openrouter_key(user)
    except MissingApiKeyError as exc:
        return AnalyzerOutput(error=str(exc))

    code_blob = _format_code(code)
    if not code_blob.strip():
        return AnalyzerOutput(summary={"message": "No code to review"})

    client = OpenRouterClient(api_key=api_key)
    try:
        response = client.chat_completion(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": code_blob},
            ],
            temperature=0.2,
            max_tokens=int(config.get("max_tokens", 4000)),
        )
    except OpenRouterError as exc:
        logger.warning("AI review failed: %s", exc)
        return AnalyzerOutput(error=str(exc))

    content = OpenRouterClient.extract_content(response)
    findings = _parse_findings(content)
    return AnalyzerOutput(
        findings=findings,
        raw_output={"model": model, "response": content[:8000]},
        summary={"model": model, "finding_count": len(findings)},
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
    end = content.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
