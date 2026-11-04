"""Flask adapter for the notes_markdown_render oracle reference (logic and Markdown rendering in core.py)."""

import core
from flask import Flask, Response, jsonify, render_template, request

app = Flask(__name__)


def reply(result):
    status, payload = result
    if payload is None:
        return "", 204
    if isinstance(payload, core.Html):
        return Response(str(payload), status=status, mimetype="text/html")
    return jsonify(payload), status


def authed(fn):
    def wrapper(*args, **kwargs):
        user = core.user_from_header(request.headers.get("Authorization"))
        if user is None:
            return jsonify({"detail": "authentication required"}), 401
        return reply(fn(user, *args, **kwargs))

    wrapper.__name__ = fn.__name__
    return wrapper


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/auth/register")
def register():
    return reply(core.register(request.get_json(silent=True)))


@app.post("/api/auth/login")
def login():
    return reply(core.login(request.get_json(silent=True)))


@app.get("/api/notes")
@authed
def list_notes(user):
    return core.list_notes(user)


@app.post("/api/notes")
@authed
def create_note(user):
    return core.create_note(user, request.get_json(silent=True))


@app.get("/api/notes/<int:note_id>")
@authed
def get_note(user, note_id):
    return core.get_note(user, note_id)


@app.patch("/api/notes/<int:note_id>")
@authed
def update_note(user, note_id):
    return core.update_note(user, note_id, request.get_json(silent=True))


@app.delete("/api/notes/<int:note_id>")
@authed
def delete_note(user, note_id):
    return core.delete_note(user, note_id)


@app.get("/api/notes/<int:note_id>/html")
@authed
def render_note(user, note_id):
    return core.render_note(user, note_id)


@app.get("/api/public/notes/<int:note_id>/html")
def render_public(note_id):
    return reply(core.render_public(note_id))
