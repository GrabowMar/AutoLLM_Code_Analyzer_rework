"""Shared builders for the AI reviewer tests: samples, frozen sections, fake OpenRouter HTTP.

Other stages' modules are mocked at their public boundary only:
``normalize.normalize`` (measure stage) via :func:`patch_normalize`, and
``agentic`` where a test needs it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import requests

from backend.analysis.engine.reviewer import prompt
from backend.analysis.engine.reviewer.config import REVIEWER_ENGINE_VERSION
from backend.analysis.engine.types import NormalizedFinding
from backend.analysis.engine.types import RawFinding
from backend.analysis.engine.types import Sample
from backend.analysis.engine.types import SampleFile

MODEL = "deepseek/deepseek-v4.1-flash"
CANONICAL = "deepseek_deepseek-v4.1-flash-20260910"

APP_PY = """from flask import Flask, request
import sqlite3

app = Flask(__name__)
SECRET_KEY = "hunter2-super-secret"


@app.route("/users")
def users():
    name = request.args.get("name")
    conn = sqlite3.connect("app.db")
    rows = conn.execute(f"SELECT * FROM users WHERE name = '{name}'").fetchall()
    return {"rows": rows}


if __name__ == "__main__":
    app.run(debug=True)
"""

APP_JSX = """import { useEffect, useState } from 'react';

export default function App() {
  const [html, setHtml] = useState('');
  useEffect(() => {
    fetch('/api/banner').then((r) => r.text()).then(setHtml);
  }, []);
  return <div dangerouslySetInnerHTML={{ __html: html }} />;
}
"""


def make_file(
    path: str,
    text: str | bytes,
    *,
    origin: str = "added",
    kind: str = "source",
    language: str = "python",
    included: bool = True,
    exclude_reason: str = "",
) -> SampleFile:
    content = text.encode("utf-8") if isinstance(text, str) else text
    return SampleFile(
        path=path,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
        language=language,
        kind=kind,
        origin=origin,
        included=included,
        exclude_reason=exclude_reason,
        sloc=len(content.splitlines()),
        measured=included and origin in ("authored", "added", "modified") and kind == "source",
    )


def make_sample(files: list[SampleFile]) -> Sample:
    inc = sorted((f for f in files if f.included), key=lambda f: f.path)
    digest = hashlib.sha256("".join(f"{f.path}\0{f.sha256}\n" for f in inc).encode()).hexdigest()
    return Sample(files=files, source="inline", sample_hash=digest)


def app_sample() -> Sample:
    return make_sample(
        [
            make_file("backend/app.py", APP_PY),
            make_file("frontend/src/App.jsx", APP_JSX, language="jsx"),
            make_file("package-lock.json", "{}\n", kind="lockfile", language="json", included=False),
            make_file("README.md", "# App\n", kind="doc", language="markdown"),
            make_file("backend/scaffold.py", "print('hi')\n", origin="scaffold"),
        ],
    )


def frozen_section(**overrides: Any) -> dict[str, Any]:
    """A frozen reviewer section like ``config.freeze_reviewer`` builds (no DB needed)."""
    frozen: dict[str, Any] = {
        "enabled": True,
        "required": True,
        "tool_slug": "ai_review",
        "engine": "single_shot",
        "engine_version": REVIEWER_ENGINE_VERSION,
        "harness": None,
        "harness_options": {},
        "harness_version": "",
        "harness_images": {},
        "model": {
            "slug": MODEL,
            "id": 71,
            "canonical_slug": CANONICAL,
            "provider": "deepseek",
            "context_window": 1_048_576,
            "max_output_tokens": 943_718,
            "pricing": {
                "input_per_token": 4.9e-08,
                "output_per_token": 5.25e-07,
                "snapshot_at": "2026-10-22T00:00:00",
            },
            "supports": {
                "structured_outputs": True,
                "response_format": True,
                "tools": True,
                "seed": True,
                "reasoning": True,
                "reasoning_efforts": ["max", "high", "low", "none"],
                "reasoning_default_effort": "high",
            },
        },
        "params": {
            "temperature": 0.2,
            "max_tokens": 16000,
            "reasoning": {"effort": "low"},
            "seed_base": 1,
            "seed_forwarded": True,
            "dropped": {},
        },
        "provider": {"require_parameters": True, "allow_fallbacks": True},
        "provider_routing_source": "default",
        "output_mode": "json_schema",
        "samples": 3,
        "quorum": 2,
        "prompt_version": prompt.PROMPT_VERSION,
        "prompt_sha256": prompt.prompt_sha256(),
        "schema_version": prompt.SCHEMA_VERSION,
        "schema_sha256": prompt.schema_sha256(),
        "include_origins": ["authored", "added", "modified"],
        "include_kinds": ["source", "config", "manifest", "env"],
        "chunk_char_budget": 300_000,
        "chunking": {"effective_chars": 300_000},
        "consensus": {"line_window": 3},
        "budget": {"max_cost_usd": 0.5, "max_llm_requests": 40, "wall_clock_seconds": 900},
        "notes": [],
    }
    frozen.update(overrides)
    return frozen


def fake_normalize(tool_slug: str, raw: RawFinding, sample: Sample, scope: dict[str, Any]) -> NormalizedFinding:
    """Stand-in for the measure stage's normalize: identity mapping + line-level origin."""
    f = sample.by_path().get(raw.file_path)
    origin = f.line_origin(raw.line) if f else "unknown"
    measured = scope.get("measured_origins", ["authored", "added", "modified"])
    return NormalizedFinding(
        tool_slug=tool_slug,
        rule_id=raw.rule_id,
        title=raw.title,
        severity=raw.native_severity,
        native_severity=raw.native_severity,
        category=raw.extra.get("category", "other"),
        cwe=list(raw.cwe),
        confidence="medium",
        file_path=raw.file_path,
        line=raw.line,
        end_line=raw.end_line,
        column=None,
        description=raw.description,
        suggestion=raw.suggestion,
        snippet=raw.snippet,
        file_origin=origin,
        in_measured_scope=bool(f and f.included and origin in measured),
        # Deliberately True: parse_payload must force AI findings uncounted.
        counted=True,
        fingerprint=hashlib.sha256(f"{raw.file_path}:{raw.line}:{raw.rule_id}".encode()).hexdigest(),
    )


def ai_finding(
    path="app.py",
    line=10,
    end=None,
    *,
    category="security",
    cwe=("CWE-89",),
    severity="high",
    grounding="exact",
    in_scope=True,
    index=0,
    title="t",
    evidence_at=None,
) -> NormalizedFinding:
    """A normalized AI finding as parse_payload produces it (for consensus/run_review tests).

    *evidence_at*: ``(first, last)`` lines of its evidence (grounding sets it); None = unknown (cited range).
    """
    return NormalizedFinding(
        tool_slug="ai_review",
        rule_id=category,
        title=title,
        severity=severity,
        native_severity=severity,
        category=category,
        cwe=list(cwe),
        confidence="high",
        file_path=path,
        line=line,
        end_line=end if end is not None else line,
        column=None,
        in_measured_scope=in_scope,
        counted=False,
        is_ai=True,
        grounding=grounding,
        sample_index=index,
        extra={"evidence_at": list(evidence_at)} if evidence_at else {},
    )


def patch_normalize(monkeypatch) -> None:
    from backend.analysis.engine import normalize

    monkeypatch.setattr(normalize, "normalize", fake_normalize)


def finding(file: str, start: int, end: int, evidence: str, **kw: Any) -> dict[str, Any]:
    item = {
        "file": file,
        "start_line": start,
        "end_line": end,
        "evidence": evidence,
        "title": kw.pop("title", "SQL injection"),
        "rationale": kw.pop("rationale", "User input is formatted into SQL."),
        "category": kw.pop("category", "security"),
        "cwe": kw.pop("cwe", "CWE-89"),
        "severity": kw.pop("severity", "high"),
        "confidence": kw.pop("confidence", 0.9),
        "fix": kw.pop("fix", "Use a parameterized query."),
    }
    item.update(kw)
    return item


def answer(findings: list[dict[str, Any]], files: list[str] | None = None, *, complete: bool = True) -> str:
    return json.dumps({"findings": findings, "files_reviewed": files or [], "review_complete": complete})


SQLI = finding(
    "backend/app.py",
    12,
    12,
    "rows = conn.execute(f\"SELECT * FROM users WHERE name = '{name}'\").fetchall()",
)
SECRET = finding(
    "backend/app.py",
    5,
    5,
    'SECRET_KEY = "hunter2-super-secret"',
    title="Hard-coded secret",
    category="secrets",
    cwe="CWE-798",
    severity="medium",
)
XSS = finding(
    "frontend/src/App.jsx",
    8,
    8,
    "return <div dangerouslySetInnerHTML={{ __html: html }} />;",
    title="XSS via dangerouslySetInnerHTML",
    cwe="CWE-79",
)


def chat_body(
    content: str | None,
    *,
    finish_reason: str = "stop",
    provider: str = "InferenceNet",
    model: str = MODEL,
    cost: float | None = 0.0012,
    prompt_tokens: int = 3000,
    completion_tokens: int = 800,
    gen_id: str = "gen-1",
) -> dict[str, Any]:
    usage: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "prompt_tokens_details": {"cached_tokens": 0},
        "completion_tokens_details": {"reasoning_tokens": 200},
    }
    if cost is not None:
        usage["cost"] = cost
    return {
        "id": gen_id,
        "provider": provider,
        "model": model,
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "native_finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            },
        ],
        "usage": usage,
    }


class FakeResponse:
    def __init__(self, status_code: int = 200, body: Any = None, *, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self) -> Any:
        if isinstance(self._body, str):
            return json.loads(self._body)
        if self._body is None:
            msg = "no body"
            raise ValueError(msg)
        return self._body


class FakeSession:
    """Replays queued responses (or raises queued exceptions); records every request."""

    def __init__(self, *responses: Any) -> None:
        self.queue = list(responses)
        self.requests: list[dict[str, Any]] = []

    def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float) -> FakeResponse:
        self.requests.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if not self.queue:
            msg = "FakeSession: no response queued"
            raise AssertionError(msg)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(json)
        if isinstance(item, FakeResponse):
            return item
        return FakeResponse(200, item)


def ok(content: str, **kw: Any) -> FakeResponse:
    return FakeResponse(200, chat_body(content, **kw))


CONNECTION_ERROR = requests.exceptions.ConnectionError("connection reset")
