"""AI-powered code analyzers using LLM APIs."""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from typing import ClassVar

from django.conf import settings

from llm_lab.analysis.services.base import AnalyzerOutput
from llm_lab.analysis.services.base import BaseAnalyzer
from llm_lab.analysis.services.base import FindingData
from llm_lab.analysis.services.base import _safe_int
from llm_lab.generation.services.openrouter_client import OpenRouterClient
from llm_lab.generation.services.openrouter_client import OpenRouterError

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are an expert code reviewer. Analyze the provided code and return findings \
in a specific JSON format.

You MUST return ONLY a valid JSON object (no markdown, no extra text) \
with this structure:

{
  "findings": [
    {
      "severity": "high",
      "category": "security",
      "title": "SQL Injection Vulnerability",
      "description": "Detailed description of the issue",
      "suggestion": "How to fix it",
      "file_path": "app.py",
      "line_number": 42,
      "code_snippet": "the problematic code",
      "rule_id": "AI-SEC-001",
      "confidence": "high"
    }
  ],
  "summary": {
    "overall_quality": 7.5,
    "security_score": 6.0,
    "maintainability_score": 8.0,
    "performance_score": 7.0,
    "key_strengths": ["strength 1", "strength 2"],
    "key_improvements": ["improvement 1", "improvement 2"],
    "compliance_percentage": 75
  }
}

Rules for severity: critical, high, medium, low, info
Rules for category: security, quality, performance, style, best_practice
Rules for confidence: high, medium, low
Rules for rule_id: Use prefixes AI-SEC for security, AI-QUAL for quality, \
AI-PERF for performance, AI-STYLE for style, AI-BP for best_practice, \
followed by a numeric suffix (e.g. AI-SEC-001)."""

CATEGORY_RULE_PREFIX: dict[str, str] = {
    "security": "AI-SEC",
    "quality": "AI-QUAL",
    "performance": "AI-PERF",
    "style": "AI-STYLE",
    "best_practice": "AI-BP",
}

VALID_SEVERITIES = {"critical", "high", "medium", "low", "info"}
VALID_CATEGORIES = {"security", "quality", "performance", "style", "best_practice"}
VALID_CONFIDENCES = {"high", "medium", "low"}


def _extract_json(text: str) -> dict[str, Any] | None:
    """Try to extract a JSON object from text that may contain markdown fences."""
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        text = match.group(1)

    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # Last resort: find first { ... last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    return None


def _normalize_finding(raw: dict[str, Any]) -> FindingData:
    """Map a raw finding dict from the LLM to a FindingData instance."""
    severity = str(raw.get("severity", "info")).lower()
    if severity not in VALID_SEVERITIES:
        severity = "info"

    category = str(raw.get("category", "quality")).lower()
    if category not in VALID_CATEGORIES:
        category = "quality"

    confidence = str(raw.get("confidence", "medium")).lower()
    if confidence not in VALID_CONFIDENCES:
        confidence = "medium"

    rule_id = raw.get("rule_id", "")
    if not rule_id:
        prefix = CATEGORY_RULE_PREFIX.get(category, "AI-QUAL")
        rule_id = f"{prefix}-000"

    return FindingData(
        severity=severity,
        category=category,
        title=raw.get("title", "Untitled Finding"),
        description=raw.get("description", ""),
        suggestion=raw.get("suggestion", ""),
        file_path=raw.get("file_path", ""),
        line_number=_safe_int(raw.get("line_number")) or None,
        column_number=_safe_int(raw.get("column_number")) or None,
        code_snippet=raw.get("code_snippet", ""),
        rule_id=rule_id,
        confidence=confidence,
    )


def _build_user_message(
    code: dict[str, str],
    review_focus: list[str] | None = None,
) -> str:
    """Build the user message containing the code to review."""
    parts: list[str] = []

    if review_focus:
        parts.append(f"Focus areas: {', '.join(review_focus)}\n")

    parts.append("Please review the following code:\n")

    for label, content in code.items():
        parts.append(f"--- {label} ---")
        parts.append(content)
        parts.append("")

    return "\n".join(parts)


class LLMReviewAnalyzer(BaseAnalyzer):
    """AI-powered comprehensive code review using large language models."""

    name: ClassVar[str] = "llm_review"
    analyzer_type: ClassVar[str] = "ai"
    display_name: ClassVar[str] = "AI Code Review"
    description: ClassVar[str] = "AI-powered comprehensive code review using large language models"
    default_config: ClassVar[dict[str, Any]] = {
        "model": "openai/gpt-4o-mini",
        "temperature": 0.2,
        "max_tokens": 8000,
    }

    def check_available(self) -> tuple[bool, str]:
        api_key = getattr(settings, "OPENROUTER_API_KEY", None)
        if not api_key:
            return False, "OpenRouter API key not configured"
        return True, "Available"

    def analyze(
        self,
        code: dict[str, str],
        config: dict[str, Any] | None = None,
    ) -> AnalyzerOutput:
        available, message = self.check_available()
        if not available:
            return AnalyzerOutput(error=message)

        merged = {**self.default_config, **(config or {})}
        model: str = merged["model"]
        temperature: float = merged["temperature"]
        max_tokens: int = merged["max_tokens"]
        review_focus: list[str] | None = merged.get("review_focus")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_message(code, review_focus)},
        ]

        try:
            from llm_lab.credentials.services.resolver import MissingApiKeyError
            from llm_lab.credentials.services.resolver import get_openrouter_key

            try:
                api_key = get_openrouter_key(user=None)
            except MissingApiKeyError as exc:
                return AnalyzerOutput(error=f"AI review unavailable: {exc}")

            client = OpenRouterClient(api_key=api_key)
            response = client.chat_completion(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except OpenRouterError as exc:
            logger.exception("AI review API call failed")
            return AnalyzerOutput(error=f"AI review failed: {exc}")
        except Exception as exc:
            logger.exception("Unexpected error during AI review")
            return AnalyzerOutput(error=f"AI review failed: {exc}")

        content = client.extract_content(response)
        usage = client.extract_usage(response)

        return self._parse_response(content, usage, response)

    def _parse_response(
        self,
        content: str,
        usage: dict[str, Any],
        raw_response: dict[str, Any],
    ) -> AnalyzerOutput:
        parsed = _extract_json(content)

        if parsed is not None:
            return self._build_output_from_json(parsed, usage, raw_response)

        finding = FindingData(
            severity="info",
            category="quality",
            title="AI Code Review",
            description=content,
            suggestion="",
            rule_id="AI-QUAL-000",
            confidence="low",
        )
        return AnalyzerOutput(
            findings=[finding],
            summary={
                "token_usage": usage,
                "parse_error": "Could not parse structured JSON from LLM response",
            },
            raw_output={"response": raw_response, "content": content},
        )

    def _build_output_from_json(
        self,
        parsed: dict[str, Any],
        usage: dict[str, Any],
        raw_response: dict[str, Any],
    ) -> AnalyzerOutput:
        findings = [_normalize_finding(f) for f in parsed.get("findings", []) if isinstance(f, dict)]

        llm_summary: dict[str, Any] = parsed.get("summary", {})
        summary = {
            **llm_summary,
            "token_usage": usage,
            "total_findings": len(findings),
        }

        return AnalyzerOutput(
            findings=findings,
            summary=summary,
            raw_output={"response": raw_response, "parsed": parsed},
        )
