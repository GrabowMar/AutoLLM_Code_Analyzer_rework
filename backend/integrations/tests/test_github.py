"""GitHub integration: service + API, with every GitHub call faked (no network)."""

from __future__ import annotations

import base64
import gzip
import json
from unittest.mock import patch

import pytest
import requests
from django.test import Client

from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.credentials.models import Provider
from backend.credentials.models import UserApiCredential
from backend.credentials.models import ValidationStatus
from backend.credentials.services.validator import validate_for_provider
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.integrations.services import github
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

API = "https://api.github.com"


def _response(status: int, body=None, headers=None) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = b"" if body is None else json.dumps(body).encode()
    resp.headers.update(headers or {})
    return resp


class FakeGitHub:
    """Records requests and answers them from a route table / sensible defaults."""

    def __init__(self, overrides=None):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.overrides = overrides or {}

    def __call__(self, method, url, *, headers=None, json=None, timeout=None):  # noqa: PLR0911
        self.calls.append((method, url, json))
        path = url.removeprefix(API)
        key = (method, path)
        if key in self.overrides:
            override = self.overrides[key]
            return override(json) if callable(override) else override
        if key == ("GET", "/user"):
            return _response(200, {"login": "octo", "name": "Octo Cat"}, {"X-OAuth-Scopes": "repo, gist"})
        if key == ("POST", "/user/repos"):
            name = json["name"]
            return _response(
                201,
                {"full_name": f"octo/{name}", "html_url": f"https://github.com/octo/{name}", "default_branch": "main"},
            )
        if method == "PUT" and "/contents/" in path:
            return _response(201, {"commit": {"sha": "init"}})
        if path.endswith("/git/blobs"):
            return _response(201, {"sha": f"blob{len(self.calls)}"})
        if path.endswith("/git/trees"):
            return _response(201, {"sha": "tree1"})
        if path.endswith("/git/commits"):
            return _response(201, {"sha": "commit1"})
        if method == "PATCH" and "/git/refs/heads/" in path:
            return _response(200, {"object": {"sha": json["sha"]}})
        if path.endswith("/code-scanning/sarifs"):
            return _response(202, {"id": "sarif-1", "url": "x"})
        if key == ("POST", "/gists"):
            return _response(201, {"html_url": "https://gist.github.com/octo/abc"})
        return _response(500, {"message": f"unexpected {method} {path}"})

    def find(self, method, suffix):
        return [c for c in self.calls if c[0] == method and c[1].endswith(suffix)]


def _plan(*slugs: str) -> dict:
    return {"schema_version": 1, "tools": [{"slug": s} for s in slugs]}


@pytest.fixture
def owner():
    return UserFactory.create()


@pytest.fixture
def token(owner):
    cred = UserApiCredential(user=owner, provider=Provider.GITHUB)
    cred.set_secret("ghp_testtoken1234567890")
    cred.save()
    return cred


@pytest.fixture
def job(owner):
    return GenerationJobFactory.create(
        created_by=owner,
        status="completed",
        model=LLMModelFactory.create(model_id="acme/coder-1", model_name="Coder One"),
        app_requirement=AppRequirementTemplateFactory.create(name="Todo App", slug="todo"),
        result_data={
            "result_schema_version": 2,
            "files": {
                "backend/app.py": "print('hi')\n",
                "frontend/src/App.jsx": "export default () => null;\n",
                "node_modules/left-pad/index.js": "junk",
                "backend/__pycache__/app.cpython-313.pyc": "junk",
                "big.txt": "x" * (github.MAX_FILE_BYTES + 1),
            },
        },
    )


@pytest.fixture
def findings(owner, job):
    run = AnalysisRun.objects.create(created_by=owner, generation_job=job, status="completed", plan=_plan("bandit"))
    result = ToolResult.objects.create(run=run, tool_slug="bandit", status="completed")
    Finding.objects.create(
        result=result,
        severity="high",
        title="Use of assert",
        rule_id="B101",
        file_path="/tmp/workspace/backend/app.py",  # noqa: S108
        line_number=1,
    )
    Finding.objects.create(result=result, severity="low", title="No location", rule_id="B000")
    return run


@pytest.fixture
def client(owner):
    c = Client()
    c.force_login(owner)
    return c


def _post(client, payload):
    return client.post("/api/integrations/github/export/", data=json.dumps(payload), content_type="application/json")


# ── validator ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "headers", "expected"),
    [
        (200, {}, ValidationStatus.VALID),
        (401, {}, ValidationStatus.INVALID),
        (403, {}, ValidationStatus.INVALID),
        (403, {"X-RateLimit-Remaining": "0"}, ValidationStatus.RATE_LIMITED),
        (429, {}, ValidationStatus.RATE_LIMITED),
        (500, {}, ValidationStatus.NETWORK_ERROR),
    ],
)
def test_validator_maps_github_statuses(status, headers, expected):
    with patch(
        "backend.credentials.services.validator.requests.get",
        return_value=_response(status, {"login": "octo"}, headers),
    ) as get:
        result = validate_for_provider("github", "ghp_x")
    assert result.status == expected
    assert get.call_args.args[0] == "https://api.github.com/user"


def test_github_is_a_credentials_provider(client):
    with patch(
        "backend.credentials.services.validator.requests.get",
        return_value=_response(200, {"login": "octo"}),
    ):
        resp = client.put(
            "/api/credentials/github/",
            data=json.dumps({"api_key": "ghp_abcdefghijklmnop"}),
            content_type="application/json",
        )
    assert resp.status_code == 200
    assert resp.json()["provider"] == "github"
    assert resp.json()["configured"] is True


# ── status ────────────────────────────────────────────────────────────


def test_status_not_connected(client):
    resp = client.get("/api/integrations/github/status/")
    assert resp.status_code == 200
    assert resp.json()["configured"] is False
    assert resp.json()["connected"] is False


def test_status_connected(client, token):
    with patch.object(github.requests, "request", FakeGitHub()):
        data = client.get("/api/integrations/github/status/").json()
    assert data["connected"] is True
    assert data["login"] == "octo"
    assert data["scopes"] == ["repo", "gist"]
    assert data["token_type"] == "classic"  # noqa: S105


def test_status_bad_token(client, token):
    fake = FakeGitHub({("GET", "/user"): _response(401, {"message": "Bad credentials"})})
    with patch.object(github.requests, "request", fake):
        data = client.get("/api/integrations/github/status/").json()
    assert data["configured"] is True
    assert data["connected"] is False
    assert "401" in data["message"]


# ── repo export ───────────────────────────────────────────────────────


def test_export_repo_single_commit(client, token, job, findings):
    fake = FakeGitHub()
    with patch.object(github.requests, "request", fake):
        resp = _post(
            client,
            {"application_id": str(job.id), "repo_name": "todo-app", "include_scaffold": False},
        )
    assert resp.status_code == 200, resp.content
    data = resp.json()
    assert data["url"] == "https://github.com/octo/todo-app"
    assert data["commit_sha"] == "commit1"
    assert data["sarif"]["uploaded"] is False

    create = fake.find("POST", "/user/repos")[0][2]
    assert create["auto_init"] is False
    assert create["private"] is True

    tree = fake.find("POST", "/git/trees")[0][2]["tree"]
    paths = {e["path"] for e in tree}
    assert paths == {"backend/app.py", "frontend/src/App.jsx", "README.md"}
    readme = next(e["content"] for e in tree if e["path"] == "README.md")
    assert "acme/coder-1" in readme
    assert "Todo App" in readme
    assert "| high | 1 |" in readme
    assert set(data["skipped"]) >= {"big.txt", "node_modules/left-pad/index.js"}

    commit = fake.find("POST", "/git/commits")[0][2]
    assert commit["parents"] == []
    assert commit["tree"] == "tree1"
    ref = fake.find("PATCH", "/git/refs/heads/main")[0][2]
    assert ref == {"sha": "commit1", "force": True}
    assert not fake.find("POST", "/code-scanning/sarifs")


def test_export_repo_uses_blobs_for_binary(owner, token, job):
    with (
        patch.object(
            github,
            "_scaffolded_file_map",
            return_value=({"logo.png": b"\x89PNG\x00\xff", "run.sh": b"#!/bin/sh\n"}, {"run.sh"}),
        ),
        patch.object(github.requests, "request", fake := FakeGitHub()),
    ):
        github.export_to_repo(owner, job.id, "bin-app")
    tree = {e["path"]: e for e in fake.find("POST", "/git/trees")[0][2]["tree"]}
    assert "sha" in tree["logo.png"]
    assert "content" not in tree["logo.png"]
    assert tree["run.sh"]["mode"] == "100755"
    blob = fake.find("POST", "/git/blobs")[0][2]
    assert base64.b64decode(blob["content"]) == b"\x89PNG\x00\xff"


def test_export_repo_with_sarif(client, token, job, findings):
    fake = FakeGitHub()
    with patch.object(github.requests, "request", fake):
        resp = _post(
            client,
            {"application_id": str(job.id), "repo_name": "todo-app", "upload_sarif": True, "include_scaffold": False},
        )
    data = resp.json()
    assert data["sarif"]["uploaded"] is True
    assert data["sarif"]["id"] == "sarif-1"
    body = fake.find("POST", "/code-scanning/sarifs")[0][2]
    assert body["commit_sha"] == "commit1"
    assert body["ref"] == "refs/heads/main"
    sarif = json.loads(gzip.decompress(base64.b64decode(body["sarif"])))
    results = sarif["runs"][0]["results"]
    assert len(results) == 1  # the location-less finding is dropped
    uri = results[0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
    assert uri == "backend/app.py"  # absolute analyzer path mapped onto the repo
    # v2 seeds the tool catalog on migrate, so the driver links the tool's docs.
    docs_url = AnalyzerTool.objects.filter(slug="bandit").values_list("docs_url", flat=True).first()
    assert sarif["runs"][0]["tool"]["driver"].get("informationUri") == (docs_url or None)


@pytest.mark.parametrize("status", [403, 404])
def test_sarif_unavailable_is_a_warning(client, token, job, findings, status):
    fake = FakeGitHub(
        {
            ("POST", "/repos/octo/todo-app/code-scanning/sarifs"): _response(
                status,
                {"message": "Advanced Security must be enabled"},
            ),
        },
    )
    with patch.object(github.requests, "request", fake):
        resp = _post(
            client,
            {"application_id": str(job.id), "repo_name": "todo-app", "upload_sarif": True, "include_scaffold": False},
        )
    assert resp.status_code == 200
    sarif = resp.json()["sarif"]
    assert sarif["uploaded"] is False
    assert "Advanced Security" in sarif["message"]


def test_sarif_without_analysis(client, token, job):
    with patch.object(github.requests, "request", FakeGitHub()):
        resp = _post(
            client,
            {"application_id": str(job.id), "repo_name": "todo-app", "upload_sarif": True, "include_scaffold": False},
        )
    assert resp.status_code == 200
    assert "no completed analysis" in resp.json()["sarif"]["message"]


def test_repo_name_exists_is_422(client, token, job):
    fake = FakeGitHub(
        {
            ("POST", "/user/repos"): _response(
                422,
                {
                    "message": "Repository creation failed.",
                    "errors": [{"message": "name already exists on this account"}],
                },
            ),
        },
    )
    with patch.object(github.requests, "request", fake):
        resp = _post(client, {"application_id": str(job.id), "repo_name": "todo-app"})
    assert resp.status_code == 422
    assert "already exists" in resp.json()["detail"]


def test_bad_token_is_400_not_401(client, token, job):
    fake = FakeGitHub({("POST", "/user/repos"): _response(401, {"message": "Bad credentials"})})
    with patch.object(github.requests, "request", fake):
        resp = _post(client, {"application_id": str(job.id), "repo_name": "todo-app"})
    assert resp.status_code == 400
    assert "rejected the stored token" in resp.json()["detail"]


def test_missing_token(client, job):
    resp = _post(client, {"application_id": str(job.id), "repo_name": "todo-app"})
    assert resp.status_code == 400
    assert "No GitHub token" in resp.json()["detail"]


def test_invalid_repo_name(client, token, job):
    resp = _post(client, {"application_id": str(job.id), "repo_name": "bad name!"})
    assert resp.status_code == 400


def test_repo_name_required(client, token, job):
    resp = _post(client, {"application_id": str(job.id)})
    assert resp.status_code == 400


def test_other_users_application_is_404(token, job):
    stranger = Client()
    stranger.force_login(UserFactory.create())
    resp = _post(stranger, {"application_id": str(job.id), "repo_name": "steal"})
    assert resp.status_code == 404
    resp = _post(stranger, {"application_id": "not-a-uuid", "repo_name": "steal"})
    assert resp.status_code == 404


def test_unauthenticated():
    assert Client().get("/api/integrations/github/status/").status_code == 401


def test_scaffold_failure_falls_back_to_raw_files(owner, job):
    with patch("backend.runtime.services.scaffolding.apply_scaffold", side_effect=RuntimeError("boom")):
        app = github.collect_app_files(job, include_scaffold=True)
    assert set(app.files) == {"backend/app.py", "frontend/src/App.jsx"}


# ── gist export ───────────────────────────────────────────────────────


def test_export_gist(client, token, job):
    fake = FakeGitHub()
    with patch.object(github.requests, "request", fake):
        resp = _post(client, {"application_id": str(job.id), "target": "gist", "private": True})
    assert resp.status_code == 200, resp.content
    assert resp.json()["url"] == "https://gist.github.com/octo/abc"
    body = fake.find("POST", "/gists")[0][2]
    assert body["public"] is False
    assert set(body["files"]) == {"backend__app.py", "frontend__src__App.jsx", "README.md"}


def test_gist_too_large(client, token, job):
    job.result_data["files"] = {f"f{i}.py": "x = 1\n" for i in range(github.GIST_MAX_FILES + 1)}
    job.save()
    with patch.object(github.requests, "request", FakeGitHub()):
        resp = _post(client, {"application_id": str(job.id), "target": "gist"})
    assert resp.status_code == 413
    assert "repository instead" in resp.json()["detail"]


# ── findings summary (v2 measurement semantics) ───────────────────────


def test_findings_summary_counts_what_the_measurement_counts(owner, job, findings):
    ToolResult.objects.filter(run=findings).update(status="succeeded")
    result = findings.results.get(tool_slug="bandit")
    # Uncounted (style / out of scope) findings and AI replicates are not static counts.
    Finding.objects.create(result=result, severity="info", title="Line too long", rule_id="E501", counted=False)
    ai = ToolResult.objects.create(run=findings, tool_slug="ai_review", status="succeeded")
    for _ in range(3):
        Finding.objects.create(result=ai, severity="high", title="AI", is_ai=True, counted=True, issue_key="k")
    findings.measurement = {"schema_version": 1, "static": {"available": True}}
    findings.save(update_fields=["measurement"])

    summary = github._findings_summary(findings)
    assert summary["total"] == 2
    assert summary["by_tool"] == {"bandit": 2}
    assert summary["ai_consensus"] == 1
    assert github._findings_qs(findings).filter(is_ai=True).count() == 1
    readme = github.build_readme(job, summary, files_count=3)
    assert "**2 finding(s)**" in readme
    assert "consensus adds 1" in readme


def test_unmeasured_static_is_not_reported_clean(owner, job):
    run = AnalysisRun.objects.create(
        created_by=owner,
        generation_job=job,
        status="partial",
        plan=_plan("bandit"),
        measurement={"schema_version": 1, "static": {"available": False}},
    )
    summary = github._findings_summary(run)
    assert summary["total"] is None
    readme = github.build_readme(job, summary, files_count=3)
    assert "not a clean result" in readme
    assert "No counted findings" not in readme
