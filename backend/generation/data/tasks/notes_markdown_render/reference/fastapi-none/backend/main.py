"""FastAPI adapter for the notes_markdown_render oracle reference (logic and Markdown rendering in core.py).

Request bodies are read as raw JSON (``Body``) so the shared validation answers 400.
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
    if payload is None:
        return Response(status_code=204)
    if isinstance(payload, core.Html):
        return HTMLResponse(str(payload), status_code=status)
    return JSONResponse(payload, status_code=status)


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


@app.get("/api/notes")
def list_notes(user: User) -> Response:
    return reply(core.list_notes(user))


@app.post("/api/notes")
def create_note(user: User, body: Json = None) -> Response:
    return reply(core.create_note(user, body))


@app.get("/api/notes/{note_id}")
def get_note(note_id: int, user: User) -> Response:
    return reply(core.get_note(user, note_id))


@app.patch("/api/notes/{note_id}")
def update_note(note_id: int, user: User, body: Json = None) -> Response:
    return reply(core.update_note(user, note_id, body))


@app.delete("/api/notes/{note_id}")
def delete_note(note_id: int, user: User) -> Response:
    return reply(core.delete_note(user, note_id))


@app.get("/api/notes/{note_id}/html")
def render_note(note_id: int, user: User) -> Response:
    return reply(core.render_note(user, note_id))


@app.get("/api/public/notes/{note_id}/html")
def render_public(note_id: int) -> Response:
    return reply(core.render_public(note_id))
