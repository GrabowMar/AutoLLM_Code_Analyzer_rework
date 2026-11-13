"""Django reference views for file_share_multipart (conformance kit, contract_bearer).

URLs end with a slash (Django convention; ``CommonMiddleware`` redirects ``/api/files`` with 301). Uploads over
10 MiB are refused with 413 from ``Content-Length`` before the body is read.
"""

import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from functools import wraps
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, JsonResponse

MAX_UPLOAD = 10 * 1024 * 1024
FILES = settings.DATA_DIR / "files"
FILES.mkdir(parents=True, exist_ok=True)
DB = settings.DATA_DIR / "app.db"
with sqlite3.connect(DB) as _conn:
    _conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, stored TEXT NOT NULL,
          filename TEXT, description TEXT, size_bytes INTEGER, content_type TEXT, uploaded_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS shares (token TEXT PRIMARY KEY, file_id INTEGER NOT NULL);
        """,
    )


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_pw(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()


def sign(uid: int) -> str:
    return f"{uid}.{hmac.new(settings.SECRET_KEY.encode(), str(uid).encode(), 'sha256').hexdigest()}"


def err(status: int, message: str) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def authed(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        header = request.headers.get("Authorization", "")
        uid, _, sig = header.removeprefix("Bearer ").partition(".")
        if not header.startswith("Bearer ") or not uid.isdigit() or not hmac.compare_digest(sign(int(uid)), f"{uid}.{sig}"):
            return err(401, "authentication required")
        with conn() as c:
            if c.execute("SELECT 1 FROM users WHERE id = ?", (int(uid),)).fetchone() is None:
                return err(401, "authentication required")
        return view(request, int(uid), *args, **kwargs)

    return wrapper


def file_json(row) -> dict:
    return {
        "id": row["id"], "description": row["description"], "filename": row["filename"],
        "size_bytes": row["size_bytes"], "content_type": row["content_type"], "uploaded_at": row["uploaded_at"],
        "download_url": f"/api/files/{row['id']}/download/",
    }


def send(row) -> HttpResponse:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", row["filename"] or "file")
    response = HttpResponse((FILES / row["stored"]).read_bytes(), content_type="application/octet-stream")
    response["Content-Disposition"] = f'attachment; filename="{safe}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


def register(request):
    if request.method != "POST":
        return err(405, "method not allowed")
    try:
        body = json.loads(request.body or b"null")
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return err(400, "JSON object expected")
    username, password = body.get("username"), body.get("password")
    if not isinstance(username, str) or not 1 <= len(username) <= 50:
        return err(400, "username must be 1-50 characters")
    if not isinstance(password, str) or not password:
        return err(400, "password is required")
    try:
        with conn() as c:
            cur = c.execute("INSERT INTO users (username, pw) VALUES (?, ?)", (username, hash_pw(password)))
    except sqlite3.IntegrityError:
        return err(409, "username taken")
    return JsonResponse({"id": cur.lastrowid, "username": username}, status=201)


def login(request):
    if request.method != "POST":
        return err(405, "method not allowed")
    try:
        body = json.loads(request.body or b"null")
    except ValueError:
        body = None
    if not isinstance(body, dict) or not isinstance(body.get("username"), str) or not isinstance(body.get("password"), str):
        return err(401, "invalid credentials")
    with conn() as c:
        row = c.execute("SELECT id, pw FROM users WHERE username = ?", (body["username"],)).fetchone()
    if row is None or not hmac.compare_digest(hash_pw(body["password"], row["pw"].split("$")[0]), row["pw"]):
        return err(401, "invalid credentials")
    return JsonResponse({"token": sign(row["id"])})


def files(request):
    if request.method == "POST" and int(request.META.get("CONTENT_LENGTH") or 0) > MAX_UPLOAD:
        return err(413, "upload too large")  # before authentication: never read an oversize body
    return _files(request)


@authed
def _files(request, uid: int):
    if request.method == "GET":
        try:
            page = max(1, int(request.GET.get("page", 1)))
            page_size = min(100, max(1, int(request.GET.get("page_size", 20))))
        except ValueError:
            return err(400, "page and page_size must be integers")
        with conn() as c:
            total = c.execute("SELECT COUNT(*) FROM files WHERE user_id = ?", (uid,)).fetchone()[0]
            rows = c.execute(
                "SELECT * FROM files WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (uid, page_size, (page - 1) * page_size),
            ).fetchall()
        return JsonResponse({"items": [file_json(r) for r in rows], "total": total, "page": page, "page_size": page_size})
    if request.method != "POST":
        return err(405, "method not allowed")
    upload = request.FILES.get("file")
    if upload is None:
        return err(400, "file is required")
    description = request.POST.get("description")
    if description is not None and len(description) > 300:
        return err(400, "description must be at most 300 characters")
    data = upload.read()
    stored = secrets.token_hex(16)
    (FILES / stored).write_bytes(data)
    with conn() as c:
        cur = c.execute(
            "INSERT INTO files (user_id, stored, filename, description, size_bytes, content_type, uploaded_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uid, stored, Path(upload.name or "file").name, description, len(data), upload.content_type, now()),
        )
        row = c.execute("SELECT * FROM files WHERE id = ?", (cur.lastrowid,)).fetchone()
    return JsonResponse(file_json(row), status=201)


def _owned(c, file_id: int, uid: int):
    return c.execute("SELECT * FROM files WHERE id = ? AND user_id = ?", (file_id, uid)).fetchone()


@authed
def file_detail(request, uid: int, file_id: int):
    with conn() as c:
        row = _owned(c, file_id, uid)
        if row is None:
            return err(404, "not found")
        if request.method == "GET":
            return JsonResponse(file_json(row))
        if request.method != "DELETE":
            return err(405, "method not allowed")
        c.execute("DELETE FROM shares WHERE file_id = ?", (file_id,))
        c.execute("DELETE FROM files WHERE id = ?", (file_id,))
    (FILES / row["stored"]).unlink(missing_ok=True)
    return HttpResponse(status=204)


@authed
def download(request, uid: int, file_id: int):
    with conn() as c:
        row = _owned(c, file_id, uid)
    return send(row) if row is not None else err(404, "not found")


@authed
def shares(request, uid: int, file_id: int):
    if request.method != "POST":
        return err(405, "method not allowed")
    with conn() as c:
        if _owned(c, file_id, uid) is None:
            return err(404, "not found")
        token = secrets.token_urlsafe(24)
        c.execute("INSERT INTO shares (token, file_id) VALUES (?, ?)", (token, file_id))
    return JsonResponse({"token": token, "url": f"/api/shared/{token}/"}, status=201)


@authed
def share_detail(request, uid: int, file_id: int, token: str):
    if request.method != "DELETE":
        return err(405, "method not allowed")
    with conn() as c:
        if _owned(c, file_id, uid) is None:
            return err(404, "not found")
        cur = c.execute("DELETE FROM shares WHERE token = ? AND file_id = ?", (token, file_id))
    return HttpResponse(status=204) if cur.rowcount else err(404, "not found")


def shared(request, token: str):
    with conn() as c:
        row = c.execute(
            "SELECT files.* FROM shares JOIN files ON files.id = shares.file_id WHERE shares.token = ?",
            (token,),
        ).fetchone()
    return send(row) if row is not None else err(404, "not found")
