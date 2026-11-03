"""Flask adapter for the team_roles_multitenant kit reference (logic in core.py)."""

import core
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)


def reply(result):
    status, payload = result
    return ("", 204) if payload is None else (jsonify(payload), status)


def uid():
    return core.user_from_header(request.headers.get("Authorization"))


def authed(fn):
    def wrapper(*args, **kwargs):
        user = uid()
        if user is None:
            return jsonify({"error": "authentication required"}), 401
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


@app.post("/api/orgs")
@authed
def create_org(user):
    return core.create_org(user, request.get_json(silent=True))


@app.get("/api/orgs")
@authed
def list_orgs(user):
    return core.list_orgs(user)


@app.get("/api/orgs/<int:org_id>/members")
@authed
def list_members(user, org_id):
    return core.list_members(user, org_id)


@app.post("/api/orgs/<int:org_id>/members")
@authed
def add_member(user, org_id):
    return core.add_member(user, org_id, request.get_json(silent=True))


@app.patch("/api/orgs/<int:org_id>/members/<username>")
@authed
def update_member(user, org_id, username):
    return core.update_member(user, org_id, username, request.get_json(silent=True))


@app.delete("/api/orgs/<int:org_id>/members/<username>")
@authed
def remove_member(user, org_id, username):
    return core.remove_member(user, org_id, username)


@app.get("/api/orgs/<int:org_id>/projects")
@authed
def list_projects(user, org_id):
    return core.list_projects(user, org_id, request.args.get("status"))


@app.post("/api/orgs/<int:org_id>/projects")
@authed
def create_project(user, org_id):
    return core.create_project(user, org_id, request.get_json(silent=True))


@app.get("/api/projects/<int:project_id>")
@authed
def get_project(user, project_id):
    return core.get_project(user, project_id)


@app.patch("/api/projects/<int:project_id>")
@authed
def update_project(user, project_id):
    return core.update_project(user, project_id, request.get_json(silent=True))


@app.delete("/api/projects/<int:project_id>")
@authed
def delete_project(user, project_id):
    return core.delete_project(user, project_id)
