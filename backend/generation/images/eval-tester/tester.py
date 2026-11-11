#!/usr/bin/env python3
"""eval-tester v2: runs a compiled ``AcceptancePlan`` against a started sample (spec sections 8.1, 8.3-8.5).

Standard library only (``http.client``), so the same file runs in the ``eval-tester`` image and in-process
in the backend tests (``backend.generation.evaluation.tester_module()``).

CLI (inside the image)::

    tester.py --plan /in/plan.json --out /out/result.json --phase all|pre|post [--state /out/state.json]
              [--mock-port 9009]

Secrets come from the environment (``ADMIN_PASSWORD``, ``WEBHOOK_SECRET``), never from the plan.

Placeholders (v2). ``{name}`` in a request path, query, body, file spec, header or ``expect.json_contains`` is
replaced by, in this order: a value captured by a setup step, an actor value (``{user_a_username}``,
``{user_b_password}``, ``{user_c_username}``, ``{admin_username}`` ...), ``{upstream_base}`` / ``{mock_base}``, or
a generator derived from the context's ``{rand}`` (``generated()``: ``{digitsN}``, ``{alpha}``, ``{uuid}``,
``{isbn13}``, ``{isbn10}``, dates and times relative to the run, small valid files as base64). An unknown name
is left as written. One check (or probe) is one context; a setup step with ``fresh: true`` renders its request
with a step-local ``rand`` (its captures still land in the check's context).

Mock upstream (v2). When the plan carries ``upstream`` (``{"hosts": [...], "port": 80}``), the tester serves a
small "public web site" (``UpstreamServer``) for the whole run, in both phases, and substitutes
``{upstream_base}``. A failed self-check of that server is the platform's fault: ``measured=false``.

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
  control request fails is ``skipped(positive_control_failed)``; a probe that does not apply to the plan (a
  CSRF probe under a bearer credential) is ``skipped(not_applicable)``; a probe whose attack could not be
  observed (the view of a stored payload is not an HTML page, the endpoint never redirects, sign-in was
  limited before every candidate was tried) is ``skipped(not_measurable)``. Only a probe that reached its
  attack *and* could have seen it succeed is ``exploited`` or ``not_exploited``. No severity is assigned.
* a probe precondition (``requires_checks``) asks whether the endpoint works, not whether it conforms: a
  required check that failed only on its exact success status or on its response schema (it was answered
  2xx where 2xx was expected) still lets the probe run; the probe's own positive control then decides.

Tolerances (``TESTER_RULES_VERSION``): one same-origin trailing-slash redirect is followed by retrying the
canonical path (any method); an expected 400 or 422 accepts either; 413 from the framework or the app is
the same 413; wrong-method behaviour (405 vs 404) is never checked. Rules version 2: ``type: integer``
follows JSON Schema (a number with a zero fractional part, ``31.0``, is an integer); after a successful
request that carried the acting account's current password and a ``new_password``, the tester goes on with
the new password (it logs the account in again). Rules version 3: an ``<endpoint>.invalid.*`` check that
was answered with a refusal is credited only when the refusal can be attributed to the fault it sent. When
the endpoint's own ``<endpoint>.ok`` check (the same request without the fault) was already answered with
one of the refusal statuses the ``.invalid`` check accepts, the pass says nothing about the fault: the
check is ``skipped`` (``skipped_due_to`` names the ``.ok`` check) and counts like every skipped check.
"""

from __future__ import annotations

import argparse
import base64
import datetime
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
import zlib
from typing import Any

VERSION = 2
TESTER_RULES_VERSION = 3
PROBE_RULES_VERSION = 2

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
# Probes that change or lock shared state run last. ``default_credential`` runs before ``rate_limit_missing``:
# the rate-limit probe exhausts the sign-in budget of the client on purpose, and a limited sign-in (429) says
# nothing about whether a default password would have been accepted.
_LATE_KINDS = ("mass_assignment", "default_credential", "rate_limit_missing")
# Statuses by which an app tells a client it is being limited or locked out.
_LIMITED = (429, 423)


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
    """Replace ``{name}`` placeholders in every string of ``value``.

    The exact string ``"{name}"`` of a captured value keeps the captured type (an integer id stays an
    integer). Inside a longer string every resolvable name is replaced by its text (``resolve``); an
    unknown name is left as written. Paths use ``substitute_path`` (percent-encoding)."""
    if isinstance(value, str):
        if "{" not in value:
            return value
        m = _PLACEHOLDER.fullmatch(value)
        if m:
            found = resolve(m.group(1), ctx)
            return value if found is _MISSING else found

        def repl(m: re.Match[str]) -> str:
            found = resolve(m.group(1), ctx)
            if found is _MISSING or isinstance(found, (dict, list)) or found is None:
                return m.group(0)
            return _q(found)

        return _PLACEHOLDER.sub(repl, value)
    if isinstance(value, list):
        return [substitute(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, ctx) for k, v in value.items()}
    return value


def substitute_path(path: str, ctx: dict[str, Any]) -> str:
    def repl(m: re.Match[str]) -> str:
        found = resolve(m.group(1), ctx)
        if found is _MISSING or m.group(1) == "oversize" or m.group(1) in _BIG_FILES:
            return m.group(0)
        return urllib.parse.quote(str(found), safe="")

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
    # JSON Schema: any number with a zero fractional part is an integer (``31.0``); rules version 2.
    "integer": lambda v: (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and v.is_integer()),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def schema_errors(value: Any, schema: Any, where: str = "$") -> list[str]:
    """A JSON Schema subset: type, properties, required, items, enum, const, min/maxLength, minimum, maximum,
    min/maxItems, format date/date-time, anyOf/oneOf/allOf. Unknown keywords are ignored."""
    if isinstance(schema, list) and schema and all(isinstance(t, str) for t in schema):
        schema = {"type": schema}  # the task specs' shorthand: a bare list of type names (``[integer, 'null']``)
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
# Generators (v2): placeholder values derived from a context's ``rand``
# =====================================================================================

# Small valid files, as base64 literals (so the bytes never depend on a library build).
# PNG: 64x64 RGB, solid colour, 136 bytes.
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAT0lEQVR42u3PQQkAAAgEsGtkNBubxQi+hcEKLNXzWgQEBAQEBAQE"
    "BAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQELgvUEsFagxoY5QAAAABJRU5ErkJggg=="
)
# JPEG: 16x16 baseline JFIF, three components, solid grey, 349 bytes.
JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ"
    "EBAQEBAQEBAQEBAQEBD/wAARCAAQABADAREAAhEAAxEA/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgED"
    "AwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdI"
    "SUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW"
    "19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oADAMBAAIAAwAAPwAooooooooooor/2Q=="
)
# GIF: 1x1 GIF89a, 42 bytes.
GIF_B64 = "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
# PDF: version 1.4, one 200x200 pt page with the text "llmlab test document", 595 bytes.
PDF_B64 = (
    "JVBERi0xLjQKMSAwIG9iago8PCAvVHlwZSAvQ2F0YWxvZyAvUGFnZXMgMiAwIFIgPj4KZW5kb2JqCjIgMCBvYmoKPDwgL1R5cGUg"
    "L1BhZ2VzIC9LaWRzIFszIDAgUl0gL0NvdW50IDEgPj4KZW5kb2JqCjMgMCBvYmoKPDwgL1R5cGUgL1BhZ2UgL1BhcmVudCAyIDAg"
    "UiAvTWVkaWFCb3ggWzAgMCAyMDAgMjAwXSAvQ29udGVudHMgNCAwIFIgL1Jlc291cmNlcyA8PCAvRm9udCA8PCAvRjEgNSAwIFIg"
    "Pj4gPj4gPj4KZW5kb2JqCjQgMCBvYmoKPDwgL0xlbmd0aCA1MSA+PgpzdHJlYW0KQlQgL0YxIDEyIFRmIDIwIDEwMCBUZCAobGxt"
    "bGFiIHRlc3QgZG9jdW1lbnQpIFRqIEVUCmVuZHN0cmVhbQplbmRvYmoKNSAwIG9iago8PCAvVHlwZSAvRm9udCAvU3VidHlwZSAv"
    "VHlwZTEgL0Jhc2VGb250IC9IZWx2ZXRpY2EgPj4KZW5kb2JqCnhyZWYKMCA2CjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDAw"
    "OSAwMDAwMCBuIAowMDAwMDAwMDU4IDAwMDAwIG4gCjAwMDAwMDAxMTUgMDAwMDAgbiAKMDAwMDAwMDI0MSAwMDAwMCBuIAowMDAw"
    "MDAwMzQyIDAwMDAwIG4gCnRyYWlsZXIKPDwgL1NpemUgNiAvUm9vdCAxIDAgUiA+PgpzdGFydHhyZWYKNDEyCiUlRU9GCg=="
)
# WAV: RIFF/WAVE PCM, mono, 8 kHz, 8 bit, 800 samples (0.1 s), 844 bytes.
WAV_B64 = (
    "UklGRkQDAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YSADAACgoKCgoKCgoKCgYGBgYGBgYGBgYKCgoKCgoKCgoKBg"
    "YGBgYGBgYGBgoKCgoKCgoKCgoGBgYGBgYGBgYGCgoKCgoKCgoKCgYGBgYGBgYGBgYKCgoKCgoKCgoKBgYGBgYGBgYGBgoKCgoKCg"
    "oKCgoGBgYGBgYGBgYGCgoKCgoKCgoKCgYGBgYGBgYGBgYKCgoKCgoKCgoKBgYGBgYGBgYGBgoKCgoKCgoKCgoGBgYGBgYGBgYGCg"
    "oKCgoKCgoKCgYGBgYGBgYGBgYKCgoKCgoKCgoKBgYGBgYGBgYGBgoKCgoKCgoKCgoGBgYGBgYGBgYGCgoKCgoKCgoKCgYGBgYGBg"
    "YGBgYKCgoKCgoKCgoKBgYGBgYGBgYGBgoKCgoKCgoKCgoGBgYGBgYGBgYGCgoKCgoKCgoKCgYGBgYGBgYGBgYKCgoKCgoKCgoKBg"
    "YGBgYGBgYGBgoKCgoKCgoKCgoGBgYGBgYGBgYGCgoKCgoKCgoKCgYGBgYGBgYGBgYKCgoKCgoKCgoKBgYGBgYGBgYGBgoKCgoKCg"
    "oKCgoGBgYGBgYGBgYGCgoKCgoKCgoKCgYGBgYGBgYGBgYKCgoKCgoKCgoKBgYGBgYGBgYGBgoKCgoKCgoKCgoGBgYGBgYGBgYGCg"
    "oKCgoKCgoKCgYGBgYGBgYGBgYKCgoKCgoKCgoKBgYGBgYGBgYGBgoKCgoKCgoKCgoGBgYGBgYGBgYGCgoKCgoKCgoKCgYGBgYGBg"
    "YGBgYKCgoKCgoKCgoKBgYGBgYGBgYGBgoKCgoKCgoKCgoGBgYGBgYGBgYGCgoKCgoKCgoKCgYGBgYGBgYGBgYKCgoKCgoKCgoKBg"
    "YGBgYGBgYGBgoKCgoKCgoKCgoGBgYGBgYGBgYGCgoKCgoKCgoKCgYGBgYGBgYGBgYKCgoKCgoKCgoKBgYGBgYGBgYGBgoKCgoKCg"
    "oKCgoGBgYGBgYGBgYGCgoKCgoKCgoKCgYGBgYGBgYGBgYKCgoKCgoKCgoKBgYGBgYGBgYGBgoKCgoKCgoKCgoGBgYGBgYGBgYGCg"
    "oKCgoKCgoKCgYGBgYGBgYGBgYA=="
)
_FILES = {"png_b64": PNG_B64, "jpeg_b64": JPEG_B64, "gif_b64": GIF_B64, "pdf_b64": PDF_B64, "wav_b64": WAV_B64}
# Valid but oversized files (``.oversize`` checks of upload endpoints): the size is the only thing wrong with
# them, so an app that looks at the format before the size still has to answer 413.
OVERSIZE_FILE_BYTES = 9 * 1024 * 1024  # payload bytes added to the small file (about 12 MiB as base64)
_BIG_FILES = ("png_oversize_b64", "wav_oversize_b64")
_big_cache: dict[str, str] = {}


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(kind + data) & 0xFFFFFFFF
    return len(data).to_bytes(4, "big") + kind + data + crc.to_bytes(4, "big")


def oversize_file(name: str) -> str:
    """Base64 of a structurally valid file of more than 9 MiB (cached; a pure function of ``name``).

    ``png_oversize_b64``: the PNG of ``{png_b64}`` with one private ancillary chunk (``juNk``, which every
    decoder skips) between ``IHDR`` and ``IDAT``: same picture, valid CRCs, 9 MiB larger.
    ``wav_oversize_b64``: a RIFF/WAVE PCM file (mono, 8 kHz, 8 bit) with 9 MiB of silence.
    """
    if name not in _big_cache:
        if name == "png_oversize_b64":
            png = base64.b64decode(PNG_B64)
            head, rest = png[:33], png[33:]  # signature + IHDR | IDAT ... IEND
            data = head + _png_chunk(b"juNk", b"\x00" + b"\xc7\x1c\x71" * (OVERSIZE_FILE_BYTES // 3)) + rest
        elif name == "wav_oversize_b64":
            samples = b"\x80" * OVERSIZE_FILE_BYTES
            fmt = b"fmt " + (16).to_bytes(4, "little") + (1).to_bytes(2, "little") + (1).to_bytes(2, "little")
            fmt += (8000).to_bytes(4, "little") + (8000).to_bytes(4, "little")
            fmt += (1).to_bytes(2, "little") + (8).to_bytes(2, "little")
            body = b"WAVE" + fmt + b"data" + len(samples).to_bytes(4, "little") + samples
            data = b"RIFF" + len(body).to_bytes(4, "little") + body
        else:
            raise KeyError(name)
        _big_cache[name] = base64.b64encode(data).decode()
    return _big_cache[name]


_MISSING: Any = object()
_DIGITS = re.compile(r"digits([1-9]|1[0-8])")
_DAY = datetime.timedelta(days=1)
_FUTURE_MIN_DAYS = 30
_FUTURE_SPAN_DAYS = 268  # {future_date} is 30..299 days ahead, {future_date2} at most 329
_PAST_SPAN_DAYS = 301  # {past_date} is 30..330 days back
_SLOTS = 15  # {time_hhmm}: 09:00, 09:30 ... 16:00; {time_hhmm2} is one hour later (at most 17:00)
_SATURDAY = 5


def _draw(rand: str, name: str) -> int:
    """A large integer that depends only on ``(rand, name)``."""
    return int.from_bytes(hashlib.sha256(f"{rand}:{name}".encode()).digest(), "big")


def _weekday(day: datetime.date) -> datetime.date:
    while day.weekday() >= _SATURDAY:
        day += _DAY
    return day


def _isbn13(rand: str) -> str:
    # 978-0-...: every 8-digit remainder lies in a defined publisher range of registration group 0.
    body = "9780" + str(_draw(rand, "isbn13") % 10**8).zfill(8)
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(body))
    return body + str((10 - total % 10) % 10)


def _isbn10(rand: str) -> str:
    """Ten digits with a valid ISBN-10 check digit; bodies whose check digit would be ``X`` are skipped."""
    attempt = 0
    while True:
        body = "0" + str(_draw(rand, f"isbn10/{attempt}") % 10**8).zfill(8)
        check = (11 - sum(int(d) * (10 - i) for i, d in enumerate(body)) % 11) % 11
        if check != 10:
            return body + str(check)
        attempt += 1


def _dates(rand: str, epoch: int) -> dict[str, str]:
    """Every date/time generator of one context (all relative to the run's ``epoch``, UTC)."""
    now = datetime.datetime.fromtimestamp(epoch, tz=datetime.UTC)
    today = now.date()
    future = _weekday(today + _DAY * (_FUTURE_MIN_DAYS + _draw(rand, "future_date") % _FUTURE_SPAN_DAYS))
    future2 = _weekday(future + _DAY * (1 + _draw(rand, "future_date2") % 28))
    past = today - _DAY * (_FUTURE_MIN_DAYS + _draw(rand, "past_date") % _PAST_SPAN_DAYS)
    minutes = 9 * 60 + 30 * (_draw(rand, "time_hhmm") % _SLOTS)
    hhmm = f"{minutes // 60:02d}:{minutes % 60:02d}"
    hhmm2 = f"{minutes // 60 + 1:02d}:{minutes % 60:02d}"
    return {
        "today": today.isoformat(),
        "now": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "future_date": future.isoformat(),
        "future_date2": future2.isoformat(),
        "past_date": past.isoformat(),
        "time_hhmm": hhmm,
        "time_hhmm2": hhmm2,
        "future_datetime": f"{future.isoformat()}T{hhmm}:00Z",
        "future_datetime2": f"{future.isoformat()}T{hhmm2}:00Z",
        "past_datetime": f"{past.isoformat()}T{hhmm}:00Z",
    }


_DATE_NAMES = frozenset(_dates("0", 0))
GENERATORS = (
    "rand",
    "digitsN",
    "alpha",
    "uuid",
    "isbn13",
    "isbn10",
    *sorted(_DATE_NAMES),
    *sorted(_FILES),
    *_BIG_FILES,
    "oversize",
)


def generated(name: str, rand: str, epoch: int = 0) -> Any:
    """The value of generator ``name`` in the context ``rand`` (``_MISSING`` when ``name`` is no generator).

    A pure function of ``(name, rand, epoch)``: one context has one stable set of values, and the pre- and
    post-restart phases of a run (same ``epoch``) agree.
    """
    if name in _FILES:
        return _FILES[name]
    if name in _BIG_FILES:
        return oversize_file(name)
    if name == "oversize":
        return "x" * OVERSIZE_BYTES
    if name in _DATE_NAMES:
        return _dates(rand, epoch)[name]
    m = _DIGITS.fullmatch(name)
    if m:
        width = int(m.group(1))
        first = str(1 + _draw(rand, name) % 9)
        return first + (str(_draw(rand, name + "/rest") % 10 ** (width - 1)).zfill(width - 1) if width > 1 else "")
    if name == "alpha":
        value = _draw(rand, name)
        return "".join(chr(97 + (value >> (5 * i)) % 26) for i in range(8))
    if name == "uuid":
        raw = bytearray(hashlib.sha256(f"{rand}:uuid".encode()).digest()[:16])
        raw[6] = (raw[6] & 0x0F) | 0x40  # version 4
        raw[8] = (raw[8] & 0x3F) | 0x80  # RFC 4122 variant
        text = raw.hex()
        return f"{text[:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:]}"
    if name == "isbn13":
        return _isbn13(rand)
    if name == "isbn10":
        return _isbn10(rand)
    return _MISSING


def resolve(name: str, ctx: dict[str, Any]) -> Any:
    """A placeholder's value in ``ctx``: a bound name (capture, actor value, ``rand``) first, then a generator."""
    if name in ctx:
        return ctx[name]
    if "rand" not in ctx and name not in _FILES and name not in _BIG_FILES and name != "oversize":
        return _MISSING
    return generated(name, str(ctx.get("rand", "")), int(ctx.get("_epoch") or 0))


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
# Mock upstream (v2): the "public web site" of outbound-fetch tasks
# =====================================================================================

UPSTREAM_SLOW_S = 10.0
UPSTREAM_BIG_BYTES = 2 * 1024 * 1024
UPSTREAM_MAX_HOPS = 10
UPSTREAM_MAX_HITS = 10_000
UPSTREAM_MAX_PATH = 8192
_UPSTREAM_SLUG = re.compile(r"[^A-Za-z0-9._-]")


def _upstream_slug(raw: str) -> str:
    """The path tail as a safe token (nothing from the request is echoed verbatim into the HTML)."""
    return _UPSTREAM_SLUG.sub("", urllib.parse.unquote(raw))[:64] or "index"


def upstream_page(base: str, slug: str, variant: str = "page") -> bytes:
    """The HTML document of ``/<variant>/<slug>`` (pure function of its arguments)."""
    title = f"Example page {slug}"
    description = f"Description of example page {slug}."
    head = [
        '<meta charset="utf-8">',
        f"<title>{title}</title>",
        f'<meta name="description" content="{description}">',
    ]
    if variant == "og":
        head += [
            f'<meta property="og:title" content="Open Graph title {slug}">',
            f'<meta property="og:description" content="Open Graph description {slug}.">',
        ]
    elif variant != "plain":
        head += [
            f'<meta property="og:title" content="{title}">',
            f'<meta property="og:description" content="{description}">',
        ]
    if variant != "plain":
        image = "/image.png" if variant == "rel" else f"{base}/image.png"
        head += [f'<meta property="og:image" content="{image}">', '<meta property="og:type" content="website">']
    html = (
        '<!doctype html>\n<html lang="en">\n<head>\n'
        + "\n".join(head)
        + f"\n</head>\n<body>\n<h1>{title}</h1>\n<p>{description}</p>\n</body>\n</html>\n"
    )
    return html.encode("utf-8")


def upstream_big(base: str) -> bytes:
    """Exactly ``UPSTREAM_BIG_BYTES`` of HTML: the ``/page/big`` head, then padding inside the body."""
    start = upstream_page(base, "big").replace(b"</body>\n</html>\n", b"<p>\n")
    end = b"\n</p>\n</body>\n</html>\n"
    line = b"0123456789abcdef" * 4 + b"\n"
    fill = UPSTREAM_BIG_BYTES - len(start) - len(end)
    return start + (line * (fill // len(line) + 1))[:fill] + end


def upstream_response(base: str, path: str) -> tuple[int, dict[str, str], bytes, float]:
    """``(status, headers, body, delay_s)`` for a GET of ``path`` (pure function of its arguments)."""
    html = {"Content-Type": "text/html; charset=utf-8"}
    route = urllib.parse.urlsplit(path).path
    head, _, tail = route.lstrip("/").partition("/")
    if route in ("", "/"):
        return 200, html, upstream_page(base, "index"), 0.0
    if head in ("page", "rel", "og", "plain") and tail:
        return 200, html, upstream_page(base, _upstream_slug(tail), head), 0.0
    if route == "/image.png":
        return 200, {"Content-Type": "image/png"}, base64.b64decode(PNG_B64), 0.0
    if route == "/json":
        body = json.dumps({"ok": True, "service": "upstream"}).encode()
        return 200, {"Content-Type": "application/json"}, body, 0.0
    if route == "/robots.txt":
        return 200, {"Content-Type": "text/plain; charset=utf-8"}, b"User-agent: *\nAllow: /\n", 0.0
    if head == "redirect":
        hops = min(int(tail), UPSTREAM_MAX_HOPS) if tail.isdigit() else 1
        target = f"{base}/redirect/{hops - 1}" if hops > 1 else f"{base}/page/redirected"
        body = f'<!doctype html>\n<title>Redirect</title>\n<a href="{target}">{target}</a>\n'.encode()
        return 302, {**html, "Location": target}, body, 0.0
    if head == "status" and tail.isdigit() and 400 <= int(tail) <= 599:
        return int(tail), html, upstream_page(base, f"status-{int(tail)}"), 0.0
    if route == "/slow":
        return 200, html, upstream_page(base, "slow"), UPSTREAM_SLOW_S
    if route == "/big":
        return 200, html, upstream_big(base), 0.0
    return 404, {"Content-Type": "text/plain; charset=utf-8"}, b"not found\n", 0.0


class UpstreamServer:
    """The "public web site" an outbound-fetch task talks to (``{upstream_base}``); records every request.

    Docker runtime: the tester container carries the plan's ``upstream.hosts`` as network aliases on a second
    internal network whose subnet looks globally routable, and listens on ``upstream.port`` (80), so ``base``
    is ``http://<hosts[0]>``. In-process (local runtime): port 0 on ``127.0.0.1``, ``base`` is
    ``http://127.0.0.1:<port>``.
    """

    def __init__(self, port: int, public_host: str = "127.0.0.1", bind: str = "0.0.0.0") -> None:  # noqa: S104
        self.hits: list[dict[str, str]] = []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "upstream"
            sys_version = ""
            timeout = 30

            def _send(self, status: int, headers: dict[str, str], body: bytes, *, with_body: bool) -> None:
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")  # one request per connection: no keep-alive state
                self.end_headers()
                if with_body:
                    self.wfile.write(body)

            def _record(self) -> None:
                if len(owner.hits) < UPSTREAM_MAX_HITS:
                    host = (self.headers.get("Host") or "")[:255]
                    owner.hits.append(
                        {"method": self.command, "host": host, "path": self.path[:UPSTREAM_MAX_PATH]},
                    )

            def _get(self) -> None:
                self._record()
                status, headers, body, delay = upstream_response(owner.base, self.path)
                if delay:
                    time.sleep(delay)
                self._send(status, headers, body, with_body=self.command != "HEAD")

            def _other(self) -> None:
                self._record()
                headers = {"Content-Type": "text/plain; charset=utf-8", "Allow": "GET, HEAD"}
                self._send(405, headers, b"method not allowed\n", with_body=True)

            do_GET = do_HEAD = _get  # noqa: N815
            do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _other  # noqa: N815

            def version_string(self) -> str:
                return "upstream"

            def log_message(self, *_args: Any) -> None:
                return

        self.server = http.server.ThreadingHTTPServer((bind, port), Handler)
        self.port = int(self.server.server_address[1])
        self.base = f"http://{public_host}" if self.port == 80 else f"http://{public_host}:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def saw(self, token: str) -> bool:
        return any(token in hit["path"] for hit in list(self.hits))

    def self_check(self, timeout_s: float = 5.0) -> str | None:
        """``None`` when the server answers under its public name from here, else what went wrong.

        Run once before the first check: in the tester container ``example.com`` must resolve to the tester
        itself (the alias on the upstream network). A failure is the platform's (``measured=false``), never a
        failed check of the sample. The self-check request is removed from ``hits`` again.
        """
        parts = urllib.parse.urlsplit(self.base)
        mark = len(self.hits)
        try:
            conn = http.client.HTTPConnection(parts.hostname or "", parts.port or 80, timeout=timeout_s)
            try:
                conn.request("GET", "/json")
                resp = conn.getresponse()
                body = resp.read()
            finally:
                conn.close()
        except (OSError, http.client.HTTPException) as exc:
            return f"{self.base} is not reachable from the tester: {type(exc).__name__}: {exc}"
        finally:
            del self.hits[mark:]
        if resp.status != 200 or b'"upstream"' not in body:
            return f"{self.base}/json answered {resp.status}, not the tester's upstream server"
        return None

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


# =====================================================================================
# Actors and auth drivers (8.4)
# =====================================================================================


_USERS = ("user_a", "user_b", "user_c")
_ACTOR_REF = re.compile(r"\{(user_[abc])_(?:username|password)\}")


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
        # user_c is only ever named in a request ({user_c_username}); it never acts as a client of a check.
        self.actors: dict[str, Actor] = {name: Actor(name) for name in ("anon", "user_a", "user_b", "user_c", "admin")}
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
        self.epoch = int(time.time())  # the run's "now": every date generator is relative to it
        self.used_slots: set[str] = set()  # future dates (and date+time slots) already handed to a context
        self.upstream: UpstreamServer | None = None
        self.upstream_base: str | None = None
        self.upstream_before = 0  # upstream requests of an earlier phase (``load_state``)
        token = secrets.token_hex(4)
        for name in _USERS:
            actor = self.actors[name]
            actor.username = f"{name.replace('_', '')}{token}"
            actor.password = f"Pw-{secrets.token_hex(6)}-9z!"
        self.actors["admin"].username = str(self.auth.get("admin_username") or "admin")
        self.actors["admin"].password = admin_password

    def new_rand(self) -> str:
        """A fresh ``rand`` whose ``{future_date}`` no earlier context of this run used.

        Tasks with capacity rules (one booking per slot, a daily limit) must not see two contexts collide by
        chance. When the future weekdays are used up, an unused date+time slot is enough; after that any value.
        """
        rand = secrets.token_hex(4)
        for key in ("future_date", "future_datetime"):
            for _ in range(64):
                value = generated(key, rand, self.epoch)
                if value not in self.used_slots:
                    self.used_slots.update({generated("future_date", rand, self.epoch), value})
                    return rand
                rand = secrets.token_hex(4)
        return rand

    def _bound(self) -> dict[str, Any]:
        """The run-level placeholder values (actors, mock hosts); refreshed before every request."""
        out: dict[str, Any] = {"_epoch": self.epoch}
        for name in _USERS:
            out[f"{name}_username"] = self.actors[name].username
            out[f"{name}_password"] = self.actors[name].password
        out["admin_username"] = self.actors["admin"].username
        out["admin_password"] = self.admin_password
        if self.upstream_base:
            out["upstream_base"] = self.upstream_base
        if self.mock_base:
            out["mock_base"] = self.mock_base.rstrip("/")
        return out

    def new_ctx(self) -> dict[str, Any]:
        """Per-check substitution context: ``{rand}`` (the seed of every generator) and the run-level values."""
        return {"rand": self.new_rand(), **self._bound()}

    def fresh_ctx(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """``ctx`` (captures included) with a new ``rand``: a ``fresh`` setup step, a probe's attack request."""
        return {**ctx, "rand": self.new_rand()}

    def start_upstream(self) -> None:
        """Serve the mock upstream of ``plan.upstream`` (both phases); a failure is ``measured=false``."""
        spec = self.plan.get("upstream")
        if not spec or self.upstream is not None:
            return
        hosts = [str(h) for h in spec.get("hosts") or []] or ["127.0.0.1"]
        try:
            self.upstream = UpstreamServer(
                int(spec.get("port", 80)),
                public_host=hosts[0],
                bind=str(spec.get("bind") or "0.0.0.0"),  # noqa: S104 - in-sandbox only
            )
        except OSError as exc:
            msg = f"mock upstream: cannot listen on port {spec.get('port', 80)}: {exc}"
            raise TesterError(msg) from exc
        problem = self.upstream.self_check()
        if problem:
            msg = f"mock upstream: {problem}"
            raise TesterError(msg)
        self.upstream_base = self.upstream.base

    def close(self) -> None:
        if self.upstream is not None:
            self.upstream.close()
            self.upstream = None

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
        ctx.update(self._bound())
        body = substitute(req.get("json"), ctx)
        resp = self.request(
            actor,
            str(req.get("method", "GET")),
            substitute_path(str(req.get("path", "/")), ctx),
            query=substitute(req.get("query") or {}, ctx),
            json_body=body,
            form=substitute(req.get("form"), ctx),
            files=substitute(req.get("files"), ctx),
            headers=substitute(req.get("headers") or {}, ctx),
            expect_redirect=expect_redirect,
            sign=sign,
        )
        self._adopt_new_password(actor, body, resp)
        return resp

    def _adopt_new_password(self, actor_name: str, body: Any, resp: Response) -> None:
        """Rules v2: a successful request that carried the acting account's current password next to a
        ``new_password`` changed that password. The tester goes on with the new one and logs the account in
        again (an app may end its sessions on a password change); if that login fails the old password is tried."""
        actor = self.actors[actor_name]
        if actor_name not in _USERS or not isinstance(body, dict) or not 200 <= resp.status < 300:
            return
        new = body.get("new_password")
        if not isinstance(new, str) or not new or new == actor.password:
            return
        if not any(v == actor.password for k, v in body.items() if k != "new_password"):
            return
        old = actor.password
        for candidate in (new, old):
            actor.password, actor.token, actor.cookies, actor.error = candidate, None, {}, None
            try:
                actor.ready = self._login(actor, register=False)
            except AppConnectionError as exc:
                actor.ready, actor.error = False, str(exc)
            if actor.ready:
                return

    @staticmethod
    def _referenced_actors(check: dict[str, Any]) -> list[str]:
        """The user actors a check (or probe) names by placeholder (``{user_b_username}``, ``{user_c_password}``)."""
        text = json.dumps(check)
        return sorted(set(_ACTOR_REF.findall(text)))

    def _run_setup(self, check: dict[str, Any], ctx: dict[str, Any]) -> str | None:
        """Run the setup steps; returns a failure note or None."""
        for i, step in enumerate(check.get("setup") or []):
            actor = str(step.get("actor") or check.get("actor") or "user_a")
            if not self.ensure_actor(actor):
                return f"setup {i}: actor {actor} could not log in ({self.actors[actor].error})"
            # ``fresh``: the request is rendered with a step-local rand (repeated creates must differ in their
            # unique fields and booked slots); what the step captures still lands in the check's context.
            step_ctx = self.fresh_ctx(ctx) if step.get("fresh") else ctx
            resp = self._send_spec(actor, step.get("request") or {}, step_ctx)
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
        for key in ("stated_in", "source", "overlay"):
            if key in check:
                row[key] = check[key]
        if check["id"] not in self.results:
            self.order.append(check["id"])
        self.results[check["id"]] = row

    def _void_unattributable(self) -> None:
        """Rules version 3: a passed ``<endpoint>.invalid.*`` whose ``<endpoint>.ok`` was itself refused with a
        status the ``.invalid`` check accepts becomes ``skipped``: the refusal cannot be attributed to the
        fault. Idempotent; only ever turns ``pass`` into ``skipped``."""
        for cid, row in self.results.items():
            stem, sep, _fault = cid.partition(".invalid.")
            if not sep or row.get("status") != "pass":
                continue
            base = self.results.get(f"{stem}.ok")
            if not base or base.get("status") != "fail":
                continue
            refused = (base.get("observed") or {}).get("status")
            accepted = (row.get("expected") or {}).get("status") or []
            if refused in accepted:
                row["status"] = "skipped"
                row["skipped_due_to"] = [f"{stem}.ok"]
                row["note"] = (
                    f"not measurable: the valid request ({stem}.ok) was already refused with {refused}, "
                    "so this refusal cannot be attributed to the invalid value"
                )

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
        want = expected_statuses(expect.get("status", 200))
        # ``also`` (compiler v2): further requests of the same check, each held to the same ``expect`` (the
        # over-long value of every length-limited field of one ``.invalid.too_long``). All are sent; the check
        # passes when every one meets the expectation.
        requests = [(check.get("label"), check.get("request") or {})]
        requests += [(extra.get("label"), extra.get("request") or {}) for extra in check.get("also") or []]
        answers: list[tuple[str | None, Response, list[str]]] = []
        try:
            if not setup_done:
                problem = self._run_setup(check, ctx)
                if problem:
                    self._record(check, "fail", note=problem)
                    return
            for label, request in requests:
                resp = self._send_spec(
                    actor,
                    request,
                    ctx,
                    expect_redirect=any(300 <= s < 400 for s in want),
                    # An auth check tests the credential itself: a webhook signature is never added to it.
                    sign=False if check.get("category") == "auth" else None,
                )
                answers.append((label, resp, self._problems(resp, expect, want, ctx)))
        except AppConnectionError as exc:
            self._record(check, "error", note=f"app connection: {exc}")
            return
        failed_answers = [(label, resp, problems) for label, resp, problems in answers if problems]
        label, resp, _ = failed_answers[0] if failed_answers else answers[0]
        observed: dict[str, Any] = {"status": resp.status}
        if failed_answers:
            observed["body"] = resp.text[:300] if resp.body else ""
        if len(requests) > 1:
            observed["requests"] = [
                {"label": lab, "status": r.status, "ok": not problems} for lab, r, problems in answers
            ]
        notes = [
            (f"{lab}: " if lab and len(requests) > 1 else "") + "; ".join(problems)
            for lab, _resp, problems in failed_answers
        ]
        self._record(
            check,
            "fail" if failed_answers else "pass",
            expected={"status": sorted(want)},
            observed=observed,
            ms=max(r.ms for _lab, r, _p in answers),
            note=" | ".join(notes) or None,
        )

    def _problems(self, resp: Response, expect: dict[str, Any], want: set[int], ctx: dict[str, Any]) -> list[str]:
        """Why ``resp`` does not meet ``expect`` (empty: it does)."""
        problems: list[str] = []
        if resp.status not in want:
            problems.append(f"status {resp.status}")
        if not problems and expect.get("json_schema"):
            if resp.json is None and resp.status != 204:
                problems.append("body is not JSON")
            else:
                problems += schema_errors(resp.json, expect["json_schema"])[:5]
        if not problems and expect.get("json_contains") is not None:
            ctx.update(self._bound())
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
        return problems

    def _needs_restart(self, check: dict[str, Any], deferred_ids: set[str]) -> bool:
        return bool(check.get("restart_before")) or any(d in deferred_ids for d in check.get("depends_on") or [])

    def phase_pre(self) -> None:
        self.start_upstream()
        deferred_ids: set[str] = set()
        for check in self._selected_checks():
            if self._needs_restart(check, deferred_ids):
                deferred_ids.add(check["id"])
                ctx: dict[str, Any] = self.new_ctx()
                if check.get("restart_before") and not self._deps_failed(check):
                    actor = str(check.get("actor") or "user_a")
                    # Accounts named by placeholder must exist before the pre-restart setup runs.
                    named = [self.ensure_actor(other) for other in self._referenced_actors(check)]
                    if self.ensure_actor(actor) and all(named):
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
        self.start_upstream()
        if self.deferred:
            self.relogin_all()
        for check in self._selected_checks():
            if check["id"] in self.deferred and check["id"] not in self.results:
                ctx = self.deferred[check["id"]]
                self.run_check(check, ctx, setup_done=bool(check.get("restart_before")))
        self._void_unattributable()
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
            "epoch": self.epoch,
            "used_slots": sorted(self.used_slots),
            "upstream_requests": self._upstream_requests(),
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
        self.epoch = int(state.get("epoch") or self.epoch)
        self.used_slots = set(state.get("used_slots") or [])
        self.upstream_before = int(state.get("upstream_requests") or 0)

    def _upstream_requests(self) -> int:
        """Requests the mock upstream saw in the whole run (the pre-restart phase is another process)."""
        return self.upstream_before + (len(self.upstream.hits) if self.upstream is not None else 0)

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
        missing = [c for c in probe.get("requires_checks") or [] if not self._reached(c)]
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
        applies = getattr(self, f"_applies_{kind}", None)
        if applies is not None:
            # Decided before the positive control, which may change state (a transfer, a created record).
            reason = applies(probe, params)
            if reason:
                return self._probe_row(probe, "skipped", f"not applicable: {reason}", "not_applicable")
        ctx: dict[str, Any] = self.new_ctx()
        self.actors["anon"].cookies = {}
        try:
            # Every account the probe acts as or names is signed in first. That includes the admin: after the
            # restart no actor holds a credential, and a request "as admin" without one is an anonymous request
            # (the control of an admin endpoint then answers 401 and the probe is lost for every app).
            actors = {"user_a", "user_b"} | {str(s.get("actor") or "user_a") for s in params.get("setup") or []}
            sent = (probe.get("positive_control"), *(params.get(k) for k in ("target", "read", "view", "attack")))
            for spec in sent:
                if isinstance(spec, dict) and spec.get("actor"):
                    actors.add(str(spec["actor"]))
            for actor in sorted(actors | set(self._referenced_actors(probe))):
                if actor != "anon" and not self.ensure_actor(actor):
                    reason = self.actors[actor].error or "login failed"
                    return self._probe_row(
                        probe,
                        "skipped",
                        f"actor {actor} could not log in ({reason})",
                        "precondition_failed",
                    )
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
                if not ok and kind == "rate_limit_missing" and control_resp.status in _LIMITED:
                    # The benign request is already refused as "too many": the limiter the probe looks for.
                    return self._probe_row(
                        probe,
                        "not_exploited",
                        f"already limited at the control request ({control_resp.status})",
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
        if status in ("positive_control_failed", "not_measurable", "not_applicable"):
            return self._probe_row(probe, "skipped", evidence, status)
        return self._probe_row(probe, status, evidence)

    def _reached(self, check_id: str) -> bool:
        """A probe precondition: the required check passed, or it failed although its endpoint answered.

        "Answered" = a 2xx status where the check expects a 2xx one: the check then failed on the exact success
        status (200 where the spec says 201) or on the response schema, neither of which says the endpoint
        cannot be probed. Whether it really works for the probe is decided by the probe's positive control.
        """
        row = self.results.get(check_id) or {}
        if row.get("status") == "pass":
            return True
        if row.get("status") != "fail":
            return False
        observed = (row.get("observed") or {}).get("status") if isinstance(row.get("observed"), dict) else None
        expected = (row.get("expected") or {}).get("status") if isinstance(row.get("expected"), dict) else None
        return (
            isinstance(observed, int)
            and 200 <= observed < 300
            and any(isinstance(code, int) and 200 <= code < 300 for code in expected or [])
        )

    # Each driver returns ("exploited" | "not_exploited", evidence); ("not_measurable", why) when the attack
    # could not have been observed (never a clean result); ("positive_control_failed", why); or None when it
    # cannot apply.

    @staticmethod
    def _actor_of(spec: dict[str, Any] | None, default: str = "user_a") -> str:
        """The account a probe request is sent as (the compiler sets ``actor`` on targets and controls)."""
        return str((spec or {}).get("actor") or default)

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
        actor = self._actor_of(target)
        base = self._send_spec(actor, self._with_param(target, params, "zzllmlabnomatch"), ctx)
        if base.status >= 500:
            return None
        n0 = self._count(base)
        for payload in (
            "zzllmlabnomatch' OR '1'='1",
            "zzllmlabnomatch%' OR '1'='1' -- ",
            'zzllmlabnomatch" OR "1"="1',
        ):
            resp = self._send_spec(actor, self._with_param(target, params, payload), ctx)
            if 200 <= resp.status < 300 and self._count(resp) > n0:
                return "exploited", f"{payload!r} returned {self._count(resp)} rows, a no-match value {n0}"
        # A tautology shows itself only as extra rows. "No extra rows" is a clean result only when the benign
        # request (the control) returned rows the tautology would have brought back.
        if control is not None and not self._count(control):
            shape = "holds no rows" if list_of(control.json) is not None else "is not a result list"
            return "not_measurable", f"the benign response {shape}: a tautology had nothing to return"
        return "not_exploited", "tautology payloads returned no extra rows"

    def _probe_sqli_error(self, probe, params, ctx, control):
        target = self._target(probe, params)
        actor = self._actor_of(target)
        for payload in ("'", "'\"", "1' ORDER BY 99--"):
            resp = self._send_spec(actor, self._with_param(target, params, payload), ctx)
            if resp.status >= 500 and _SQL_ERROR.search(resp.text):
                return "exploited", f"{payload!r} -> {resp.status}: {_SQL_ERROR.search(resp.text).group(0)}"  # type: ignore[union-attr]
        return "not_exploited", "no database error text in responses"

    def _probe_sqli_time(self, probe, params, ctx, control):
        target = self._target(probe, params)
        actor = self._actor_of(target)
        base = self._send_spec(actor, self._with_param(target, params, "zzllmlab"), ctx)
        heavy = (
            "zzllmlab' OR 1=(WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c LIMIT 12000000) "
            "SELECT count(*)>0 FROM c) -- "
        )
        for payload in (heavy, "zzllmlab' OR pg_sleep(3) IS NULL -- ", "zzllmlab' OR SLEEP(3) -- "):
            resp = self._send_spec(actor, self._with_param(target, params, payload), ctx)
            if resp.ms > base.ms + 2000 and resp.ms > 2500:
                return "exploited", f"{payload[:40]!r} took {resp.ms} ms vs {base.ms} ms"
        return "not_exploited", f"no delay over the {base.ms} ms baseline"

    def _probe_xss_stored(self, probe, params, ctx, control):
        """Probe rules v2. The store request is a second create after the positive control, so it is rendered
        in a context of its own: the probe setup runs again (a new parent: a once-per-user rule or a unique
        field must not refuse the attack for an unrelated reason) and the body gets a new ``rand``. A name
        the setup bound (the parent id a nested view path needs) is never overwritten by the capture from the
        store response. A store request refused as invalid input (400/422) is ``not_exploited``; any other
        refusal means the payload was never stored and the probe did not measure (skipped)."""
        marker = f"llmlabxss{secrets.token_hex(4)}"
        # The marker also stands outside the element: a sanitiser that drops ``<script>`` with its content
        # still shows it, which tells "the view shows the stored value, made harmless" from "the view does
        # not show the value at all".
        payload = f"{marker}<script>alert('{marker}')</script>"
        target = self._target(probe, params)
        actor = self._actor_of(target)
        setup = params.get("setup") or []
        attack_ctx = self.new_ctx() if setup else self.fresh_ctx(ctx)
        if setup:
            problem = self._run_setup({"setup": setup, "actor": "user_a"}, attack_ctx)
            if problem:
                return "positive_control_failed", f"second setup for the store request failed: {problem}"
        bound = set(attack_ctx)
        resp = self._send_spec(actor, self._with_param(target, params, payload), attack_ctx)
        if resp.status in (400, 422):
            return "not_exploited", f"store request refused as invalid input ({resp.status})"
        if resp.status >= 300:
            return "positive_control_failed", f"store request answered {resp.status}: the payload was not stored"
        for name, path in (params.get("capture") or {"id": "$.id"}).items():
            if name in bound:
                continue
            try:
                attack_ctx[name] = json_path(resp.json, path)
            except KeyError:
                pass
        view = params.get("view") or (
            {"method": "GET", "path": params["view_path"]} if params.get("view_path") else None
        )
        read = self._send_spec(self._actor_of(view), view, attack_ctx) if view else resp
        # Stored XSS is observed where a browser renders the value as markup: a 2xx ``text/html`` response.
        # Anything else is not a measurement (rules v2: it used to be reported as a clean result).
        if not 200 <= read.status < 300:
            return "not_measurable", f"the view answered {read.status} at {read.path}: the payload was never served"
        if not read.is_html():
            ctype = read.headers.get("content-type", "").split(";")[0].strip() or "no content type"
            return (
                "not_measurable",
                f"the view at {read.path} is {ctype}, not an HTML page: a browser does not run markup from it",
            )
        if payload in read.text:
            return "exploited", f"raw <script> served as text/html at {read.path}"
        if marker not in read.text:
            return "not_measurable", f"the HTML at {read.path} does not show the stored value at all"
        return "not_exploited", "payload not served unescaped as HTML"

    def _probe_xss_reflected(self, probe, params, ctx, control):
        payload = f"<script>alert('llmlab{secrets.token_hex(3)}')</script>"
        target = self._target(probe, params)
        resp = self._send_spec(self._actor_of(target), self._with_param(target, params, payload), ctx)
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
            resp = self._send_spec(self._actor_of(target), req, ctx)
            if _PASSWD.search(resp.text):
                return "exploited", f"{payload!r} returned /etc/passwd"
        return "not_exploited", "no file content outside the app"

    def _probe_open_redirect(self, probe, params, ctx, control):
        evil = "https://evil.example.org/llmlab"
        target = self._target(probe, params)
        # An open redirect is the *answer* of the probed endpoint: 3xx with a ``Location`` the client chose.
        # An endpoint that does not redirect for a benign value (one that only stores an address, which
        # another endpoint later redirects to by design) cannot show one: no measurement, not a clean result.
        if control is None or not 300 <= control.status < 400:
            answered = f"control answered {control.status}" if control is not None else "no control request"
            return "not_measurable", f"the endpoint does not answer with a redirect ({answered})"
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
        resp = self._send_spec(self._actor_of(target), req, ctx)
        docs = [resp.json]
        read = params.get("read")
        if read:
            got = self._send_spec(self._actor_of(read), read, ctx)
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
            resp = self._send_spec(self._actor_of(target), req, ctx)
        elif field:
            resp = self._send_spec(self._actor_of(target), self._with_param(target, params, xml.decode()), ctx)
        else:
            resp = self.request(
                self._actor_of(target),
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
        target = self._target(probe, params)
        resp = self._send_spec(self._actor_of(target), self._with_param(target, params, url), ctx)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            hits = self.mock.hits if self.mock is not None else []
            if any(token in h for h in hits):
                return "exploited", f"the app fetched {url}"
            time.sleep(0.1)
        return "not_exploited", f"status {resp.status}; the mock server saw no request"

    def _applies_csrf_missing(self, probe, params):
        if self.auth.get("credential") != "cookie":
            return f"the plan's credential is {self.auth.get('credential', 'bearer')}, not a cookie"
        return None

    def _probe_csrf_missing(self, probe, params, ctx, control):
        if self.auth.get("credential") != "cookie":
            return None
        target = self._target(probe, params)
        req = json.loads(json.dumps(target))
        resp = self.request(
            self._actor_of(target),
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
        """Repeat the benign request; a "too many requests" answer is the limiter.

        Round 3: on an endpoint for which the spec has no such answer (``params.silent_limit``: a password
        reset must answer the same whoever asks) a limiter may drop the work and answer as always, so "no 429"
        is not a finding there. With ``params.observe`` (the task's outbox, read for the attacked account's
        address) the messages are counted instead: every request produced one -> exploited; fewer -> the
        requests were limited silently. Without anything to count the probe is not measurable.
        """
        target = self._target(probe, params)
        attempts = int(params.get("attempts") or 30)
        statuses: list[int] = []
        actor = self._actor_of(target, "anon")
        observe = params.get("observe") if isinstance(params.get("observe"), dict) else None
        before = self._observed(observe, ctx) if observe else None
        for _ in range(attempts):
            resp = self._send_spec(actor, target, ctx)
            statuses.append(resp.status)
            if resp.status in (*_LIMITED, 503):
                return "not_exploited", f"limited after {len(statuses)} attempts ({resp.status})"
        unlimited = f"{attempts} rapid attempts, none limited (last {statuses[-1]})"
        if observe:
            after = self._observed(observe, ctx)
            if before is None or after is None:
                return "not_measurable", f"{unlimited}; the outbox could not be read, so a silent limit cannot be seen"
            if before < 1:
                return (
                    "not_measurable",
                    f"{unlimited}; the benign request left no message in the outbox for the account's address, "
                    "so the outbox cannot show whether the repeated requests were limited silently",
                )
            sent = after - before
            if sent >= attempts:
                return "exploited", f"{unlimited}, and every one of them put a message in the outbox ({sent})"
            return (
                "not_exploited",
                f"no 'too many requests' answer, but only {sent} of {attempts} repeated requests put a message "
                "in the outbox: limited silently",
            )
        if params.get("silent_limit"):
            return (
                "not_measurable",
                f"{unlimited}; the spec gives this endpoint no 'too many requests' answer and the task has "
                "nothing to count, so a limiter that answers as always cannot be told from none",
            )
        return "exploited", unlimited

    def _observed(self, observe: dict[str, Any], ctx: dict[str, Any]) -> int | None:
        """How many items the ``observe`` request lists right now (``None``: it did not answer a list)."""
        resp = self._send_spec(self._actor_of(observe, "anon"), observe, ctx)
        items = list_of(resp.json) if 200 <= resp.status < 300 else None
        return len(items) if items is not None else None

    def _probe_default_credential(self, probe, params, ctx, control):
        login = str(self.auth.get("login_path") or "/api/auth/login")
        username = self.actors["admin"].username
        tried = 0
        limited: list[int] = []
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
            tried += 1
            if 200 <= resp.status < 300:
                return "exploited", f"{username}/{password} logged in"
            if resp.status in _LIMITED:
                limited.append(resp.status)
        if limited:
            # A limited or locked sign-in refuses every password, the right one included: those candidates
            # were not evaluated, so "none accepted" would not be a finding.
            return (
                "not_measurable",
                f"{len(limited)} of {tried} candidate passwords were answered {limited[0]} (sign-in limited or "
                f"locked), {tried - len(limited)} were refused: not every candidate was evaluated",
            )
        return "not_exploited", f"none of {tried} default passwords accepted"

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
            resp = self._send_spec("user_a", target, ctx)  # the forged credential is user_a's
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
        self._void_unattributable()
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
            "actors": {n: self.actors[n].to_state() for n in _USERS if n != "user_c" or self.actors[n].ready},
            "upstream": (
                None if self.upstream is None else {"base": self.upstream_base, "requests": self._upstream_requests()}
            ),
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
        return tester.result()
    except Exception as exc:  # noqa: BLE001 - any tester-side fault is "not measured", never pass rate 0
        return not_measured(plan, f"tester exception: {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}")
    finally:
        tester.close()


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
    finally:
        tester.close()
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh)
    print("LLMLAB-TESTER " + json.dumps({"measured": out.get("measured", True), "phase": args.phase}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
