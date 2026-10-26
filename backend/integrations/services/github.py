"""GitHub integration: export a generated application to a repository or Gist.

The token is the user's ``github`` credential (``/api/credentials/github/``).
Repository exports land as a single commit built with the Git Data API; the
app's latest static-analysis findings can optionally be uploaded as SARIF to
GitHub code scanning so they show up in the repository's Security tab.

This module is ninja-free: failures raise :class:`GitHubError` (an
:class:`~backend.common.exceptions.APIError`) carrying the HTTP status the
API layer should answer with.
"""

from __future__ import annotations

import base64
import gzip
import json
import logging
import re
import tempfile
import uuid
from collections import Counter
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

import requests

from backend.common.exceptions import APIError
from backend.credentials.models import Provider
from backend.credentials.services.resolver import get_user_credential_for

if TYPE_CHECKING:
    from backend.generation.models import GenerationJob
    from backend.users.models import User

logger = logging.getLogger(__name__)

API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
TIMEOUT = 30

MAX_FILE_BYTES = 1024 * 1024  # files above 1 MB are skipped
MAX_TOTAL_BYTES = 50 * 1024 * 1024  # repository export cap
INLINE_TREE_BYTES = 100 * 1024  # text files up to this size go inline in the tree
GIST_MAX_FILES = 50
GIST_MAX_TOTAL_BYTES = 1024 * 1024
SARIF_MAX_RESULTS_PER_RUN = 20000

REPO_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")

SKIP_DIRS = frozenset(
    {
        "node_modules",
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".next",
        ".svelte-kit",
        ".cache",
    },
)
SKIP_FILES = frozenset({".DS_Store", "Thumbs.db", ".env"})
SKIP_SUFFIXES = (".pyc", ".pyo", ".log")

HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
HTTP_UNPROCESSABLE = 422
HTTP_RATE_LIMITED = 429


class GitHubError(APIError):
    """A GitHub operation failed; ``status_code`` is what our API should return."""

    status_code = 502


_NOT_FOUND = "Application not found."


# ── HTTP ──────────────────────────────────────────────────────────────


def _token_for(user: User) -> str:
    cred = get_user_credential_for(user, Provider.GITHUB)
    if cred is None:
        msg = "No GitHub token connected. Add one in Settings → API Access (provider 'github')."
        raise GitHubError(msg, status_code=400)
    try:
        token = cred.get_secret()
    except Exception as exc:
        msg = "The stored GitHub token could not be read. Save it again in Settings."
        raise GitHubError(msg, status_code=400) from exc
    if not token:
        msg = "The stored GitHub token is empty. Save it again in Settings."
        raise GitHubError(msg, status_code=400)
    return token


def _github_message(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return (response.text or "")[:300]
    if not isinstance(body, dict):
        return ""
    message = str(body.get("message") or "")
    errors = body.get("errors")
    if isinstance(errors, list):
        details = [str(e.get("message") or e.get("code") or "") if isinstance(e, dict) else str(e) for e in errors]
        details = [d for d in details if d]
        if details:
            message = f"{message} ({'; '.join(details)})" if message else "; ".join(details)
    return message[:500]


def _raise_for_status(response: requests.Response, action: str) -> None:
    """Map a GitHub error response to a :class:`GitHubError`.

    A rejected token is answered with 400, not 401: our 401 means "your LLM
    Lab session expired" and makes the web client log the user out.
    """
    status = response.status_code
    if status < 400:
        return
    detail = _github_message(response)
    suffix = f": {detail}" if detail else ""
    if status == HTTP_UNAUTHORIZED:
        msg = "GitHub rejected the stored token (401). It is invalid, revoked or expired; update it in Settings."
        raise GitHubError(msg, status_code=400)
    if status == HTTP_RATE_LIMITED or (
        status == HTTP_FORBIDDEN and response.headers.get("X-RateLimit-Remaining") == "0"
    ):
        msg = f"GitHub rate limit reached while trying to {action}. Try again later."
        raise GitHubError(msg, status_code=429)
    if status == HTTP_FORBIDDEN:
        msg = (
            f"GitHub refused to {action} (403){suffix}. The token may lack the required scope "
            "('repo' / 'gist' for classic tokens, or Administration + Contents write for fine-grained ones)."
        )
        raise GitHubError(msg, status_code=403)
    if status == HTTP_NOT_FOUND:
        msg = f"GitHub returned 404 while trying to {action}{suffix}. The token may not have access."
        raise GitHubError(msg, status_code=502)
    if status == HTTP_UNPROCESSABLE:
        msg = f"GitHub could not {action} (422){suffix}."
        raise GitHubError(msg, status_code=422)
    msg = f"GitHub error while trying to {action} ({status}){suffix}."
    raise GitHubError(msg, status_code=502)


class GitHubClient:
    """Minimal authenticated client for the GitHub REST API."""

    def __init__(self, token: str) -> None:
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "llm-lab",
        }

    def request(self, method: str, path: str, *, action: str, json_body: Any = None) -> requests.Response:
        url = path if path.startswith("http") else f"{API_BASE}{path}"
        try:
            return requests.request(method, url, headers=self.headers, json=json_body, timeout=TIMEOUT)
        except requests.exceptions.Timeout as exc:
            msg = f"GitHub did not respond within {TIMEOUT}s while trying to {action}."
            raise GitHubError(msg, status_code=504) from exc
        except requests.exceptions.RequestException as exc:
            msg = f"Could not reach GitHub to {action}: {exc}"
            raise GitHubError(msg, status_code=502) from exc

    def call(self, method: str, path: str, *, action: str, json_body: Any = None) -> dict[str, Any]:
        response = self.request(method, path, action=action, json_body=json_body)
        _raise_for_status(response, action)
        try:
            body = response.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}


# ── status ────────────────────────────────────────────────────────────


def status(user: User) -> dict[str, Any]:
    """Connection status of the user's GitHub token (never raises for a bad token)."""
    empty: dict[str, Any] = {
        "configured": False,
        "connected": False,
        "login": None,
        "name": None,
        "html_url": None,
        "avatar_url": None,
        "scopes": [],
        "token_type": None,
        "message": "No GitHub token connected.",
    }
    cred = get_user_credential_for(user, Provider.GITHUB)
    if cred is None:
        return empty
    result = {**empty, "configured": True}
    try:
        token = _token_for(user)
        response = GitHubClient(token).request("GET", "/user", action="read your GitHub account")
        _raise_for_status(response, "read your GitHub account")
    except GitHubError as exc:
        result["message"] = exc.message
        return result
    try:
        body = response.json() or {}
    except ValueError:
        body = {}
    scopes_header = response.headers.get("X-OAuth-Scopes")
    result.update(
        {
            "connected": True,
            "login": body.get("login"),
            "name": body.get("name"),
            "html_url": body.get("html_url"),
            "avatar_url": body.get("avatar_url"),
            # Only classic PATs report scopes; fine-grained tokens send no header.
            "scopes": [s.strip() for s in scopes_header.split(",") if s.strip()] if scopes_header else [],
            "token_type": "classic" if scopes_header is not None else "fine-grained",
            "message": f"Connected as {body.get('login')}.",
        },
    )
    return result


# ── application files ─────────────────────────────────────────────────


def resolve_application(user: User, application_ref: GenerationJob | str | uuid.UUID) -> GenerationJob:
    """The user's generation job (an "application") for an id, or the job itself if already loaded.

    Mirrors the generation API: applications are visible to their creator only.
    """
    from backend.generation.models import GenerationJob

    if isinstance(application_ref, GenerationJob):
        job = application_ref
        if job.created_by_id != user.pk:
            raise GitHubError(_NOT_FOUND, status_code=404)
        return job
    try:
        job_id = uuid.UUID(str(application_ref))
    except ValueError as exc:
        raise GitHubError(_NOT_FOUND, status_code=404) from exc
    job = GenerationJob.objects.select_related("model", "app_requirement").filter(id=job_id, created_by=user).first()
    if job is None:
        raise GitHubError(_NOT_FOUND, status_code=404)
    return job


@dataclass
class AppFiles:
    files: dict[str, bytes] = field(default_factory=dict)
    executable: set[str] = field(default_factory=set)
    skipped: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(len(b) for b in self.files.values())


def _is_junk(rel: str) -> bool:
    parts = rel.split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return True
    name = parts[-1]
    return name in SKIP_FILES or name.endswith(SKIP_SUFFIXES)


def _raw_file_map(job: GenerationJob) -> dict[str, str]:
    """The generated files alone (no stack skeleton)."""
    data = job.result_data if isinstance(job.result_data, dict) else {}
    files = data.get("files")
    if isinstance(files, dict):
        out = {k: v for k, v in files.items() if isinstance(k, str) and isinstance(v, str)}
        if out:
            return out
    # Legacy single-blob results.
    legacy = {"backend_code": "app.py", "frontend_code": "App.jsx", "content": "output.md"}
    return {name: data[key] for key, name in legacy.items() if isinstance(data.get(key), str) and data[key].strip()}


def _scaffolded_file_map(job: GenerationJob) -> tuple[dict[str, bytes], set[str]] | None:
    """The app as it is built (stack skeleton + generated code), or None if it cannot be materialized."""
    from backend.runtime.services.scaffolding import ScaffoldPhase
    from backend.runtime.services.scaffolding import apply_scaffold

    with tempfile.TemporaryDirectory(prefix="gh-export-") as tmp:
        root = Path(tmp) / "app"
        try:
            apply_scaffold(job, root, phase=ScaffoldPhase.BUILD)
        except Exception:  # noqa: BLE001 — any scaffold failure degrades to the raw files
            logger.warning("GitHub export: scaffolding failed for job %s; exporting raw files", job.id, exc_info=True)
            return None
        files: dict[str, bytes] = {}
        executable: set[str] = set()
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            rel = path.relative_to(root).as_posix()
            files[rel] = path.read_bytes()
            if path.stat().st_mode & 0o111:
                executable.add(rel)
        return files, executable


def collect_app_files(job: GenerationJob, *, include_scaffold: bool = True) -> AppFiles:
    """Gather an application's files, dropping junk and anything over the size caps."""
    raw: dict[str, bytes]
    executable: set[str] = set()
    scaffolded = _scaffolded_file_map(job) if include_scaffold else None
    if scaffolded is not None:
        raw, executable = scaffolded
    else:
        raw = {k: v.encode("utf-8") for k, v in _raw_file_map(job).items()}

    out = AppFiles(executable=executable)
    total = 0
    for rel in sorted(raw):
        clean = rel.strip().lstrip("/")
        if not clean or ".." in clean.split("/") or _is_junk(clean):
            out.skipped.append(rel)
            continue
        content = raw[rel]
        if len(content) > MAX_FILE_BYTES:
            out.skipped.append(rel)
            continue
        if total + len(content) > MAX_TOTAL_BYTES:
            out.skipped.append(rel)
            continue
        total += len(content)
        out.files[clean] = content
    out.executable &= set(out.files)
    return out


# ── findings ──────────────────────────────────────────────────────────


def latest_analysis_run(user: User, job: GenerationJob):
    from backend.analysis.models import AnalysisRun

    return (
        AnalysisRun.objects.filter(
            generation_job=job,
            created_by=user,
            status__in=[AnalysisRun.Status.COMPLETED, AnalysisRun.Status.PARTIAL],
        )
        .order_by("-created_at")
        .first()
    )


def _findings_qs(run):
    from backend.analysis.models import Finding

    return Finding.objects.filter(result__run=run)


def _findings_summary(run) -> dict[str, Any] | None:
    if run is None:
        return None
    rows = list(_findings_qs(run).values_list("severity", "result__tool_slug"))
    return {
        "run_id": str(run.id),
        "completed_at": run.completed_at,
        "total": len(rows),
        "by_severity": Counter(sev for sev, _ in rows),
        "by_tool": Counter(tool for _, tool in rows),
        "tools": list(run.tool_slugs or []),
    }


def _match_path(uri: str, exported: set[str]) -> str:
    """Map an analyzer path (maybe absolute / prefixed) onto an exported repo path."""
    norm = uri.replace("\\", "/").removeprefix("file://").removeprefix("./")
    if norm in exported:
        return norm
    candidates = [p for p in exported if norm.endswith("/" + p)]
    if candidates:
        return max(candidates, key=len)
    return norm.lstrip("/")


def build_sarif(run, exported_paths: set[str]) -> tuple[dict[str, Any], int]:
    """SARIF for ``run``'s findings, shaped for GitHub code scanning.

    Returns the document and the number of results kept. Results without a
    file location are dropped (code scanning cannot place them).
    """
    from backend.export.services.findings import findings_sarif

    sarif = findings_sarif(_findings_qs(run))
    kept = 0
    for sarif_run in sarif.get("runs", []):
        driver = sarif_run.get("tool", {}).get("driver", {})
        if not driver.get("informationUri"):
            driver.pop("informationUri", None)
        results = []
        for result in sarif_run.get("results", []):
            locations = result.get("locations") or []
            if not locations:
                continue
            for loc in locations:
                art = loc.get("physicalLocation", {}).get("artifactLocation", {})
                if art.get("uri"):
                    art["uri"] = _match_path(art["uri"], exported_paths)
            results.append(result)
        sarif_run["results"] = results[:SARIF_MAX_RESULTS_PER_RUN]
        kept += len(sarif_run["results"])
    sarif["runs"] = [r for r in sarif.get("runs", []) if r["results"]]
    return sarif, kept


def _upload_sarif(
    client: GitHubClient,
    full_name: str,
    ref: str,
    commit_sha: str,
    user: User,
    job: GenerationJob,
    exported: set[str],
) -> dict[str, Any]:
    """Upload the app's findings; never raises — failures come back as a message."""
    run = latest_analysis_run(user, job)
    if run is None:
        return {"uploaded": False, "message": "This application has no completed analysis run; nothing to upload."}
    sarif, kept = build_sarif(run, exported)
    if kept == 0:
        return {"uploaded": False, "message": "The latest analysis run has no file-located findings to upload."}
    payload = base64.b64encode(gzip.compress(json.dumps(sarif).encode("utf-8"))).decode("ascii")
    try:
        response = client.request(
            "POST",
            f"/repos/{full_name}/code-scanning/sarifs",
            action="upload SARIF",
            json_body={"commit_sha": commit_sha, "ref": ref, "sarif": payload, "tool_name": "LLM Lab"},
        )
    except GitHubError as exc:
        return {"uploaded": False, "message": exc.message}
    if response.status_code in (HTTP_FORBIDDEN, HTTP_NOT_FOUND):
        detail = _github_message(response)
        return {
            "uploaded": False,
            "message": (
                f"Code scanning is not available for this repository ({response.status_code}"
                f"{': ' + detail if detail else ''}). It needs a public repository or GitHub Advanced "
                "Security, and a token with the 'security_events' scope. The repository was still created."
            ),
        }
    if response.status_code >= 400:
        detail = _github_message(response)
        return {
            "uploaded": False,
            "message": f"GitHub rejected the SARIF upload ({response.status_code}){': ' + detail if detail else ''}.",
        }
    try:
        body = response.json() or {}
    except ValueError:
        body = {}
    return {
        "uploaded": True,
        "message": f"Uploaded {kept} finding(s) to code scanning; they appear in the Security tab once processed.",
        "id": body.get("id"),
    }


# ── README ────────────────────────────────────────────────────────────

_SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")


def build_readme(job: GenerationJob, summary: dict[str, Any] | None, *, files_count: int) -> str:
    resolved = job.resolved_bundle if isinstance(job.resolved_bundle, dict) else {}
    template = job.app_requirement
    title = template.name if template else f"Generated application {str(job.id)[:8]}"
    model = job.model
    lines = [
        f"# {title}",
        "",
        "This application was generated by a large language model with **LLM Lab** and exported automatically.",
        "It has not been reviewed by a human; treat it as experimental code.",
        "",
        "## Provenance",
        "",
        "| | |",
        "|---|---|",
        f"| Model | {f'{model.model_name} (`{model.model_id}`)' if model else 'unknown'} |",
        f"| Generation mode | {job.get_mode_display()} |",
    ]
    if template:
        lines.append(f"| App template | {template.name} (`{template.slug}`) |")
    if job.bundle_key:
        lines.append(f"| Generation profile | `{job.bundle_key}` |")
    stack = (resolved.get("stack") or {}).get("slug") or resolved.get("scaffolding_slug") or job.stack_slug
    if stack:
        lines.append(f"| Stack | `{stack}` |")
    lines += [
        f"| Job id | `{job.id}` |",
        f"| Generated at | {job.completed_at or job.created_at:%Y-%m-%d %H:%M UTC} |",
        f"| Files exported | {files_count} |",
        "",
    ]
    if template and template.description:
        lines += ["## Task", "", template.description.strip(), ""]

    lines += ["## Static analysis", ""]
    if summary is None:
        lines += ["No static analysis has been run on this application yet.", ""]
    else:
        tools = ", ".join(f"`{t}`" for t in summary["tools"]) or "n/a"
        lines += [f"Latest analysis run `{summary['run_id']}` (tools: {tools}).", ""]
        if summary["total"] == 0:
            lines += ["No findings were reported.", ""]
        else:
            lines += [f"**{summary['total']} finding(s)**", "", "| Severity | Count |", "|---|---|"]
            by_sev = summary["by_severity"]
            lines += [f"| {sev} | {by_sev[sev]} |" for sev in _SEVERITY_ORDER if by_sev.get(sev)]
            lines += ["", "| Tool | Findings |", "|---|---|"]
            lines += [f"| `{tool}` | {count} |" for tool, count in summary["by_tool"].most_common()]
            lines.append("")
    return "\n".join(lines)


# ── exports ───────────────────────────────────────────────────────────


def _readme_path(files: dict[str, bytes]) -> str:
    return "LLM_LAB_EXPORT.md" if "README.md" in files else "README.md"


def validate_repo_name(name: str) -> str:
    name = (name or "").strip()
    if not REPO_NAME_RE.match(name) or name in {".", ".."}:
        msg = "Invalid repository name: use 1-100 letters, digits, '.', '-' or '_'."
        raise GitHubError(msg, status_code=400)
    return name


def _tree_entry(client: GitHubClient, full_name: str, path: str, content: bytes, mode: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"path": path, "mode": mode, "type": "blob"}
    if len(content) <= INLINE_TREE_BYTES:
        try:
            entry["content"] = content.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            return entry
    blob = client.call(
        "POST",
        f"/repos/{full_name}/git/blobs",
        action=f"upload {path}",
        json_body={"content": base64.b64encode(content).decode("ascii"), "encoding": "base64"},
    )
    entry["sha"] = blob["sha"]
    return entry


def export_to_repo(
    user: User,
    application_ref: GenerationJob | str | uuid.UUID,
    repo_name: str,
    *,
    private: bool = True,
    description: str = "",
    upload_sarif: bool = False,
    include_scaffold: bool = True,
) -> dict[str, Any]:
    """Create ``repo_name`` on the user's account and push the app as one commit.

    The Git Data API refuses to write to an empty repository, so the README is
    first created through the Contents API to initialize it; the real commit
    is then built as an orphan (no parent) and force-set as the branch head,
    leaving a single-commit history.
    """
    job = resolve_application(user, application_ref)
    repo_name = validate_repo_name(repo_name)
    token = _token_for(user)
    app = collect_app_files(job, include_scaffold=include_scaffold)
    if not app.files:
        msg = "This application has no exportable files."
        raise GitHubError(msg, status_code=400)

    run = latest_analysis_run(user, job)
    readme_name = _readme_path(app.files)
    readme = build_readme(job, _findings_summary(run), files_count=len(app.files) + 1).encode("utf-8")
    files = {**app.files, readme_name: readme}

    client = GitHubClient(token)
    response = client.request(
        "POST",
        "/user/repos",
        action="create the repository",
        json_body={
            "name": repo_name,
            "private": private,
            "description": (description or f"LLM-generated application exported from LLM Lab ({job.id})")[:350],
            "auto_init": False,
        },
    )
    if response.status_code == HTTP_UNPROCESSABLE and "already exists" in _github_message(response).lower():
        msg = f"A repository named '{repo_name}' already exists on your GitHub account. Choose another name."
        raise GitHubError(msg, status_code=422)
    _raise_for_status(response, "create the repository")
    repo = response.json()
    full_name = repo["full_name"]
    branch = repo.get("default_branch") or "main"
    ref = f"refs/heads/{branch}"

    # Initialize the empty repository (Git Data API answers 409 until then).
    client.call(
        "PUT",
        f"/repos/{full_name}/contents/{readme_name}",
        action="initialize the repository",
        json_body={
            "message": "Initialize repository",
            "content": base64.b64encode(readme).decode("ascii"),
            "branch": branch,
        },
    )

    tree_entries = [
        _tree_entry(client, full_name, path, content, "100755" if path in app.executable else "100644")
        for path, content in sorted(files.items())
    ]
    tree = client.call(
        "POST",
        f"/repos/{full_name}/git/trees",
        action="create the tree",
        json_body={"tree": tree_entries},
    )
    model_label = job.model.model_id if job.model else "an LLM"
    commit = client.call(
        "POST",
        f"/repos/{full_name}/git/commits",
        action="create the commit",
        json_body={
            "message": f"Generated application exported from LLM Lab\n\nModel: {model_label}\nJob: {job.id}",
            "tree": tree["sha"],
            "parents": [],
        },
    )
    commit_sha = commit["sha"]
    client.call(
        "PATCH",
        f"/repos/{full_name}/git/{ref}",
        action="update the branch",
        json_body={"sha": commit_sha, "force": True},
    )

    result: dict[str, Any] = {
        "target": "repo",
        "url": repo["html_url"],
        "repo_full_name": full_name,
        "commit_sha": commit_sha,
        "files_count": len(files),
        "skipped": app.skipped,
        "sarif": {"uploaded": False, "message": "SARIF upload not requested."},
    }
    if upload_sarif:
        result["sarif"] = _upload_sarif(client, full_name, ref, commit_sha, user, job, set(files))
    return result


def export_to_gist(
    user: User,
    application_ref: GenerationJob | str | uuid.UUID,
    *,
    public: bool = False,
    description: str = "",
) -> dict[str, Any]:
    """Export the generated files (no stack skeleton) as a Gist.

    Gists have no directories, so ``a/b.py`` becomes ``a__b.py``. Only small,
    text-only apps qualify.
    """
    job = resolve_application(user, application_ref)
    token = _token_for(user)
    app = collect_app_files(job, include_scaffold=False)
    if not app.files:
        msg = "This application has no exportable files."
        raise GitHubError(msg, status_code=400)
    if len(app.files) > GIST_MAX_FILES or app.total_bytes > GIST_MAX_TOTAL_BYTES:
        msg = (
            f"Too large for a Gist ({len(app.files)} files, {app.total_bytes // 1024} KB; limit "
            f"{GIST_MAX_FILES} files / {GIST_MAX_TOTAL_BYTES // 1024} KB). Export to a repository instead."
        )
        raise GitHubError(msg, status_code=413)

    gist_files: dict[str, dict[str, str]] = {}
    for path, content in app.files.items():
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            app.skipped.append(path)
            continue
        if text.strip():
            gist_files[path.replace("/", "__")] = {"content": text}
    readme_name = "LLM_LAB_EXPORT.md" if "README.md" in gist_files else "README.md"
    summary = _findings_summary(latest_analysis_run(user, job))
    gist_files[readme_name] = {"content": build_readme(job, summary, files_count=len(gist_files) + 1)}

    gist = GitHubClient(token).call(
        "POST",
        "/gists",
        action="create the Gist",
        json_body={
            "description": (description or f"LLM-generated application exported from LLM Lab ({job.id})")[:1000],
            "public": public,
            "files": gist_files,
        },
    )
    return {
        "target": "gist",
        "url": gist.get("html_url"),
        "repo_full_name": None,
        "commit_sha": None,
        "files_count": len(gist_files),
        "skipped": app.skipped,
        "sarif": {"uploaded": False, "message": "SARIF upload is only available for repository exports."},
    }
