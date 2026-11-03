"""Django reference views for crud_todo_list (conformance kit, contract_bearer).

URLs end with a slash (Django convention): with ``APPEND_SLASH`` and ``CommonMiddleware`` a request to
``/api/todos`` is redirected (301) to ``/api/todos/``, which exercises the tester's trailing-slash tolerance.
"""

import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from functools import wraps

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

PRIORITIES = ("low", "medium", "high")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DB = settings.DATA_DIR / "app.db"
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(DB) as _conn:
    _conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL,
          description TEXT, priority TEXT NOT NULL DEFAULT 'medium', due_date TEXT, completed INTEGER NOT NULL DEFAULT 0,
          completed_at TEXT, created_at TEXT NOT NULL);
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


def body_of(request):
    try:
        return json.loads(request.body or b"null")
    except ValueError:
        return None


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

    return csrf_exempt(wrapper)


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
    if body.get("description") is not None and (not isinstance(body["description"], str) or len(body["description"]) > 2000):
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
        "id": row["id"], "title": row["title"], "description": row["description"], "priority": row["priority"],
        "due_date": row["due_date"], "completed": bool(row["completed"]), "completed_at": row["completed_at"],
        "created_at": row["created_at"],
    }


@csrf_exempt
def register(request):
    if request.method != "POST":
        return err(405, "method not allowed")
    body = body_of(request)
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


@csrf_exempt
def login(request):
    if request.method != "POST":
        return err(405, "method not allowed")
    body = body_of(request)
    if not isinstance(body, dict) or not isinstance(body.get("username"), str) or not isinstance(body.get("password"), str):
        return err(401, "invalid credentials")
    with conn() as c:
        row = c.execute("SELECT id, pw FROM users WHERE username = ?", (body["username"],)).fetchone()
    if row is None or not hmac.compare_digest(hash_pw(body["password"], row["pw"].split("$")[0]), row["pw"]):
        return err(401, "invalid credentials")
    return JsonResponse({"token": sign(row["id"])})


@authed
def todos(request, uid: int):
    if request.method == "GET":
        return list_todos(request, uid)
    if request.method == "POST":
        return create_todo(request, uid)
    return err(405, "method not allowed")


def list_todos(request, uid: int):
    where, args = ["user_id = ?"], [uid]
    completed = request.GET.get("completed")
    if completed is not None:
        where.append("completed = ?")
        args.append(1 if completed.lower() in ("true", "1") else 0)
    priority = request.GET.get("priority")
    if priority is not None:
        if priority not in PRIORITIES:
            return err(400, "invalid priority")
        where.append("priority = ?")
        args.append(priority)
    try:
        page = max(1, int(request.GET.get("page", 1)))
        page_size = min(200, max(1, int(request.GET.get("page_size", 50))))
    except ValueError:
        return err(400, "page and page_size must be integers")
    clause = " AND ".join(where)
    with conn() as c:
        total = c.execute(f"SELECT COUNT(*) FROM todos WHERE {clause}", args).fetchone()[0]  # noqa: S608
        rows = c.execute(
            f"SELECT * FROM todos WHERE {clause} ORDER BY completed ASC, id DESC LIMIT ? OFFSET ?",  # noqa: S608
            [*args, page_size, (page - 1) * page_size],
        ).fetchall()
        remaining = c.execute("SELECT COUNT(*) FROM todos WHERE user_id = ? AND completed = 0", (uid,)).fetchone()[0]
    return JsonResponse(
        {"items": [todo_json(r) for r in rows], "total": total, "page": page, "page_size": page_size, "remaining": remaining},
    )


def create_todo(request, uid: int):
    body = body_of(request)
    problem = validate(body, partial=False)
    if problem:
        return err(400, problem)
    completed = bool(body.get("completed") or False)
    with conn() as c:
        cur = c.execute(
            "INSERT INTO todos (user_id, title, description, priority, due_date, completed, completed_at, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, body["title"], body.get("description"), body.get("priority") or "medium", body.get("due_date"),
             int(completed), now() if completed else None, now()),
        )
        row = c.execute("SELECT * FROM todos WHERE id = ?", (cur.lastrowid,)).fetchone()
    return JsonResponse(todo_json(row), status=201)


@authed
def todo(request, uid: int, todo_id: int):
    with conn() as c:
        row = c.execute("SELECT * FROM todos WHERE id = ? AND user_id = ?", (todo_id, uid)).fetchone()
        if row is None:
            return err(404, "not found")
        if request.method == "GET":
            return JsonResponse(todo_json(row))
        if request.method == "DELETE":
            c.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
            return HttpResponse(status=204)
        if request.method != "PATCH":
            return err(405, "method not allowed")
        body = body_of(request)
        problem = validate(body, partial=True)
        if problem:
            return err(400, problem)
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
            c.execute(f"UPDATE todos SET {sets} WHERE id = ?", [*fields.values(), todo_id])  # noqa: S608
        row = c.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
    return JsonResponse(todo_json(row))
