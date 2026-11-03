"""FastAPI adapter for the team_roles_multitenant kit reference (logic in core.py).

Request bodies are read as raw JSON (``Body``) so the shared validation answers 400; FastAPI's own 422 is
exercised by the crud_todo_list and file_share_multipart references.
"""

from pathlib import Path
from typing import Annotated, Any

import core
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse

TEMPLATES = Path(__file__).resolve().parent / "templates"
app = FastAPI()


def current_user(authorization: Annotated[str | None, Header()] = None) -> int:
    user = core.user_from_header(authorization)
    if user is None:
        raise HTTPException(401, "authentication required")
    return user


User = Annotated[int, Depends(current_user)]
Json = Annotated[Any, Body()]


def reply(result) -> Response:
    status, payload = result
    return Response(status_code=204) if payload is None else JSONResponse(payload, status_code=status)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> str:
    return (TEMPLATES / "index.html").read_text(encoding="utf-8")


@app.post("/api/auth/register")
def register(body: Json = None) -> Response:
    return reply(core.register(body))


@app.post("/api/auth/login")
def login(body: Json = None) -> Response:
    return reply(core.login(body))


@app.post("/api/orgs")
def create_org(user: User, body: Json = None) -> Response:
    return reply(core.create_org(user, body))


@app.get("/api/orgs")
def list_orgs(user: User) -> Response:
    return reply(core.list_orgs(user))


@app.get("/api/orgs/{org_id}/members")
def list_members(org_id: int, user: User) -> Response:
    return reply(core.list_members(user, org_id))


@app.post("/api/orgs/{org_id}/members")
def add_member(org_id: int, user: User, body: Json = None) -> Response:
    return reply(core.add_member(user, org_id, body))


@app.patch("/api/orgs/{org_id}/members/{username}")
def update_member(org_id: int, username: str, user: User, body: Json = None) -> Response:
    return reply(core.update_member(user, org_id, username, body))


@app.delete("/api/orgs/{org_id}/members/{username}")
def remove_member(org_id: int, username: str, user: User) -> Response:
    return reply(core.remove_member(user, org_id, username))


@app.get("/api/orgs/{org_id}/projects")
def list_projects(org_id: int, user: User, status: str | None = None) -> Response:
    return reply(core.list_projects(user, org_id, status))


@app.post("/api/orgs/{org_id}/projects")
def create_project(org_id: int, user: User, body: Json = None) -> Response:
    return reply(core.create_project(user, org_id, body))


@app.get("/api/projects/{project_id}")
def get_project(project_id: int, user: User) -> Response:
    return reply(core.get_project(user, project_id))


@app.patch("/api/projects/{project_id}")
def update_project(project_id: int, user: User, body: Json = None) -> Response:
    return reply(core.update_project(user, project_id, body))


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: int, user: User) -> Response:
    return reply(core.delete_project(user, project_id))
