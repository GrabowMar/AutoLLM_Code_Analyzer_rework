"""Registration, login and the bearer-token check."""

import hashlib
import secrets
import sqlite3
from functools import wraps

from backend import db
from flask import Blueprint
from flask import g
from flask import request

auth = Blueprint("auth", __name__)


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()


@auth.post("/register")
def register():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    if not username or len(username) > 50 or not password:
        return {"error": "username and password are required"}, 400
    salt = secrets.token_hex(8)
    try:
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, f"{salt}${hash_password(password, salt)}"),
            )
    except sqlite3.IntegrityError:
        return {"error": "username taken"}, 409
    return {"username": username}, 201


@auth.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    with db.connect() as conn:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (data.get("username"),)).fetchone()
        if user is None:
            return {"error": "invalid credentials"}, 401
        salt, digest = user["password_hash"].split("$", 1)
        if not secrets.compare_digest(digest, hash_password(str(data.get("password") or ""), salt)):
            return {"error": "invalid credentials"}, 401
        token = secrets.token_urlsafe(32)
        conn.execute("UPDATE users SET token = ? WHERE id = ?", (token, user["id"]))
    return {"token": token}


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        token = header.removeprefix("Bearer ").strip()
        with db.connect() as conn:
            user = conn.execute("SELECT id FROM users WHERE token = ?", (token,)).fetchone() if token else None
        if user is None:
            return {"error": "unauthorized"}, 401
        g.user_id = user["id"]
        return view(*args, **kwargs)

    return wrapper
