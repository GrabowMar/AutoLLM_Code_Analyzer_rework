"""Hidden conformance suite: checks a running app against its task brief.

Reads a plan (``plan.json``: base URL, the app's API contract, the shared
runtime/accounts contract, the admin password the app was started with) and
prints one JSON report on stdout. Only what the brief states is checked, in
the same deterministic order for every sample:

1. runtime   -- health endpoint answers 200 + JSON within the startup window
2. frontend  -- ``/`` and a deep link serve the SPA's HTML (frontend stacks)
3. auth      -- register + login yield a bearer token; protected writes
                reject anonymous requests; the admin account signs in
4. api/admin -- every contract endpoint answers 2xx with the documented
                response fields, exercised in a CRUD-friendly order with ids
                taken from the app's own responses. Destructive or
                state-changing item checks (DELETE, item actions such as
                ``/:id/toggle``) each get a freshly created resource, so no
                check depends on another check's side effects (v1 deleted an
                item an earlier admin toggle had soft-deleted).

Request bodies are synthesised from the contract's sample bodies. A check
that cannot run because an earlier one failed (no token, no resource id)
counts as failed, not skipped: the app did not provide what the brief asked.

Stdlib only; baked into its own image. Bump VERSION with any change.
"""

# Standalone script baked into its own image; the password is a throwaway test credential.
# ruff: noqa: INP001, PTH123, S310, BLE001, S105, PLR0911, FBT003, TRY003, EM102

from __future__ import annotations

import base64
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC
from datetime import datetime

VERSION = 3
REQUEST_TIMEOUT_S = 15
PASSWORD = "Eval-Pass-2026!"
PARAM_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)|\{([A-Za-z_][A-Za-z0-9_]*)\}")
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# "<type>", "<type> (optional)", "<type> (<detail>)" in a sample body.
TYPE_DESCRIPTOR_RE = re.compile(
    r"(string|str|text|integer|int|number|float|decimal|boolean|bool|array|list|object|dict)"
    r"(?:\s*\(([^)]*)\))?$",
    re.IGNORECASE,
)


class Client:
    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")

    def call(self, method: str, path: str, body=None, token: str | None = None) -> tuple[int, str, object, str]:
        """Return (status, content_type, parsed_json_or_None, text)."""
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Accept", "application/json, text/html;q=0.9")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                status, ctype, raw = resp.status, resp.headers.get("Content-Type", ""), resp.read()
        except urllib.error.HTTPError as exc:
            status, ctype, raw = exc.code, exc.headers.get("Content-Type", ""), exc.read()
        except Exception as exc:
            return 0, "", None, f"request failed: {exc}"
        text = raw.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text) if text.strip() else None
        except ValueError:
            parsed = None
        return status, ctype, parsed, text


class Report:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def add(self, category: str, name: str, ok: bool, detail: str = "", **extra) -> bool:
        self.checks.append({"category": category, "name": name, "passed": bool(ok), "detail": detail[:500], **extra})
        return ok

    def summary(self) -> dict:
        total = len(self.checks)
        passed = sum(1 for c in self.checks if c["passed"])
        by_cat: dict[str, dict[str, int]] = {}
        for c in self.checks:
            slot = by_cat.setdefault(c["category"], {"passed": 0, "total": 0})
            slot["total"] += 1
            slot["passed"] += int(c["passed"])
        return {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / total, 4) if total else 0.0,
            "by_category": by_cat,
        }


class Synth:
    """Deterministic request bodies from the contract's sample bodies."""

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.n = 0

    def value(self, sample, key: str = ""):
        k = key.lower()
        if isinstance(sample, dict):
            return {name: self.value(v, name) for name, v in sample.items()}
        if isinstance(sample, list):
            return [self.value(sample[0], key)] if sample else []
        if isinstance(sample, bool) or sample is None:
            return sample
        if isinstance(sample, (int, float)):
            return sample
        text = str(sample)
        if "|" in text and " " not in text:
            return text.split("|", 1)[0]
        if k == "password":
            return PASSWORD
        # Type descriptors ("integer (optional)", "string (todo|done)",
        # "string (base64)"): v2 sent them verbatim, so a correct app
        # rejected the body and every check needing the created id failed.
        described = TYPE_DESCRIPTOR_RE.match(text.strip())
        if described:
            base, detail = described.group(1).lower(), (described.group(2) or "").strip()
            if "|" in detail:
                return detail.split("|", 1)[0].strip()
            if base in {"integer", "int"}:
                return datetime.now(UTC).year if k == "year" else 1
            if base in {"number", "float", "decimal"}:
                return 1.5
            if base in {"boolean", "bool"}:
                return True
            if base in {"array", "list"}:
                return []
            if base in {"object", "dict"}:
                return {}
            if detail.lower() == "base64":
                return base64.b64encode(f"eval {self.tag}".encode()).decode()
            text = base
        now = datetime.now(UTC).replace(microsecond=0)
        if text.upper() == "YYYY-MM-DD":
            return now.date().isoformat()
        if text.upper() in {"HH:MM", "HH:MM:SS"}:
            return now.strftime("%H:%M" if len(text) == 5 else "%H:%M:%S")
        self.n += 1
        if "email" in k:
            return f"eval{self.tag}{self.n}@example.com"
        if text.upper().startswith("ISO8601") or k.endswith(("_at", "date", "time")):
            return now.isoformat()
        if "url" in k:
            return f"https://example.com/eval/{self.tag}/{self.n}"
        if text.lower() in {"string", "text", "str"} or text == "":
            # Short (v2's "eval isbn <tag>-10" broke a 20-char limit only from the 10th value on).
            return f"{(k or 'value')[:10]} {self.tag[:4]}{self.n}"
        return text


def shape_problems(spec, actual, where: str = "response") -> list[str]:
    """Documented fields missing from *actual* (extra fields are fine)."""
    if isinstance(spec, dict):
        if not spec:
            return []
        if not isinstance(actual, dict):
            return [f"{where} is not a JSON object"]
        problems = [f"{where} lacks field {name!r}" for name in spec if name not in actual]
        for name, sub in spec.items():
            if name in actual and isinstance(sub, list) and sub and isinstance(sub[0], dict):
                items = actual[name]
                if not isinstance(items, list):
                    problems.append(f"{where}.{name} is not a list")
                elif items:
                    problems += shape_problems(sub[0], items[0], f"{where}.{name}[0]")
        return problems
    if isinstance(spec, list):
        if not isinstance(actual, list):
            return [f"{where} is not a JSON list"]
        if spec and isinstance(spec[0], dict) and actual:
            return shape_problems(spec[0], actual[0], f"{where}[0]")
    return []


def first_id(payload, param: str):
    """An identifier for path param *param* from a create/list response."""
    candidates = []
    if isinstance(payload, dict):
        candidates.append(payload)
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                candidates.append(value[0])
            elif isinstance(value, dict):
                candidates.append(value)
    elif isinstance(payload, list) and payload and isinstance(payload[0], dict):
        candidates.append(payload[0])
    for obj in candidates:
        for key in (param, "id"):
            if obj.get(key) not in (None, ""):
                return obj[key]
    return None


def collection_of(path: str) -> str:
    """``/api/todos/:id/toggle`` -> ``/api/todos``."""
    m = PARAM_RE.search(path)
    return path[: m.start()].rstrip("/") if m else path


def run(plan: dict) -> dict:
    client = Client(plan["base_url"])
    report = Report()
    contract = plan["contract"]
    synth = Synth(plan.get("nonce", "x"))

    # 1. runtime
    deadline = time.monotonic() + plan.get("startup_timeout_s", 90)
    status = 0
    parsed = None
    while time.monotonic() < deadline:
        status, _ctype, parsed, _text = client.call("GET", contract["health_path"])
        if status == 200:
            break
        time.sleep(2)
    up = report.add("runtime", "health responds 200", status == 200, f"last status {status}")
    report.add("runtime", "health returns JSON", up and parsed is not None, "" if parsed is not None else "not JSON")
    endpoints = [e for e in plan.get("endpoints", []) if str(e.get("method", "")).upper() != "WS"]
    if not up:
        for ep in endpoints:
            report.add(
                "admin" if ep.get("admin") else "api",
                f"{ep['method']} {ep['path']}",
                False,
                "app not reachable",
            )
        return {"version": VERSION, "checks": report.checks, "summary": report.summary()}

    # 2. frontend
    if plan.get("has_frontend"):
        for name, path in (("index served at /", "/"), ("SPA fallback for deep links", "/llmlab-eval/deep/link")):
            status, ctype, _p, text = client.call("GET", path)
            ok = status == 200 and "html" in ctype.lower() and "<" in text
            report.add("frontend", name, ok, f"status {status}, content-type {ctype!r}")

    # 3. accounts
    token = admin_token = None
    auth_paths = {contract["register_path"], contract["login_path"]}
    if contract.get("accounts"):
        field = contract["identity_field"]
        identity = (
            f"eval{plan.get('nonce', 'x')}@example.com" if field == "email" else f"eval_{plan.get('nonce', 'x')}"
        )
        creds = {field: identity, "password": PASSWORD}
        status, _c, body, text = client.call("POST", contract["register_path"], creds)
        report.add("auth", "register account", status in (200, 201), f"status {status}: {text[:120]}")
        status, _c, body, text = client.call("POST", contract["login_path"], creds)
        token = body.get("token") if isinstance(body, dict) else None
        report.add(
            "auth",
            "login returns token",
            status == 200 and isinstance(token, str) and bool(token),
            f"status {status}",
        )
        if contract.get("admin"):
            admin_creds = {field: contract["admin_identity"], "password": plan["admin_password"]}
            status, _c, body, _t = client.call("POST", contract["login_path"], admin_creds)
            admin_token = body.get("token") if isinstance(body, dict) else None
            report.add("auth", "admin account signs in", status == 200 and bool(admin_token), f"status {status}")
        if contract.get("writes_require_auth"):
            target = next(
                (
                    e
                    for e in endpoints
                    if e["method"].upper() == "POST"
                    and not e.get("admin")
                    and e["path"] not in auth_paths
                    and not PARAM_RE.search(e["path"])
                ),
                None,
            )
            if target is not None:
                status, *_ = client.call("POST", target["path"], synth.value(target.get("request") or {}))
                report.add(
                    "auth",
                    f"anonymous POST {target['path']} is rejected",
                    status in (401, 403),
                    f"status {status}",
                )

    # 4. contract endpoints, CRUD-friendly order
    created: dict[str, object] = {}

    creators = {
        e["path"]: e
        for e in endpoints
        if e["method"].upper() == "POST" and not PARAM_RE.search(e["path"]) and e["path"] not in auth_paths
    }
    param_names = [m.group(1) or m.group(2) for e in endpoints for m in PARAM_RE.finditer(e["path"])]

    def create(collection: str):
        """POST a new resource to *collection*; return its id (or None)."""
        ep = creators.get(collection)
        if ep is None:
            return None
        status, _c, body, _t = client.call("POST", collection, synth.value(ep.get("request") or {}), token=token)
        if not 200 <= status < 300:
            return None
        for param in [*param_names, "id"]:
            if (value := first_id(body, param)) is not None:
                return value
        return None

    def resolve(path: str, *, fresh: bool = False):
        def sub(m):
            param = m.group(1) or m.group(2)
            base = collection_of(path)
            if fresh:
                keys = [key for key in (base, base.replace("/api/admin/", "/api/")) if key in creators]
                for key in keys:
                    if (value := create(key)) is not None:
                        return str(value)
                if keys:
                    # Falling back to the shared id would hand this check an item an
                    # earlier destructive check already consumed (v2: DELETE 404).
                    raise LookupError(f"could not create a fresh resource for {path}")
            for key in (base, base.replace("/api/admin/", "/api/")):
                if key in created:
                    return str(created[key])
            for key in (base, base.replace("/api/admin/", "/api/")):
                status, _c, body, _t = client.call("GET", key, token=admin_token or token)
                if status == 200 and (value := first_id(body, param)) is not None:
                    created[key] = value
                    return str(value)
            raise LookupError(f"no {param} available for {path}")

        return PARAM_RE.sub(sub, path)

    def rank(ep):
        # list -> create -> read one -> update -> item actions -> delete
        method, has_param = ep["method"].upper(), bool(PARAM_RE.search(ep["path"]))
        order = {
            ("GET", False): 0,
            ("POST", False): 1,
            ("GET", True): 2,
            ("PUT", True): 3,
            ("PATCH", True): 3,
            ("POST", True): 4,
            ("DELETE", True): 5,
        }.get((method, has_param), 4)
        return (order, ep["path"])

    for ep in sorted(endpoints, key=rank):
        method = ep["method"].upper()
        if method == "POST" and ep["path"] in auth_paths and contract.get("accounts"):
            continue  # exercised by the accounts checks
        category = "admin" if ep.get("admin") else "api"
        name = f"{method} {ep['path']}"
        mutates_item = bool(PARAM_RE.search(ep["path"])) and method in ("DELETE", "POST")
        try:
            path = resolve(ep["path"], fresh=mutates_item)
        except LookupError as exc:
            report.add(category, name, False, str(exc))
            continue
        body = synth.value(ep.get("request")) if method in WRITE_METHODS and ep.get("request") else None
        if method in ("POST", "PUT", "PATCH") and body is None:
            body = {}
        use_token = admin_token if category == "admin" else token
        status, _ctype, parsed, text = client.call(method, path, body, token=use_token)
        problems = [] if 200 <= status < 300 else [f"status {status}: {text[:160]}"]
        spec_response = ep.get("response")
        if not problems and spec_response not in (None, {}, []):
            if parsed is None:
                problems.append("response is not JSON")
            else:
                problems += shape_problems(spec_response, parsed)
        if not problems and method == "POST" and not PARAM_RE.search(ep["path"]):
            for param in [*param_names, "id"]:
                if (value := first_id(parsed, param)) is not None:
                    created[ep["path"]] = value
                    break
        report.add(category, name, not problems, "; ".join(problems))

    return {"version": VERSION, "checks": report.checks, "summary": report.summary()}


def main() -> None:
    with open(sys.argv[1], encoding="utf-8") as fh:
        plan = json.load(fh)
    try:
        result = run(plan)
    except Exception as exc:
        result = {"version": VERSION, "error": f"tester crashed: {exc!r}", "checks": [], "summary": {}}
    sys.stdout.write("LLMLAB-CONFORMANCE " + json.dumps(result) + "\n")


if __name__ == "__main__":
    main()
