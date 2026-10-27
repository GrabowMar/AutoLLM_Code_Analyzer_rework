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


def test_vue_lucide_imports_are_healed_like_react(tmp_path):
    vue = '<script setup>\nimport { Github, House } from "@lucide/vue";\n</script>\n<template><Github /></template>\n'
    job = GenerationJobFactory(
        mode="scaffolding",
        stack_slug="fastapi-vue",
        result_data={"result_schema_version": 2, "files": {"app.py": "app = None\n", "frontend/src/App.vue": vue}},
    )
    dest, report = _build(job, tmp_path)

    text = (dest / "frontend" / "src" / "App.vue").read_text()
    assert 'import { CircleAlert as Github, House } from "@lucide/vue"' in text
    assert "frontend/src/App.vue:lucide_icons" in report.patches


def test_react_router_dom_imports_point_at_react_router(tmp_path):
    app = "import { Routes, Route } from 'react-router-dom';\nexport default function App() { return null; }\n"
    page = 'import { useNavigate } from "react-router-dom";\nexport const x = useNavigate;\n'
    dest, report = _build(
        _job(files={"app.py": _BACKEND, "frontend/src/App.jsx": app, "frontend/src/pages/x.js": page}),
        tmp_path,
    )

    assert "from 'react-router';" in (dest / "frontend" / "src" / "App.jsx").read_text()
    assert 'from "react-router";' in (dest / "frontend" / "src" / "pages" / "x.js").read_text()
    assert {"frontend/src/App.jsx:react_router_dom", "frontend/src/pages/x.js:react_router_dom"} <= set(report.patches)

    raw, raw_report = _build(_job(files={"app.py": _BACKEND, "frontend/src/App.jsx": app}), tmp_path / "raw", "none")
    assert "react-router-dom" in (raw / "frontend" / "src" / "App.jsx").read_text()
    assert not raw_report.patches


def test_router_dom_kept_when_the_model_installs_it(tmp_path):
    app = "import { Routes } from 'react-router-dom';\nexport default function App() { return null; }\n"
    package = '{"dependencies": {"react-router-dom": "7.0.0"}}'
    dest, report = _build(
        _job(files={"app.py": _BACKEND, "frontend/src/App.jsx": app, "frontend/package.json": package}),
        tmp_path,
    )
    assert "react-router-dom" in (dest / "frontend" / "src" / "App.jsx").read_text()
    assert not any(p.endswith(":react_router_dom") for p in report.patches)


def test_model_stylesheet_keeps_the_tailwind4_entry(tmp_path):
    css = (
        "@import url('https://fonts.example/x.css');\n"
        "@tailwind base;\n@tailwind utilities;\n.card { @apply rounded-lg; }\n"
    )
    dest, report = _build(_job(files={"app.py": _BACKEND, "frontend/src/App.css": css}), tmp_path)

    healed = (dest / "frontend" / "src" / "App.css").read_text()
    assert healed.startswith("@import \"tailwindcss\";\n@import url('https://fonts.example/x.css');\n")
    assert "@tailwind" not in healed
    assert "--color-primary-500" in healed
    assert healed.rstrip().endswith(".card { @apply rounded-lg; }")
    assert "frontend/src/App.css:tailwind_entry" in report.patches

    own = '@import "tailwindcss";\n.x { color: red; }\n'
    dest2, report2 = _build(_job(files={"app.py": _BACKEND, "frontend/src/App.css": own}), tmp_path / "own")
    assert (dest2 / "frontend" / "src" / "App.css").read_text() == own
    assert not any(p.endswith(":tailwind_entry") for p in report2.patches)


def test_passlib_gets_a_compatible_bcrypt_unless_the_template_pins_one(tmp_path):
    code = "from passlib.context import CryptContext\nimport bcrypt\nfrom flask import Flask\napp = Flask(__name__)\n"
    dest, report = _build(_job(files={"app.py": code}), tmp_path)

    # flask-react pins bcrypt==5.0.0: the template's pin wins, no duplicate line.
    assert "passlib[bcrypt]" in report.added_requirements
    assert not any(r.startswith("bcrypt") for r in report.added_requirements)
    assert (dest / "requirements.txt").read_text().count("bcrypt==") == 1

    fastapi = GenerationJobFactory(
        mode="scaffolding",
        stack_slug="fastapi-react",
        result_data={"result_schema_version": 2, "files": {"app.py": "from passlib.context import CryptContext\n"}},
    )
    _dest, fa_report = _build(fastapi, tmp_path / "fa")
    assert {"passlib[bcrypt]", "bcrypt==4.0.1"} <= set(fa_report.added_requirements)
