"""Build healing modes: what the build repairs in model output, and the report of it."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.generation.tests.factories import GenerationJobFactory
from backend.runtime.services import scaffolding as svc

pytestmark = pytest.mark.django_db

_BACKEND = (
    "import requests\n"
    "from flask import Flask\n"
    "app = Flask(__name__)\n"
    "app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///app.db'\n"
    "if __name__ == '__main__':\n"
    "    app.run(port=5000)\n"
)
_FRONTEND = "import { NotARealIcon } from 'lucide-react';\nexport default function App() { return null; }\n"


def _job(**kw):
    files = kw.pop("files", {"app.py": _BACKEND, "frontend/src/App.jsx": _FRONTEND})
    return GenerationJobFactory(
        mode="scaffolding",
        stack_slug="flask-react",
        result_data={"result_schema_version": 2, "backend_entry": "app.py", "files": files},
        **kw,
    )


def _build(job, tmp_path: Path, healing=None):
    report = svc.HealingReport()
    dest = svc.prepare_build_dir(job, tmp_path / "build", healing=healing, report=report)
    return dest, report


def test_full_healing_infers_deps_and_patches(tmp_path):
    dest, report = _build(_job(), tmp_path)

    assert report.mode == "full"
    assert report.changed
    assert "requests" in report.added_requirements
    assert "requests" in (dest / "requirements.txt").read_text()
    assert {"app.py:sqlite_uri", "app.py:flask_port", "frontend/src/App.jsx:lucide_icons"} <= set(report.patches)
    assert "8000" in (dest / "app.py").read_text()


def test_no_deps_keeps_patches_but_not_requirements(tmp_path):
    dest, report = _build(_job(), tmp_path, healing="no_deps")

    assert report.added_requirements == []
    assert "requests" not in (dest / "requirements.txt").read_text()
    assert "app.py:flask_port" in report.patches


def test_none_builds_output_as_written(tmp_path):
    dest, report = _build(_job(), tmp_path, healing="none")

    assert not report.changed
    assert (dest / "app.py").read_text() == _BACKEND
    assert (dest / "frontend" / "src" / "App.jsx").read_text() == _FRONTEND
    assert "requests" not in (dest / "requirements.txt").read_text()


def test_none_removes_skeleton_entry_the_model_never_wrote(tmp_path):
    job = _job(files={"frontend/src/App.jsx": _FRONTEND})

    dest, _report = _build(job, tmp_path, healing="none")
    assert not (dest / "app.py").exists()

    dest_full, report_full = _build(job, tmp_path / "full")
    assert (dest_full / "app.py").is_file()
    assert "app.py:placeholder" in report_full.patches


def test_job_build_healing_is_the_default(tmp_path):
    _dest, report = _build(_job(build_healing="none"), tmp_path)
    assert report.mode == "none"
    assert not report.changed


def test_legacy_result_honours_mode(tmp_path):
    job = GenerationJobFactory(
        mode="scaffolding",
        stack_slug="flask-react",
        build_healing="none",
        result_data={"backend_code": _BACKEND, "frontend_code": _FRONTEND},
    )
    dest, report = _build(job, tmp_path)
    assert (dest / "app.py").read_text() == _BACKEND
    assert not report.changed


def test_verbatim_agent_output_is_not_applicable(tmp_path):
    job = GenerationJobFactory(
        mode="agent",
        stack_slug="flask-react",
        result_data={"result_schema_version": 2, "materialize": "verbatim", "files": {"app.py": _BACKEND}},
    )
    _dest, report = _build(job, tmp_path)
    assert report.applicable is False
    assert report.to_dict()["changed"] is False


def test_unknown_mode_falls_back_to_full():
    assert svc.resolve_healing_mode(object(), "bogus") == svc.HealingMode.FULL
