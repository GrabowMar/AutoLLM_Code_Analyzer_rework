#!/usr/bin/env python3
"""LLM Lab relay: connects a local model server (Ollama / OpenAI-compatible) to an LLM Lab server.

Runs on the machine with the GPU, next to the model server, and dials out
over HTTPS to ``<server>/api/relay/v1/``; nothing listens for inbound
connections. The relay is deliberately dumb:

* the server builds every upstream request; the relay checks its method and
  path against a hard-coded allowlist (inference, model listing and
  warm-up/unload only; never pull, delete, create, push or copy) and runs
  it non-streaming against the local model server,
* it pushes the model inventory (``/api/tags``, ``/api/show``, ``/api/ps``),
* it never downloads or updates code.

Single file, Python >= 3.9 standard library only. See relay/README.md.

Exit codes: 0 ok, 1 generic or --check failure, 2 usage, 3 unauthorized
(the token was revoked or is wrong; a supervisor should not restart).
"""

# Standalone stdlib script run on GPU boxes (prints for --check, os.path for 3.9 portability).
# ruff: noqa: T201, PTH103, PTH111, PTH116, PTH118, PTH123, N818, TRY400, UP041

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import http.client
import json
import logging
import os
import signal
import socket
import ssl
import stat
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import zlib
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

__version__ = "1.0.0"

log = logging.getLogger("llmlab-relay")

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_UNAUTHORIZED = 3

# (method, path) pairs the server may ask for. Must match
# backend/inference/upstream.py ALLOWED_PATHS (a server-side test checks it).
ALLOWED_PATHS = frozenset(
    {
        ("POST", "/api/chat"),
        ("POST", "/api/generate"),
        ("POST", "/api/show"),
        ("GET", "/api/tags"),
        ("GET", "/api/ps"),
        ("GET", "/api/version"),
        ("POST", "/v1/chat/completions"),
        ("GET", "/v1/models"),
    },
)
GZIP_THRESHOLD = 64 * 1024
DEFAULT_MAX_RESULT = 8 * 1024 * 1024
SHUTDOWN_GRACE_S = 30
DISABLED_POLL_S = 15  # well under the server's session TTL, so an idle session is never reaped
UNAUTHORIZED_WAIT_S = 60
MAX_BACKOFF_S = 60.0
# While requests are running, retry the server quickly: a session that misses
# its TTL is ended and its in-flight work requeued (and cancelled here).
MAX_BACKOFF_INFLIGHT_S = 5.0
# Errors raised while reading a response that are not OSError subclasses
# (truncated bodies, bad status lines, corrupt gzip); converted to OSError.
TRANSPORT_ERRORS = (http.client.HTTPException, EOFError, zlib.error)


def path_allowed(method: str, path: str, body) -> bool:
    if (str(method).upper(), path) not in ALLOWED_PATHS:
        return False
    if path == "/api/generate":
        # Only warm-up / unload: an empty prompt.
        return isinstance(body, dict) and not body.get("prompt")
    return True


class Unauthorized(Exception):
    pass


class ServerError(Exception):
    def __init__(self, status: int, payload):
        super().__init__(f"server answered {status}: {payload}")
        self.status = status
        self.payload = payload


# ── Configuration ───────────────────────────────────────────────────────


def parse_args(argv=None) -> argparse.Namespace:
    env = os.environ.get
    parser = argparse.ArgumentParser(
        prog="llmlab_relay.py",
        description="Connect a local Ollama / OpenAI-compatible server to LLM Lab.",
    )
    parser.add_argument("--server", default=env("LLMLAB_SERVER"), help="LLM Lab URL, e.g. https://lab.example.org")
    parser.add_argument("--token-file", default=env("LLMLAB_TOKEN_FILE"), help="file holding the relay token (0600)")
    parser.add_argument("--upstream", default=env("LLMLAB_UPSTREAM", "http://127.0.0.1:11434"))
    parser.add_argument("--kind", default=env("LLMLAB_KIND", "ollama"), choices=("ollama", "openai_compatible"))
    parser.add_argument("--parallel", type=int, default=int(env("LLMLAB_PARALLEL", "1")))
    parser.add_argument("--state-dir", default=env("LLMLAB_STATE_DIR", os.path.expanduser("~/.llmlab-relay")))
    parser.add_argument("--upstream-api-key-env", default=None, help="name of an env var holding the upstream key")
    parser.add_argument("--ca-file", default=None, help="extra CA bundle for the LLM Lab server")
    parser.add_argument("--max-num-ctx", type=int, default=None, help="clamp options.num_ctx (VRAM protection)")
    parser.add_argument("--on-unauthorized", choices=("exit", "wait"), default="exit")
    parser.add_argument("--check", action="store_true", help="diagnose the setup and exit")
    parser.add_argument("--log-level", default="info")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    if not args.server:
        parser.error("--server (or LLMLAB_SERVER) is required")
    if args.parallel < 1 or args.parallel > 256:
        parser.error("--parallel must be between 1 and 256")
    parts = urlsplit(args.server)
    local = parts.hostname in ("localhost", "127.0.0.1", "::1")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        parser.error("--server must use https:// (plain http is only allowed for localhost)")
    args.server = args.server.rstrip("/")
    return args


def read_token(args: argparse.Namespace) -> str:
    """The token from ``--token-file`` / ``LLMLAB_TOKEN_FILE`` or ``LLMLAB_TOKEN`` (never argv)."""
    if args.token_file:
        try:
            info = os.stat(args.token_file)
        except OSError as exc:
            msg = f"cannot read token file {args.token_file}: {exc}"
            raise SystemExit(msg) from exc
        if stat.S_IMODE(info.st_mode) & 0o077:
            msg = f"token file {args.token_file} is readable by others; run: chmod 600 {args.token_file}"
            raise SystemExit(msg)
        with open(args.token_file, encoding="utf-8") as fh:
            token = fh.read().strip()
    else:
        token = os.environ.get("LLMLAB_TOKEN", "").strip()
    if not token:
        msg = "no relay token: use --token-file (or LLMLAB_TOKEN_FILE / LLMLAB_TOKEN)"
        raise SystemExit(msg)
    return token


def relay_id(state_dir: str) -> str:
    path = os.path.join(state_dir, "relay_id")
    try:
        with open(path, encoding="utf-8") as fh:
            value = fh.read().strip()
        return str(uuid.UUID(value))
    except (OSError, ValueError):
        pass
    value = str(uuid.uuid4())
    try:
        os.makedirs(state_dir, mode=0o700, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(value)
    except OSError as exc:
        log.warning("cannot persist relay id in %s (%s); using a temporary one", state_dir, exc)
    return value


# ── Server client ───────────────────────────────────────────────────────


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: following one would resend the bearer token, possibly over http or to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"redirect to {newurl} refused", headers, fp)


class Server:
    def __init__(self, args: argparse.Namespace, token: str) -> None:
        self.base = args.server + "/api/relay/v1/"
        self.token = token
        self.session_id: str | None = None
        self.context = (
            ssl.create_default_context(cafile=args.ca_file) if args.ca_file else ssl.create_default_context()
        )
        self.opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=self.context))
        self.timeout = 45.0

    def post(self, path: str, payload: dict, *, timeout: float | None = None) -> dict:
        data = json.dumps(payload).encode()
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": f"llmlab-relay/{__version__}",
        }
        if len(data) > GZIP_THRESHOLD:
            data = gzip.compress(data)
            headers["Content-Encoding"] = "gzip"
        if self.session_id:
            headers["X-Relay-Session"] = self.session_id
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method="POST")  # noqa: S310
        try:
            with self.opener.open(request, timeout=timeout or self.timeout) as resp:
                raw = resp.read()
                if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read() or b""
                if (exc.headers.get("Content-Encoding") or "").lower() == "gzip":
                    body = gzip.decompress(body)
            except (OSError, *TRANSPORT_ERRORS):
                body = b""
            try:
                payload = json.loads(body or b"{}")
            except ValueError:
                payload = {"error": body[:200].decode(errors="replace")}
            if exc.code == 401:
                raise Unauthorized from exc
            raise ServerError(exc.code, payload) from exc
        except TRANSPORT_ERRORS as exc:
            msg = f"{path}: {type(exc).__name__}: {exc}"
            raise OSError(msg) from exc


# ── Upstream ────────────────────────────────────────────────────────────


class Upstream:
    def __init__(self, url: str, api_key: str | None) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            msg = "--upstream must be http:// or https://"
            raise SystemExit(msg)
        self.scheme = parts.scheme
        self.host = parts.hostname or "127.0.0.1"
        self.port = parts.port or (443 if parts.scheme == "https" else 80)
        self.base_path = parts.path.rstrip("/")
        if self.base_path.endswith("/v1"):
            self.base_path = self.base_path[:-3]
        self.api_key = api_key

    def connect(self, timeout: float) -> http.client.HTTPConnection:
        if self.scheme == "https":
            return http.client.HTTPSConnection(
                self.host,
                self.port,
                timeout=timeout,
                context=ssl.create_default_context(),
            )
        return http.client.HTTPConnection(self.host, self.port, timeout=timeout)

    def call(self, method: str, path: str, body=None, *, timeout: float = 30.0, holder=None):
        """``(status, parsed JSON or None, raw bytes)``; ``holder`` gets the connection for cancellation."""
        conn = self.connect(timeout)
        if holder is not None:
            holder.conn = conn
        try:
            headers = {"Accept": "application/json"}
            data = None
            if body is not None:
                data = json.dumps(body).encode()
                headers["Content-Type"] = "application/json"
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            conn.request(method, self.base_path + path, body=data, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
        except TRANSPORT_ERRORS as exc:
            msg = f"{method} {path}: {type(exc).__name__}: {exc}"
            raise OSError(msg) from exc
        finally:
            conn.close()
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = None
        return resp.status, parsed, raw

    def get_json(self, path: str, timeout: float = 30.0):
        status, parsed, raw = self.call("GET", path, timeout=timeout)
        if status >= 400 or parsed is None:
            msg = f"GET {path} answered {status}: {raw[:200]!r}"
            raise OSError(msg)
        return parsed


def _digest(value) -> str:
    text = str(value or "")
    return text if not text or text.startswith("sha256:") else f"sha256:{text}"


def show_entry(info: dict) -> dict:
    model_info = info.get("model_info") or {}
    context = None
    for key, value in model_info.items():
        if str(key).endswith(".context_length") and isinstance(value, int):
            context = value
            break
    template = info.get("template") or ""
    return {
        "context_length": context,
        "capabilities": [c for c in info.get("capabilities") or [] if isinstance(c, str)][:16],
        "template_sha": hashlib.sha256(template.encode()).hexdigest()[:16] if template else "",
    }


def ps_entries(models) -> list:
    return [
        {
            "name": str(m.get("name")),
            "digest": _digest(m.get("digest")),
            "size": m.get("size"),
            "size_vram": m.get("size_vram"),
            "expires_at": str(m.get("expires_at") or ""),
            "context_length": m.get("context_length"),
        }
        for m in models or []
        if isinstance(m, dict) and m.get("name")
    ][:64]


# ── Relay ───────────────────────────────────────────────────────────────


class _Holder:
    conn = None


class Inflight:
    def __init__(self, rid: str, claim: str, deadline: float) -> None:
        self.rid = rid
        self.claim = claim
        self.deadline = deadline
        self.holder = _Holder()
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True
        conn = self.holder.conn
        sock = getattr(conn, "sock", None) if conn is not None else None
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)


class Relay:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.token = read_token(args)
        self.server = Server(args, self.token)
        api_key = os.environ.get(args.upstream_api_key_env, "") if args.upstream_api_key_env else ""
        self.upstream = Upstream(args.upstream, api_key or None)
        self.relay_id = relay_id(args.state_dir)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.inflight: dict[tuple[str, str], Inflight] = {}
        self.pool = ThreadPoolExecutor(max_workers=args.parallel, thread_name_prefix="upstream")
        self.inventory_due = threading.Event()
        self.show_cache: dict[str, dict] = {}
        self.max_result = DEFAULT_MAX_RESULT
        self.poll_timeout = 25
        self.inventory_interval = 60
        self.last_tags: str = ""
        self.last_ps_at = 0.0
        self.role = None
        self.reload_token = threading.Event()

    # -- server session ------------------------------------------------------

    def upstream_version(self) -> str:
        try:
            if self.args.kind == "ollama":
                return str(self.upstream.get_json("/api/version", timeout=10).get("version") or "")
        except OSError as exc:
            log.warning("model server not reachable at %s: %s", self.args.upstream, exc)
        return ""

    def hello(self, *, probe: bool = False) -> dict:
        payload = {
            "relay_version": __version__,
            "relay_id": self.relay_id,
            "hostname": socket.gethostname(),
            "probe": probe,
            "upstream": {"kind": self.args.kind, "url_hint": self.args.upstream, "version": self.upstream_version()},
            "parallel": self.args.parallel,
        }
        self.server.session_id = None
        doc = self.server.post("hello", payload)
        if not probe:
            self.server.session_id = doc.get("session_id")
            self.role = doc.get("role")
            self.max_result = int(doc.get("max_result_bytes") or DEFAULT_MAX_RESULT)
            self.poll_timeout = int(doc.get("poll_timeout_s") or 25)
            self.server.timeout = self.poll_timeout + 20
            self.inventory_interval = int(doc.get("inventory_interval_s") or 60)
            log.info(
                "connected to %s as %s (session %s, endpoint %s)",
                self.args.server,
                self.role,
                self.server.session_id,
                (doc.get("endpoint") or {}).get("slug"),
            )
        return doc

    def reread_token(self) -> bool:
        try:
            token = read_token(self.args)
        except SystemExit as exc:
            log.error("%s", exc)
            return False
        changed = token != self.server.token
        self.server.token = token
        return changed

    # -- inventory -------------------------------------------------------------

    def collect_inventory(self) -> dict:
        if self.args.kind == "ollama":
            version = str(self.upstream.get_json("/api/version").get("version") or "")
            tags = self.upstream.get_json("/api/tags").get("models") or []
            loaded = ps_entries(self.upstream.get_json("/api/ps").get("models"))
            models = []
            for tag in tags:
                if not isinstance(tag, dict) or not tag.get("name"):
                    continue
                details = tag.get("details") or {}
                models.append(
                    {
                        "name": str(tag["name"]),
                        "digest": _digest(tag.get("digest")),
                        "size": tag.get("size"),
                        "modified_at": tag.get("modified_at"),
                        "family": details.get("family") or "",
                        "parameter_size": details.get("parameter_size") or "",
                        "quantization": details.get("quantization_level") or "",
                    },
                )
            self.last_tags = json.dumps(sorted((m["name"], m["digest"]) for m in models))
            show = {m["name"]: self.show_cache[m["digest"]] for m in models if m["digest"] in self.show_cache}
            return {"version": version, "models": models, "show": show, "loaded": loaded}
        data = self.upstream.get_json("/v1/models").get("data") or []
        models = [
            {"name": str(m["id"]), "context_length": m.get("max_model_len")}
            for m in data
            if isinstance(m, dict) and m.get("id")
        ]
        return {"version": "", "models": models, "show": {}, "loaded": []}

    def push_inventory(self) -> None:
        payload = self.collect_inventory()
        digests = {m["name"]: m.get("digest", "") for m in payload["models"]}
        failed: set[str] = set()
        for _ in range(20):
            doc = self.server.post("inventory", payload)
            need = [n for n in doc.get("need_show") or [] if n in digests and n not in failed]
            if not need or self.args.kind != "ollama":
                log.info("inventory: %s models", doc.get("accepted_models"))
                return
            added = 0
            for name in need:
                entry = self.show(name)
                if entry is None:
                    # Never report (or cache) a failed show as "no capabilities":
                    # the server keeps asking for it on the next push.
                    failed.add(name)
                    continue
                self.show_cache[digests[name]] = entry
                payload["show"][name] = entry
                added += 1
            if not added:
                log.info("inventory: %s models (%d without show details yet)", doc.get("accepted_models"), len(failed))
                return

    def show(self, name: str) -> dict | None:
        """The ``show_entry`` for ``name``, or None when ``/api/show`` failed."""
        try:
            status, info, raw = self.upstream.call("POST", "/api/show", {"model": name}, timeout=30)
        except OSError as exc:
            log.warning("show %s failed: %s", name, exc)
            return None
        if status != 200 or not isinstance(info, dict):
            log.warning("show %s answered %s: %r", name, status, raw[:200])
            return None
        try:
            return show_entry(info)
        except (AttributeError, TypeError, ValueError) as exc:
            log.warning("show %s returned an unexpected document: %s", name, exc)
            return None

    def inventory_loop(self) -> None:
        last = 0.0
        while not self.stop.is_set():
            due = self.inventory_due.wait(timeout=5)
            changed = False
            if not due and time.monotonic() - last < self.inventory_interval:
                if self.args.kind == "ollama" and time.monotonic() - last > 15:
                    try:
                        tags = self.upstream.get_json("/api/tags").get("models") or []
                        current = json.dumps(
                            sorted(
                                (str(t.get("name")), _digest(t.get("digest"))) for t in tags if isinstance(t, dict)
                            ),
                        )
                        changed = current != self.last_tags
                    except (OSError, AttributeError, TypeError):
                        changed = False
                if not changed:
                    continue
            self.inventory_due.clear()
            if not self.server.session_id:
                continue
            try:
                self.push_inventory()
                last = time.monotonic()
            except Unauthorized:
                self.reload_token.set()
            except (OSError, ServerError, ValueError) as exc:
                log.warning("inventory push failed: %s", exc)
                last = time.monotonic() - self.inventory_interval + 15
            except Exception:
                log.exception("inventory push failed unexpectedly")
                last = time.monotonic() - self.inventory_interval + 15

    # -- execution ---------------------------------------------------------------

    def execute(self, job: Inflight, request: dict) -> None:
        try:
            self._execute(job, request)
        except Exception:
            log.exception("request %s failed unexpectedly", job.rid[:8])
        finally:
            # Always free the slot, or the relay would echo this job forever.
            with self.lock:
                self.inflight.pop((job.rid, job.claim), None)

    def _execute(self, job: Inflight, request: dict) -> None:
        upstream = request.get("upstream") or {}
        method = str(upstream.get("method") or "POST").upper()
        path = str(upstream.get("path") or "")
        body = upstream.get("json")
        timeout = float(request.get("timeout_s") or 900)
        result: dict = {"claim_token": job.claim}
        started = time.monotonic()
        clamped = None
        if job.cancelled:
            # Cancelled while it waited for a free upstream slot: never start it.
            return
        try:
            if not path_allowed(method, path, body):
                result.update(
                    ok=False,
                    status_code=None,
                    error={"kind": "forbidden_path", "message": f"{method} {path}"},
                )
            else:
                if self.args.max_num_ctx and isinstance(body, dict):
                    options = body.get("options")
                    if isinstance(options, dict) and int(options.get("num_ctx") or 0) > self.args.max_num_ctx:
                        clamped = options["num_ctx"] = self.args.max_num_ctx
                status, parsed, raw = self.upstream.call(method, path, body, timeout=timeout, holder=job.holder)
                if len(raw) > self.max_result:
                    result.update(
                        ok=False,
                        status_code=None,
                        error={"kind": "too_large", "message": f"{len(raw)} bytes"},
                    )
                elif status >= 400:
                    result.update(
                        ok=False,
                        status_code=status,
                        json=parsed,
                        error={"kind": "http", "message": raw[:2000].decode(errors="replace")},
                    )
                elif parsed is None:
                    result.update(ok=False, status_code=status, error={"kind": "bad_json", "message": "not JSON"})
                else:
                    result.update(ok=True, status_code=status, json=parsed)
        except (TimeoutError, socket.timeout):  # distinct classes before Python 3.10
            result.update(
                ok=False,
                status_code=None,
                error={"kind": "timeout", "message": f"no answer in {timeout:.0f}s"},
            )
        except (OSError, http.client.HTTPException) as exc:
            kind = "cancelled" if job.cancelled else "connect"
            result.update(ok=False, status_code=None, error={"kind": kind, "message": str(exc)[:500]})
        upstream_ms = int((time.monotonic() - started) * 1000)
        result["timings"] = {"upstream_ms": upstream_ms}
        if clamped:
            result["served"] = {"num_ctx_clamped": clamped}
        log.info(
            "request %s model=%s %s %s in %d ms",
            job.rid[:8],
            (request.get("model") or {}).get("name"),
            path,
            "ok" if result.get("ok") else (result.get("error") or {}).get("kind"),
            upstream_ms,
        )
        if not job.cancelled:
            self.post_result(job, result)

    def post_result(self, job: Inflight, result: dict) -> None:
        delay = 1.0
        while True:
            try:
                doc = self.server.post(f"requests/{job.rid}/result", result, timeout=60)
                if not doc.get("accepted"):
                    log.info("result %s not accepted: %s", job.rid[:8], doc.get("reason"))
                return
            except Unauthorized:
                if self.stop.is_set():
                    log.warning("giving up on result %s: token rejected while stopping", job.rid[:8])
                    return
                self.reload_token.set()
            except (OSError, ServerError, ValueError) as exc:
                log.warning("posting result %s failed: %s", job.rid[:8], exc)
            if job.cancelled:
                return
            if time.time() > job.deadline or (self.stop.is_set() and delay > 8):
                log.warning("giving up on result %s", job.rid[:8])
                return
            time.sleep(delay + (hash(job.rid) % 1000) / 1000.0)
            delay = min(delay * 2, 60.0)

    # -- polling -------------------------------------------------------------------

    def poll_once(self) -> None:
        with self.lock:
            inflight = [{"id": j.rid, "claim_token": j.claim} for j in self.inflight.values()]
            free = max(0, self.args.parallel - len(self.inflight))
        payload: dict = {"inflight": inflight, "free_slots": 0 if self.stop.is_set() else free}
        if self.args.kind == "ollama" and time.monotonic() - self.last_ps_at > 30:
            try:
                payload["loaded"] = ps_entries(self.upstream.get_json("/api/ps", timeout=5).get("models"))
                self.last_ps_at = time.monotonic()
            except (OSError, AttributeError):
                pass
        doc = self.server.post("poll", payload, timeout=self.poll_timeout + 20)
        if not doc.get("session_valid", True):
            log.info("session no longer valid; reconnecting")
            self.hello()
            self.inventory_due.set()
            return
        self.role = doc.get("role") or self.role
        for item in doc.get("cancel") or []:
            key = (str(item.get("id")), str(item.get("claim_token")))
            with self.lock:
                job = self.inflight.get(key)
            if job is not None:
                log.info("cancelling request %s", job.rid[:8])
                job.cancel()
        if doc.get("resync_inventory"):
            self.inventory_due.set()
        for request in doc.get("requests") or []:
            rid = str(request.get("id"))
            claim = str(request.get("claim_token"))
            deadline = time.time() + float(request.get("timeout_s") or 900) + 600
            job = Inflight(rid, claim, deadline)
            with self.lock:
                self.inflight[(rid, claim)] = job
            self.pool.submit(self.execute, job, request)
        if not doc.get("endpoint_enabled", True):
            log.info("endpoint disabled on the server; idling")
            self.stop.wait(DISABLED_POLL_S)

    def cancel_all(self) -> None:
        """Abort every running upstream call (its result is never posted)."""
        with self.lock:
            jobs = list(self.inflight.values())
        for job in jobs:
            job.cancel()

    def max_backoff(self) -> float:
        with self.lock:
            busy = bool(self.inflight)
        return MAX_BACKOFF_INFLIGHT_S if busy else MAX_BACKOFF_S

    def handle_unauthorized(self) -> None:
        if self.reread_token():
            log.info("token file changed; reconnecting")
            return
        if self.args.on_unauthorized == "wait":
            log.error("the server rejected the token; waiting for a new token file")
            while not self.stop.wait(UNAUTHORIZED_WAIT_S):
                if self.reread_token():
                    return
            return
        log.error("the server rejected the token (revoked or wrong); exiting")
        # The server already requeued this session's work: stop the GPU now
        # instead of finishing generations whose results would be rejected.
        self.stop.set()
        self.cancel_all()
        self.pool.shutdown(wait=False, cancel_futures=True)
        raise SystemExit(EXIT_UNAUTHORIZED)

    def run(self) -> int:
        threading.Thread(target=self.inventory_loop, name="inventory", daemon=True).start()
        backoff = 1.0
        connected = False
        while not self.stop.is_set():
            try:
                if self.reload_token.is_set():
                    self.reload_token.clear()
                    # Reconnect (a session belongs to its token) only when the
                    # token really changed, so in-flight requests survive SIGHUP.
                    if self.reread_token():
                        connected = False
                if not connected:
                    self.hello()
                    connected = True
                    self.inventory_due.set()
                self.poll_once()
                backoff = 1.0
            except Unauthorized:
                connected = False
                self.handle_unauthorized()
            except ServerError as exc:
                if exc.status == 426:
                    log.error("this relay is too old for the server: %s", exc.payload)
                    return EXIT_FAILURE
                if exc.status == 409:
                    log.error("configuration mismatch: %s", exc.payload)
                    return EXIT_FAILURE
                backoff = min(backoff, self.max_backoff())
                log.warning("%s; retrying in %.0fs", exc, backoff)
                connected = connected and exc.status not in (404, 410)
                self.stop.wait(backoff)
                backoff = backoff * 2
            except (OSError, ValueError) as exc:
                backoff = min(backoff, self.max_backoff())
                log.warning("server unreachable (%s); retrying in %.0fs", exc, backoff)
                self.stop.wait(backoff)
                backoff = backoff * 2
        return self.shutdown()

    def shutdown(self) -> int:
        deadline = time.monotonic() + SHUTDOWN_GRACE_S
        while time.monotonic() < deadline:
            with self.lock:
                if not self.inflight:
                    break
            time.sleep(0.5)
        with self.lock:
            inflight = [{"id": j.rid, "claim_token": j.claim} for j in self.inflight.values()]
        # Past the grace period: abort what is still running (the server
        # requeues it on bye), so the process exits instead of waiting for
        # the generations and the GPU stops working on them.
        self.cancel_all()
        if self.server.session_id:
            try:
                self.server.post("bye", {"inflight": inflight}, timeout=10)
            except (OSError, ServerError, Unauthorized, ValueError) as exc:
                log.warning("bye failed: %s", exc)
        self.pool.shutdown(wait=False, cancel_futures=True)
        log.info("stopped")
        return EXIT_OK


def check(relay: Relay) -> int:
    ok = True
    print(f"llmlab relay {__version__}")
    try:
        doc = relay.hello(probe=True)
        endpoint = doc.get("endpoint") or {}
        slug, kind = endpoint.get("slug"), endpoint.get("kind")
        print(f"[ok] server {relay.args.server}: token valid for endpoint {slug} ({kind})")
        if not doc.get("endpoint_enabled", True):
            print("[warn] the endpoint is disabled on the server")
        if endpoint.get("kind") and endpoint.get("kind") != relay.args.kind:
            print(f"[fail] the endpoint is {endpoint.get('kind')} but --kind is {relay.args.kind}")
            ok = False
    except Unauthorized:
        print("[fail] server rejected the token (wrong, revoked or expired)")
        ok = False
    except (OSError, ServerError, ValueError) as exc:
        print(f"[fail] server {relay.args.server}: {exc}")
        ok = False
    try:
        if relay.args.kind == "ollama":
            version = relay.upstream.get_json("/api/version", timeout=10).get("version")
            models = relay.upstream.get_json("/api/tags", timeout=10).get("models") or []
            print(f"[ok] Ollama {version} at {relay.args.upstream}: {len(models)} models")
        else:
            models = relay.upstream.get_json("/v1/models", timeout=10).get("data") or []
            print(f"[ok] OpenAI-compatible server at {relay.args.upstream}: {len(models)} models")
    except OSError as exc:
        print(f"[fail] model server {relay.args.upstream}: {exc}")
        ok = False
    parallel_env = os.environ.get("OLLAMA_NUM_PARALLEL")
    if parallel_env and parallel_env.isdigit() and relay.args.parallel > int(parallel_env):
        print(f"[warn] --parallel {relay.args.parallel} exceeds OLLAMA_NUM_PARALLEL={parallel_env}")
    return EXIT_OK if ok else EXIT_FAILURE


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        relay = Relay(args)
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            return EXIT_USAGE
        raise
    if args.check:
        return check(relay)
    parallel_env = os.environ.get("OLLAMA_NUM_PARALLEL")
    if parallel_env and parallel_env.isdigit() and args.parallel > int(parallel_env):
        log.warning("--parallel %s exceeds OLLAMA_NUM_PARALLEL=%s", args.parallel, parallel_env)

    def on_term(_signum, _frame):
        if relay.stop.is_set():
            log.info("stopping now (aborting in-flight requests)")
            relay.cancel_all()
            return
        log.info("stopping (finishing in-flight requests, up to %ss; repeat to abort them)", SHUTDOWN_GRACE_S)
        relay.stop.set()

    def on_hup(_signum, _frame):
        relay.reload_token.set()

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, on_hup)
    return relay.run()


if __name__ == "__main__":
    sys.exit(main())
