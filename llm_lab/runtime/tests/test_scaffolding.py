"""Tests for scaffolding manifest, resolution, patches, and apply_scaffold."""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_lab.generation.tests.factories import AppRequirementTemplateFactory
from llm_lab.generation.tests.factories import GenerationJobFactory
from llm_lab.generation.tests.factories import ScaffoldingTemplateFactory
from llm_lab.runtime.services import scaffolding as svc


def test_load_manifest_has_expected_stacks():
    manifest = svc.load_manifest()
    assert manifest["schema_version"] == 1
    assert "flask-react" in manifest["stacks"]
    assert "generic-python" in manifest["stacks"]


@pytest.mark.django_db
def test_resolve_stack_slug_alias():
    template = ScaffoldingTemplateFactory(slug="react-flask")
    job = GenerationJobFactory(scaffolding_template=template, mode="scaffolding")
    assert svc.resolve_stack_slug(job) == "flask-react"


@pytest.mark.django_db
def test_resolve_stack_slug_unknown_falls_back():
    template = ScaffoldingTemplateFactory(slug="vue-fastapi")
    job = GenerationJobFactory(scaffolding_template=template, mode="scaffolding")
    assert svc.resolve_stack_slug(job) == "generic-python"


def test_stack_has_frontend():
    assert svc.stack_has_frontend("flask-react") is True
    assert svc.stack_has_frontend("generic-python") is False


def test_fix_sqlite_uri_relative_to_absolute():
    code = "app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///data/app.db'"
    patched = svc._fix_sqlite_uri(code)
    assert "sqlite:////app/data/app.db" in patched


def test_fix_sqlite_uri_leaves_four_slash_absolute():
    code = "uri = 'sqlite:////app/data/existing.db'"
    assert svc._fix_sqlite_uri(code) == code


def test_fix_sqlite_uri_does_not_corrupt_fstring():
    # The f-string form is correct at runtime (resolves to an absolute path);
    # the patcher must not touch it or it produces broken syntax.
    code = "app.config['SQLALCHEMY_DATABASE_URI'] = f\"sqlite:///{Path(__file__).parent / 'data' / 'app.db'}\""
    assert svc._fix_sqlite_uri(code) == code


def test_fix_flask_port_replaces_5000_default():
    code = "port = int(os.environ.get('PORT', 5000))"
    assert "8000" in svc._fix_flask_port(code)


def test_fix_flask_port_replaces_any_port_number():
    for port in (3000, 3001, 5001, 8080):
        code = f"app.run(host='0.0.0.0', port={port}, debug=False)"
        patched = svc._fix_flask_port(code)
        assert "8000" in patched, f"port {port} not patched: {patched}"


def test_fix_flask_port_replaces_env_string_default():
    code = "int(os.environ.get('PORT', '5000'))"
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


def test_fix_seed_data_call_wraps_bare_call():
    code = (
        "if __name__ == '__main__':\n"
        "    with app.app_context():\n"
        "        db.create_all()\n"
        "        seed_data()\n"
        "    app.run()\n"
    )
    patched = svc._fix_seed_data_call(code)
    assert "try:" in patched
    assert "    seed_data()" in patched
    assert "except Exception:" in patched


def test_fix_seed_data_call_noop_when_already_wrapped():
    code = (
        "    try:\n"
        "        seed_data()\n"
        "    except Exception:\n"
        "        pass\n"
    )
    patched = svc._fix_seed_data_call(code)
    assert patched == code


def test_fix_sqlalchemy_text_wraps_raw_select():
    code = (
        "from sqlalchemy import Column\n"
        "@app.route('/api/health')\n"
        "def health():\n"
        "    try:\n"
        "        db.session.execute('SELECT 1')\n"
        "        return jsonify({'status': 'healthy'})\n"
        "    except Exception as e:\n"
        "        return jsonify({'status': 'error'})\n"
    )
    patched = svc._fix_sqlalchemy_text(code)
    assert "text('SELECT 1')" in patched
    assert "from sqlalchemy import" in patched
    assert "text" in patched.split("from sqlalchemy import")[1].split("\n")[0]


def test_fix_sqlalchemy_text_noop_when_already_wrapped():
    code = "db.session.execute(text('SELECT 1'))\n"
    assert svc._fix_sqlalchemy_text(code) == code


def test_ensure_spa_catchall_injects_when_missing():
    code = (
        "from flask import Flask, jsonify\n"
        "app = Flask(__name__)\n\n"
        "@app.route('/api/health')\n"
        "def health(): return jsonify({'status': 'ok'})\n\n"
        "if __name__ == '__main__':\n"
        "    app.run(host='0.0.0.0', port=8000)\n"
    )
    patched = svc._ensure_spa_catchall(code)
    assert "_serve_spa" in patched
    assert "/<path:path>" in patched
    # catch-all must appear before the main block
    assert patched.index("_serve_spa") < patched.index("if __name__")


def test_ensure_spa_catchall_noop_when_present():
    code = (
        "from flask import Flask, send_from_directory\n"
        "app = Flask(__name__)\n\n"
        "@app.route('/', defaults={'path': ''})\n"
        "@app.route('/<path:path>')\n"
        "def serve_spa(path): return send_from_directory('/app/static', 'index.html')\n"
    )
    patched = svc._ensure_spa_catchall(code)
    assert patched == code


def test_strip_filename_header_removes_bare_filename():
    code = "app.py\nfrom flask import Flask\napp = Flask(__name__)\n"
    assert svc._strip_filename_header(code) == "from flask import Flask\napp = Flask(__name__)\n"


def test_strip_filename_header_noop_when_no_header():
    code = "from flask import Flask\napp = Flask(__name__)\n"
    assert svc._strip_filename_header(code) == code


def test_strip_markdown_fence_removes_opening_fence():
    code = "```jsx:App.jsx\nimport React from 'react';\n"
    assert svc._strip_markdown_fence(code) == "import React from 'react';\n"


def test_strip_markdown_fence_noop_when_no_fence():
    code = "import React from 'react';\n"
    assert svc._strip_markdown_fence(code) == code


def test_fix_template_literal_quotes_fixes_mismatched_single_quote():
    code = "  toggleUser: (id) => api.post(`/api/users/${id}/toggle')\n"
    patched = svc._fix_template_literal_quotes(code)
    assert patched == "  toggleUser: (id) => api.post(`/api/users/${id}/toggle`)\n"


def test_fix_template_literal_quotes_noop_when_correct():
    code = "  toggleUser: (id) => api.post(`/api/users/${id}/toggle`)\n"
    assert svc._fix_template_literal_quotes(code) == code


def test_render_substitutions_with_default():
    text = "PORT={{app_port|8000}}\nNAME={{PROJECT_NAME}}"
    out = svc._render_substitutions(text, {"PROJECT_NAME": "my-app"})
    assert "PORT=8000" in out
    assert "NAME=my-app" in out


@pytest.mark.django_db
def test_apply_scaffold_seed_flask_react(tmp_path: Path):
    template = ScaffoldingTemplateFactory(slug="flask-react", name="Flask React")
    app_req = AppRequirementTemplateFactory(slug="demo-app")
    job = GenerationJobFactory(
        mode="copilot",
        scaffolding_template=template,
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
    template = ScaffoldingTemplateFactory(slug="flask-react")
    job = GenerationJobFactory(
        mode="scaffolding",
        scaffolding_template=template,
        result_data={
            "backend_code": (
                "from flask import Flask\napp = Flask(__name__)\n"
                "if __name__ == '__main__':\n"
                "    app.run(port=5000)\n"
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
        scaffolding_template=None,
        result_data={"backend_code": "from flask import Flask\napp = Flask(__name__)\n"},
    )
    dest = tmp_path / "build"
    svc.prepare_build_dir(job, dest)

    assert (dest / "app.py").is_file()
    assert (dest / "Dockerfile").is_file()
    dockerfile = (dest / "Dockerfile").read_text(encoding="utf-8")
    assert "8000" in dockerfile
    assert not (dest / "frontend").exists()
