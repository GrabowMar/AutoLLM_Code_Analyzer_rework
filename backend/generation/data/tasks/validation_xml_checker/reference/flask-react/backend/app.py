"""Independent API reference with authenticated, persistent per-user history."""

import base64
import binascii
import datetime as dt
import json
import xml.etree.ElementTree as ET
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
        CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
          payload TEXT NOT NULL, created_at TEXT NOT NULL);
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
    return jsonify({"detail": message}), status


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



def save_history(user, payload):
    created_at = now()
    cur = db().execute("INSERT INTO history (user_id, payload, created_at) VALUES (?, ?, ?)",
                       (user["id"], json.dumps(payload), created_at))
    db().commit()
    return {"id": cur.lastrowid, **payload, "created_at": created_at}


def history_list(filter_valid=False):
    user = current_user()
    if user is None:
        return error(401, "authentication required")
    try:
        page = max(1, int(request.args.get("page", 1)))
        size = min(100, max(1, int(request.args.get("page_size", 20))))
    except ValueError:
        return error(400, "page and page_size must be integers")
    rows = db().execute("SELECT * FROM history WHERE user_id = ? ORDER BY id DESC", (user["id"],)).fetchall()
    items = [{"id": r["id"], **json.loads(r["payload"]), "created_at": r["created_at"]} for r in rows]
    if filter_valid and "valid" in request.args:
        option = request.args["valid"].lower()
        if option not in {"true", "false"}:
            return error(400, "valid must be true or false")
        items = [r for r in items if r["valid"] == (option == "true")]
    start = (page - 1) * size
    return {"items": items[start:start+size], "total": len(items), "page": page, "page_size": size}


def history_delete(item_id):
    user = current_user()
    if user is None:
        return error(401, "authentication required")
    cur = db().execute("DELETE FROM history WHERE id = ? AND user_id = ?", (item_id, user["id"]))
    db().commit()
    return ("", 204) if cur.rowcount else error(404, "not found")


def check_xml(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 200000:
        raise ValueError("xml must be a nonempty string of at most 200000 characters")
    try:
        # ElementTree does not fetch external entities; Expat bounds entity amplification.
        root = ET.fromstring(value)
        return {"valid": True, "error": None, "line": None, "column": None, "root_element": root.tag}
    except ET.ParseError as exc:
        line, column = getattr(exc, "position", (None, None))
        return {"valid": False, "error": str(exc), "line": line,
                "column": column + 1 if column is not None else None, "root_element": None}


@app.post("/api/validate/xml")
def validate_xml():
    body = request.get_json(silent=True)
    try:
        return check_xml(body.get("xml") if isinstance(body, dict) else None)
    except ValueError as exc:
        return error(400, str(exc))


@app.post("/api/validations")
def create_validation():
    user = current_user()
    if user is None:
        return error(401, "authentication required")
    body = request.get_json(silent=True)
    try:
        result = check_xml(body.get("xml") if isinstance(body, dict) else None)
    except ValueError as exc:
        return error(400, str(exc))
    return save_history(user, {"xml_preview": body["xml"][:200], **result}), 201


@app.get("/api/validations")
def list_validations():
    return history_list(filter_valid=True)


@app.delete("/api/validations/<int:item_id>")
def delete_validation(item_id):
    return history_delete(item_id)
