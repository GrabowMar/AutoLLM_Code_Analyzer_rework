"""Shared fixtures for the WP-13 pattern tests: seed detectors in the library and the fake Semgrep runner."""

from __future__ import annotations

import pytest

from backend.generation.patterns.catalog import load_seed_detectors
from backend.generation.testing import fake_semgrep
from backend.generation.testing.factories import PatternDetectorFactory

FSTRING_SEMGREP = r"""
rules:
  - id: sql-fstring
    languages: [python]
    severity: ERROR
    message: SQL built with an f-string
    pattern-regex: execute\(\s*f["']
"""


@pytest.fixture
def fake_semgrep_runner(monkeypatch):
    return fake_semgrep.install(monkeypatch)


@pytest.fixture
def detectors(db):
    """System detectors: the seed regex/ts_query/probe/manual ones plus a regex-based Semgrep rule."""
    rows = {}
    for seed in load_seed_detectors():
        if seed.slug in {"py-sql-fstring", "py-parameterized-sql", "probe-sqli", "manual-owner-check"}:
            rows[seed.slug] = PatternDetectorFactory.create(slug=seed.slug, content=seed.content, system=True)
    rows["sg-sql-fstring"] = PatternDetectorFactory.create(
        slug="sg-sql-fstring",
        system=True,
        content={
            "engine": "semgrep",
            "languages": ["python"],
            "rule": FSTRING_SEMGREP,
            "tool": {"name": "semgrep", "version": "1.x"},
            "feature_key": "sg-sql-fstring",
            "polarity": "insecure",
            "description": "f-string SQL (semgrep)",
            "cwe": ["cwe-89"],
        },
    )
    return rows
