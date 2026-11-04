#!/usr/bin/env python3
"""eval-tester v1: runs a compiled ``AcceptancePlan`` against a started sample (spec sections 8.1, 8.3-8.5).

Standard library only (``http.client``), so the same file runs in the ``eval-tester`` image and in-process
in the backend tests (``backend.generation.evaluation.tester_module()``).

CLI (inside the image)::

    tester.py --plan /in/plan.json --out /out/result.json --phase all|pre|post [--state /out/state.json]
              [--mock-port 9009]

Secrets come from the environment (``ADMIN_PASSWORD``, ``WEBHOOK_SECRET``), never from the plan.

Phases. Persistence checks (``restart_before``) need the app restarted between their setup and their
request, which only the suite can do. ``pre`` runs every check that does not wait for a restart, plus the
setup of the restart checks, and writes the state (actor credentials, captures, results so far). The suite
restarts the app and runs ``post``, which logs the actors in again, runs the deferred checks and then the
probes. ``all`` (in-process, with a ``restart`` callback) does both in one call.

Validity rules carried here:

* a tester-side exception makes the whole result ``measured=false`` (``not_measured_reason``); it is never a
  pass rate of 0;
* an app-side connection failure is a check ``error`` (counted as a fail by the suite);
* a probe whose precondition checks did not pass is ``skipped(precondition_failed)``; a probe whose benign
  control request fails is ``skipped(positive_control_failed)``; only a probe that reached its attack is
  ``exploited`` or ``not_exploited``. No severity is assigned.

Tolerances (``TESTER_RULES_VERSION``): one same-origin trailing-slash redirect is followed by retrying the
canonical path (any method); an expected 400 or 422 accepts either; 413 from the framework or the app is
the same 413; wrong-method behaviour (405 vs 404) is never checked.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import http.client
import http.cookies
import http.server
import json
import os
import re
import secrets
import select
import sys
import threading
import time
import traceback
import urllib.parse
from typing import Any

VERSION = 1
TESTER_RULES_VERSION = 1
PROBE_RULES_VERSION = 1

REQUEST_TIMEOUT_S = 20.0
OVERSIZE_BYTES = 12 * 1024 * 1024
_STREAM_ABOVE = 1024 * 1024  # bodies above this are streamed in chunks (early-answer tolerance)
_STREAM_CHUNK = 64 * 1024
_STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SQL_ERROR = re.compile(
    r"sqlite3?\.|OperationalError|ProgrammingError|syntax error|unrecognized token|SQLSTATE|psycopg|"
    r"near \"|SQL syntax|unterminated quoted string|QueryFailedError|SequelizeDatabaseError",
    re.IGNORECASE,
)
_STACK_TRACE = re.compile(
    r"Traceback \(most recent call last\)|File \"[^\"]+\", line \d+|\bat [\w.$<>]+ \([^)]*:\d+:\d+\)|"
    r"node_modules/|Exception in thread|java\.lang\.\w+Exception|werkzeug\.debug|django\.core\.|"
    r"at [\w.$]+\([\w]+\.java:\d+\)",
)
_PASSWD = re.compile(r"root:[x*]?:0:0:")
# Probes that change or lock shared state run last.
_LATE_KINDS = ("mass_assignment", "rate_limit_missing", "default_credential")


class TesterError(Exception):
    """A fault of the tester itself: the whole result becomes ``measured=false``."""


class AppConnectionError(Exception):
    """The app reset or refused the connection (the app's fault: a check ``error``)."""


# =====================================================================================
# HTTP
# =====================================================================================


class Response:
    def __init__(self, status: int, headers: list[tuple[str, str]], body: bytes, ms: int, path: str) -> None:
        self.status = status
        self.header_list = headers
        self.headers = {k.lower(): v for k, v in headers}
        self.body = body
        self.ms = ms
        self.path = path
        self._json: Any = ...

    @property
    def json(self) -> Any:
        if self._json is ...:
            try:
                self._json = json.loads(self.body.decode("utf-8")) if self.body else None
            except (UnicodeDecodeError, ValueError):
                self._json = None
        return self._json

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def is_html(self) -> bool:
        return "text/html" in self.headers.get("content-type", "").lower()

    def set_cookies(self) -> list[str]:
        return [v for k, v in self.header_list if k.lower() == "set-cookie"]


def _multipart(form: dict[str, Any] | None, files: dict[str, Any]) -> tuple[bytes, str]:
    boundary = "llmlab" + secrets.token_hex(12)
    parts: list[bytes] = []
    for name, value in (form or {}).items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode(),
        )
    for name, spec in files.items():
        spec = spec if isinstance(spec, dict) else {"content": str(spec)}
        filename = str(spec.get("filename") or "file.txt")
        content = spec.get("content", "")
        data = content.encode() if isinstance(content, str) else bytes(content)
        if spec.get("content_b64"):
            data = base64.b64decode(spec["content_b64"])
        ctype = str(spec.get("content_type") or "application/octet-stream")
        head = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n"
        ).encode()
        parts.append(head + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class Http:
    def __init__(self, base_url: str) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        self.scheme = parsed.scheme or "http"
        self.host = parsed.hostname or "app"
        self.port = parsed.port or (443 if self.scheme == "https" else 80)
        self.base_path = parsed.path.rstrip("/")
        self.origin = f"{self.scheme}://{parsed.netloc}"

    def send(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        url = self.base_path + path
        if query:
            pairs = [(k, _q(v)) for k, v in query.items() if v is not None]
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(pairs)
        conn_cls = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(self.host, self.port, timeout=REQUEST_TIMEOUT_S)
        started = time.monotonic()
        try:
            if body is not None and len(body) > _STREAM_ABOVE:
                self._stream(conn, method, url, body, headers or {})
            else:
                conn.request(method, url, body=body, headers=headers or {})
            raw = conn.getresponse()
            data = raw.read()
            out = Response(raw.status, list(raw.getheaders()), data, int((time.monotonic() - started) * 1000), path)
        except (ConnectionError, TimeoutError, http.client.HTTPException, OSError) as exc:
            msg = f"{type(exc).__name__}: {exc}"
            raise AppConnectionError(msg) from exc
        finally:
            conn.close()
        return out

    @staticmethod
    def _stream(conn: http.client.HTTPConnection, method: str, url: str, body: bytes, headers: dict[str, str]) -> None:
        """Send a large body in chunks and stop as soon as the app answers early (tolerance 8.5: 413 "from the
        framework or the app"). A server that refuses an oversize upload often answers 413 before reading the
        body and then closes the connection; writing the rest would fail with a broken pipe or a reset and turn
        a correct 413 into a connection error. The response already sent is read instead."""
        conn.putrequest(method, url, skip_accept_encoding=True)
        for name, value in headers.items():
            conn.putheader(name, value)
        if not any(name.lower() == "content-length" for name in headers):
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        sock = conn.sock
        view = memoryview(body)
        for start in range(0, len(body), _STREAM_CHUNK):
            if sock is not None and select.select([sock], [], [], 0)[0]:
                return  # an early answer: read it
            try:
                sock.sendall(view[start : start + _STREAM_CHUNK])
            except (BrokenPipeError, ConnectionResetError):
                return  # the app stopped reading; its answer (if any) is read by the caller


def _q(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# =====================================================================================
# Helpers: JSON path, substitution, matching, schema
# =====================================================================================

_PATH_TOKEN = re.compile(r"\.([A-Za-z_][A-Za-z0-9_-]*)|\[(\d+)\]|\[\"([^\"]+)\"\]")


def json_path(doc: Any, path: str) -> Any:
    """``$.a.b[0].c`` (dot names, ``[n]`` indexes); raises ``KeyError`` when absent."""
    if not path.startswith("$"):
        path = "$." + path
    rest = path[1:]
    pos = 0
    cur = doc
    while pos < len(rest):
        m = _PATH_TOKEN.match(rest, pos)
        if m is None:
            raise KeyError(path)
        name, index, quoted = m.group(1), m.group(2), m.group(3)
        if index is not None:
            if not isinstance(cur, list) or int(index) >= len(cur):
                raise KeyError(path)
            cur = cur[int(index)]
        else:
            key = name if name is not None else quoted
            if not isinstance(cur, dict) or key not in cur:
                raise KeyError(path)
            cur = cur[key]
        pos = m.end()
    return cur


def substitute(value: Any, ctx: dict[str, Any]) -> Any:
    """Captured ``{name}`` values: exact string ``"{name}"`` keeps the captured type; ``{rand}`` and
    ``{oversize}`` are substituted inside strings; paths use ``substitute_path``."""
    if isinstance(value, str):
        m = _PLACEHOLDER.fullmatch(value)
        if m and m.group(1) in ctx and m.group(1) not in ("rand",):
            return ctx[m.group(1)]
        if "{oversize}" in value:
            value = value.replace("{oversize}", "x" * OVERSIZE_BYTES)
        if "{rand}" in value and "rand" in ctx:
            value = value.replace("{rand}", str(ctx["rand"]))
        return value
    if isinstance(value, list):
        return [substitute(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, ctx) for k, v in value.items()}
    return value


def substitute_path(path: str, ctx: dict[str, Any]) -> str:
    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in ctx:
            return urllib.parse.quote(str(ctx[name]), safe="")
        return m.group(0)

    return _PLACEHOLDER.sub(repl, path)


def contains(observed: Any, expected: Any) -> bool:
    """Recursive subset; a list matches when the observed list holds, in the same relative order, one
    element matching each listed element (an ordered subsequence)."""
    if isinstance(expected, dict):
        return isinstance(observed, dict) and all(
            k in observed and contains(observed[k], v) for k, v in expected.items()
        )
    if isinstance(expected, list):
        if not isinstance(observed, list):
            return False
        pos = 0
        for item in expected:
            while pos < len(observed) and not contains(observed[pos], item):
                pos += 1
            if pos >= len(observed):
                return False
            pos += 1
        return True
    if isinstance(expected, bool) or isinstance(observed, bool):
        return (
            expected is observed if isinstance(expected, bool) and isinstance(observed, bool) else expected == observed
        )
    if isinstance(expected, (int, float)) and isinstance(observed, (int, float)):
        return float(expected) == float(observed)
    return expected == observed


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$")
_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def schema_errors(value: Any, schema: Any, where: str = "$") -> list[str]:
    """A JSON Schema subset: type, properties, required, items, enum, const, min/maxLength, minimum, maximum,
    min/maxItems, format date/date-time, anyOf/oneOf/allOf. Unknown keywords are ignored."""
    if not isinstance(schema, dict):
        return []
    errors: list[str] = []
    for sub in schema.get("allOf") or []:
        errors += schema_errors(value, sub, where)
    for key in ("anyOf", "oneOf"):
        options = schema.get(key)
        if isinstance(options, list) and options and not any(not schema_errors(value, s, where) for s in options):
            errors.append(f"{where}: matches none of {key}")
    stype = schema.get("type")
    if stype is not None:
        types = stype if isinstance(stype, list) else [stype]
        if not any(_TYPES.get(t, lambda _v: True)(value) for t in types):
            return [*errors, f"{where}: expected {'/'.join(types)}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: {value!r} not in enum")
    if "const" in schema and not contains(value, schema["const"]):
        errors.append(f"{where}: {value!r} != {schema['const']!r}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{where}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{where}: longer than {schema['maxLength']}")
        fmt = schema.get("format")
        if fmt == "date" and not _DATE.match(value):
            errors.append(f"{where}: not a date")
        if fmt == "date-time" and not _DATETIME.match(value):
            errors.append(f"{where}: not a date-time")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{where}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{where}: above maximum")
    if isinstance(value, dict):
        for name in schema.get("required") or []:
            if name not in value:
                errors.append(f"{where}.{name}: required")
        for name, sub in (schema.get("properties") or {}).items():
            if name in value:
                errors += schema_errors(value[name], sub, f"{where}.{name}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{where}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{where}: more than {schema['maxItems']} items")
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(value):
                errors += schema_errors(item, items, f"{where}[{i}]")
    return errors


def expected_statuses(expected: Any) -> set[int]:
    statuses = {int(s) for s in (expected if isinstance(expected, list) else [expected])}
    if statuses & {400, 422}:
        statuses |= {400, 422}  # tolerance: validation failures accept 400 or 422 (FastAPI)
    return statuses


def list_of(doc: Any) -> list[Any] | None:
    """The result list of a list response: the document itself, ``items``/``results``/``data``, or the first
    list value."""
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        for key in ("items", "results", "data"):
            if isinstance(doc.get(key), list):
                return doc[key]
        for value in doc.values():
            if isinstance(value, list):
                return value
    return None


def _route_regex(template: str) -> re.Pattern[str]:
    parts = _PLACEHOLDER.split(template)
    out = ""
    for i, part in enumerate(parts):
        out += re.escape(part) if i % 2 == 0 else "[^/]+"
    return re.compile("^" + out + "/?$")


# =====================================================================================
# Mock server (ssrf_mock)
# =====================================================================================


class MockServer:
    """Records every request path; the app reaches it as ``mock.internal`` (eval network alias)."""

    def __init__(self, port: int, host: str = "0.0.0.0") -> None:  # noqa: S104 - in-sandbox only
        self.hits: list[str] = []
        hits = self.hits

        class Handler(http.server.BaseHTTPRequestHandler):
            def _answer(self) -> None:
                hits.append(self.path)
                body = b'{"ok": true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_HEAD = _answer  # noqa: N815

            def log_message(self, *_args: Any) -> None:
                return

        self.server = http.server.ThreadingHTTPServer((host, port), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


# =====================================================================================
# Actors and auth drivers (8.4)
# =====================================================================================


class Actor:
    def __init__(self, name: str) -> None:
        self.name = name
        self.username = ""
        self.password = ""
        self.token: str | None = None
        self.cookies: dict[str, str] = {}
        self.ready: bool | None = None  # None: not tried; False: login failed
        self.error: str | None = None

    def to_state(self) -> dict[str, Any]:
        return {"username": self.username, "password": self.password}


class Tester:
    def __init__(
        self,
        plan: dict[str, Any],
        *,
        admin_password: str = "",
        webhook_secret: str = "",
        mock_base: str | None = None,
    ) -> None:
        self.plan = plan
        self.http = Http(plan["base_url"])
        self.auth = dict(plan.get("auth") or {})
        self.admin_password = admin_password
        self.webhook_secret = webhook_secret
        self.mock_base = mock_base or plan.get("mock_base")
        self.mock: MockServer | None = None
        self.actors: dict[str, Actor] = {name: Actor(name) for name in ("anon", "user_a", "user_b", "admin")}
        self.actors["anon"].ready = True
        self.results: dict[str, dict[str, Any]] = {}
        self.order: list[str] = []
        self.deferred: dict[str, dict[str, Any]] = {}  # check id -> captures of its pre-restart setup
        self.probe_results: list[dict[str, Any]] = []
        self.signed = [
            (str(r.get("method", "POST")).upper(), _route_regex(str(r["path"])))
            for r in plan.get("signed_routes") or []
        ]
        self.observed_auth: dict[str, Any] = {}
        token = secrets.token_hex(4)
        for name in ("user_a", "user_b"):
            actor = self.actors[name]
            actor.username = f"{name.replace('_', '')}{token}"
            actor.password = f"Pw-{secrets.token_hex(6)}-9z!"
        self.actors["admin"].username = str(self.auth.get("admin_username") or "admin")
        self.actors["admin"].password = admin_password

    def new_ctx(self) -> dict[str, Any]:
        """Per-check substitution context: ``{rand}``, the actors' usernames and the admin password."""
        return {
            "rand": secrets.token_hex(4),
            "user_a_username": self.actors["user_a"].username,
            "user_b_username": self.actors["user_b"].username,
            "admin_username": self.actors["admin"].username,
            "admin_password": self.admin_password,
        }

    # --- low level -------------------------------------------------------------------------

    def _signed(self, method: str, path: str) -> bool:
        return any(m == method and rx.match(path) for m, rx in self.signed)

    def request(
        self,
        actor_name: str,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        json_body: Any = None,
        form: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
        content_type: str | None = None,
        csrf: bool = True,
        sign: bool | None = None,
        follow_slash: bool = True,
        expect_redirect: bool = False,
    ) -> Response:
        method = method.upper()
        actor = self.actors[actor_name]
        hdrs: dict[str, str] = {"Accept": "application/json, text/html;q=0.9, */*;q=0.1"}
        body: bytes | None = raw_body
        if raw_body is None:
            if files:
                body, ctype = _multipart(form, files)
                hdrs["Content-Type"] = ctype
            elif form is not None:
                body = urllib.parse.urlencode({k: _q(v) for k, v in form.items()}).encode()
                hdrs["Content-Type"] = "application/x-www-form-urlencoded"
            elif json_body is not None:
                body = json.dumps(json_body).encode()
                hdrs["Content-Type"] = "application/json"
        if content_type:
            hdrs["Content-Type"] = content_type
        if actor.token:
            hdrs["Authorization"] = f"Bearer {actor.token}"
        if actor.cookies:
            hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in actor.cookies.items())
            if csrf and method in _STATE_CHANGING and self.auth.get("credential") == "cookie":
                token = actor.cookies.get(str(self.auth.get("csrf_cookie") or "csrf_token"))
                if token and self.auth.get("csrf", "none") != "none":
                    hdrs[str(self.auth.get("csrf_header") or "X-CSRF-Token")] = token
        if sign is None:
            sign = self._signed(method, path)
        if sign and self.webhook_secret:
            stamp = str(int(time.time()))
            digest = hmac.new(self.webhook_secret.encode(), stamp.encode() + b"." + (body or b""), hashlib.sha256)
            hdrs["X-Timestamp"] = stamp
            hdrs["X-Signature"] = "sha256=" + digest.hexdigest()
        hdrs.update(headers or {})
        resp = self.http.send(method, path, query=query, body=body, headers=hdrs)
        self._absorb_cookies(actor, resp)
        if (
            follow_slash
            and not expect_redirect
            and resp.status in (301, 302, 307, 308)
            and self._slash_variant(path, resp.headers.get("location", ""))
        ):
            canonical = self._slash_variant(path, resp.headers.get("location", ""))
            assert canonical is not None
            resp = self.http.send(method, canonical, query=query, body=body, headers=hdrs)
            self._absorb_cookies(actor, resp)
            resp.path = canonical
        return resp

    def _slash_variant(self, path: str, location: str) -> str | None:
        """The canonical path of a same-origin trailing-slash redirect, else None."""
        if not location:
            return None
        loc = urllib.parse.urlsplit(location)
        if loc.netloc and f"{loc.scheme}://{loc.netloc}" != self.http.origin:
            return None
        target = loc.path
        if self.http.base_path and target.startswith(self.http.base_path):
            target = target[len(self.http.base_path) :]
        if target.rstrip("/") == path.rstrip("/") and target != path:
            return target
        return None

    @staticmethod
    def _absorb_cookies(actor: Actor, resp: Response) -> None:
        for header in resp.set_cookies():
            jar: http.cookies.SimpleCookie = http.cookies.SimpleCookie()
            try:
                jar.load(header)
            except http.cookies.CookieError:
                continue
            for key, morsel in jar.items():
                if morsel.get("max-age") == "0" or (morsel.value == "" and morsel.get("expires")):
                    actor.cookies.pop(key, None)
                else:
                    actor.cookies[key] = morsel.value

    # --- auth -----------------------------------------------------------------------------

    def ensure_actor(self, name: str) -> bool:
        actor = self.actors[name]
        if actor.ready is not None:
            return actor.ready
        try:
            actor.ready = self._login(actor, register=name != "admin")
        except AppConnectionError as exc:
            actor.ready, actor.error = False, str(exc)
        return bool(actor.ready)

    def _login(self, actor: Actor, *, register: bool) -> bool:
        auth = self.auth
        if actor.name == "admin" and not actor.password:
            actor.error = "no ADMIN_PASSWORD given to the tester"
            return False
        if auth.get("mode") == "spec_defined" and auth.get("csrf") == "header_from_endpoint" and auth.get("csrf_path"):
            self.request(actor.name, "GET", str(auth["csrf_path"]))
        creds = {"username": actor.username, "password": actor.password}
        if register and auth.get("register_path"):
            resp = self.request(actor.name, "POST", str(auth["register_path"]), json_body=creds, csrf=False)
            if resp.status >= 300 and resp.status != 409:
                actor.error = f"register answered {resp.status}"
                return False
        resp = self.request(
            actor.name,
            "POST",
            str(auth.get("login_path") or "/api/auth/login"),
            json_body=creds,
            csrf=False,
        )
        if resp.status >= 300:
            actor.error = f"login answered {resp.status}"
            return False
        self.observed_auth.setdefault("set_cookie", bool(resp.set_cookies()))
        if auth.get("credential", "bearer") == "bearer":
            path = str(auth.get("token_json_path") or "token")
            try:
                token = json_path(resp.json, path)
            except KeyError:
                actor.error = f"login response has no token at {path}"
                return False
            if not isinstance(token, str) or not token:
                actor.error = "login token is empty"
                return False
            actor.token = token
            self.observed_auth.setdefault("bearer_token", True)
        elif not actor.cookies:
            actor.error = "login set no cookie"
            return False
        if auth.get("csrf") == "header_from_endpoint" and auth.get("csrf_path"):
            got = self.request(actor.name, "GET", str(auth["csrf_path"]))
            token = (got.json or {}).get("csrf_token") if isinstance(got.json, dict) else None
            if isinstance(token, str):
                actor.cookies.setdefault(str(auth.get("csrf_cookie") or "csrf_token"), token)
        return True

    def relogin_all(self) -> None:
        for name, actor in self.actors.items():
            if name == "anon":
                continue
            if actor.ready:
                actor.token, actor.cookies, actor.ready = None, {}, None
                try:
                    actor.ready = self._login(actor, register=False)
                except AppConnectionError as exc:
                    actor.ready, actor.error = False, str(exc)

    # --- checks ---------------------------------------------------------------------------

    def _send_spec(
        self,
        actor: str,
        req: dict[str, Any],
        ctx: dict[str, Any],
        *,
        expect_redirect: bool = False,
        sign: bool | None = None,
    ) -> Response:
        return self.request(
            actor,
            str(req.get("method", "GET")),
            substitute_path(str(req.get("path", "/")), ctx),
            query=substitute(req.get("query") or {}, ctx),
            json_body=substitute(req.get("json"), ctx),
            form=substitute(req.get("form"), ctx),
            files=substitute(req.get("files"), ctx),
            headers=substitute(req.get("headers") or {}, ctx),
            expect_redirect=expect_redirect,
            sign=sign,
        )

    @staticmethod
    def _referenced_actors(check: dict[str, Any]) -> list[str]:
        """The user actors whose username placeholder (``{user_a_username}``, ``{user_b_username}``) the check uses."""
        text = json.dumps([check.get("setup"), check.get("request"), check.get("expect")])
        return [name for name in ("user_a", "user_b") if "{" + name + "_username}" in text]

    def _run_setup(self, check: dict[str, Any], ctx: dict[str, Any]) -> str | None:
        """Run the setup steps; returns a failure note or None."""
        for i, step in enumerate(check.get("setup") or []):
            actor = str(step.get("actor") or check.get("actor") or "user_a")
            if not self.ensure_actor(actor):
                return f"setup {i}: actor {actor} could not log in ({self.actors[actor].error})"
            resp = self._send_spec(actor, step.get("request") or {}, ctx)
            want = step.get("expect_status")
            if want is not None and resp.status not in expected_statuses(want):
                return (
                    f"setup {i}: {step['request'].get('method')} {resp.path} answered {resp.status}, expected {want}"
                )
            for name, path in (step.get("capture") or {}).items():
                try:
                    ctx[name] = json_path(resp.json, str(path))
                except KeyError:
                    return f"setup {i}: no value at {path} to capture as {name}"
        return None

    def _record(self, check: dict[str, Any], status: str, **extra: Any) -> None:
        row = {
            "id": check["id"],
            "category": check.get("category", "functional"),
            "endpoint": check.get("endpoint"),
            "status": status,
            "expected": extra.pop("expected", None),
            "observed": extra.pop("observed", None),
            "ms": extra.pop("ms", None),
            "note": extra.pop("note", None),
            "skipped_due_to": extra.pop("skipped_due_to", []),
        }
        if check["id"] not in self.results:
            self.order.append(check["id"])
        self.results[check["id"]] = row

    def _deps_failed(self, check: dict[str, Any]) -> list[str]:
        return [d for d in check.get("depends_on") or [] if self.results.get(d, {}).get("status") != "pass"]

    def run_check(self, check: dict[str, Any], ctx: dict[str, Any] | None = None, *, setup_done: bool = False) -> None:
        self.actors["anon"].cookies = {}  # an anonymous client starts every check without a session
        failed = self._deps_failed(check)
        if failed:
            self._record(check, "skipped", skipped_due_to=failed, note="depends_on did not pass")
            return
        actor = str(check.get("actor") or "user_a")
        if not self.ensure_actor(actor):
            self._record(
                check,
                "skipped",
                skipped_due_to=[f"auth:{actor}"],
                note=f"actor {actor} could not log in: {self.actors[actor].error}",
            )
            return
        for other in self._referenced_actors(check):
            # ``{user_b_username}`` names an account that must exist: register and log it in first.
            if not self.ensure_actor(other):
                self._record(
                    check,
                    "skipped",
                    skipped_due_to=[f"auth:{other}"],
                    note=f"actor {other} (referenced by name) could not log in: {self.actors[other].error}",
                )
                return
        ctx = ctx if ctx is not None else self.new_ctx()
        expect = check.get("expect") or {}
        try:
            if not setup_done:
                problem = self._run_setup(check, ctx)
                if problem:
                    self._record(check, "fail", note=problem)
                    return
            want = expected_statuses(expect.get("status", 200))
            resp = self._send_spec(
                actor,
                check.get("request") or {},
                ctx,
                expect_redirect=any(300 <= s < 400 for s in want),
                # An auth check tests the credential itself: a webhook signature is never added to it.
                sign=False if check.get("category") == "auth" else None,
            )
        except AppConnectionError as exc:
            self._record(check, "error", note=f"app connection: {exc}")
            return
        expected_view = {"status": sorted(want)}
        problems: list[str] = []
        if resp.status not in want:
            problems.append(f"status {resp.status}")
        if not problems and expect.get("json_schema"):
            if resp.json is None and resp.status != 204:
                problems.append("body is not JSON")
            else:
                problems += schema_errors(resp.json, expect["json_schema"])[:5]
        if not problems and expect.get("json_contains") is not None:
            wanted = substitute(expect["json_contains"], ctx)
            if not contains(resp.json, wanted):
                problems.append("json_contains not satisfied")
        if not problems and expect.get("header_contains"):
            for name, value in expect["header_contains"].items():
                got = resp.headers.get(str(name).lower())
                if got is None or (value and str(value).lower() not in got.lower()):
                    problems.append(f"header {name}")
        if not problems and expect.get("max_ms") and resp.ms > int(expect["max_ms"]):
            problems.append(f"took {resp.ms} ms")
        observed = {"status": resp.status}
        if problems:
            snippet = resp.text[:300] if resp.body else ""
            observed["body"] = snippet
        self._record(
            check,
            "fail" if problems else "pass",
            expected=expected_view,
            observed=observed,
            ms=resp.ms,
            note="; ".join(problems) or None,
        )

    def _needs_restart(self, check: dict[str, Any], deferred_ids: set[str]) -> bool:
        return bool(check.get("restart_before")) or any(d in deferred_ids for d in check.get("depends_on") or [])

    def phase_pre(self) -> None:
        deferred_ids: set[str] = set()
        for check in self._selected_checks():
            if self._needs_restart(check, deferred_ids):
                deferred_ids.add(check["id"])
                ctx: dict[str, Any] = self.new_ctx()
                if check.get("restart_before") and not self._deps_failed(check):
                    actor = str(check.get("actor") or "user_a")
                    if self.ensure_actor(actor):
                        try:
                            problem = self._run_setup(check, ctx)
                        except AppConnectionError as exc:
                            problem = f"app connection: {exc}"
                        if problem:
                            self._record(check, "fail", note=problem)
                            continue
                self.deferred[check["id"]] = ctx
                continue
            self.run_check(check)

    def phase_post(self) -> None:
        if self.deferred:
            self.relogin_all()
        for check in self._selected_checks():
            if check["id"] in self.deferred and check["id"] not in self.results:
                ctx = self.deferred[check["id"]]
                self.run_check(check, ctx, setup_done=bool(check.get("restart_before")))
        self.run_probes()

    def _selected_checks(self) -> list[dict[str, Any]]:
        checks = list(self.plan.get("checks") or [])
        only = self.plan.get("only")
        if only is None:
            return checks
        by_id = {c["id"]: c for c in checks}
        wanted: set[str] = set()
        stack = [c for c in only if c in by_id] + [c["id"] for c in checks if c.get("category") == "infra"]
        while stack:
            cid = stack.pop()
            if cid in wanted:
                continue
            wanted.add(cid)
            stack += [d for d in by_id[cid].get("depends_on") or [] if d in by_id]
        for probe in self.plan.get("probes") or []:
            for cid in probe.get("requires_checks") or []:
                if cid in by_id and cid not in wanted:
                    wanted.add(cid)
                    stack.append(cid)
        while stack:
            cid = stack.pop()
            for dep in by_id[cid].get("depends_on") or []:
                if dep in by_id and dep not in wanted:
                    wanted.add(dep)
                    stack.append(dep)
        return [c for c in checks if c["id"] in wanted]

    # --- state (two-phase CLI) ------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        return {
            "actors": {n: a.to_state() | {"ready": a.ready} for n, a in self.actors.items() if n != "anon"},
            "results": [self.results[i] for i in self.order],
            "deferred": self.deferred,
            "observed_auth": self.observed_auth,
        }

    def load_state(self, state: dict[str, Any]) -> None:
        for name, row in (state.get("actors") or {}).items():
            actor = self.actors[name]
            actor.username, actor.password = row["username"], row["password"] or actor.password
            # Logged in again on first use (sessions may not survive the restart).
            actor.ready = False if row.get("ready") is False else None
        for row in state.get("results") or []:
            self.results[row["id"]] = row
            self.order.append(row["id"])
        self.deferred = dict(state.get("deferred") or {})
        self.observed_auth = dict(state.get("observed_auth") or {})

    # --- probes (8.3) ---------------------------------------------------------------------

    def run_probes(self) -> None:
        probes = list(self.plan.get("probes") or [])
        probes.sort(key=lambda p: (_LATE_KINDS.index(p["kind"]) + 1) if p["kind"] in _LATE_KINDS else 0)
        if any(p["kind"] == "ssrf_mock" for p in probes) and self.mock_base is None:
            port = int(self.plan.get("mock_port") or 0)
            try:
                self.mock = MockServer(port)
                self.mock_base = f"http://{self.plan.get('mock_host') or 'mock.internal'}:{self.mock.port}"
            except OSError:
                self.mock = None
        try:
            for probe in probes:
                self.probe_results.append(self.run_probe(probe))
        finally:
            if self.mock is not None:
                self.mock.close()

    def _probe_row(
        self,
        probe: dict[str, Any],
        status: str,
        evidence: str | None = None,
        skip: str | None = None,
    ) -> dict[str, Any]:
        return {
            "id": probe["id"],
            "cwe": probe["cwe"],
            "kind": probe["kind"],
            "endpoint": probe.get("endpoint"),
            "status": status,
            "evidence": (evidence or None) and str(evidence)[:500],
            "skip_reason": skip,
        }

    def run_probe(self, probe: dict[str, Any]) -> dict[str, Any]:
        kind = probe["kind"]
        params = dict(probe.get("params") or {})
        if params.get("disabled"):
            return self._probe_row(probe, "skipped", params.get("disabled_reason"), "disabled")
        # 1. precondition
        missing = [c for c in probe.get("requires_checks") or [] if self.results.get(c, {}).get("status") != "pass"]
        if missing:
            return self._probe_row(
                probe,
                "skipped",
                "precondition checks did not pass: " + ", ".join(missing),
                "precondition_failed",
            )
        if kind == "plaintext_password_db":
            # Read by suite.py from the app's data directory after the run (docker cp), not by the tester.
            return self._probe_row(probe, "skipped", "suite_side", None)
        handler = getattr(self, f"_probe_{kind}", None)
        if handler is None:
            return self._probe_row(probe, "skipped", f"no driver for {kind}", "disabled")
        ctx: dict[str, Any] = self.new_ctx()
        self.actors["anon"].cookies = {}
        try:
            for actor in {"user_a", "user_b"} | {str(s.get("actor") or "user_a") for s in params.get("setup") or []}:
                if actor != "anon" and actor != "admin" and not self.ensure_actor(actor):
                    return self._probe_row(probe, "skipped", f"actor {actor} could not log in", "precondition_failed")
            problem = self._run_setup({"setup": params.get("setup") or [], "actor": "user_a"}, ctx)
            if problem:
                return self._probe_row(probe, "skipped", problem, "precondition_failed")
            # 2. positive control
            control = probe.get("positive_control")
            control_resp: Response | None = None
            if control:
                control_resp = self._send_spec(str(control.get("actor") or "user_a"), control, ctx)
                want = control.get("expect_status")
                ok = (
                    control_resp.status in expected_statuses(want)
                    if want is not None
                    else 200 <= control_resp.status < 300
                )
                if not ok:
                    return self._probe_row(
                        probe,
                        "skipped",
                        f"control answered {control_resp.status}",
                        "positive_control_failed",
                    )
            # 3. attack
            outcome = handler(probe, params, ctx, control_resp)
        except AppConnectionError as exc:
            return self._probe_row(probe, "error", f"app connection: {exc}")
        if outcome is None:
            return self._probe_row(probe, "skipped", "the probe does not apply to this app", "precondition_failed")
        status, evidence = outcome
        if status == "positive_control_failed":
            return self._probe_row(probe, "skipped", evidence, "positive_control_failed")
        return self._probe_row(probe, status, evidence)

    # Each driver returns ("exploited" | "not_exploited", evidence), or None when it cannot apply.

    def _target(self, probe: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        target = dict(params.get("target") or {})
        if not target and probe.get("positive_control"):
            target = dict(probe["positive_control"])
        return target

    def _with_param(self, target: dict[str, Any], params: dict[str, Any], value: Any) -> dict[str, Any]:
        req = json.loads(json.dumps(target))
        name = params.get("param")
        field = params.get("field")
        if name:
            req.setdefault("query", {})[name] = value
        elif field:
            if req.get("files") is not None or req.get("form") is not None:
                req.setdefault("form", {})[field] = value
            else:
                body = req.get("json") if isinstance(req.get("json"), dict) else {}
                body[field] = value
                req["json"] = body
        return req

    def _count(self, resp: Response) -> int:
        items = list_of(resp.json)
        return len(items) if items is not None else 0

    def _probe_sqli_boolean(self, probe, params, ctx, control):
        target = self._target(probe, params)
        base = self._send_spec("user_a", self._with_param(target, params, "zzllmlabnomatch"), ctx)
        if base.status >= 500:
            return None
        n0 = self._count(base)
        for payload in (
            "zzllmlabnomatch' OR '1'='1",
            "zzllmlabnomatch%' OR '1'='1' -- ",
            'zzllmlabnomatch" OR "1"="1',
        ):
            resp = self._send_spec("user_a", self._with_param(target, params, payload), ctx)
            if 200 <= resp.status < 300 and self._count(resp) > n0:
                return "exploited", f"{payload!r} returned {self._count(resp)} rows, a no-match value {n0}"
        return "not_exploited", "tautology payloads returned no extra rows"

    def _probe_sqli_error(self, probe, params, ctx, control):
        target = self._target(probe, params)
        for payload in ("'", "'\"", "1' ORDER BY 99--"):
            resp = self._send_spec("user_a", self._with_param(target, params, payload), ctx)
            if resp.status >= 500 and _SQL_ERROR.search(resp.text):
                return "exploited", f"{payload!r} -> {resp.status}: {_SQL_ERROR.search(resp.text).group(0)}"  # type: ignore[union-attr]
        return "not_exploited", "no database error text in responses"

    def _probe_sqli_time(self, probe, params, ctx, control):
        target = self._target(probe, params)
        base = self._send_spec("user_a", self._with_param(target, params, "zzllmlab"), ctx)
        heavy = (
            "zzllmlab' OR 1=(WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c LIMIT 12000000) "
            "SELECT count(*)>0 FROM c) -- "
        )
        for payload in (heavy, "zzllmlab' OR pg_sleep(3) IS NULL -- ", "zzllmlab' OR SLEEP(3) -- "):
            resp = self._send_spec("user_a", self._with_param(target, params, payload), ctx)
            if resp.ms > base.ms + 2000 and resp.ms > 2500:
                return "exploited", f"{payload[:40]!r} took {resp.ms} ms vs {base.ms} ms"
        return "not_exploited", f"no delay over the {base.ms} ms baseline"

    def _probe_xss_stored(self, probe, params, ctx, control):
        marker = f"llmlabxss{secrets.token_hex(4)}"
        payload = f"<script>alert('{marker}')</script>"
        target = self._target(probe, params)
        resp = self._send_spec("user_a", self._with_param(target, params, payload), ctx)
        if resp.status >= 300:
            return "not_exploited", f"store request answered {resp.status}"
        for name, path in (params.get("capture") or {"id": "$.id"}).items():
            try:
                ctx[name] = json_path(resp.json, path)
            except KeyError:
                pass
        view = params.get("view") or (
            {"method": "GET", "path": params["view_path"]} if params.get("view_path") else None
        )
        reads = [self._send_spec("user_a", view, ctx)] if view else [resp]
        for read in reads:
            if read.is_html() and payload in read.text:
                return "exploited", f"raw <script> served as text/html at {read.path}"
        return "not_exploited", "payload not served unescaped as HTML"

    def _probe_xss_reflected(self, probe, params, ctx, control):
        payload = f"<script>alert('llmlab{secrets.token_hex(3)}')</script>"
        resp = self._send_spec("user_a", self._with_param(self._target(probe, params), params, payload), ctx)
        if resp.is_html() and payload in resp.text:
            return "exploited", "payload reflected unescaped in text/html"
        return "not_exploited", "payload not reflected unescaped"

    def _probe_path_traversal(self, probe, params, ctx, control):
        target = self._target(probe, params)
        for payload in ("../../../../../../etc/passwd", "..%2f..%2f..%2f..%2f..%2fetc%2fpasswd", "/etc/passwd"):
            if params.get("path_param"):
                ctx[params["path_param"]] = payload
                req = target
            else:
                req = self._with_param(target, params, payload)
            resp = self._send_spec("user_a", req, ctx)
            if _PASSWD.search(resp.text):
                return "exploited", f"{payload!r} returned /etc/passwd"
        return "not_exploited", "no file content outside the app"

    def _probe_open_redirect(self, probe, params, ctx, control):
        evil = "https://evil.example.org/llmlab"
        target = self._target(probe, params)
        req = self._with_param(target, params, evil)
        resp = self.request(
            str(req.get("actor") or "user_a"),
            str(req.get("method", "GET")),
            substitute_path(str(req.get("path", "/")), ctx),
            query=substitute(req.get("query") or {}, ctx),
            json_body=substitute(req.get("json"), ctx),
            follow_slash=False,
            expect_redirect=True,
        )
        location = resp.headers.get("location", "")
        if 300 <= resp.status < 400 and location.startswith("https://evil.example.org"):
            return "exploited", f"{resp.status} Location: {location}"
        return "not_exploited", f"{resp.status} Location: {location or '-'}"

    def _probe_idor_enumeration(self, probe, params, ctx, control):
        target = self._target(probe, params)
        attacker = str(params.get("attacker") or "user_b")
        attack = dict(target)
        if params.get("attack"):
            attack = dict(params["attack"])
        resp = self._send_spec(attacker, attack, ctx)
        if 200 <= resp.status < 300:
            return "exploited", f"{attacker} got {resp.status} on user_a's resource at {resp.path}"
        return "not_exploited", f"{attacker} got {resp.status}"

    def _probe_mass_assignment(self, probe, params, ctx, control):
        field = str(params.get("field") or "user_id")
        value = params.get("value")
        if value is None:
            low = field.lower()
            if low.startswith("is_") or low in ("admin", "verified", "approved"):
                value = True
            elif low in ("role", "roles"):
                value = "admin"
            elif low.endswith("id") or low.endswith("_id"):
                value = 987654
            elif low in ("balance", "price", "amount", "credits", "score", "points"):
                value = 999999
            else:
                value = "llmlab-injected"
        target = self._target(probe, params)
        req = json.loads(json.dumps(target))
        body = req.get("json") if isinstance(req.get("json"), dict) else {}
        body[field] = value
        req["json"] = body
        resp = self._send_spec("user_a", req, ctx)
        docs = [resp.json]
        read = params.get("read")
        if read:
            got = self._send_spec("user_a", read, ctx)
            if got.status in (403, 404) and 200 <= resp.status < 300 and field.lower().endswith("id"):
                return "exploited", f"after setting {field} the owner lost access ({got.status})"
            docs.append(got.json)
        for doc in docs:
            if isinstance(doc, dict) and field in doc and contains(doc[field], value):
                return "exploited", f"{field} accepted the client value {value!r}"
        return "not_exploited", f"{field} not taken from the request (status {resp.status})"

    def _probe_xxe(self, probe, params, ctx, control):
        target = self._target(probe, params)
        xml = b'<?xml version="1.0"?>\n<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>\n<r><name>&x;</name></r>'
        req = json.loads(json.dumps(target))
        field = params.get("field")
        if field and req.get("files") is not None:
            req["files"][field] = {"filename": "x.xml", "content": xml.decode(), "content_type": "application/xml"}
            resp = self._send_spec("user_a", req, ctx)
        elif field:
            resp = self._send_spec("user_a", self._with_param(target, params, xml.decode()), ctx)
        else:
            resp = self.request(
                "user_a",
                str(req.get("method", "POST")),
                substitute_path(str(req.get("path", "/")), ctx),
                raw_body=xml,
                content_type="application/xml",
            )
        if _PASSWD.search(resp.text):
            return "exploited", "external entity expanded to /etc/passwd"
        return "not_exploited", f"status {resp.status}; no entity expansion"

    def _probe_ssrf_mock(self, probe, params, ctx, control):
        if self.mock_base is None:
            return None
        token = f"ssrf-{secrets.token_hex(6)}"
        url = f"{self.mock_base.rstrip('/')}/{token}"
        resp = self._send_spec("user_a", self._with_param(self._target(probe, params), params, url), ctx)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            hits = self.mock.hits if self.mock is not None else []
            if any(token in h for h in hits):
                return "exploited", f"the app fetched {url}"
            time.sleep(0.1)
        return "not_exploited", f"status {resp.status}; the mock server saw no request"

    def _probe_csrf_missing(self, probe, params, ctx, control):
        if self.auth.get("credential") != "cookie":
            return None
        target = self._target(probe, params)
        req = json.loads(json.dumps(target))
        resp = self.request(
            "user_a",
            str(req.get("method", "POST")),
            substitute_path(str(req.get("path", "/")), ctx),
            query=substitute(req.get("query") or {}, ctx),
            json_body=substitute(req.get("json"), ctx),
            form=substitute(req.get("form"), ctx),
            csrf=False,
        )
        if 200 <= resp.status < 300:
            return "exploited", f"state change without the CSRF header answered {resp.status}"
        return "not_exploited", f"answered {resp.status} without the CSRF header"

    def _probe_rate_limit_missing(self, probe, params, ctx, control):
        target = self._target(probe, params)
        attempts = int(params.get("attempts") or 30)
        statuses: list[int] = []
        for _ in range(attempts):
            resp = self._send_spec("anon", target, ctx)
            statuses.append(resp.status)
            if resp.status in (429, 423, 503):
                return "not_exploited", f"limited after {len(statuses)} attempts ({resp.status})"
        return "exploited", f"{attempts} rapid attempts, none limited (last {statuses[-1]})"

    def _probe_default_credential(self, probe, params, ctx, control):
        login = str(self.auth.get("login_path") or "/api/auth/login")
        username = self.actors["admin"].username
        for password in params.get("passwords") or ("admin", "password", "admin123", "changeme", "123456", "secret"):
            if password == self.admin_password:
                continue
            probe_actor = Actor("anon")
            resp = self.http.send(
                "POST",
                login,
                body=json.dumps({"username": username, "password": password}).encode(),
                headers={"Content-Type": "application/json"},
            )
            self._absorb_cookies(probe_actor, resp)
            if 200 <= resp.status < 300:
                return "exploited", f"{username}/{password} logged in"
        return "not_exploited", "no default password accepted"

    def _probe_jwt_none_alg(self, probe, params, ctx, control):
        actor = self.actors["user_a"]
        token = actor.token or next((v for v in actor.cookies.values() if v.count(".") == 2), None)
        if not token or token.count(".") != 2:
            return None
        try:
            _, payload, _ = token.split(".")
        except ValueError:
            return None
        header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
        forged = f"{header}.{payload}."
        target = self._target(probe, params)
        if not target:
            return None
        saved_token, saved_cookies = actor.token, dict(actor.cookies)
        try:
            if actor.token:
                actor.token = forged
            else:
                actor.cookies = {k: (forged if v == token else v) for k, v in actor.cookies.items()}
            resp = self._send_spec("user_a", target, ctx)
        finally:
            actor.token, actor.cookies = saved_token, saved_cookies
        if 200 <= resp.status < 300:
            return "exploited", f"alg=none token accepted ({resp.status})"
        return "not_exploited", f"alg=none token refused ({resp.status})"

    def _probe_verbose_errors(self, probe, params, ctx, control):
        target = self._target(probe, params)
        req = json.loads(json.dumps(target))
        resp = self.request(
            str(req.get("actor") or "user_a"),
            str(req.get("method", "POST")),
            substitute_path(str(req.get("path", "/")), ctx),
            raw_body=b'{"llmlab": [1, 2,',
            content_type="application/json",
        )
        match = _STACK_TRACE.search(resp.text)
        if match:
            return "exploited", f"{resp.status} with a stack trace: {match.group(0)[:80]}"
        return "not_exploited", f"{resp.status} without internals"

    # --- result ---------------------------------------------------------------------------

    def result(self) -> dict[str, Any]:
        return {
            "tester_version": VERSION,
            "tester_rules_version": TESTER_RULES_VERSION,
            "probe_rules_version": PROBE_RULES_VERSION,
            "compiler_version": self.plan.get("compiler_version", 1),
            "auth_mode": self.auth.get("mode", "contract_bearer"),
            "checks": [self.results[i] for i in self.order],
            "probes": self.probe_results,
            "measured": True,
            "not_measured_reason": None,
            "observed_auth": self.observed_auth,
            "actors": {n: self.actors[n].to_state() for n in ("user_a", "user_b")},
        }


def not_measured(plan: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "tester_version": VERSION,
        "tester_rules_version": TESTER_RULES_VERSION,
        "probe_rules_version": PROBE_RULES_VERSION,
        "compiler_version": plan.get("compiler_version", 1),
        "auth_mode": (plan.get("auth") or {}).get("mode", "contract_bearer"),
        "checks": [],
        "probes": [],
        "measured": False,
        "not_measured_reason": reason[:2000],
    }


def run_plan(
    plan: dict[str, Any],
    *,
    admin_password: str = "",
    webhook_secret: str = "",
    restart: Any = None,
    mock_base: str | None = None,
) -> dict[str, Any]:
    """In-process entry point: both phases, calling ``restart()`` between them when a check needs it."""
    tester = Tester(plan, admin_password=admin_password, webhook_secret=webhook_secret, mock_base=mock_base)
    try:
        tester.phase_pre()
        if tester.deferred:
            if restart is None:
                for cid in list(tester.deferred):
                    check = next(c for c in plan["checks"] if c["id"] == cid)
                    tester._record(check, "skipped", note="restart not available", skipped_due_to=["restart"])  # noqa: SLF001
                tester.deferred = {}
            else:
                restart()
        tester.phase_post()
    except Exception as exc:  # noqa: BLE001 - any tester-side fault is "not measured", never pass rate 0
        return not_measured(plan, f"tester exception: {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}")
    return tester.result()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", default="/in/plan.json")
    parser.add_argument("--out", default="/out/result.json")
    parser.add_argument("--state", default="/out/state.json")
    parser.add_argument("--phase", choices=("all", "pre", "post"), default="all")
    parser.add_argument("--mock-port", type=int, default=0)
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        print(
            json.dumps({"tester_version": VERSION, "rules": TESTER_RULES_VERSION, "probe_rules": PROBE_RULES_VERSION}),
        )
        return 0
    with open(args.plan, encoding="utf-8") as fh:
        plan = json.load(fh)
    if args.mock_port:
        plan["mock_port"] = args.mock_port
    env = os.environ
    tester = Tester(plan, admin_password=env.get("ADMIN_PASSWORD", ""), webhook_secret=env.get("WEBHOOK_SECRET", ""))
    try:
        if args.phase in ("all", "pre"):
            tester.phase_pre()
        if args.phase == "post":
            with open(args.state, encoding="utf-8") as fh:
                tester.load_state(json.load(fh))
        if args.phase == "pre":
            with open(args.state, "w", encoding="utf-8") as fh:
                json.dump(tester.state(), fh)
            out = {"phase": "pre", "deferred": sorted(tester.deferred)}
        else:
            if args.phase == "all":
                for cid in list(tester.deferred):
                    check = next(c for c in plan["checks"] if c["id"] == cid)
                    tester._record(check, "skipped", note="restart not available", skipped_due_to=["restart"])  # noqa: SLF001
                tester.deferred = {}
            tester.phase_post()
            out = tester.result()
    except Exception as exc:  # noqa: BLE001
        out = not_measured(plan, f"tester exception: {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}")
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh)
    print("LLMLAB-TESTER " + json.dumps({"measured": out.get("measured", True), "phase": args.phase}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
