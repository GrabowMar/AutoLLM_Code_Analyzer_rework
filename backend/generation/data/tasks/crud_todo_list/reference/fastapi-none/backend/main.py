"""FastAPI reference for crud_todo_list (conformance kit, contract_bearer). Validation errors are FastAPI's 422."""

import datetime as dt
import hashlib
import hmac
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

TEMPLATES = Path(__file__).resolve().parent / "templates"
DATA = Path(os.environ.get("DATA_DIR", "data")).resolve()
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / "app.db"
SECRET = (os.environ.get("SECRET_KEY") or secrets.token_hex(32)).encode()

with sqlite3.connect(DB) as _conn:
    _conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL,
          description TEXT, priority TEXT NOT NULL DEFAULT 'medium', due_date TEXT, completed INTEGER NOT NULL DEFAULT 0,
          completed_at TEXT, created_at TEXT NOT NULL);
        """,
    )

app = FastAPI()
Priority = Literal["low", "medium", "high"]


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


class TodoCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    priority: Priority | None = None
    due_date: dt.date | None = None
    completed: StrictBool | None = None


class TodoUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    priority: Priority | None = None
    due_date: dt.date | None = None
    completed: StrictBool | None = None


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


def todo_json(row: sqlite3.Row) -> dict:
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


def owned(conn: sqlite3.Connection, todo_id: int, uid: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM todos WHERE id = ? AND user_id = ?", (todo_id, uid)).fetchone()
    if row is None:
        raise HTTPException(404, "not found")
    return row


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


@app.get("/api/todos")
def list_todos(
    conn: Db,
    uid: User,
    completed: bool | None = None,
    priority: Priority | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    where, args = ["user_id = ?"], [uid]
    if completed is not None:
        where.append("completed = ?")
        args.append(int(completed))
    if priority is not None:
        where.append("priority = ?")
        args.append(priority)
    clause = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM todos WHERE {clause}", args).fetchone()[0]  # noqa: S608
    rows = conn.execute(
        f"SELECT * FROM todos WHERE {clause} ORDER BY completed ASC, id DESC LIMIT ? OFFSET ?",  # noqa: S608
        [*args, page_size, (page - 1) * page_size],
    ).fetchall()
    remaining = conn.execute("SELECT COUNT(*) FROM todos WHERE user_id = ? AND completed = 0", (uid,)).fetchone()[0]
    return {"items": [todo_json(r) for r in rows], "total": total, "page": page, "page_size": page_size, "remaining": remaining}


@app.post("/api/todos", status_code=201)
def create_todo(body: TodoCreate, conn: Db, uid: User) -> dict:
    completed = bool(body.completed)
    cur = conn.execute(
        "INSERT INTO todos (user_id, title, description, priority, due_date, completed, completed_at, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (uid, body.title, body.description, body.priority or "medium",
         body.due_date.isoformat() if body.due_date else None, int(completed), now() if completed else None, now()),
    )
    conn.commit()
    return todo_json(owned(conn, cur.lastrowid, uid))


@app.get("/api/todos/{todo_id}")
def get_todo(todo_id: int, conn: Db, uid: User) -> dict:
    return todo_json(owned(conn, todo_id, uid))


@app.patch("/api/todos/{todo_id}")
def update_todo(todo_id: int, body: TodoUpdate, conn: Db, uid: User) -> dict:
    row = owned(conn, todo_id, uid)
    sent = body.model_fields_set
    fields: dict = {}
    if "title" in sent:
        if body.title is None:
            raise HTTPException(422, "title cannot be null")
        fields["title"] = body.title
    if "description" in sent:
        fields["description"] = body.description
    if "priority" in sent:
        fields["priority"] = body.priority or "medium"
    if "due_date" in sent:
        fields["due_date"] = body.due_date.isoformat() if body.due_date else None
    if body.completed is not None:
        fields["completed"] = int(body.completed)
        if body.completed and not row["completed"]:
            fields["completed_at"] = now()
        elif not body.completed:
            fields["completed_at"] = None
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE todos SET {sets} WHERE id = ?", [*fields.values(), todo_id])  # noqa: S608
        conn.commit()
    return todo_json(owned(conn, todo_id, uid))


@app.delete("/api/todos/{todo_id}", status_code=204)
def delete_todo(todo_id: int, conn: Db, uid: User) -> Response:
    owned(conn, todo_id, uid)
    conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
    conn.commit()
    return Response(status_code=204)
