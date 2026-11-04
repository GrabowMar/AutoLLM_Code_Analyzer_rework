"""Flask reference for crud_todo_list (conformance kit, contract_bearer)."""

import datetime as dt
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from pathlib import Path

from flask import Flask, g, jsonify, request, send_from_directory

app = Flask(__name__)
DATA = Path(os.environ.get("DATA_DIR", "data")).resolve()
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / "app.db"
SECRET = (os.environ.get("SECRET_KEY") or secrets.token_hex(32)).encode()
PRIORITIES = ("low", "medium", "high")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


with sqlite3.connect(DB) as conn:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL,
          description TEXT, priority TEXT NOT NULL DEFAULT 'medium', due_date TEXT, completed INTEGER NOT NULL DEFAULT 0,
          completed_at TEXT, created_at TEXT NOT NULL);
        """,
    )


def now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_pw(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()


def sign(uid: int) -> str:
    return f"{uid}.{hmac.new(SECRET, str(uid).encode(), 'sha256').hexdigest()}"


def current_user():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    uid, _, sig = header[7:].partition(".")
    if not uid.isdigit() or not hmac.compare_digest(sign(int(uid)), f"{uid}.{sig}"):
        return None
    return db().execute("SELECT id FROM users WHERE id = ?", (int(uid),)).fetchone()


def error(status: int, message: str):
    return jsonify({"error": message}), status


def valid_date(value) -> bool:
    if not isinstance(value, str) or not DATE.match(value):
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def validate(body, *, partial: bool) -> str | None:
    if not isinstance(body, dict):
        return "JSON object expected"
    if "title" in body or not partial:
        title = body.get("title")
        if not isinstance(title, str) or not 1 <= len(title) <= 255:
            return "title must be 1-255 characters"
    if body.get("description") is not None and (
        not isinstance(body["description"], str) or len(body["description"]) > 2000
    ):
        return "description must be at most 2000 characters"
    if body.get("priority") is not None and body["priority"] not in PRIORITIES:
        return "priority must be low, medium or high"
    if body.get("due_date") is not None and not valid_date(body["due_date"]):
        return "due_date must be YYYY-MM-DD"
    if body.get("completed") is not None and not isinstance(body["completed"], bool):
        return "completed must be a boolean"
    return None


def todo_json(row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "priority": row["priority"],
        "due_date": row["due_date"],
        "completed": bool(row["completed"]),
        "completed_at": row["completed_at"],
        "created_at": row["created_at"],
    }


@app.get("/api/health")
def health():
    return {"status": "ok"}


DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@app.get("/")
@app.get("/<path:path>")
def frontend(path: str = "index.html"):
    """The React build (``frontend/dist``); unknown non-API paths get ``index.html`` (client routing)."""
    if path.startswith("api/"):
        return jsonify({"error": "not found"}), 404
    if (DIST / path).is_file():
        return send_from_directory(DIST, path)
    return send_from_directory(DIST, "index.html")


@app.post("/api/auth/register")
def register():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error(400, "JSON object expected")
    username, password = body.get("username"), body.get("password")
    if not isinstance(username, str) or not 1 <= len(username) <= 50:
        return error(400, "username must be 1-50 characters")
    if not isinstance(password, str) or not password:
        return error(400, "password is required")
    try:
        cur = db().execute("INSERT INTO users (username, pw) VALUES (?, ?)", (username, hash_pw(password)))
        db().commit()
    except sqlite3.IntegrityError:
        return error(409, "username taken")
    return {"id": cur.lastrowid, "username": username}, 201


@app.post("/api/auth/login")
def login():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("username"), str) or not isinstance(body.get("password"), str):
        return error(401, "invalid credentials")
    row = db().execute("SELECT id, pw FROM users WHERE username = ?", (body["username"],)).fetchone()
    if row is None or not hmac.compare_digest(hash_pw(body["password"], row["pw"].split("$")[0]), row["pw"]):
        return error(401, "invalid credentials")
    return {"token": sign(row["id"])}


@app.get("/api/todos")
def list_todos():
    user = current_user()
    if user is None:
        return error(401, "authentication required")
    where, args = ["user_id = ?"], [user["id"]]
    completed = request.args.get("completed")
    if completed is not None:
        where.append("completed = ?")
        args.append(1 if completed.lower() in ("true", "1") else 0)
    priority = request.args.get("priority")
    if priority is not None:
        if priority not in PRIORITIES:
            return error(400, "invalid priority")
        where.append("priority = ?")
        args.append(priority)
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(200, max(1, int(request.args.get("page_size", 50))))
    except ValueError:
        return error(400, "page and page_size must be integers")
    clause = " AND ".join(where)
    total = db().execute(f"SELECT COUNT(*) FROM todos WHERE {clause}", args).fetchone()[0]  # noqa: S608
    rows = db().execute(
        f"SELECT * FROM todos WHERE {clause} ORDER BY completed ASC, id DESC LIMIT ? OFFSET ?",  # noqa: S608
        [*args, page_size, (page - 1) * page_size],
    ).fetchall()
    remaining = db().execute("SELECT COUNT(*) FROM todos WHERE user_id = ? AND completed = 0", (user["id"],)).fetchone()[0]
    return {"items": [todo_json(r) for r in rows], "total": total, "page": page, "page_size": page_size, "remaining": remaining}


@app.post("/api/todos")
def create_todo():
    user = current_user()
    if user is None:
        return error(401, "authentication required")
    body = request.get_json(silent=True)
    problem = validate(body, partial=False)
    if problem:
        return error(400, problem)
    completed = bool(body.get("completed") or False)
    cur = db().execute(
        "INSERT INTO todos (user_id, title, description, priority, due_date, completed, completed_at, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user["id"], body["title"], body.get("description"), body.get("priority") or "medium", body.get("due_date"),
         int(completed), now() if completed else None, now()),
    )
    db().commit()
    row = db().execute("SELECT * FROM todos WHERE id = ?", (cur.lastrowid,)).fetchone()
    return todo_json(row), 201


def owned(todo_id: int, user):
    return db().execute("SELECT * FROM todos WHERE id = ? AND user_id = ?", (todo_id, user["id"])).fetchone()


@app.get("/api/todos/<int:todo_id>")
def get_todo(todo_id: int):
    user = current_user()
    if user is None:
        return error(401, "authentication required")
    row = owned(todo_id, user)
    return todo_json(row) if row else error(404, "not found")


@app.patch("/api/todos/<int:todo_id>")
def update_todo(todo_id: int):
    user = current_user()
    if user is None:
        return error(401, "authentication required")
    row = owned(todo_id, user)
    if row is None:
        return error(404, "not found")
    body = request.get_json(silent=True)
    problem = validate(body, partial=True)
    if problem:
        return error(400, problem)
    fields = {k: body[k] for k in ("title", "description", "priority", "due_date") if k in body}
    if "priority" in fields and fields["priority"] is None:
        fields["priority"] = "medium"
    if body.get("completed") is not None:
        fields["completed"] = int(body["completed"])
        if body["completed"] and not row["completed"]:
            fields["completed_at"] = now()
        elif not body["completed"]:
            fields["completed_at"] = None
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        db().execute(f"UPDATE todos SET {sets} WHERE id = ?", [*fields.values(), todo_id])  # noqa: S608
        db().commit()
    return todo_json(owned(todo_id, user))


@app.delete("/api/todos/<int:todo_id>")
def delete_todo(todo_id: int):
    user = current_user()
    if user is None:
        return error(401, "authentication required")
    if owned(todo_id, user) is None:
        return error(404, "not found")
    db().execute("DELETE FROM todos WHERE id = ?", (todo_id,))
    db().commit()
    return "", 204
