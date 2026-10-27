"""Tests for scaffolding manifest, resolution, patches, and apply_scaffold."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.runtime.services import scaffolding as svc


def test_load_manifest_has_expected_stacks():
    manifest = svc.load_manifest()
    assert manifest["schema_version"] == 2
    assert "flask-react" in manifest["stacks"]
    assert "generic-python" in manifest["stacks"]
    assert "fastapi-react" in manifest["stacks"]


@pytest.mark.django_db
def test_resolve_stack_slug_alias():
    job = GenerationJobFactory(stack_slug="react-flask", mode="scaffolding")
    assert svc.resolve_stack_slug(job) == "flask-react"


@pytest.mark.django_db
def test_resolve_stack_slug_unknown_falls_back():
    job = GenerationJobFactory(stack_slug="vue-fastapi", mode="scaffolding")
    assert svc.resolve_stack_slug(job) == "generic-python"


def test_canonical_stack_slug_passthrough():
    assert svc.canonical_stack_slug("flask-react") == "flask-react"
    assert svc.canonical_stack_slug("fastapi-vue") == "fastapi-vue"


def test_canonical_stack_slug_alias():
    assert svc.canonical_stack_slug("react-flask") == "flask-react"


def test_canonical_stack_slug_unknown_falls_back():
    assert svc.canonical_stack_slug("does-not-exist") == "generic-python"


def test_stack_has_frontend():
    assert svc.stack_has_frontend("flask-react") is True
    assert svc.stack_has_frontend("fastapi-react") is True
    assert svc.stack_has_frontend("generic-python") is False


def test_fix_sqlite_uri_relative_to_absolute():
    code = "app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///data/app.db'"
    patched = svc._fix_sqlite_uri(code)
    assert "sqlite:////app/data/app.db" in patched


def test_fix_sqlite_uri_leaves_four_slash_absolute():
    code = "uri = 'sqlite:////app/data/existing.db'"
    assert svc._fix_sqlite_uri(code) == code


def test_fix_sqlite_uri_fstring_expression_with_quotes():
    # DeepSeek-style: quotes inside the brace expression. Cutting at the first
    # quote used to leave an unbalanced '}' → SyntaxError on boot.
    code = "app.config['X'] = f\"sqlite:///{Path(__file__).parent / 'data' / 'app.db'}\""
    patched = svc._fix_sqlite_uri(code)
    assert 'f"sqlite:////app/data/app.db"' in patched
    assert "{" not in patched.split("=", 1)[1]


def test_fix_sqlite_uri_fstring_variable():
    # Haiku-style: a plain variable. Used to become /app/data/{db_path},
    # nesting whatever absolute path the variable held.
    code = "app.config['X'] = f'sqlite:///{db_path}'"
    patched = svc._fix_sqlite_uri(code)
    assert "sqlite:////app/data/app.db" in patched
    assert "{db_path}" not in patched


def test_fix_flask_port_replaces_5000_default():
    code = "port = int(os.environ.get('PORT', 5000))"
    assert "8000" in svc._fix_flask_port(code)


def test_fix_db_create_all_wraps_init_block():
    code = (
        "if __name__ == '__main__':\n"
        "    db.create_all()\n"
        "    seed()\n"
        "    port = int(os.environ.get('PORT', 8000))\n"
        "    app.run(host='0.0.0.0', port=port)\n"
    )
    patched = svc._fix_db_create_all(code)
    assert "with app.app_context():" in patched
    assert "db.create_all()" in patched


def test_render_substitutions_with_default():
    text = "PORT={{app_port|8000}}\nNAME={{PROJECT_NAME}}"
    out = svc._render_substitutions(text, {"PROJECT_NAME": "my-app"})
    assert "PORT=8000" in out
    assert "NAME=my-app" in out


@pytest.mark.django_db
def test_apply_scaffold_seed_flask_react(tmp_path: Path):
    app_req = AppRequirementTemplateFactory(slug="demo-app")
    job = GenerationJobFactory(
        mode="copilot",
        stack_slug="flask-react",
        app_requirement=app_req,
        copilot_description="Build a demo",
    )
    dest = tmp_path / "workspace"
    svc.apply_scaffold(job, dest, phase=svc.ScaffoldPhase.SEED)

    assert (dest / "app.py").is_file()
    assert (dest / "frontend" / "src" / "App.jsx").is_file()
    assert (dest / "Dockerfile").is_file()
    env_example = dest / ".env.example"
    if env_example.is_file():
        content = env_example.read_text(encoding="utf-8")
        assert "{{" not in content
        assert "demo-app" in content or "8000" in content


@pytest.mark.django_db
def test_prepare_build_dir_writes_generated_code(tmp_path: Path):
    job = GenerationJobFactory(
        mode="scaffolding",
        stack_slug="flask-react",
        result_data={
            "backend_code": (
                "from flask import Flask\napp = Flask(__name__)\nif __name__ == '__main__':\n    app.run(port=5000)\n"
            ),
            "frontend_code": "export default function App() { return null; }",
        },
    )
    dest = tmp_path / "build"
    svc.prepare_build_dir(job, dest)

    app_py = (dest / "app.py").read_text(encoding="utf-8")
    assert "8000" in app_py
    assert (dest / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8").startswith("export")


@pytest.mark.django_db
def test_prepare_build_dir_generic_python(tmp_path: Path):
    job = GenerationJobFactory(
        mode="scaffolding",
        result_data={"backend_code": "from flask import Flask\napp = Flask(__name__)\n"},
    )
    dest = tmp_path / "build"
    svc.prepare_build_dir(job, dest)

    assert (dest / "app.py").is_file()
    assert (dest / "Dockerfile").is_file()
    dockerfile = (dest / "Dockerfile").read_text(encoding="utf-8")
    assert "8000" in dockerfile
    assert not (dest / "frontend").exists()


@pytest.mark.django_db
def test_prepare_build_dir_multi_file_writes_all_files(tmp_path: Path):
    job = GenerationJobFactory(
        mode="scaffolding",
        stack_slug="flask-react",
        result_data={
            "result_schema_version": 2,
            "backend_entry": "app.py",
            "files": {
                "app.py": (
                    "from models import db\nfrom flask import Flask\n"
                    "app = Flask(__name__)\nif __name__ == '__main__':\n    app.run(port=5000)\n"
                ),
                "models.py": "import yaml\ndb = None\n",
                "frontend/src/App.jsx": "export default function App() { return null; }",
                "frontend/src/api.js": "export const base = '/api';",
            },
        },
    )
    dest = tmp_path / "build"
    svc.prepare_build_dir(job, dest)

    app_py = (dest / "app.py").read_text(encoding="utf-8")
    assert "8000" in app_py  # entry file gets the flask patch profile applied
    assert (dest / "models.py").read_text(encoding="utf-8") == "import yaml\ndb = None\n"
    assert (dest / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8").startswith("export")
    assert (dest / "frontend" / "src" / "api.js").is_file()

    requirements = (dest / "requirements.txt").read_text(encoding="utf-8")
    assert "pyyaml" in requirements.lower()  # dependency inferred from every python file, not just the entry


@pytest.mark.django_db
def test_prepare_build_dir_multi_file_renames_non_canonical_entry(tmp_path: Path):
    """The backend entry can arrive under any name; it always lands at the stack's canonical filename."""
    job = GenerationJobFactory(
        mode="scaffolding",
        stack_slug="flask-react",
        result_data={
            "result_schema_version": 2,
            "backend_entry": "main.py",
            "files": {"main.py": "from flask import Flask\napp = Flask(__name__)\n"},
        },
    )
    dest = tmp_path / "build"
    svc.prepare_build_dir(job, dest)

    assert (dest / "app.py").is_file()
    assert not (dest / "main.py").exists()


@pytest.mark.django_db
def test_prepare_build_dir_multi_file_falls_back_to_placeholder_when_files_empty(tmp_path: Path):
    job = GenerationJobFactory(
        mode="scaffolding",
        stack_slug="flask-react",
        result_data={"result_schema_version": 2, "files": {}},
    )
    dest = tmp_path / "build"
    svc.prepare_build_dir(job, dest)

    # Empty files map isn't truthy, so this takes the legacy branch and still
    # produces a bootable placeholder rather than an empty build dir.
    assert (dest / "app.py").is_file()


@pytest.mark.django_db
def test_apply_scaffold_seed_fastapi_react(tmp_path: Path):
    app_req = AppRequirementTemplateFactory(slug="campaign-monitor")
    job = GenerationJobFactory(
        mode="copilot",
        stack_slug="fastapi-react",
        app_requirement=app_req,
        copilot_description="Build a campaign monitor",
    )
    dest = tmp_path / "workspace"
    svc.apply_scaffold(job, dest, phase=svc.ScaffoldPhase.SEED)

    assert (dest / "app.py").is_file()
    assert (dest / "frontend" / "src" / "App.jsx").is_file()
    assert (dest / "requirements.txt").is_file()


def test_manifest_server_kind_matches_start_command():
    stacks = svc.load_manifest()["stacks"]
    assert {slug: c["server_kind"] for slug, c in stacks.items()} == {
        "flask-react": "python",
        "generic-python": "python",
        "fastapi-vue": "uvicorn",
        "fastapi-react": "uvicorn",
    }


def test_builtin_skeletons_use_current_runtimes():
    root = Path(svc._TEMPLATES_DIR)
    for slug in ("flask-react", "fastapi-react", "fastapi-vue", "generic-python"):
        dockerfile = (root / slug / "Dockerfile").read_text()
        assert "FROM python:3.13-slim" in dockerfile
        assert "USER appuser" in dockerfile
        assert "HEALTHCHECK" in dockerfile
    for slug in ("flask-react", "fastapi-react", "fastapi-vue"):
        assert "FROM node:24-alpine AS frontend-builder" in (root / slug / "Dockerfile").read_text()
        vite = (root / slug / "frontend" / "vite.config.js").read_text()
        # FastAPI mounts ./static under /static/; Flask's catch-all serves it by path from /.
        assert ("base: " in vite) == slug.startswith("fastapi-")
        assert not (root / slug / "frontend" / "tailwind.config.js").exists()
        assert not (root / slug / "frontend" / "postcss.config.js").exists()


def test_skeleton_npm_and_pip_pins_are_exact():
    import json
    import re

    root = Path(svc._TEMPLATES_DIR)
    for slug in ("flask-react", "fastapi-react", "fastapi-vue"):
        package = json.loads((root / slug / "frontend" / "package.json").read_text())
        for name, version in {**package["dependencies"], **package["devDependencies"]}.items():
            assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"{slug}: {name} {version}"
    for slug in ("flask-react", "fastapi-react", "fastapi-vue", "generic-python"):
        for line in (root / slug / "requirements.txt").read_text().splitlines():
            assert "==" in line, f"{slug}: {line}"


def test_placeholder_backend_follows_server_kind_not_slug():
    assert "FastAPI" in svc._placeholder_backend_for_stack({"canonical_slug": "anything", "server_kind": "uvicorn"})
    assert "Flask" in svc._placeholder_backend_for_stack({"canonical_slug": "fastapi-react", "server_kind": "python"})
    # Builtin fastapi-vue rows seeded before server_kind existed say "python"
    # and got the FastAPI stub by slug; pinned retries keep it.
    assert "FastAPI" in svc._placeholder_backend_for_stack({"canonical_slug": "fastapi-vue", "server_kind": "python"})


def test_fastapi_placeholder_serves_vite_static_base():
    code = svc._placeholder_fastapi()
    assert "app.mount('/static'" in code
    assert "/assets" not in code
    compile(code, "app.py", "exec")


@pytest.mark.django_db
def test_seed_fastapi_react_gets_fastapi_placeholder(tmp_path: Path):
    job = GenerationJobFactory(mode="copilot", stack_slug="fastapi-react", copilot_description="x")
    dest = tmp_path / "workspace"
    svc.apply_scaffold(job, dest, phase=svc.ScaffoldPhase.SEED)

    app_py = (dest / "app.py").read_text()
    assert "from fastapi import" in app_py
    assert "flask" not in app_py.lower()


def test_lucide_unknown_icon_aliased_to_fallback():
    code = "import { House, NotARealIcon, Github as Gh } from 'lucide-react';\n"
    fixed = svc._sanitize_lucide_imports(code)
    assert "House" in fixed
    assert f"{svc._LUCIDE_FALLBACK} as NotARealIcon" in fixed
    assert f"{svc._LUCIDE_FALLBACK} as Gh" in fixed
    assert svc._LUCIDE_FALLBACK in svc._get_valid_lucide_icons()


def test_lucide_valid_imports_untouched():
    code = "import {\n  House,\n  LoaderCircle as Spinner,\n  type LucideProps,\n} from 'lucide-react';\n"
    assert svc._sanitize_lucide_imports(code) == code


def test_requirement_names_strip_extras_pins_and_markers():
    text = (
        "uvicorn[standard]==0.54.0\nFlask_SQLAlchemy==3.1.1\n"
        "pwdlib[argon2]; python_version >= '3.10'\n# c\n-r base.txt\n"
    )
    assert svc._requirement_names(text) == {"uvicorn", "flask-sqlalchemy", "pwdlib"}


@pytest.mark.django_db
def test_fastapi_build_does_not_duplicate_pinned_requirements(tmp_path: Path):
    backend = (
        "import uvicorn\nimport jwt\nfrom fastapi import FastAPI\nfrom pwdlib import PasswordHash\n"
        "from pydantic import EmailStr\nfrom sqlalchemy import select\nfrom database import Base\n"
        "app = FastAPI()\n"
    )
    job = GenerationJobFactory(
        mode="scaffolding",
        stack_slug="fastapi-react",
        result_data={
            "result_schema_version": 2,
            "backend_entry": "app.py",
            "files": {"app.py": backend, "database.py": "Base = object\n"},
        },
    )
    report = svc.HealingReport()
    dest = svc.prepare_build_dir(job, tmp_path / "build", report=report)

    assert report.added_requirements == []
    assert (dest / "requirements.txt").read_text().count("uvicorn") == 1
