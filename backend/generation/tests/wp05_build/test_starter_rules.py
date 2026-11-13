"""The starter rule list (spec 11.4; the rules WP-03's lint reports for kind ``starter``)."""

from __future__ import annotations

import copy
import json

import pytest

from backend.generation import identity
from backend.generation.build import starter_rules
from backend.generation.build import starters
from backend.generation.contracts.issues import ISSUE_CODES

PY = "python:3.13-slim@sha256:" + "a" * 64


def _starter(files: dict[str, str], *, level: str = "minimal", lockfiles=(), bases=None, dockerfile=True) -> tuple:
    data = {path: text.encode() for path, text in files.items()}
    if dockerfile:
        data.setdefault("Dockerfile", f"FROM {PY}\nCOPY . .\n".encode())
    content = {
        "stack_profile": "flask-none@1#" + "0" * 12,
        "level": level,
        "files": {path: {"blob": identity.bytes_sha(b), "mode": 0o644} for path, b in data.items()},
        "dockerfile": "Dockerfile",
        "base_images": bases if bases is not None else [{"ref": "python:3.13-slim", "digest": PY}],
        "lockfiles": list(lockfiles),
        "composition": {"backend_fragment": "x", "frontend_fragment": None, "dockerfile_template": "flask"},
        "forbidden_content_check": {"passed": True, "linter_version": 1},
    }
    return content, data


FLASK_NONE = starters.load_stack_yaml("flask-none")["content"]
GOOD = {"backend/requirements.txt": "flask==3.1.3\n", "backend/requirements.lock": "flask==3.1.3\n"}


def codes(issues) -> list[str]:
    return sorted(issue.code for issue in issues)


def test_every_rule_code_is_registered():
    for code in (
        "lint_starter_forbidden_content",
        "lint_starter_security_library",
        "lint_starter_unpinned_image",
        "lint_starter_file_missing",
        "lint_starter_stack_mismatch",
    ):
        assert code in ISSUE_CODES


def test_a_clean_starter_has_no_issues():
    content, files = _starter(GOOD, lockfiles=["backend/requirements.lock"])
    assert starter_rules.starter_issues(content, files, stack=FLASK_NONE) == []


@pytest.mark.parametrize(
    "text",
    ["# LLM: add auth here\n", '{"_comment": "use bcrypt"}\n', "// todo: Model should add CSRF\n"],
)
def test_forbidden_content(text):
    content, files = _starter({**GOOD, "backend/app.py": text}, lockfiles=["backend/requirements.lock"])
    issues = starter_rules.starter_issues(content, files, stack=FLASK_NONE)
    assert codes(issues) == ["lint_starter_forbidden_content"]
    assert issues[0].path == "files.backend/app.py"


@pytest.mark.parametrize(
    ("path", "text"),
    [
        ("backend/requirements.txt", "flask==3.1.3\nPyJWT==2.10\n"),
        ("backend/requirements.lock", "flask==3.1.3\nbcrypt==4.0 --hash=sha256:00\n"),
        ("frontend/package.json", json.dumps({"dependencies": {"helmet": "8"}})),
        (
            "frontend/package-lock.json",
            json.dumps({"lockfileVersion": 3, "packages": {"": {}, "node_modules/jsonwebtoken": {"version": "9"}}}),
        ),
        ("go.mod", "module app\n\nrequire github.com/golang-jwt/jwt/v5 v5.2.1\n"),
        ("backend/pyproject.toml", '[project]\nname = "x"\ndependencies = ["Flask-Login>=0.6"]\n'),
    ],
)
def test_security_libraries_are_denied(path, text):
    content, files = _starter({**GOOD, path: text}, level="bare")
    issues = [i for i in starter_rules.starter_issues(content, files) if i.code == "lint_starter_security_library"]
    assert issues, path


def test_unpinned_from_and_base_image_list_mismatch():
    content, files = _starter(GOOD, lockfiles=["backend/requirements.lock"])
    files["Dockerfile"] = b"FROM python:3.13-slim\n"
    assert "lint_starter_unpinned_image" in codes(starter_rules.starter_issues(content, files))
    content, files = _starter(GOOD, lockfiles=["backend/requirements.lock"], bases=[])
    assert codes(starter_rules.starter_issues(content, files)) == ["lint_starter_unpinned_image"]


def test_missing_dockerfile_lockfile_or_lock():
    content, files = _starter(GOOD, lockfiles=["backend/requirements.lock"], dockerfile=False)
    assert codes(starter_rules.starter_issues(content, files, stack=FLASK_NONE)) == ["lint_starter_file_missing"]
    content, files = _starter({"backend/requirements.txt": "flask==3.1.3\n"}, lockfiles=["backend/requirements.lock"])
    found = codes(starter_rules.starter_issues(content, files, stack=FLASK_NONE))
    assert found == ["lint_starter_file_missing", "lint_starter_file_missing"]  # listed lock absent + no lock


def test_stack_mismatch():
    content, files = _starter({**GOOD, "frontend/package.json": "{}"}, lockfiles=["backend/requirements.lock"])
    assert "lint_starter_stack_mismatch" in codes(starter_rules.starter_issues(content, files, stack=FLASK_NONE))
    content, files = _starter(
        {"backend/requirements.txt": "fastapi==1\n", "backend/requirements.lock": "fastapi==1\n"},
        lockfiles=["backend/requirements.lock"],
    )
    issues = starter_rules.starter_issues(content, files, stack=FLASK_NONE)
    assert codes(issues) == ["lint_starter_stack_mismatch"]
    assert issues[0].data["framework"] == "flask"
    free = starters.load_stack_yaml("free-choice")["content"]
    content, files = _starter({"README.md": "# App\n"}, level="minimal")
    found = codes(starter_rules.starter_issues(content, files, stack=free))
    assert found == ["lint_starter_stack_mismatch", "lint_starter_stack_mismatch"]


def test_issues_from_blobs_reads_by_sha():
    content, files = _starter(GOOD, lockfiles=["backend/requirements.lock"])
    by_sha = {identity.bytes_sha(data): data for data in files.values()}
    assert starter_rules.starter_issues_from_blobs(content, stack=FLASK_NONE, read=by_sha.__getitem__) == []
    bad = copy.deepcopy(content)
    bad["files"]["backend/app.py"] = {"blob": identity.bytes_sha(b"# LLM: hi\n"), "mode": 0o644}
    by_sha[identity.bytes_sha(b"# LLM: hi\n")] = b"# LLM: hi\n"
    found = codes(starter_rules.starter_issues_from_blobs(bad, stack=FLASK_NONE, read=by_sha.__getitem__))
    assert found == ["lint_starter_forbidden_content"]


def test_unparseable_manifest_is_reported_not_skipped():
    content, files = _starter({**GOOD, "frontend/package.json": "{not json"}, level="bare")
    issues = starter_rules.starter_issues(content, files)
    assert any(i.code == "lint_starter_file_missing" and i.data.get("unparseable") for i in issues)
