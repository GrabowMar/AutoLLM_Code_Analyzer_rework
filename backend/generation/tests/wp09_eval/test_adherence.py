"""WP-09: the adherence checker on fixture trees (spec 8.7)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.generation.contracts.evaluation import AdherenceRecord
from backend.generation.evaluation.tester import checker_module

FLASK = "from flask import Flask\napp = Flask(__name__)\n"
FASTAPI = "from fastapi import FastAPI\napp = FastAPI()\n"


def stack(backend: str, frontend: str = "react", database: str = "sqlite", choice: str = "fixed") -> dict[str, Any]:
    return {
        "choice_mode": choice,
        "backend": {"language": "python", "framework": backend, "runtime": "python3.13"},
        "frontend": {"framework": frontend, "language": "ts", "build_tool": "vite"},
        "database": database,
    }


def tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return root


def analyse(root: Path, expect: dict[str, Any]) -> dict[str, Any]:
    record = checker_module().analyse(root, expect)
    AdherenceRecord.model_validate(record)  # the contract holds
    return record


def react_frontend(app_tsx: str = "export default function App() { return <div/> }\n") -> dict[str, str]:
    return {
        "frontend/package.json": json.dumps({"dependencies": {"react": "19.0.0", "react-dom": "19.0.0"}}),
        "frontend/src/App.tsx": "import React from 'react'\n" + app_tsx,
    }


def test_flask_labelled_fastapi_is_violated(tmp_path: Path) -> None:
    root = tree(tmp_path, {"backend/app.py": FLASK, "backend/requirements.txt": "flask==3.1.0\n", **react_frontend()})
    record = analyse(root, {"stack": stack("fastapi"), "auth_mode": "contract_bearer"})
    assert record["detected"]["backend"]["value"] == "flask"
    assert record["detected"]["backend"]["confidence"] == "high"
    assert record["status"] == "violated"
    assert [d["dimension"] for d in record["deviations"]] == ["backend"]


def test_matching_stack_is_compliant_and_lists_packages(tmp_path: Path) -> None:
    root = tree(
        tmp_path,
        {"backend/app.py": FASTAPI, "backend/requirements.txt": "fastapi\nuvicorn==0.30.0\n", **react_frontend()},
    )
    record = analyse(root, {"stack": stack("fastapi"), "auth_mode": "contract_bearer", "runtime": {"auth": "bearer"}})
    assert record["status"] == "compliant"
    assert record["unpinned"] == ["fastapi"]
    assert {p["name"] for p in record["packages"]} >= {"fastapi", "uvicorn", "react"}
    assert record["detected"]["auth"]["value"] == "bearer"


def test_tailwind_class_share(tmp_path: Path) -> None:
    app = 'export default () => <div className="flex p-4 text-sm bg-white rounded-lg custom-card"/>\n'
    files = react_frontend(app)
    files["frontend/package.json"] = json.dumps(
        {"dependencies": {"react": "19.0.0"}, "devDependencies": {"tailwindcss": "4.0.0"}},
    )
    root = tree(tmp_path, {"backend/app.py": FLASK, **files})
    record = analyse(root, {"stack": stack("flask"), "style": {"system": "tailwind"}, "auth_mode": "contract_bearer"})
    assert record["style_metrics"]["tailwind_class_share"] == 0.8333
    assert record["detected"]["styling"]["value"] == "tailwind"
    assert not [d for d in record["deviations"] if d["dimension"] == "styling"]


def test_component_library_imports_against_plain_css_are_partial(tmp_path: Path) -> None:
    app = (
        "import Button from '@mui/material/Button'\nimport { TextField } from '@mui/material'\n"
        "export default () => <Button/>\n"
    )
    root = tree(tmp_path, {"backend/app.py": FLASK, **react_frontend(app)})
    record = analyse(root, {"stack": stack("flask"), "style": {"system": "plain-css"}, "auth_mode": "contract_bearer"})
    assert record["style_metrics"]["component_library_imports"] == {"@mui/material": 2}
    assert record["detected"]["styling"]["value"] == "component-library"
    assert record["status"] == "partial"


def test_auth_mode_mismatch_is_violated(tmp_path: Path) -> None:
    root = tree(tmp_path, {"backend/app.py": FLASK, **react_frontend()})
    record = analyse(root, {"stack": stack("flask"), "auth_mode": "cookie_session", "runtime": {"auth": "bearer"}})
    assert record["status"] == "violated"
    assert [d["dimension"] for d in record["deviations"]] == ["auth"]


def test_free_choice_records_the_pick_and_is_compliant(tmp_path: Path) -> None:
    root = tree(tmp_path, {"server/main.py": FASTAPI, "server/db.py": "import sqlite3\n", **react_frontend()})
    record = analyse(root, {"stack": stack("free", "free", "free", choice="free"), "auth_mode": "contract_bearer"})
    assert record["status"] == "compliant"
    assert record["detected"]["backend"]["value"] == "fastapi"
    assert record["detected"]["database"]["value"] == "sqlite"
    assert record["detected"]["frontend"]["value"] == "react"
