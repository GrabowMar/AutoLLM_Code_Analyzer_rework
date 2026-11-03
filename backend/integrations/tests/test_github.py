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
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.credentials.models import Provider
from backend.credentials.models import UserApiCredential
from backend.credentials.models import ValidationStatus
from backend.credentials.services.validator import validate_for_provider
from backend.generation import blobs
from backend.generation.models.choices import FileOrigin
from backend.generation.testing.content import task_content
from backend.generation.testing.factories import GenerationRunFactory
from backend.generation.testing.factories import RunFileFactory
from backend.generation.testing.factories import TaskSpecVersionFactory
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


@pytest.fixture
def owner():
    return UserFactory.create()


@pytest.fixture
def token(owner):
    cred = UserApiCredential(user=owner, provider=Provider.GITHUB)
    cred.set_secret("ghp_testtoken1234567890")
    cred.save()
    return cred


@pytest.fixture(autouse=True)
def _blob_root(settings, tmp_path):
    settings.GENERATION_BLOBS_ROOT = str(tmp_path / "blobs")


def add_file(run, path: str, content: bytes | str, origin=FileOrigin.AUTHORED, **extra):
    data = content.encode() if isinstance(content, str) else content
    return RunFileFactory.create(
        run=run,
        path=path,
        blob=blobs.put_bytes(data),
        size=len(data),
        origin=origin,
        **extra,
    )


@pytest.fixture
def job(owner):
    """A tested app run: two starter files, two files the model wrote, junk and an oversized file."""
    content = task_content(title="Todo App")
    run = GenerationRunFactory.create(
        tested=True,
        created_by=owner,
        model=LLMModelFactory.create(model_id="acme/coder-1", model_name="Coder One"),
        task_version=TaskSpecVersionFactory.create(content=content),
    )
    add_file(run, "Dockerfile", "FROM python:3.13-slim\n", origin=FileOrigin.STARTER, excluded="non_source")
    add_file(run, "run.sh", "#!/bin/sh\nexec python backend/app.py\n", origin=FileOrigin.STARTER, mode=0o755)
    add_file(run, "backend/app.py", "print('hi')\n")
    add_file(run, "frontend/src/App.jsx", "export default () => null;\n", origin=FileOrigin.MODIFIED)
    add_file(run, "frontend/dist/app.js", "built", origin=FileOrigin.ARTIFACT)
    add_file(run, "node_modules/left-pad/index.js", "junk", excluded="vendored")
    add_file(run, "backend/__pycache__/app.cpython-313.pyc", "junk", excluded="binary")
    add_file(run, "big.txt", "x" * (github.MAX_FILE_BYTES + 1))
    RunFileFactory.create(run=run, path="backend/old.py", blob="", size=0, origin=FileOrigin.DELETED)
    return run


@pytest.fixture
def findings(owner, job):
    run = AnalysisRun.objects.create(created_by=owner, generation_job=job, status="completed", tool_slugs=["bandit"])
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
            {"application_id": str(job.id), "repo_name": "todo-app", "scope": "model"},
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
    assert f"| Run id | `{job.id}` |" in readme
    # The level the tool printed (bandit's findings here carry none), never the normalized bucket.
    assert "| `bandit` | unrated | 2 |" in readme
    assert "Severity" not in readme
    assert set(data["skipped"]) == {"big.txt"}  # model scope: vendored, artifact and starter rows are not in it

    commit = fake.find("POST", "/git/commits")[0][2]
    assert commit["parents"] == []
    assert commit["tree"] == "tree1"
    ref = fake.find("PATCH", "/git/refs/heads/main")[0][2]
    assert ref == {"sha": "commit1", "force": True}
    assert not fake.find("POST", "/code-scanning/sarifs")


def test_export_repo_app_scope_is_the_buildable_tree(owner, token, job):
    add_file(job, "static/logo.png", b"\x89PNG\x00\xff", excluded="binary")
    with patch.object(github.requests, "request", fake := FakeGitHub()):
        result = github.export_to_repo(owner, job.id, "bin-app")
    tree = {e["path"]: e for e in fake.find("POST", "/git/trees")[0][2]["tree"]}
    assert set(tree) == {
        "Dockerfile",
        "run.sh",
        "backend/app.py",
        "frontend/src/App.jsx",
        "static/logo.png",
        "README.md",
    }
    assert "sha" in tree["static/logo.png"]
    assert "content" not in tree["static/logo.png"]
    assert tree["run.sh"]["mode"] == "100755"
    assert tree["backend/app.py"]["mode"] == "100644"
    blob = fake.find("POST", "/git/blobs")[0][2]
    assert base64.b64decode(blob["content"]) == b"\x89PNG\x00\xff"
    # Junk paths are dropped even in app scope; artifacts and deleted paths are never in the tree.
    assert set(result["skipped"]) == {
        "big.txt",
        "node_modules/left-pad/index.js",
        "backend/__pycache__/app.cpython-313.pyc",
    }


def test_missing_blob_is_skipped_not_exported_empty(owner, job):
    RunFileFactory.create(run=job, path="backend/lost.py", blob="f" * 64, size=10, origin=FileOrigin.AUTHORED)
    app = github.collect_app_files(job, scope="model")
    assert "backend/lost.py" in app.skipped
    assert "backend/lost.py" not in app.files


def test_run_without_files_has_nothing_to_export(owner, token):
    run = GenerationRunFactory.create(created_by=owner)
    with pytest.raises(github.GitHubError) as exc:
        github.export_to_repo(owner, run.id, "empty")
    assert exc.value.status_code == 400


def test_export_repo_with_sarif(client, token, job, findings):
    fake = FakeGitHub()
    with patch.object(github.requests, "request", fake):
        resp = _post(
            client,
            {"application_id": str(job.id), "repo_name": "todo-app", "upload_sarif": True, "scope": "model"},
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
    assert "informationUri" not in sarif["runs"][0]["tool"]["driver"]


def test_build_sarif_keeps_only_results_code_scanning_can_place(owner, job):
    from backend.analysis.tests.factories import AnalyzerToolFactory

    AnalyzerToolFactory(slug="zap", analysis_mode="dynamic")
    run = AnalysisRun.objects.create(created_by=owner, generation_job=job, status="completed")
    bandit = ToolResult.objects.create(
        run=run,
        tool_slug="bandit",
        status="completed",
        summary={"tool_version": "1.7.10"},
    )
    Finding.objects.create(
        result=bandit,
        tool_severity="MEDIUM",
        title="placed",
        rule_id="B104",
        file_path="/tmp/workspace/backend/app.py",  # noqa: S108
        line_number=3,
    )
    # A file the repository does not contain (e.g. a starter file left out of a model-scope export).
    Finding.objects.create(result=bandit, title="elsewhere", rule_id="B105", file_path="backend/missing.py")
    zap = ToolResult.objects.create(run=run, tool_slug="zap", status="completed")
    for path in ("/", "/assets/index.js"):
        Finding.objects.create(result=zap, tool_severity="Medium", title=f"zap {path}", rule_id="1", file_path=path)

    sarif, kept = github.build_sarif(job, {"backend/app.py", "frontend/src/App.jsx"})
    assert kept == 1
    (sarif_run,) = sarif["runs"]
    assert sarif_run["tool"]["driver"]["name"] == "bandit"
    assert sarif_run["tool"]["driver"]["version"] == "1.7.10"  # the recorded version, not a made-up one
    assert "automationDetails" not in sarif_run
    (result,) = sarif_run["results"]
    assert result["message"]["text"] == "placed"
    assert result["level"] == "warning"  # bandit's own MEDIUM
    assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "backend/app.py"


def test_readme_and_sarif_use_each_tools_latest_result_and_keep_advisories_apart(owner, job):
    full = AnalysisRun.objects.create(created_by=owner, generation_job=job, status="completed")
    bandit = ToolResult.objects.create(run=full, tool_slug="bandit", status="completed")
    Finding.objects.create(
        result=bandit,
        severity="medium",
        tool_severity="HIGH",
        title="hardcoded password",
        rule_id="B105",
        file_path="backend/app.py",
        line_number=1,
    )
    audit = ToolResult.objects.create(run=full, tool_slug="pip-audit", status="completed")
    Finding.objects.create(
        result=audit,
        severity="high",
        category="dependency",
        rule_id="PYSEC-1",
        title="flask advisory",
        file_path="requirements.txt",
        tool_specific_data={"package": "flask"},
    )
    # Manifest hygiene shares category "dependency" but is the app's own issue (P2).
    deptry = ToolResult.objects.create(run=full, tool_slug="deptry", status="completed")
    Finding.objects.create(
        result=deptry,
        severity="medium",
        category="dependency",
        rule_id="DEP001",
        title="requests imported but not declared",
        file_path="backend/app.py",
    )
    # A later re-run of one tool supersedes only that tool's result.
    rerun = AnalysisRun.objects.create(created_by=owner, generation_job=job, status="completed")
    ruff = ToolResult.objects.create(run=rerun, tool_slug="ruff", status="completed")
    Finding.objects.create(
        result=ruff,
        severity="low",
        title="unused import",
        rule_id="F401",
        file_path="backend/app.py",
        line_number=2,
    )

    readme = github.build_readme(job, github._findings_summary(github.latest_results(job)), files_count=3)
    assert "**3 finding(s) in the code**" in readme
    assert "| `bandit` | HIGH | 1 |" in readme
    assert "| `deptry` | unrated | 1 |" in readme
    assert "| `pip-audit` | unrated" not in readme
    assert "| `ruff` | unrated | 1 |" in readme
    assert "**1 dependency advisory(ies)**" in readme
    assert "| `pip-audit` | 1 |" in readme
    assert str(full.id) in readme
    assert str(rerun.id) in readme

    sarif, kept = github.build_sarif(job, {"backend/app.py"})
    assert kept == 3
    assert {r["tool"]["driver"]["name"] for r in sarif["runs"]} == {"bandit", "deptry", "ruff"}


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
            {"application_id": str(job.id), "repo_name": "todo-app", "upload_sarif": True, "scope": "model"},
        )
    assert resp.status_code == 200
    sarif = resp.json()["sarif"]
    assert sarif["uploaded"] is False
    assert "Advanced Security" in sarif["message"]


def test_sarif_without_analysis(client, token, job):
    with patch.object(github.requests, "request", FakeGitHub()):
        resp = _post(
            client,
            {"application_id": str(job.id), "repo_name": "todo-app", "upload_sarif": True, "scope": "model"},
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


# ── gist export ───────────────────────────────────────────────────────


def test_export_gist(client, token, job):
    fake = FakeGitHub()
    with patch.object(github.requests, "request", fake):
        resp = _post(client, {"application_id": str(job.id), "target": "gist", "private": True})
    assert resp.status_code == 200, resp.content
    assert resp.json()["url"] == "https://gist.github.com/octo/abc"
    body = fake.find("POST", "/gists")[0][2]
    assert body["public"] is False
    assert set(body["files"]) == {"backend__app.py", "frontend__src__App.jsx", "README.md"}  # model scope only


def test_gist_too_large(client, token, job):
    for i in range(github.GIST_MAX_FILES + 1):
        add_file(job, f"f{i}.py", "x = 1\n")
    with patch.object(github.requests, "request", FakeGitHub()):
        resp = _post(client, {"application_id": str(job.id), "target": "gist"})
    assert resp.status_code == 413
    assert "repository instead" in resp.json()["detail"]
