"""API seeding for the dynamic scanners (ZAP, Wapiti).

A scanner's crawler does not run JavaScript, so on a single-page app it sees the start page and
nothing else: the JSON API, where injection and access-control flaws live, is never attacked. A
generated app has a task spec that lists every endpoint with its request schema and the sign-in
flow, so before the dynamic tools run the runner:

1. signs in from the workspace (the admin when the task has one and the run's stored credentials
   work, else a freshly registered user) and creates one object per collection, so paths with an
   ``{id}`` point at something that exists;
2. writes ``/tmp/dast/openapi.json`` (OpenAPI 3.0 of the task's endpoints, with example bodies and
   ids), ``/tmp/dast/headers`` (one ``Name: value`` line per credential header) and
   ``/tmp/dast/zap-plan.yaml`` (a ZAP Automation Framework plan: import the API, spider, active scan).

The tools' ``run_cmd`` use those files when they exist and fall back to the plain crawl when they
do not (pasted code, an app without a task spec). What a result was produced with is recorded in
``ToolResult.summary["scan_scope"]``; the credential itself is never stored.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from typing import Any

from backend.analysis.services import workspace_service
from backend.runtime.services import docker_manager

logger = logging.getLogger(__name__)

DAST_DIR = "/tmp/dast"  # noqa: S108 - inside the analyzer workspace container
OPENAPI_FILE = f"{DAST_DIR}/openapi.json"
HEADERS_FILE = f"{DAST_DIR}/headers"
ZAP_PLAN_FILE = f"{DAST_DIR}/zap-plan.yaml"
ZAP_REPORT_DIR = "/tmp/zap"  # noqa: S108 - inside the analyzer workspace container (the zap tool's run_cmd)
SEED_VERSION = 3
SEED_TIMEOUT_S = 90
ADMIN_USERNAME = "admin"
_SCAN_PASSWORD = "Dast-Scan-9x!Pw"  # noqa: S105 - throwaway account in a throwaway app container
_PATH_PARAM_RE = re.compile(r"\{([^{}/]+)\}")
_SCHEMA_KEYS = ("enum", "minLength", "maxLength", "minimum", "maximum", "format", "pattern")


# --- Example values ---------------------------------------------------------------------------


def _first_type(schema: dict) -> str:
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "string")
    if not isinstance(kind, str):
        kind = "object" if "properties" in schema else "string"
    return kind


def _example_string(name: str, schema: dict) -> str:
    low = name.lower()
    fmt = str(schema.get("format") or "")
    if fmt == "email" or "email" in low:
        value = "dast.scan@example.com"
    elif fmt in ("uri", "url") or low.endswith(("url", "link", "href")):
        value = "http://example.com/"
    elif fmt == "date-time" or low.endswith(("_at", "datetime", "timestamp")):
        value = "2026-01-15T10:00:00Z"
    elif fmt == "date" or low.endswith("date") or low in ("day", "due"):
        value = "2026-01-15"
    elif fmt == "time" or low.endswith("time"):
        value = "10:00"
    elif "color" in low or "colour" in low:
        value = "#336699"
    elif "phone" in low:
        value = "+15550100"
    else:
        value = "scan"
    low_len, high_len = schema.get("minLength"), schema.get("maxLength")
    if isinstance(low_len, int) and len(value) < low_len:
        value = (value * (low_len // len(value) + 1))[:low_len]
    if isinstance(high_len, int) and len(value) > high_len:
        value = value[:high_len]
    return value


def example_value(schema: Any, name: str = "") -> Any:  # noqa: PLR0911 - one return per JSON type
    """A value that satisfies *schema* (a JSON Schema fragment of a task spec), as far as it says."""
    if not isinstance(schema, dict):
        return "scan"
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        return schema["enum"][0]
    kind = _first_type(schema)
    if kind == "object":
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        return {key: example_value(sub, key) for key, sub in props.items()}
    if kind == "array":
        return [example_value(schema.get("items") or {}, name)]
    if kind == "boolean":
        return True
    if kind in ("integer", "number"):
        low = schema.get("minimum")
        value = 1 if not isinstance(low, (int, float)) or low <= 1 else low
        high = schema.get("maximum")
        if isinstance(high, (int, float)) and value > high:
            value = high
        return int(value) if kind == "integer" else value
    return _example_string(name, schema)


def _openapi_schema(schema: Any, name: str = "") -> dict:
    """*schema* as an OpenAPI 3.0 schema object (single ``type``, ``nullable``) carrying an example."""
    if not isinstance(schema, dict):
        return {"type": "string"}
    kind = _first_type(schema)
    out: dict[str, Any] = {"type": kind}
    if isinstance(schema.get("type"), list) and "null" in schema["type"]:
        out["nullable"] = True
    for key in _SCHEMA_KEYS:
        if key in schema:
            out[key] = schema[key]
    if kind == "object":
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        out["properties"] = {key: _openapi_schema(sub, key) for key, sub in props.items()}
        required = [key for key in schema.get("required") or [] if key in props]
        if required:
            out["required"] = required
    elif kind == "array":
        out["items"] = _openapi_schema(schema.get("items") or {}, name)
    else:
        out["example"] = example_value(schema, name)
    return out


# --- The plan: what to sign in with, what to create, what to scan ---------------------------------


def _endpoints(content: dict) -> list[dict]:
    return [e for e in content.get("endpoints") or [] if isinstance(e, dict) and e.get("path") and e.get("method")]


def _by_id(content: dict) -> dict[str, dict]:
    return {str(e.get("id")): e for e in _endpoints(content)}


def _credentials_body(endpoint: dict | None, username: str, password: str) -> dict:
    """The JSON an auth endpoint takes: its schema's example with the account's name and password."""
    body = example_value((endpoint or {}).get("request_schema") or {}) if endpoint else {}
    if not isinstance(body, dict):
        body = {}
    body.update({"username": username, "password": password})
    for key in body:
        if "email" in key.lower():
            body[key] = f"{username}@example.com"
        elif key.lower() in ("password_confirm", "confirm_password", "password2", "confirm"):
            body[key] = password
    return body


def _auth_driver(content: dict, auth_mode: str) -> dict:
    """How this run's app signs users in: the evaluation's own driver for the run's auth mode (the
    contract modes fix the paths and the credential; ``spec_defined`` follows the task's ``auth_flow``)."""
    from backend.generation.evaluation.acceptance import auth_config

    try:
        driver = auth_config(content, auth_mode or "spec_defined").model_dump()
    except Exception:  # noqa: BLE001 - an unknown mode or a malformed flow: scan without signing in
        logger.warning("No auth driver for auth mode %r", auth_mode, exc_info=True)
        return {}
    flow = content.get("auth_flow") if isinstance(content.get("auth_flow"), dict) else {}
    csrf = _by_id(content).get(str(flow.get("csrf_endpoint"))) if driver.get("mode") == "spec_defined" else None
    driver["csrf_path"] = (csrf or {}).get("path")
    return driver


def _post_endpoint(content: dict, path: str | None) -> dict | None:
    return next((e for e in _endpoints(content) if e["method"] == "POST" and e["path"] == path), None)


def seed_plan(content: dict, *, auth_mode: str = "spec_defined", admin_password: str = "") -> dict:
    """What the workspace-side script does for this task: accounts to sign in and objects to create."""
    driver = _auth_driver(content, auth_mode)
    register_path, login_path = driver.get("register_path"), driver.get("login_path")
    has_auth = bool(driver) and ("auth" in (content.get("features") or []) or bool(content.get("auth_flow")))
    register = _post_endpoint(content, register_path)
    login = _post_endpoint(content, login_path)
    username = f"dast_{secrets.token_hex(4)}"
    accounts = []
    if has_auth and admin_password and "admin" in (content.get("features") or []):
        accounts.append(
            {
                "identity": "admin",
                "login": _credentials_body(None, str(driver.get("admin_username") or ADMIN_USERNAME), admin_password),
                "register": None,
            },
        )
    if has_auth:
        accounts.append(
            {
                "identity": "user",
                "login": _credentials_body(login, username, _SCAN_PASSWORD),
                "register": _credentials_body(register, username, _SCAN_PASSWORD) if register_path else None,
            },
        )
    auth_paths = {register_path, login_path, driver.get("logout_path"), driver.get("csrf_path")}
    creates = [
        {"path": e["path"], "body": example_value(e.get("request_schema") or {})}
        for e in _endpoints(content)
        if e["method"] == "POST"
        and e["path"] not in auth_paths
        and "{" not in e["path"]
        and e.get("content_type", "json") == "json"
        and isinstance(e.get("request_schema"), dict)
    ]
    # A GET only a signed-in user may call: answered 2xx with the credential, the scan is authenticated.
    check = next(
        (
            e["path"]
            for e in _endpoints(content)
            if e["method"] == "GET" and e.get("auth") in ("user", "owner") and "{" not in e["path"]
        ),
        None,
    )
    return {
        "check_path": check,
        "accounts": accounts,
        "register_path": register_path,
        "login_path": login_path or "/api/auth/login",
        "logout_path": driver.get("logout_path"),
        "credential": driver.get("credential") or "bearer",
        "token_json_path": driver.get("token_json_path") or "token",
        "csrf": driver.get("csrf") or "none",
        "csrf_cookie": driver.get("csrf_cookie") or "csrf_token",
        "csrf_header": driver.get("csrf_header") or "X-CSRF-Token",
        "csrf_path": driver.get("csrf_path"),
        "creates": creates,
    }


# Runs in the workspace (Python 3 standard library only). Reads the plan as JSON on stdin and prints
# {"identity", "headers", "ids", "error"} as JSON. Never prints a password.
SEED_SCRIPT = r'''
import json, sys, urllib.error, urllib.request

plan = json.load(sys.stdin)
base = plan["base_url"].rstrip("/")


def call(cookies, method, path, body=None, headers=None):
    """Send one request; *cookies* (a dict, or None for no cookie handling) is sent and updated."""
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    if cookies:
        hdrs["Cookie"] = "; ".join("%s=%s" % kv for kv in cookies.items())
    req = urllib.request.Request(base + path, data=data, method=method, headers=hdrs)
    try:
        try:
            resp = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as exc:
            resp = exc
        status, raw = resp.status if hasattr(resp, "status") else resp.code, resp.read(200000)
        if cookies is not None:
            # Read by hand like the evaluation's tester: a cookie jar drops Secure cookies over http.
            for line in resp.headers.get_all("Set-Cookie") or []:
                name, _, value = line.split(";", 1)[0].strip().partition("=")
                if name:
                    cookies[name] = value
    except Exception as exc:
        return 0, None, type(exc).__name__ + ": " + str(exc)[:120]
    try:
        return status, json.loads(raw.decode("utf-8", "replace")), ""
    except ValueError:
        return status, None, ""


def dig(obj, path):
    for part in str(path).split("."):
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj


def sign_in(account):
    cookies = {}
    if plan["csrf"] == "header_from_endpoint" and plan.get("csrf_path"):
        call(cookies, "GET", plan["csrf_path"])
    if account.get("register") is not None and plan.get("register_path"):
        status, _, err = call(cookies, "POST", plan["register_path"], account["register"])
        if err or (status >= 300 and status != 409):
            return None, "register answered %s %s" % (status, err)
    status, body, err = call(cookies, "POST", plan["login_path"], account["login"])
    if err or status >= 300:
        return None, "login answered %s %s" % (status, err)
    headers = []
    if plan["credential"] == "bearer":
        token = dig(body, plan["token_json_path"])
        if not isinstance(token, str) or not token:
            return None, "login response has no token"
        headers.append(["Authorization", "Bearer " + token])
    else:
        if not cookies:
            return None, "login set no cookie"
        csrf = None
        if plan["csrf"] == "header_from_endpoint" and plan.get("csrf_path"):
            _, got, _ = call(cookies, "GET", plan["csrf_path"])
            csrf = got.get("csrf_token") if isinstance(got, dict) else None
        elif plan["csrf"] == "double_submit_cookie":
            csrf = cookies.get(plan["csrf_cookie"])
        headers.append(["Cookie", "; ".join("%s=%s" % kv for kv in cookies.items())])
        if isinstance(csrf, str) and csrf:
            headers.append([plan["csrf_header"], csrf])
    return headers, ""


out = {"identity": "anonymous", "headers": [], "ids": {}, "error": ""}
errors = []
for account in plan["accounts"]:
    headers, error = sign_in(account)
    if headers is not None:
        out["identity"], out["headers"] = account["identity"], headers
        break
    errors.append(account["identity"] + ": " + error)
out["error"] = "; ".join(errors)[:300]
if plan.get("check_path") and out["headers"]:
    out["auth_check_status"] = call(None, "GET", plan["check_path"], None, dict(out["headers"]))[0]
for create in plan["creates"]:
    status, body, _ = call(None, "POST", create["path"], create["body"], dict(out["headers"]))
    found = body.get("id") if isinstance(body, dict) else None
    if found is None and isinstance(body, dict):
        found = next((v.get("id") for v in body.values() if isinstance(v, dict) and v.get("id") is not None), None)
    if 200 <= status < 300 and isinstance(found, (int, str)) and not isinstance(found, bool):
        out["ids"][create["path"]] = found
print(json.dumps(out))
'''


# --- OpenAPI document and ZAP plan ------------------------------------------------------------------


def _param_example(path: str, ids: dict[str, Any]) -> Any:
    """An existing id for a ``{param}`` of *path*: the object created under its longest collection prefix."""
    prefix = path.split("{", 1)[0].rstrip("/")
    best = max((p for p in ids if prefix == p or prefix.startswith(p.rstrip("/") + "/")), key=len, default=None)
    return ids[best] if best is not None else 1


def _query_schema(param: dict) -> dict:
    kind = {"int": "integer", "float": "number", "bool": "boolean"}.get(str(param.get("type")), "string")
    schema: dict[str, Any] = {"type": kind}
    if param.get("enum"):
        schema["enum"] = list(param["enum"])
    default = param.get("default")
    if default is not None:
        schema["example"] = default
    elif schema.get("enum"):
        schema["example"] = schema["enum"][0]
    else:
        schema["example"] = example_value(schema, str(param.get("name") or ""))
    return schema


def openapi_document(
    content: dict,
    base_url: str,
    ids: dict[str, Any] | None = None,
    *,
    skip_paths: tuple[str | None, ...] = (),
) -> dict:
    """OpenAPI 3.0 of the task's HTTP endpoints, without *skip_paths* and without websocket and
    event-stream endpoints (not request/response).

    The caller skips sign-out (attacking it ends the scan's own session) and registration: every
    request to it creates an account, so the same request is answered "created" once and "already
    exists" afterwards, and a boolean-based injection rule reads that flip as its payload changing
    the result (ZAP raised SQL injection and path traversal on it in the pilot). Sign-in stays."""
    ids = ids or {}
    paths: dict[str, dict] = {}
    for endpoint in _endpoints(content):
        if endpoint["path"] in skip_paths or endpoint.get("realtime", "none") != "none":
            continue
        path, method = str(endpoint["path"]), str(endpoint["method"]).lower()
        operation: dict[str, Any] = {
            "operationId": re.sub(r"[^A-Za-z0-9_]", "_", str(endpoint.get("id") or f"{method}_{path}")),
            "responses": {str(endpoint.get("success_status") or 200): {"description": "success"}},
        }
        params = []
        for name in _PATH_PARAM_RE.findall(path):
            example = _param_example(path, ids)
            kind = "integer" if isinstance(example, int) else "string"
            params.append(
                {"name": name, "in": "path", "required": True, "schema": {"type": kind}, "example": example},
            )
        for param in endpoint.get("query") or []:
            if isinstance(param, dict) and param.get("name"):
                schema = _query_schema(param)
                params.append(
                    {
                        "name": str(param["name"]),
                        "in": "query",
                        "required": bool(param.get("required")),
                        "schema": schema,
                        "example": schema["example"],
                    },
                )
        if params:
            operation["parameters"] = params
        schema = endpoint.get("request_schema")
        if isinstance(schema, dict) and method != "get":
            kind = str(endpoint.get("content_type") or "json")
            media = {"multipart": "multipart/form-data", "form": "application/x-www-form-urlencoded"}.get(
                kind,
                "application/json",
            )
            body: dict[str, Any] = {"schema": _openapi_schema(schema)}
            if media == "application/json":
                body["example"] = example_value(schema)
            operation["requestBody"] = {"required": True, "content": {media: body}}
        paths.setdefault(path, {})[method] = operation
    return {
        "openapi": "3.0.3",
        "info": {"title": str(content.get("title") or "Generated app"), "version": "1"},
        "servers": [{"url": base_url.rstrip("/")}],
        "paths": paths,
    }


def zap_plan(base_url: str, headers: list[list[str]]) -> str:
    """A ZAP Automation Framework plan as YAML (JSON is YAML): credential headers on every request,
    the API imported from the OpenAPI file, the plain spider, the active scan, the JSON report.
    The quoted ``__ZAP_SPIDER_MIN__`` / ``__ZAP_SCAN_MIN__`` are replaced with the tool's options (numbers)
    by its ``run_cmd``."""
    base = base_url.rstrip("/")
    rules = [
        {
            "description": f"scan credential {index}",
            "matchType": "req_header",
            "matchString": name,
            "matchRegex": False,
            "replacementString": value,
        }
        for index, (name, value) in enumerate(headers)
    ]
    jobs: list[dict[str, Any]] = []
    if rules:
        jobs.append({"type": "replacer", "parameters": {"deleteAllRules": True}, "rules": rules})
    jobs += [
        {"type": "openapi", "parameters": {"apiFile": OPENAPI_FILE, "targetUrl": base, "context": "app"}},
        {"type": "spider", "parameters": {"context": "app", "url": f"{base}/", "maxDuration": "__ZAP_SPIDER_MIN__"}},
        {"type": "passiveScan-wait", "parameters": {"maxDuration": 2}},
        {
            "type": "activeScan",
            "parameters": {"context": "app", "maxScanDurationInMins": "__ZAP_SCAN_MIN__", "threadPerHost": 4},
        },
        {
            "type": "report",
            "parameters": {"template": "traditional-json", "reportDir": ZAP_REPORT_DIR, "reportFile": "report.json"},
        },
    ]
    plan = {
        "env": {
            "contexts": [{"name": "app", "urls": [base], "includePaths": [f"{re.escape(base)}.*"]}],
            "parameters": {"failOnError": False, "failOnWarning": False, "progressToStdout": False},
        },
        "jobs": jobs,
    }
    return json.dumps(plan, indent=1)


# --- Orchestration ------------------------------------------------------------------------------------


def _task_content(run) -> dict | None:
    job = run.generation_job if run.generation_job_id and not run.source_code else None
    version = getattr(job, "task_version", None) if job is not None else None
    content = getattr(version, "content", None)
    return content if isinstance(content, dict) and _endpoints(content) else None


def _admin_password(run) -> str:
    try:
        from backend.generation import evaluation

        record = evaluation.credentials(run.generation_job)
    except Exception:  # noqa: BLE001 - no record: scan as a registered user
        return ""
    password = record.get("admin_password") if isinstance(record, dict) else None
    return password if isinstance(password, str) else ""


def clear(container_name: str) -> None:
    """Remove a previous run's seed files, so a tool never scans with another app's API or credential."""
    docker_manager.exec_in(container_name, f"rm -rf {DAST_DIR}", timeout_s=15)


def prepare(run, container_name: str, app_url: str) -> dict:
    """Seed the workspace for *run*'s dynamic tools and return the ``scan_scope`` record.

    ``{"seeded": False, "reason": ...}`` when the run has no task spec or the files could not be
    written: the tools then crawl from the start page as before.
    """
    clear(container_name)
    content = _task_content(run)
    if content is None:
        return {"seeded": False, "reason": "no task spec with endpoints (the scanners crawl from the start page)"}
    auth_mode = str(getattr(run.generation_job, "auth_mode", "") or "spec_defined")
    plan = {**seed_plan(content, auth_mode=auth_mode, admin_password=_admin_password(run)), "base_url": app_url}
    skip = (plan["register_path"], plan["logout_path"])
    session: dict[str, Any] = {
        "identity": "anonymous",
        "headers": [],
        "ids": {},
        "error": "",
        "auth_check_status": None,
    }
    owner = workspace_service.WORKSPACE_UID
    copied = docker_manager.copy_files_in(
        container_name,
        {"seed.py": SEED_SCRIPT, "plan.json": json.dumps(plan)},
        DAST_DIR,
        owner=owner,
    )
    if "error" not in copied:
        ran = docker_manager.exec_in(
            container_name,
            f"python3 {DAST_DIR}/seed.py < {DAST_DIR}/plan.json; rm -f {DAST_DIR}/plan.json {DAST_DIR}/seed.py",
            timeout_s=SEED_TIMEOUT_S,
        )
        try:
            parsed = json.loads((ran.get("output") or "").strip().splitlines()[-1])
            if isinstance(parsed, dict):
                session.update({key: parsed[key] for key in session if key in parsed})
        except (ValueError, IndexError):
            session["error"] = f"sign-in script failed (exit {ran.get('exit_code')})"
    else:
        session["error"] = "could not copy the sign-in script into the workspace"
    headers = [[str(n), str(v)] for n, v in session.get("headers") or [] if "\n" not in str(n) + str(v)]
    ids = session["ids"] if isinstance(session.get("ids"), dict) else {}
    document = openapi_document(content, app_url, ids, skip_paths=skip)
    openapi_text = json.dumps(document, indent=1, sort_keys=True)
    files = {
        "openapi.json": openapi_text,
        "headers": "".join(f"{name}: {value}\n" for name, value in headers),
        "zap-plan.yaml": zap_plan(app_url, headers),
    }
    written = docker_manager.copy_files_in(container_name, files, DAST_DIR, owner=owner)
    if "error" in written:
        clear(container_name)
        return {"seeded": False, "reason": f"could not write the seed files: {written['error']}"[:300]}
    operations = sum(len(methods) for methods in document["paths"].values())
    # The document without its server address and created ids: the same for every app of a task.
    stable = json.dumps(openapi_document(content, "http://app", skip_paths=skip), sort_keys=True)
    return {
        "seeded": True,
        "seed_version": SEED_VERSION,
        "operations": operations,
        "identity": session.get("identity") or "anonymous",
        "auth_error": str(session.get("error") or "")[:300],
        # Status of a user-only GET sent with the credential (None: no such endpoint, or not signed in).
        "auth_check_status": session.get("auth_check_status"),
        "objects_created": len(ids),
        "collections": len(plan["creates"]),
        "openapi_sha": hashlib.sha256(stable.encode()).hexdigest()[:16],
    }
