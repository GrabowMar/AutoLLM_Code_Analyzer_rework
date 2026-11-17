"""Independent in-memory HTTP controls for narrowly scoped account lifecycle checks.

This is a stateful fixture, not a complete task implementation or an application
under comparison. Mutants each disable one named behavior; no acceptance plan or
expected assertion is imported by the server.
"""

from __future__ import annotations

import contextlib
import http.server
import json
import secrets
import threading
import time
import urllib.parse
from collections.abc import Iterator
from http.cookies import SimpleCookie
from typing import Any


class State:
    def __init__(self, mutant: str, success_status: int = 204) -> None:
        self.mutant = mutant
        self.success_status = success_status
        self.admin_password = secrets.token_urlsafe(24)
        self.users: dict[str, dict[str, Any]] = {
            "admin": {"password": self.admin_password, "email": None, "failures": 0, "locked_until": 0},
        }
        self.sessions: dict[str, str] = {}
        self.resets: dict[str, tuple[str, float]] = {}
        self.outbox: list[dict[str, str]] = []
        self.lock = threading.RLock()


def handler(state: State) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

        def send(self, code: int, data: Any = None, *, cookie: str | None = None) -> None:
            payload = json.dumps(data).encode() if data is not None else b""
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            if cookie:
                self.send_header("Set-Cookie", "session=" + cookie + "; HttpOnly; Path=/")
            self.end_headers()
            self.wfile.write(payload)

        def actor(self) -> tuple[str | None, str]:
            header = self.headers.get("Authorization", "")
            token = header[7:] if header.startswith("Bearer ") else ""
            if not token:
                jar = SimpleCookie()
                jar.load(self.headers.get("Cookie", ""))
                token = jar["session"].value if "session" in jar else ""
            return state.sessions.get(token), token

        def run_request(self) -> None:
            with state.lock:
                self.respond()

        def respond(self) -> None:
            path = urllib.parse.urlsplit(self.path)
            try:
                n = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(n)) if n else {}
            except (ValueError, UnicodeDecodeError):
                self.send(400, {"detail": "invalid JSON"})
                return
            name, session = self.actor()
            if path.path == "/api/health":
                self.send(200, {"status": "ok"})
            elif path.path == "/api/auth/register" and self.command == "POST":
                username, password = body.get("username"), body.get("password")
                if (
                    not isinstance(username, str)
                    or not 1 <= len(username) <= 50
                    or not isinstance(password, str)
                    or len(password) < 8
                ):
                    self.send(400)
                elif username in state.users:
                    self.send(409)
                else:
                    state.users[username] = {
                        "password": password,
                        "email": body.get("email"),
                        "failures": 0,
                        "locked_until": 0,
                    }
                    self.send(201, {"id": username, "username": username})
            elif path.path == "/api/auth/login" and self.command == "POST":
                username = body.get("username")
                row = state.users.get(username)
                if row and row["locked_until"] > time.time():
                    self.send(429)
                elif not row or body.get("password") != row["password"]:
                    if row:
                        row["failures"] += 1
                        if row["failures"] >= 5:
                            row["locked_until"] = time.time() + 900
                    self.send(401)
                else:
                    row["failures"] = 0
                    token = secrets.token_urlsafe(24)
                    state.sessions[token] = username
                    self.send(200, {"token": token}, cookie=token)
            elif path.path == "/api/auth/password-reset" and self.command == "POST":
                username = body.get("username")
                if username not in state.users:
                    username = next((k for k, r in state.users.items() if r["email"] == body.get("email")), None)
                if username in state.users and state.users[username]["email"]:
                    for token, (owner, _) in list(state.resets.items()):
                        if owner == username:
                            del state.resets[token]
                    token = secrets.token_urlsafe(24)
                    state.resets[token] = (username, time.time() + 1800)
                    state.outbox.insert(0, {"to": state.users[username]["email"], "body": "/reset?token=" + token})
                self.send(200 if state.success_status == 200 else 202)
            elif path.path == "/api/outbox" and self.command == "GET":
                address = urllib.parse.parse_qs(path.query).get("to", [None])[0]
                self.send(200, {"items": [m for m in state.outbox if m["to"] == address]})
            elif path.path == "/api/auth/password-reset/confirm" and self.command == "POST":
                record = state.resets.get(body.get("token"))
                password = body.get("new_password")
                if (
                    not record
                    or record[1] <= time.time()
                    or not isinstance(password, str)
                    or not 8 <= len(password) <= 128
                ):
                    self.send(400)
                else:
                    username = record[0]
                    state.users[username]["password"] = password
                    if state.mutant != "reset_accepts_reuse":
                        del state.resets[body["token"]]
                    for token, owner in list(state.sessions.items()):
                        if owner == username:
                            del state.sessions[token]
                    self.send(state.success_status)
            elif not name:
                self.send(401)
            elif path.path in {"/api/me", "/api/account"}:
                if self.command == "PATCH":
                    state.users[name]["email"] = body.get("email")
                self.send(200, {"id": name, "username": name, "email": state.users[name]["email"], "balance": 1000})
            elif path.path.startswith("/api/admin/locks/") and path.path.endswith("/unlock"):
                target = urllib.parse.unquote(path.path.split("/")[-2])
                if name != "admin" and state.mutant != "unlock_unprotected":
                    self.send(403)
                elif target not in state.users or state.users[target]["locked_until"] <= time.time():
                    self.send(404)
                else:
                    if state.mutant != "unlock_no_effect":
                        state.users[target]["locked_until"] = 0
                        state.users[target]["failures"] = 0
                    self.send(state.success_status)
            elif path.path == "/api/auth/csrf" and self.command == "GET":
                self.send(200, {} if state.mutant == "csrf_missing" else {"csrf_token": secrets.token_urlsafe(24)})
            elif path.path == "/api/auth/logout" and self.command == "POST":
                if state.mutant == "logout_unavailable":
                    self.send(404)
                else:
                    state.sessions.pop(session, None)
                    self.send(state.success_status)
            else:
                self.send(404)

        do_GET = do_POST = do_PATCH = run_request  # noqa: N815

    return Handler


@contextlib.contextmanager
def control(mutant: str = "", *, success_status: int = 204) -> Iterator[tuple[str, str]]:
    state = State(mutant, success_status)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", state.admin_password
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
