"""Real OpenRouter responses (deepseek-v4.1-flash, 2026-10-22) replayed through the reviewer.

``fixtures/review/*.json`` are raw chat-completion bodies captured during
live verification on two real agent-generated apps (the model's free-text
reasoning trimmed). They pin down the response shape the client parses
(provider, usage incl. cost and reasoning tokens) and the answer format the
grounding normalizer must accept (verbatim evidence with indentation).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.analysis.engine.reviewer import client
from backend.analysis.engine.reviewer import findings
from backend.analysis.tests.reviewer_support import FakeResponse
from backend.analysis.tests.reviewer_support import FakeSession
from backend.analysis.tests.reviewer_support import frozen_section
from backend.analysis.tests.reviewer_support import make_file
from backend.analysis.tests.reviewer_support import make_sample
from backend.analysis.tests.reviewer_support import patch_normalize

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "review"
CASES = {
    "openrouter_deepseek_v41_flash_opencode_app.json": {"findings": 8, "files": 3, "cost": 0.004707351},
    "openrouter_deepseek_v41_flash_aider_app.json": {"findings": 9, "files": 10, "cost": 0.004904163},
}


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize("name", sorted(CASES))
def test_client_parses_a_real_response(name):
    body = load(name)
    call = client.chat(
        "sk-test",
        model="deepseek/deepseek-v4.1-flash",
        messages=[{"role": "user", "content": "x"}],
        session=FakeSession(FakeResponse(200, body)),
        sleep=lambda s: None,
    )
    assert call.ok is True
    assert call.finish_reason == "stop"
    assert call.provider == "CoreWeave"
    assert call.model == "deepseek/deepseek-v4.1-flash"
    assert call.generation_id.startswith("gen-")
    assert call.usage["cost"] == pytest.approx(CASES[name]["cost"])
    assert call.usage["prompt_tokens"] == body["usage"]["prompt_tokens"]
    assert call.usage["reasoning_tokens"] == body["usage"]["completion_tokens_details"]["reasoning_tokens"] > 0
    assert call.usage["cached_tokens"] == 0


@pytest.mark.parametrize("name", sorted(CASES))
def test_real_answers_validate_against_findings_v1(name):
    doc = findings.validate_document(load(name)["choices"][0]["message"]["content"])
    assert len(doc["findings"]) == CASES[name]["findings"]
    assert len(doc["files_reviewed"]) == CASES[name]["files"]
    assert doc["review_complete"] is True


def _sample_from_evidence(doc: dict):
    """Files that hold each finding's evidence at its cited lines, padded with filler code."""
    lines_by_file: dict[str, dict[int, str]] = {}
    for item in doc["findings"]:
        for offset, line in enumerate(item["evidence"].split("\n")):
            lines_by_file.setdefault(item["file"], {})[item["start_line"] + offset] = line
    files = []
    for path, lines in lines_by_file.items():
        total = max(lines) + 5
        text = "\n".join(lines.get(n, f"filler_{n} = {n}") for n in range(1, total + 1)) + "\n"
        files.append(make_file(path, text, origin="added"))
    return make_sample(files)


@pytest.mark.parametrize("name", sorted(CASES))
def test_real_evidence_format_grounds_exactly(name, monkeypatch):
    patch_normalize(monkeypatch)
    content = load(name)["choices"][0]["message"]["content"]
    doc = json.loads(content)
    sample = _sample_from_evidence(doc)
    out, counts, info = findings.parse_payload(content, sample=sample, frozen=frozen_section(), index=0)
    assert counts["exact"] == len(doc["findings"])
    assert all(f.is_ai and not f.counted for f in out)
    assert info["lenient_json"] is False
    assert info["evidence_over_limit"] == 0
