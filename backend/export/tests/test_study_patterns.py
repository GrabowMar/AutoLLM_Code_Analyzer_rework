"""The pattern part of the study export: latest completed non-live PatternRun, never a silent empty dir."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from backend.export.services import patterns
from backend.generation.models.choices import WorkStatus
from backend.generation.testing import factories

pytestmark = pytest.mark.django_db


@pytest.fixture
def built(monkeypatch):
    calls = []

    def fake_build_export(pattern_run):
        calls.append(pattern_run.pk)
        return f"zip:{pattern_run.pk}".encode()

    monkeypatch.setattr("backend.generation.patterns.export.build_export", fake_build_export)
    return calls


def _run(study, *, status=WorkStatus.COMPLETED, is_live=False, age_min=0):
    completed = timezone.now() - timedelta(minutes=age_min) if status == WorkStatus.COMPLETED else None
    return factories.PatternRunFactory.create(study=study, status=status, is_live=is_live, completed_at=completed)


def test_exports_the_latest_completed_non_live_run(built):
    study = factories.StudyFactory.create()
    _run(study, age_min=30)
    latest = _run(study, age_min=5)
    _run(study, is_live=True, age_min=1)  # provisional: never exported
    _run(study, status=WorkStatus.FAILED)
    _run(study, status=WorkStatus.RUNNING)
    _run(factories.StudyFactory.create(), age_min=0)  # another study's run

    files = patterns.study_pattern_files(study)

    assert built == [latest.pk]
    assert files[f"{latest.pk}.zip"] == f"zip:{latest.pk}".encode()
    readme = json.loads(files["README.txt"])
    assert readme["status"] == "exported"
    assert readme["pattern_run"] == str(latest.pk)


def test_no_completed_run_is_not_measured_not_empty(built):
    study = factories.StudyFactory.create()
    _run(study, is_live=True)
    _run(study, status=WorkStatus.FAILED)

    files = patterns.study_pattern_files(study)

    assert built == []
    assert list(files) == ["README.txt"]
    assert json.loads(files["README.txt"])["status"] == "not_measured"


def test_failed_export_is_reported_as_error(monkeypatch):
    study = factories.StudyFactory.create()
    run = _run(study)

    def boom(_pattern_run):
        msg = "blob missing"
        raise RuntimeError(msg)

    monkeypatch.setattr("backend.generation.patterns.export.build_export", boom)
    files = patterns.study_pattern_files(study)

    assert list(files) == ["README.txt"]
    readme = json.loads(files["README.txt"])
    assert readme["status"] == "error"
    assert readme["pattern_run"] == str(run.pk)
    assert "blob missing" in readme["error"]
