"""Flask reference for file_share_multipart (conformance kit, contract_bearer).

Uploads over 10 MiB are refused by Flask itself (``MAX_CONTENT_LENGTH`` -> 413). Stored files are named by a
random id under ``data/files``; the client's filename is metadata only.
"""

import datetime as dt
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from pathlib import Path

from flask import Flask, Response, g, jsonify, render_template, request

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
DATA = Path(os.environ.get("DATA_DIR", "data")).resolve()
FILES = DATA / "files"
FILES.mkdir(parents=True, exist_ok=True)
DB = DATA / "app.db"
SECRET = (os.environ.get("SECRET_KEY") or secrets.token_hex(32)).encode()

with sqlite3.connect(DB) as conn:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, stored TEXT NOT NULL,
          filename TEXT, description TEXT, size_bytes INTEGER, content_type TEXT, uploaded_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS shares (token TEXT PRIMARY KEY, file_id INTEGER NOT NULL);
        """,
    )


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    c = g.pop("db", None)
    if c is not None:
        c.close()


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


def file_json(row) -> dict:
    return {
        "id": row["id"],
        "description": row["description"],
        "filename": row["filename"],
        "size_bytes": row["size_bytes"],
        "content_type": row["content_type"],
        "uploaded_at": row["uploaded_at"],
        "download_url": f"/api/files/{row['id']}/download",
    }


def send(row) -> Response:
    data = (FILES / row["stored"]).read_bytes()
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", row["filename"] or "file")
    return Response(
        data,
        mimetype="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe}"', "X-Content-Type-Options": "nosniff"},
    )


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    return render_template("index.html")


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


@app.post("/api/files")
def upload():
    user = current_user()
    if user is None:
        return error(401, "authentication required")
    upload = request.files.get("file")
    if upload is None:
        return error(400, "file is required")
    description = request.form.get("description")
    if description is not None and len(description) > 300:
        return error(400, "description must be at most 300 characters")
    data = upload.read()
    stored = secrets.token_hex(16)
    (FILES / stored).write_bytes(data)
    cur = db().execute(
        "INSERT INTO files (user_id, stored, filename, description, size_bytes, content_type, uploaded_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user["id"], stored, Path(upload.filename or "file").name, description, len(data), upload.mimetype, now()),
    )
    db().commit()
    return file_json(db().execute("SELECT * FROM files WHERE id = ?", (cur.lastrowid,)).fetchone()), 201


@app.get("/api/files")
def list_files():
    user = current_user()
    if user is None:
        return error(401, "authentication required")
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 20))))
    except ValueError:
        return error(400, "page and page_size must be integers")
    total = db().execute("SELECT COUNT(*) FROM files WHERE user_id = ?", (user["id"],)).fetchone()[0]
    rows = db().execute(
        "SELECT * FROM files WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
        (user["id"], page_size, (page - 1) * page_size),
    ).fetchall()
    return {"items": [file_json(r) for r in rows], "total": total, "page": page, "page_size": page_size}


def owned(file_id: int, user):
    return db().execute("SELECT * FROM files WHERE id = ? AND user_id = ?", (file_id, user["id"])).fetchone()


@app.get("/api/files/<int:file_id>")
def get_file(file_id: int):
    user = current_user()
    if user is None:
        return error(401, "authentication required")
    row = owned(file_id, user)
    return file_json(row) if row else error(404, "not found")


@app.get("/api/files/<int:file_id>/download")
def download(file_id: int):
    user = current_user()
    if user is None:
        return error(401, "authentication required")
    row = owned(file_id, user)
    return send(row) if row else error(404, "not found")


@app.delete("/api/files/<int:file_id>")
def delete_file(file_id: int):
    user = current_user()
    if user is None:
        return error(401, "authentication required")
    row = owned(file_id, user)
    if row is None:
        return error(404, "not found")
    db().execute("DELETE FROM shares WHERE file_id = ?", (file_id,))
    db().execute("DELETE FROM files WHERE id = ?", (file_id,))
    db().commit()
    (FILES / row["stored"]).unlink(missing_ok=True)
    return "", 204


@app.post("/api/files/<int:file_id>/shares")
def create_share(file_id: int):
    user = current_user()
    if user is None:
        return error(401, "authentication required")
    if owned(file_id, user) is None:
        return error(404, "not found")
    token = secrets.token_urlsafe(24)
    db().execute("INSERT INTO shares (token, file_id) VALUES (?, ?)", (token, file_id))
    db().commit()
    return {"token": token, "url": f"/api/shared/{token}"}, 201


@app.delete("/api/files/<int:file_id>/shares/<token>")
def delete_share(file_id: int, token: str):
    user = current_user()
    if user is None:
        return error(401, "authentication required")
    if owned(file_id, user) is None:
        return error(404, "not found")
    cur = db().execute("DELETE FROM shares WHERE token = ? AND file_id = ?", (token, file_id))
    db().commit()
    return ("", 204) if cur.rowcount else error(404, "not found")


@app.get("/api/shared/<token>")
def shared(token: str):
    row = db().execute(
        "SELECT files.* FROM shares JOIN files ON files.id = shares.file_id WHERE shares.token = ?",
        (token,),
    ).fetchone()
    return send(row) if row else error(404, "not found")
