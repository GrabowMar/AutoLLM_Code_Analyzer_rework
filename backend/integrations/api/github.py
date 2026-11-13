"""/api/integrations/github/ — export generated applications to GitHub.

The token is stored through the generic credentials API
(``PUT /api/credentials/github/`` with ``{"api_key": "<PAT>"}``).
"""

from __future__ import annotations

from typing import Literal

from ninja import Field
from ninja import Router
from ninja import Schema
from ninja.errors import HttpError

from backend.integrations.services import github as github_service
from backend.integrations.services.github import GitHubError

router = Router(tags=["integrations"])


class GitHubStatusSchema(Schema):
    configured: bool
    connected: bool
    login: str | None = None
    name: str | None = None
    html_url: str | None = None
    avatar_url: str | None = None
    scopes: list[str] = []
    token_type: str | None = None
    message: str = ""


class GitHubExportIn(Schema):
    application_id: str = Field(..., description="Generation run UUID (the sample's id, /samples/{id})")
    target: Literal["repo", "gist"] = "repo"
    repo_name: str | None = Field(None, description="Required for target=repo")
    private: bool = True
    description: str | None = None
    upload_sarif: bool = False
    scope: Literal["app", "model"] = Field(
        default="app",
        description="Repo only: 'app' exports the buildable tree (starter files included), 'model' only the "
        "files the model wrote. Gists always export 'model'.",
    )


class SarifStatusSchema(Schema):
    uploaded: bool
    message: str
    id: str | None = None


class GitHubExportOut(Schema):
    target: str
    url: str
    repo_full_name: str | None = None
    commit_sha: str | None = None
    files_count: int
    skipped: list[str] = []
    sarif: SarifStatusSchema


@router.get("/status/", response=GitHubStatusSchema)
def github_status(request):
    """Whether the user's GitHub token is set and accepted, and as whom."""
    return github_service.status(request.auth)


@router.post("/export/", response=GitHubExportOut)
def github_export(request, payload: GitHubExportIn):
    """Export an application to a new repository (one commit) or a Gist."""
    try:
        run = github_service.resolve_application(request.auth, payload.application_id)
        if payload.target == "gist":
            result = github_service.export_to_gist(
                request.auth,
                run,
                public=not payload.private,
                description=payload.description or "",
            )
            if not payload.upload_sarif:
                result["sarif"] = {"uploaded": False, "message": "SARIF upload not requested."}
            return result
        if not payload.repo_name:
            raise HttpError(400, "repo_name is required when exporting to a repository.")
        return github_service.export_to_repo(
            request.auth,
            run,
            payload.repo_name,
            private=payload.private,
            description=payload.description or "",
            upload_sarif=payload.upload_sarif,
            scope=payload.scope,
        )
    except GitHubError as exc:
        raise HttpError(exc.status_code, exc.message) from exc
