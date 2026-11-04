#!/usr/bin/env python3
"""Probe mutant reference app (spec 8.3 "probe validity", 8.5 conformance; standard library only).

One small app implements the probe-kit task (``kit_task.yaml``: the todo API of ``crud_todo_list`` plus one
endpoint per probe surface) under both contract auth modes. Environment switches turn it into the
deliberately broken variants the validation needs:

``LLMLAB_VULN=<kind>[,<kind>...]``  the vulnerable mutant of each probe kind (``oracle_validate`` expects
                                    ``exploited`` on it and ``not_exploited`` on the fixed app)
``LLMLAB_MUTANT=<name>``            tester mini-app mutants: ``bare`` (health and index only, the bare
                                    starter), ``wrong_status`` (create answers 200), ``no_validation``
                                    (accepts any body), ``crash`` (exits at start), ``idor`` (no ownership)

Contract (as the generated apps): ``PORT`` (8000), ``DATA_DIR`` (``./data``, SQLite), ``ADMIN_PASSWORD``
(admin login disabled when unset), ``SECRET_KEY`` (token signing), ``AUTH_MODE`` (``contract_bearer`` or
``cookie_session``), ``WEBHOOK_SECRET``. Never deploy this app: some variants are vulnerable on purpose.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import html
import http.server
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request

PORT = int(os.environ.get("PORT", "8000"))
DATA_DIR = os.environ.get("DATA_DIR", "./data")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "") or secrets.token_hex(16)
AUTH_MODE = os.environ.get("AUTH_MODE", "contract_bearer")
VULN = {v.strip() for v in os.environ.get("LLMLAB_VULN", "").split(",") if v.strip()}
MUTANT = os.environ.get("LLMLAB_MUTANT", "")
PRIORITIES = ("low", "medium", "high")
_LOCK = threading.Lock()
_FAILED_LOGINS: dict[str, list[float]] = {}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
INDEX = b"<!doctype html><html><head><title>llmlab</title></head><body><div id=app></div></body></html>"


class HttpError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(os.path.join(DATA_DIR, "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if "sqli_time" in VULN:
        # SQLite has no SLEEP(); give the time-based mutant one (MySQL-style, once per statement's
        # connection) so the exploit does not depend on how fast the host runs the heavy-CTE payload.
        slept: list[bool] = []

        def _sleep(seconds: float) -> int:
            if not slept:
                slept.append(True)
                time.sleep(min(float(seconds), 5.0))
            return 0

        conn.create_function("SLEEP", 1, _sleep)
    return conn


def init_db() -> None:
    os.makedirs(os.path.join(DATA_DIR, "files"), exist_ok=True)
    with open(os.path.join(DATA_DIR, "files", "readme.txt"), "w", encoding="utf-8") as fh:
        fh.write("llmlab readme\n")
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, role TEXT);
            CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, description TEXT,
                priority TEXT, due_date TEXT, completed INTEGER, completed_at TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, body TEXT);
            """,
        )


# --- tokens (HS256 JWT) ---------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(user_id: int) -> str:
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64(json.dumps({"sub": user_id, "iat": int(time.time())}).encode())
    sig = _b64(hmac.new(SECRET_KEY.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    return f"{head}.{body}.{sig}"


def read_token(token: str) -> int | None:
    try:
        head, body, sig = token.split(".")
        header = json.loads(_unb64(head))
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if header.get("alg") == "none":
        return int(payload["sub"]) if "jwt_none_alg" in VULN else None
    want = _b64(hmac.new(SECRET_KEY.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    return int(payload["sub"]) if hmac.compare_digest(want, sig) else None


def hash_password(password: str) -> str:
    if "plaintext_password_db" in VULN:
        return password
    salt = secrets.token_hex(8)
    return salt + "$" + hashlib.sha256((salt + password).encode()).hexdigest()


def check_password(stored: str, password: str) -> bool:
    if "$" not in stored:
        return stored == password
    salt, digest = stored.split("$", 1)
    return hmac.compare_digest(digest, hashlib.sha256((salt + password).encode()).hexdigest())


# --- handler ----------------------------------------------------------------------------------


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "llmlab-refapp/1"

    def log_message(self, *_args: object) -> None:
        return

    # plumbing
    def _send(
        self,
        status: int,
        body: object = None,
        *,
        ctype: str = "application/json",
        headers: dict | None = None,
    ) -> None:
        if body is None:
            data = b""
        elif isinstance(body, bytes):
            data = body
        elif isinstance(body, str):
            data = body.encode()
        else:
            data = json.dumps(body).encode()
        self.send_response(status)
        if data or status != 204:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            if isinstance(value, list):
                for item in value:
                    self.send_header(key, item)
            else:
                self.send_header(key, value)
        self.end_headers()
        if data and self.command != "HEAD":
            self.wfile.write(data)

    def _raw(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 5 * 1024 * 1024:
            self.rfile.read(length)
            raise HttpError(413, "body too large")
        return self.rfile.read(length) if length else b""

    def _json(self) -> dict:
        raw = self._raw()
        try:
            data = json.loads(raw.decode() or "null")
        except (UnicodeDecodeError, json.JSONDecodeError):
            if "verbose_errors" in VULN:
                raise
            raise HttpError(400, "invalid JSON") from None
        if not isinstance(data, dict):
            raise HttpError(400, "expected an object")
        return data

    def _cookies(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for part in (self.headers.get("Cookie") or "").split(";"):
            if "=" in part:
                key, value = part.strip().split("=", 1)
                out[key] = value
        return out

    def _user(self, *, required: bool = True) -> sqlite3.Row | None:
        token = None
        auth = self.headers.get("Authorization") or ""
        if AUTH_MODE == "contract_bearer" and auth.startswith("Bearer "):
            token = auth[7:]
        elif AUTH_MODE == "cookie_session":
            token = self._cookies().get("session")
            if token and self.command in ("POST", "PUT", "PATCH", "DELETE") and "csrf_missing" not in VULN:
                if self.headers.get("X-CSRF-Token") != self._cookies().get("csrf_token"):
                    raise HttpError(403, "CSRF token missing")
        uid = read_token(token) if token else None
        row = None
        if uid is not None:
            with db() as conn:
                row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if row is None and required:
            raise HttpError(401, "authentication required")
        return row

    def _route(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path, query = parsed.path, dict(urllib.parse.parse_qsl(parsed.query))
        method = self.command
        if path == "/api/health":
            return self._send(200, {"status": "ok"})
        if not path.startswith("/api/"):
            return self._send(200, INDEX, ctype="text/html; charset=utf-8")
        if MUTANT == "bare":
            raise HttpError(404, "not found")
        if path == "/api/auth/register" and method == "POST":
            return self.register()
        if path == "/api/auth/login" and method == "POST":
            return self.login()
        if path == "/api/auth/logout" and method == "POST":
            return self._send(204, headers={"Set-Cookie": "session=; Max-Age=0; Path=/"})
        if path == "/api/todos":
            return self.todos_list(query) if method == "GET" else self.todos_create() if method == "POST" else None
        m = re.fullmatch(r"/api/todos/([^/]+)", path)
        if m:
            return self.todo_item(method, m.group(1))
        if path == "/api/notes" and method == "POST":
            return self.note_create()
        m = re.fullmatch(r"/api/notes/([^/]+)/view", path)
        if m and method == "GET":
            return self.note_view(m.group(1))
        if path == "/api/echo" and method == "GET":
            return self.echo(query)
        if path == "/api/files/download" and method == "GET":
            return self.download(query)
        if path == "/api/go" and method == "GET":
            return self.go(query)
        if path == "/api/import" and method == "POST":
            return self.xml_import()
        if path == "/api/fetch" and method == "POST":
            return self.fetch()
        if path == "/api/admin/users" and method == "GET":
            user = self._user()
            if user["role"] != "admin":
                raise HttpError(403, "admin only")
            with db() as conn:
                rows = conn.execute("SELECT id, username FROM users ORDER BY id").fetchall()
            return self._send(200, {"items": [dict(r) for r in rows]})
        raise HttpError(404, "not found")

    def _dispatch(self) -> None:
        try:
            self._route()
        except HttpError as exc:
            self._send(exc.status, {"detail": exc.detail})
        except Exception as exc:  # noqa: BLE001
            if VULN & {"verbose_errors", "sqli_error"}:
                self._send(500, f"Internal error\n{traceback.format_exc()}", ctype="text/plain")
            else:
                self._send(500, {"detail": f"internal error ({type(exc).__name__})"})

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _dispatch  # noqa: N815

    # --- auth ---
    def register(self) -> None:
        data = self._json()
        username, password = data.get("username"), data.get("password")
        if not isinstance(username, str) or not username or len(username) > 50:
            raise HttpError(400, "username required")
        if not isinstance(password, str) or not password:
            raise HttpError(400, "password required")
        if username == "admin":
            raise HttpError(409, "username taken")
        try:
            with _LOCK, db() as conn:
                cur = conn.execute(
                    "INSERT INTO users (username, password, role) VALUES (?, ?, 'user')",
                    (username, hash_password(password)),
                )
        except sqlite3.IntegrityError:
            raise HttpError(409, "username taken") from None
        status = 200 if MUTANT == "wrong_status" else 201
        self._send(status, {"id": cur.lastrowid, "username": username})

    def _admin_row(self) -> sqlite3.Row:
        with _LOCK, db() as conn:
            row = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            if row is None:
                conn.execute("INSERT INTO users (username, password, role) VALUES ('admin', '', 'admin')")
                row = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        return row

    def login(self) -> None:
        data = self._json()
        username, password = str(data.get("username") or ""), str(data.get("password") or "")
        now = time.time()
        if "rate_limit_missing" not in VULN:
            recent = [t for t in _FAILED_LOGINS.get(username, []) if now - t < 60]
            _FAILED_LOGINS[username] = recent
            if len(recent) >= 10:
                raise HttpError(429, "too many attempts")
        user = None
        if username == "admin":
            ok = bool(ADMIN_PASSWORD) and hmac.compare_digest(password, ADMIN_PASSWORD)
            if "default_credential" in VULN and password == "admin":
                ok = True
            user = self._admin_row() if ok else None
        else:
            with db() as conn:
                row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if row is not None and check_password(row["password"], password):
                user = row
        if user is None:
            _FAILED_LOGINS.setdefault(username, []).append(now)
            raise HttpError(401, "invalid credentials")
        token = make_token(int(user["id"]))
        if AUTH_MODE == "cookie_session":
            csrf = secrets.token_hex(12)
            cookies = [f"session={token}; HttpOnly; Path=/; SameSite=Lax", f"csrf_token={csrf}; Path=/; SameSite=Lax"]
            return self._send(200, {"username": user["username"]}, headers={"Set-Cookie": cookies})
        return self._send(200, {"token": token})

    # --- todos ---
    def _todo_out(self, row: sqlite3.Row) -> dict:
        out = {k: row[k] for k in ("id", "title", "description", "priority", "due_date", "completed_at", "created_at")}
        out["completed"] = bool(row["completed"])
        if "mass_assignment" in VULN:
            out["user_id"] = row["user_id"]
        return out

    def _validate(self, data: dict, *, partial: bool) -> dict:
        if MUTANT == "no_validation":
            return {k: data.get(k) for k in ("title", "description", "priority", "due_date", "completed") if k in data}
        out: dict = {}
        if "title" in data or not partial:
            title = data.get("title")
            if not isinstance(title, str) or not title.strip() or len(title) > 255:
                raise HttpError(400, "title must be 1-255 characters")
            out["title"] = title
        if data.get("description") is not None:
            if not isinstance(data["description"], str) or len(data["description"]) > 2000:
                raise HttpError(400, "description too long")
            out["description"] = data["description"]
        if data.get("priority") is not None:
            if data["priority"] not in PRIORITIES:
                raise HttpError(400, "priority must be low, medium or high")
            out["priority"] = data["priority"]
        if data.get("due_date") is not None:
            if not isinstance(data["due_date"], str) or not _DATE.match(data["due_date"]):
                raise HttpError(400, "due_date must be YYYY-MM-DD")
            out["due_date"] = data["due_date"]
        if data.get("completed") is not None:
            if not isinstance(data["completed"], bool):
                raise HttpError(400, "completed must be a boolean")
            out["completed"] = data["completed"]
        return out

    def todos_list(self, query: dict) -> None:
        user = self._user()
        where, args = ["user_id=?"], [user["id"]]
        if "completed" in query:
            where.append("completed=?")
            args.append(1 if query["completed"].lower() in ("true", "1") else 0)
        if "priority" in query:
            if query["priority"] not in PRIORITIES:
                raise HttpError(400, "bad priority")
            where.append("priority=?")
            args.append(query["priority"])
        sql_where = " AND ".join(where)
        search = query.get("search", query.get("q"))
        if search is not None:
            if VULN & {"sqli_boolean", "sqli_error", "sqli_time"}:
                sql_where += f" AND title LIKE '%{search}%'"
            else:
                sql_where += " AND title LIKE ?"
                args.append(f"%{search}%")
        try:
            page = max(int(query.get("page", 1)), 1)
            size = min(max(int(query.get("page_size", 50)), 1), 200)
        except ValueError:
            raise HttpError(400, "bad paging") from None
        with db() as conn:
            rows = conn.execute(
                f"SELECT * FROM todos WHERE {sql_where} ORDER BY completed ASC, id ASC",
                args,
            ).fetchall()
        items = [self._todo_out(r) for r in rows]
        chunk = items[(page - 1) * size : page * size]
        remaining = sum(1 for i in items if not i["completed"])
        self._send(200, {"items": chunk, "total": len(items), "page": page, "page_size": size, "remaining": remaining})

    def todos_create(self) -> None:
        user = self._user()
        data = self._json()
        fields = self._validate(data, partial=False)
        owner = user["id"]
        if "mass_assignment" in VULN and "user_id" in data:
            owner = data["user_id"]
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with _LOCK, db() as conn:
            cur = conn.execute(
                "INSERT INTO todos (user_id, title, description, priority, due_date, completed, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    owner,
                    fields.get("title"),
                    fields.get("description"),
                    fields.get("priority") or "medium",
                    fields.get("due_date"),
                    1 if fields.get("completed") else 0,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM todos WHERE id=?", (cur.lastrowid,)).fetchone()
        self._send(200 if MUTANT == "wrong_status" else 201, self._todo_out(row))

    def todo_item(self, method: str, raw_id: str) -> None:
        user = self._user()
        with db() as conn:
            row = conn.execute("SELECT * FROM todos WHERE id=?", (raw_id,)).fetchone()
        if row is None or (row["user_id"] != user["id"] and MUTANT != "idor" and "idor_enumeration" not in VULN):
            raise HttpError(404, "not found")
        if method == "GET":
            return self._send(200, self._todo_out(row))
        if method == "DELETE":
            with _LOCK, db() as conn:
                conn.execute("DELETE FROM todos WHERE id=?", (row["id"],))
            return self._send(204)
        if method not in ("PATCH", "PUT"):
            raise HttpError(405, "method not allowed")
        data = self._json()
        fields = self._validate(data, partial=True)
        if "mass_assignment" in VULN and "user_id" in data:
            fields["user_id"] = data["user_id"]
        if "completed" in fields:
            fields["completed_at"] = (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if fields["completed"] else None
            )
            fields["completed"] = 1 if fields["completed"] else 0
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            with _LOCK, db() as conn:
                conn.execute(f"UPDATE todos SET {sets} WHERE id=?", (*fields.values(), row["id"]))
        with db() as conn:
            row = conn.execute("SELECT * FROM todos WHERE id=?", (row["id"],)).fetchone()
        return self._send(200, self._todo_out(row))

    # --- probe surfaces ---
    def note_create(self) -> None:
        user = self._user()
        data = self._json()
        title, body = data.get("title"), data.get("body", "")
        if not isinstance(title, str) or not title or len(title) > 200 or not isinstance(body, str):
            raise HttpError(400, "title and body required")
        with _LOCK, db() as conn:
            cur = conn.execute("INSERT INTO notes (user_id, title, body) VALUES (?, ?, ?)", (user["id"], title, body))
        self._send(201, {"id": cur.lastrowid, "title": title, "body": body})

    def note_view(self, raw_id: str) -> None:
        user = self._user()
        with db() as conn:
            row = conn.execute("SELECT * FROM notes WHERE id=? AND user_id=?", (raw_id, user["id"])).fetchone()
        if row is None:
            raise HttpError(404, "not found")
        esc = (lambda s: s) if "xss_stored" in VULN else html.escape
        page = f"<!doctype html><html><body><h1>{esc(row['title'])}</h1><div>{esc(row['body'])}</div></body></html>"
        self._send(200, page, ctype="text/html; charset=utf-8")

    def echo(self, query: dict) -> None:
        self._user()
        q = query.get("q", "")
        shown = q if "xss_reflected" in VULN else html.escape(q)
        self._send(
            200,
            f"<!doctype html><html><body>You searched for {shown}</body></html>",
            ctype="text/html; charset=utf-8",
        )

    def download(self, query: dict) -> None:
        self._user()
        name = query.get("name", "")
        base = os.path.join(DATA_DIR, "files")
        if "path_traversal" in VULN:
            target = os.path.join(base, name)
        else:
            if not name or "/" in name or "\\" in name or name.startswith("."):
                raise HttpError(400, "bad file name")
            target = os.path.join(base, name)
        try:
            with open(target, "rb") as fh:
                data = fh.read()
        except OSError:
            raise HttpError(404, "no such file") from None
        self._send(200, data, ctype="application/octet-stream")

    def go(self, query: dict) -> None:
        target = query.get("next", "/")
        if "open_redirect" not in VULN and (not target.startswith("/") or target.startswith("//")):
            target = "/"
        self._send(302, b"", headers={"Location": target})

    def xml_import(self) -> None:
        self._user()
        if "json" in (self.headers.get("Content-Type") or ""):
            text = self._json().get("xml")
            if not isinstance(text, str):
                raise HttpError(400, "xml required")
        else:
            text = self._raw().decode("utf-8", errors="replace")
        if "<!DOCTYPE" in text or "<!ENTITY" in text:
            if "xxe" not in VULN:
                raise HttpError(400, "DTDs are not allowed")
            for name, url in re.findall(r'<!ENTITY\s+(\w+)\s+SYSTEM\s+"([^"]+)"', text):
                path = urllib.parse.urlsplit(url).path
                try:
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        text = text.replace(f"&{name};", fh.read())
                except OSError:
                    pass
        m = re.search(r"<name>(.*?)</name>", text, re.S)
        if m is None:
            raise HttpError(400, "no <name> element")
        self._send(201, {"imported": m.group(1)})

    def fetch(self) -> None:
        self._user()
        data = self._json()
        url = data.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise HttpError(400, "url required")
        if "ssrf_mock" in VULN:
            with contextlib.suppress(Exception):
                urllib.request.urlopen(url, timeout=3).read(64)  # noqa: S310 - the vulnerable mutant
        self._send(202, {"accepted": True})


def main() -> None:
    if MUTANT == "crash":
        sys.stderr.write("refusing to start (crash mutant)\n")
        sys.exit(3)
    init_db()
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print(f"llmlab refapp on :{PORT} vuln={sorted(VULN)} mutant={MUTANT or '-'}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
