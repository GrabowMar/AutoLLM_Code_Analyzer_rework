"""FastAPI reference for file_share_multipart (conformance kit, contract_bearer).

Multipart parsing needs ``python-multipart``. Uploads over 10 MiB are refused by a middleware with 413 before the
body is read. Stored files are named by a random id under ``data/files``.
"""

import datetime as dt
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

TEMPLATES = Path(__file__).resolve().parent / "templates"
DATA = Path(os.environ.get("DATA_DIR", "data")).resolve()
FILES = DATA / "files"
FILES.mkdir(parents=True, exist_ok=True)
DB = DATA / "app.db"
SECRET = (os.environ.get("SECRET_KEY") or secrets.token_hex(32)).encode()
MAX_UPLOAD = 10 * 1024 * 1024

with sqlite3.connect(DB) as _conn:
    _conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, stored TEXT NOT NULL,
          filename TEXT, description TEXT, size_bytes INTEGER, content_type TEXT, uploaded_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS shares (token TEXT PRIMARY KEY, file_id INTEGER NOT NULL);
        """,
    )

app = FastAPI()


@app.middleware("http")
async def limit_upload_size(request: Request, call_next):
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > MAX_UPLOAD:
        return JSONResponse({"detail": "upload too large"}, status_code=413)
    return await call_next(request)


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


Db = Annotated[sqlite3.Connection, Depends(db)]


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1)


class LoginBody(BaseModel):
    username: str
    password: str


def now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_pw(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()


def sign(uid: int) -> str:
    return f"{uid}.{hmac.new(SECRET, str(uid).encode(), 'sha256').hexdigest()}"


def current_user(conn: Db, authorization: Annotated[str | None, Header()] = None) -> int:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "authentication required")
    uid, _, sig = authorization[7:].partition(".")
    if not uid.isdigit() or not hmac.compare_digest(sign(int(uid)), f"{uid}.{sig}"):
        raise HTTPException(401, "authentication required")
    if conn.execute("SELECT 1 FROM users WHERE id = ?", (int(uid),)).fetchone() is None:
        raise HTTPException(401, "authentication required")
    return int(uid)


User = Annotated[int, Depends(current_user)]


def file_json(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "description": row["description"],
        "filename": row["filename"],
        "size_bytes": row["size_bytes"],
        "content_type": row["content_type"],
        "uploaded_at": row["uploaded_at"],
        "download_url": f"/api/files/{row['id']}/download",
    }


def owned(conn: sqlite3.Connection, file_id: int, uid: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM files WHERE id = ? AND user_id = ?", (file_id, uid)).fetchone()
    if row is None:
        raise HTTPException(404, "not found")
    return row


def send(row: sqlite3.Row) -> Response:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", row["filename"] or "file")
    return Response(
        (FILES / row["stored"]).read_bytes(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe}"', "X-Content-Type-Options": "nosniff"},
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> str:
    return (TEMPLATES / "index.html").read_text(encoding="utf-8")


@app.post("/api/auth/register", status_code=201)
def register(body: Credentials, conn: Db) -> dict:
    try:
        cur = conn.execute("INSERT INTO users (username, pw) VALUES (?, ?)", (body.username, hash_pw(body.password)))
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "username taken") from exc
    return {"id": cur.lastrowid, "username": body.username}


@app.post("/api/auth/login")
def login(body: LoginBody, conn: Db) -> dict:
    row = conn.execute("SELECT id, pw FROM users WHERE username = ?", (body.username,)).fetchone()
    if row is None or not hmac.compare_digest(hash_pw(body.password, row["pw"].split("$")[0]), row["pw"]):
        raise HTTPException(401, "invalid credentials")
    return {"token": sign(row["id"])}


@app.post("/api/files", status_code=201)
def upload(
    conn: Db,
    uid: User,
    file: Annotated[UploadFile, File()],
    description: Annotated[str | None, Form(max_length=300)] = None,
) -> dict:
    data = file.file.read()
    stored = secrets.token_hex(16)
    (FILES / stored).write_bytes(data)
    cur = conn.execute(
        "INSERT INTO files (user_id, stored, filename, description, size_bytes, content_type, uploaded_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (uid, stored, Path(file.filename or "file").name, description, len(data), file.content_type, now()),
    )
    conn.commit()
    return file_json(owned(conn, cur.lastrowid, uid))


@app.get("/api/files")
def list_files(
    conn: Db,
    uid: User,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM files WHERE user_id = ?", (uid,)).fetchone()[0]
    rows = conn.execute(
        "SELECT * FROM files WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
        (uid, page_size, (page - 1) * page_size),
    ).fetchall()
    return {"items": [file_json(r) for r in rows], "total": total, "page": page, "page_size": page_size}


@app.get("/api/files/{file_id}")
def get_file(file_id: int, conn: Db, uid: User) -> dict:
    return file_json(owned(conn, file_id, uid))


@app.get("/api/files/{file_id}/download")
def download(file_id: int, conn: Db, uid: User) -> Response:
    return send(owned(conn, file_id, uid))


@app.delete("/api/files/{file_id}", status_code=204)
def delete_file(file_id: int, conn: Db, uid: User) -> Response:
    row = owned(conn, file_id, uid)
    conn.execute("DELETE FROM shares WHERE file_id = ?", (file_id,))
    conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
    conn.commit()
    (FILES / row["stored"]).unlink(missing_ok=True)
    return Response(status_code=204)


@app.post("/api/files/{file_id}/shares", status_code=201)
def create_share(file_id: int, conn: Db, uid: User) -> dict:
    owned(conn, file_id, uid)
    token = secrets.token_urlsafe(24)
    conn.execute("INSERT INTO shares (token, file_id) VALUES (?, ?)", (token, file_id))
    conn.commit()
    return {"token": token, "url": f"/api/shared/{token}"}


@app.delete("/api/files/{file_id}/shares/{token}", status_code=204)
def delete_share(file_id: int, token: str, conn: Db, uid: User) -> Response:
    owned(conn, file_id, uid)
    cur = conn.execute("DELETE FROM shares WHERE token = ? AND file_id = ?", (token, file_id))
    conn.commit()
    if not cur.rowcount:
        raise HTTPException(404, "not found")
    return Response(status_code=204)


@app.get("/api/shared/{token}")
def shared(token: str, conn: Db) -> Response:
    row = conn.execute(
        "SELECT files.* FROM shares JOIN files ON files.id = shares.file_id WHERE shares.token = ?",
        (token,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "not found")
    return send(row)
