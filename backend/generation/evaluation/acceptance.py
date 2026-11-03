"""TaskSpec to ``AcceptancePlan`` compiler (spec sections 8.1-8.4; ``COMPILER_VERSION = 1``).

``compile(task_version, auth_mode, stack)`` turns the structured endpoints of a task into tester checks
(table of 8.1), merges the task's hand-written ``acceptance`` (an ``id`` collision means the hand-written
check wins, 8.2), tags every check with the prose variants that state it (``library.lint.tag_checks``; the
hand-written ones read ``TaskSpecVersion.derived.check_statedness``) and compiles the task's probes with
their preconditions and positive controls (8.3). The output is a ``TesterPlan``: the ``AcceptancePlan``
contract plus the facts the tester needs that the contract does not carry (signed webhook routes, the health
path, the mock server host). It never contains a secret: ``ADMIN_PASSWORD`` and ``WEBHOOK_SECRET`` reach the
tester through its environment.

Generated check ids (stable, the slot linker and results key on them):

=========================  ============  ==========================================================
``health``                 infra         ``GET <run_contract.health_path>`` -> 200
``index_served``           infra         ``GET /`` -> 200 text/html (backend-served frontend only)
``spa_fallback``           infra         ``GET /llmlab/client/route`` -> 200 text/html (same)
``<ep>.ok``                functional    success status and response schema (actor by ``auth``)
``<ep>.anon_401``          auth          no credentials -> 401 (``user``/``owner``/``admin``)
``<ep>.other_user``        ownership     user_b on user_a's resource -> 403/404 (``owner``)
``<ep>.non_admin_403``     auth          user_a -> 403 (``admin``)
``<ep>.invalid.missing``   validation    a required body field dropped -> 400/422
``<ep>.invalid.too_long``  validation    a string one past ``maxLength`` -> 400/422
``<ep>.invalid.enum``      validation    a value outside ``enum`` -> 400/422
``<ep>.duplicate``         negative      the same unique value twice -> 409 (when 409 is listed)
``<ep>.oversize``          negative      a 12 MiB body -> 413 (when 413 is listed)
``<ep>.page_size``         pagination    ``page_size=2`` with three items -> exactly two
``<ep>.filter.<param>``    filter        an enum/bool filter returns only matching items
``<ep>.search``            filter        a search term finds the item that holds it
``<ep>.persists``          persistence   create, restart the app, read it back
=========================  ============  ==========================================================

Every generated check ``depends_on`` ``health``. A check that needs a resource gets setup steps that create
it through the collection's ``POST`` endpoint (recursively for nested paths) and capture the path
parameters; a check whose resource cannot be created this way is not generated (the hand-written
acceptance covers it). ``checks_for_endpoints`` lists the check ids linked to endpoints (slot targets).
"""

from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from pydantic import Field

from backend.generation.contracts.evaluation import AcceptancePlan
from backend.generation.contracts.evaluation import AuthDriverConfig
from backend.generation.contracts.evaluation import CompiledCheck
from backend.generation.contracts.evaluation import CompiledProbe

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Mapping

    from backend.generation.models import StackProfile
    from backend.generation.models import TaskSpecVersion

__all__ = [
    "AUTH_MODES",
    "COMPILER_VERSION",
    "TesterPlan",
    "auth_config",
    "checks_for_endpoints",
    "compile",
    "compile_content",
    "plan_json",
]

COMPILER_VERSION: Final = 1
AUTH_MODES: Final = ("contract_bearer", "cookie_session", "spec_defined")
DEFAULT_BASE_URL: Final = "http://app:8000"
_PARAM: Final = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SETUP_DEPTH: Final = 3
_TEST_PASSWORD: Final = "Llmlab-pw-{rand}-9!"  # noqa: S105 - a throwaway test credential


class TesterPlan(AcceptancePlan):
    """``/in/plan.json``: the ``AcceptancePlan`` contract plus tester facts it does not carry."""

    health_path: str = "/api/health"
    signed_routes: list[dict[str, str]] = Field(default_factory=list)  # webhook routes the tester signs
    mock_host: str = "mock.internal"
    mock_port: int = 9009
    has_frontend: bool = False


# =====================================================================================
# Auth drivers: 8.4
# =====================================================================================


def _endpoint_map(content: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(ep["id"]): ep for ep in content.get("endpoints") or []}


def auth_config(content: Mapping[str, Any], auth_mode: str) -> AuthDriverConfig:
    """The tester's driver for ``auth_mode`` (contract modes are fixed; ``spec_defined`` follows ``auth_flow``)."""
    if auth_mode == "contract_bearer":
        return AuthDriverConfig(mode="contract_bearer")
    if auth_mode == "cookie_session":
        return AuthDriverConfig(
            mode="cookie_session",
            credential="cookie",
            token_json_path=None,
            csrf="double_submit_cookie",
        )
    if auth_mode != "spec_defined":
        msg = f"unknown auth mode {auth_mode!r}"
        raise ValueError(msg)
    flow = dict(content.get("auth_flow") or {})
    endpoints = _endpoint_map(content)

    def path_of(endpoint_id: str | None) -> str | None:
        return str(endpoints[endpoint_id]["path"]) if endpoint_id and endpoint_id in endpoints else None

    credential = flow.get("credential") or "bearer"
    return AuthDriverConfig.model_validate(
        {
            "mode": "spec_defined",
            "register_path": path_of(flow.get("register")),
            "login_path": path_of(flow.get("login")) or "/api/auth/login",
            "logout_path": path_of(flow.get("logout")),
            "credential": credential,
            "token_json_path": flow.get("token_json_path") or ("token" if credential == "bearer" else None),
            "csrf": flow.get("csrf") or "none",
            "auth_flow": flow or None,
        },
    )


# =====================================================================================
# Sample values from request schemas
# =====================================================================================


def _types(schema: Mapping[str, Any]) -> list[str]:
    value = schema.get("type")
    if isinstance(value, list):
        return [str(t) for t in value if t != "null"] or ["null"]
    return [str(value)] if value else ["string"]


def _sample(name: str, schema: Mapping[str, Any], *, unique: bool = False) -> Any:  # noqa: PLR0911
    """A valid value for ``schema`` (deterministic; unique strings carry ``{rand}``)."""
    if schema.get("enum"):
        options = [v for v in schema["enum"] if v is not None]
        return options[0] if options else None
    if schema.get("default") is not None and not unique:
        return schema["default"]
    kind = _types(schema)[0]
    fmt = schema.get("format")
    if kind == "integer":
        return max(int(schema.get("minimum", 1) or 1), 1)
    if kind == "number":
        return float(max(schema.get("minimum", 1) or 1, 1))
    if kind == "boolean":
        return False
    if kind == "array":
        items = schema.get("items") if isinstance(schema.get("items"), dict) else {"type": "string"}
        return [_sample(name, items)]
    if kind == "object":
        return _body(schema, unique_fields=set())
    if fmt == "date":
        return "2030-01-15"
    if fmt == "date-time":
        return "2030-01-15T10:00:00Z"
    if fmt == "email" or "email" in name:
        return "llmlab{rand}@example.com" if unique else "llmlab@example.com"
    if fmt in ("uri", "url") or name in ("url", "link", "website"):
        return "https://example.com/llmlab"
    base = f"llmlab {name} {{rand}}" if unique else f"llmlab {name}"
    if unique and name in ("username", "login", "name", "slug"):
        base = f"llmlab{name[:4]}{{rand}}"
    max_len = schema.get("maxLength")
    if isinstance(max_len, int) and len(base) > max_len:
        base = ("{rand}" if unique and max_len >= 8 else "x" * max(int(schema.get("minLength", 1) or 1), 1))[:max_len]
    return base


def _body(schema: Mapping[str, Any] | None, *, unique_fields: set[str]) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    out: dict[str, Any] = {}
    for name, sub in (schema.get("properties") or {}).items():
        if not isinstance(sub, dict) or sub.get("format") == "binary" or sub.get("readOnly"):
            continue
        out[name] = _sample(name, sub, unique=name in unique_fields)
    return out


def _file_fields(schema: Mapping[str, Any] | None) -> list[str]:
    props = (schema or {}).get("properties") or {}
    return [
        n
        for n, s in props.items()
        if isinstance(s, dict) and (s.get("format") == "binary" or s.get("contentMediaType"))
    ]


def _unique_fields(content: Mapping[str, Any]) -> set[str]:
    return {str(f["name"]) for e in content.get("entities") or [] for f in e.get("fields") or [] if f.get("unique")}


def _qtype(q: Mapping[str, Any]) -> str:
    return {"int": "integer", "float": "number", "bool": "boolean", "enum": "string"}.get(str(q.get("type")), "string")


_FIELD_TYPES: Final = {
    "string": "string",
    "text": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "enum": "string",
    "date": "string",
    "email": "string",
    "url": "string",
}


def _entity_for(endpoint: Mapping[str, Any], content: Mapping[str, Any]) -> dict[str, Any] | None:
    """The entity an endpoint writes: ``owner_entity``, ``User`` for the auth endpoints, else by collection name."""
    entities = {str(e["name"]).lower(): e for e in content.get("entities") or []}
    if endpoint.get("owner_entity"):
        return entities.get(str(endpoint["owner_entity"]).lower())
    path = str(endpoint["path"])
    if path.startswith("/api/auth/"):
        return entities.get("user")
    segments = [s for s in path.strip("/").split("/") if s and not _PARAM.fullmatch(s)]
    if not segments:
        return None
    name = segments[-1].lower()
    for candidate in (
        name,
        name[:-3] + "y" if name.endswith("ies") else name,
        name[:-2] if name.endswith("es") else name,
        name[:-1],
    ):
        if candidate in entities:
            return entities[candidate]
    return None


def _entity_schema(endpoint: Mapping[str, Any], content: Mapping[str, Any]) -> dict[str, Any] | None:
    """A request schema derived from the written entity's fields (tasks without ``request_schema``)."""
    entity = _entity_for(endpoint, content)
    if entity is None:
        return None
    props: dict[str, Any] = {}
    required: list[str] = []
    for f in entity.get("fields") or []:
        kind = str(f.get("type"))
        if kind not in _FIELD_TYPES or f.get("ref"):
            continue  # datetimes and references are server-side
        prop: dict[str, Any] = {"type": _FIELD_TYPES[kind]}
        if f.get("max_length"):
            prop["maxLength"] = int(f["max_length"])
        if f.get("enum"):
            prop["enum"] = list(f["enum"])
        if kind == "date":
            prop["format"] = "date"
        if f.get("default") is not None:
            prop["default"] = f["default"]
        props[str(f["name"])] = prop
        if f.get("required"):
            required.append(str(f["name"]))
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required and endpoint.get("method") != "PATCH":
        schema["required"] = required
    return schema


def _request_for(endpoint: Mapping[str, Any], content: Mapping[str, Any]) -> dict[str, Any]:
    """A valid request for ``endpoint`` (path parameters left as ``{name}`` placeholders)."""
    method = str(endpoint["method"])
    req: dict[str, Any] = {"method": method, "path": str(endpoint["path"])}
    query = {}
    for q in endpoint.get("query") or []:
        if q.get("required"):
            schema: dict[str, Any] = {"type": _qtype(q)}
            if q.get("enum"):
                schema["enum"] = q["enum"]
            if q.get("default") is not None:
                schema["default"] = q["default"]
            query[str(q["name"])] = _sample(str(q["name"]), schema)
    if query:
        req["query"] = query
    if method in ("POST", "PUT", "PATCH"):
        schema_body = endpoint.get("request_schema") or _entity_schema(endpoint, content)
        unique = _unique_fields(content)
        if str(endpoint["path"]).startswith("/api/auth/"):
            unique |= {"username", "email"}
        body = _body(schema_body, unique_fields=unique)
        if "password" in body:
            body["password"] = _TEST_PASSWORD
        if "username" in body and not str(endpoint["path"]).startswith("/api/auth/"):
            body["username"] = "{user_b_username}"  # a body naming a user names an existing one (user_b)
        ctype = endpoint.get("content_type", "json")
        if ctype == "multipart":
            files = _file_fields(schema_body) or ["file"]
            req["files"] = {
                name: {"filename": "llmlab.txt", "content": "llmlab file {rand}\n", "content_type": "text/plain"}
                for name in files
            }
            req["form"] = {k: v for k, v in body.items() if not isinstance(v, (dict, list))}
        elif ctype == "form":
            req["form"] = body
        elif ctype != "none":
            req["json"] = body
    return req


# =====================================================================================
# Resource setup: create the resource a path needs
# =====================================================================================


def _collection(path: str) -> tuple[str, str] | None:
    """``/api/todos/{id}`` -> (``/api/todos``, ``id``): the collection path and the item parameter."""
    parts = path.rstrip("/").split("/")
    for i in range(len(parts) - 1, 0, -1):
        m = _PARAM.fullmatch(parts[i])
        if m:
            return "/".join(parts[:i]) or "/", m.group(1)
    return None


def _create_endpoint(collection: str, endpoints: list[dict[str, Any]]) -> dict[str, Any] | None:
    posts = [ep for ep in endpoints if ep["method"] == "POST" and not str(ep["path"]).startswith("/api/auth/")]
    for ep in posts:
        if str(ep["path"]).rstrip("/") == collection.rstrip("/"):
            return ep
    last = collection.rstrip("/").rsplit("/", 1)[-1]
    if not last or _PARAM.fullmatch(last):
        return None
    for ep in posts:
        if str(ep["path"]).rstrip("/").rsplit("/", 1)[-1] == last:
            return ep
    return None


def _capture_path(param: str, create: Mapping[str, Any]) -> str:
    props = ((create.get("response_schema") or {}).get("properties")) or {}
    return f"$.{param}" if param in props else "$.id"


def _setup_for(path: str, actor: str, content: Mapping[str, Any], *, depth: int = 0) -> list[dict[str, Any]] | None:  # noqa: PLR0911
    """Setup steps that create the resources ``path``'s parameters name, else ``None`` (cannot generate)."""
    if not _PARAM.search(path):
        return []
    if depth >= _SETUP_DEPTH:
        return None
    found = _collection(path)
    if found is None:
        return None
    collection, param = found
    create = _create_endpoint(collection, list(content.get("endpoints") or []))
    if create is None:
        return None
    outer = set(_PARAM.findall(collection))
    if outer - set(_PARAM.findall(str(create["path"]))):
        return None  # the create path does not name every parameter the outer path needs
    steps = _setup_for(str(create["path"]), actor, content, depth=depth + 1)
    if steps is None:
        return None
    step = {
        "actor": actor,
        "request": _request_for(create, content),
        "expect_status": create.get("success_status", 200),
        "capture": {param: _capture_path(param, create)},
    }
    return [*steps, step]


# =====================================================================================
# The compiler table: 8.1
# =====================================================================================


def _actor_for(endpoint: Mapping[str, Any]) -> str:
    return {"public": "anon", "user": "user_a", "owner": "user_a", "admin": "admin"}[str(endpoint["auth"])]


def _schema_or_none(endpoint: Mapping[str, Any]) -> dict[str, Any] | None:
    if endpoint.get("success_status") == 204 or endpoint.get("content_type") == "none":
        return None
    schema = endpoint.get("response_schema")
    return copy.deepcopy(schema) if isinstance(schema, dict) else None


def _check(
    cid: str,
    category: str,
    endpoint: Mapping[str, Any] | None,
    request: dict[str, Any],
    status: int | list[int],
    *,
    actor: str = "user_a",
    setup: list[dict[str, Any]] | None = None,
    schema: dict[str, Any] | None = None,
    contains: Any = None,
    headers: dict[str, str] | None = None,
    restart: bool = False,
    depends: list[str] | None = None,
) -> dict[str, Any]:
    expect: dict[str, Any] = {"status": status}
    if schema is not None:
        expect["json_schema"] = schema
    if contains is not None:
        expect["json_contains"] = contains
    if headers:
        expect["header_contains"] = headers
    return {
        "id": cid,
        "category": category,
        "endpoint": endpoint["id"] if endpoint else None,
        "actor": actor,
        "setup": copy.deepcopy(setup or []),
        "request": copy.deepcopy(request),
        "expect": expect,
        "depends_on": depends if depends is not None else ["health"],
        "restart_before": restart,
        "source": "compiler",
    }


def _auth_endpoint_check(ep: Mapping[str, Any], content: Mapping[str, Any]) -> dict[str, Any] | None:
    """``auth.register.ok`` / ``auth.login.ok``: the auth endpoints themselves, as an anonymous client."""
    req = _request_for(ep, content)
    body = req.get("json") if isinstance(req.get("json"), dict) else {}
    if "username" in body:
        body["username"] = "llmlab{rand}"
    if str(ep["path"]).endswith("/login"):
        register = next(
            (
                e
                for e in content.get("endpoints") or []
                if str(e["path"]).endswith("/register") and e["method"] == "POST"
            ),
            None,
        )
        if register is None:
            return None
        reg_req = _request_for(register, content)
        reg_req["json"] = {
            **(reg_req.get("json") or {}),
            **{k: body[k] for k in ("username", "password") if k in body},
        }
        setup = [{"actor": "anon", "request": reg_req, "expect_status": [200, 201]}]
        return _check(
            f"{ep['id']}.ok",
            "functional",
            ep,
            req,
            ep.get("success_status", 200),
            actor="anon",
            setup=setup,
        )
    return _check(
        f"{ep['id']}.ok",
        "functional",
        ep,
        req,
        ep.get("success_status", 200),
        actor="anon",
        schema=_schema_or_none(ep),
    )


def _token_schema(path: str) -> dict[str, Any] | None:
    """A JSON schema requiring a non-empty string at a dotted ``token_json_path`` (``None`` for indexed paths)."""
    names = path.removeprefix("$").lstrip(".").split(".")
    if not names or any(not n or "[" in n for n in names):
        return None
    schema: dict[str, Any] = {"type": "string", "minLength": 1}
    for name in reversed(names):
        schema = {"type": "object", "required": [name], "properties": {name: schema}}
    return schema


def _with_login_credential(check: dict[str, Any], auth: AuthDriverConfig) -> dict[str, Any]:
    """``<login>.ok`` must hand out the credential of the auth mode, not merely answer 2xx.

    Without this, an app whose catch-all route answers every request with the SPA's ``index.html`` (200)
    passes the login check (found on the Django minimal starter in the wave-3 conformance run). Bearer: a
    non-empty string at ``token_json_path``; cookie: a ``Set-Cookie`` header.
    """
    request = check.get("request") or {}
    if (
        check.get("source") != "compiler"
        or not str(check.get("id", "")).endswith(".ok")
        or request.get("method") != "POST"
        or request.get("path") != auth.login_path
    ):
        return check
    expect = dict(check["expect"])
    if auth.credential == "bearer":
        schema = _token_schema(auth.token_json_path or "token")
        if schema is None:
            return check
        expect["json_schema"] = {"allOf": [expect["json_schema"], schema]} if expect.get("json_schema") else schema
    else:
        expect["header_contains"] = {**(expect.get("header_contains") or {}), "Set-Cookie": ""}
    return {**check, "expect": expect}


def _with_body(req: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(req)
    if "json" in out:
        out["json"] = body
    elif "form" in out:
        out["form"] = body
    return out


def _validation_checks(
    ep: Mapping[str, Any],
    req: dict[str, Any],
    actor: str,
    setup: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    schema = ep.get("request_schema") or {}
    props = schema.get("properties") or {}
    body = req.get("json") if isinstance(req.get("json"), dict) else req.get("form")
    if not schema:
        return []  # no stated request schema: the task promises no validation rules to check
    if not isinstance(body, dict) or ep.get("content_type") == "multipart":
        return []
    out: list[dict[str, Any]] = []
    required = [r for r in schema.get("required") or [] if r in body]
    if required and ep["method"] != "PATCH":
        broken = {k: v for k, v in body.items() if k != required[0]}
        out.append(
            _check(
                f"{ep['id']}.invalid.missing",
                "validation",
                ep,
                _with_body(req, broken),
                [400, 422],
                actor=actor,
                setup=setup,
            ),
        )
    for name, sub in props.items():
        if isinstance(sub, dict) and isinstance(sub.get("maxLength"), int) and "string" in _types(sub):
            broken = {**body, name: "y" * (sub["maxLength"] + 1)}
            out.append(
                _check(
                    f"{ep['id']}.invalid.too_long",
                    "validation",
                    ep,
                    _with_body(req, broken),
                    [400, 422],
                    actor=actor,
                    setup=setup,
                ),
            )
            break
    for name, sub in props.items():
        if isinstance(sub, dict) and isinstance(sub.get("enum"), list) and sub["enum"]:
            broken = {**body, name: "llmlab-not-an-option"}
            out.append(
                _check(
                    f"{ep['id']}.invalid.enum",
                    "validation",
                    ep,
                    _with_body(req, broken),
                    [400, 422],
                    actor=actor,
                    setup=setup,
                ),
            )
            break
    return out


def _list_key(schema: Mapping[str, Any] | None) -> str | None:
    if not isinstance(schema, dict) or "array" in _types(schema):
        return None
    props = schema.get("properties") or {}
    return next((k for k in ("items", "results", "data") if isinstance(props.get(k), dict)), None)


def _list_shape(schema: Mapping[str, Any] | None, inner: dict[str, Any]) -> dict[str, Any] | None:
    """A response schema that constrains the result list with ``inner`` (a list or ``{items: [...]}``)."""
    if not isinstance(schema, dict):
        return None
    if "array" in _types(schema):
        return {"type": "array", **inner}
    key = _list_key(schema)
    if key is None:
        return None
    return {"type": "object", "required": [key], "properties": {key: {"type": "array", **inner}}}


def _list_checks(ep: Mapping[str, Any], content: Mapping[str, Any], actor: str) -> list[dict[str, Any]]:
    """Pagination, filter and search checks of a ``GET`` collection endpoint."""
    if ep["method"] != "GET" or _PARAM.search(str(ep["path"])) or actor == "anon":
        return []
    create = _create_endpoint(str(ep["path"]), list(content.get("endpoints") or []))
    if create is None or create.get("content_type") == "multipart":
        return []
    base = _request_for(create, content)
    if "json" not in base:
        return []
    step = {"actor": actor, "request": base, "expect_status": create.get("success_status", 200)}
    out: list[dict[str, Any]] = []
    query = {str(q["name"]): q for q in ep.get("query") or []}
    page_size = next((q for q in query.values() if q.get("semantics") == "page_size"), None)
    if page_size is not None:
        shape = _list_shape(ep.get("response_schema"), {"minItems": 2, "maxItems": 2})
        if shape is not None:
            req = {"method": "GET", "path": ep["path"], "query": {page_size["name"]: 2}}
            out.append(
                _check(
                    f"{ep['id']}.page_size",
                    "pagination",
                    ep,
                    req,
                    200,
                    actor=actor,
                    setup=[step, step, step],
                    schema=shape,
                ),
            )
    create_schema = create.get("request_schema") or _entity_schema(create, content) or {}
    create_props = create_schema.get("properties") or {}
    for q in query.values():
        name = str(q["name"])
        if q.get("semantics") != "filter" or name not in create_props:
            continue
        values: list[Any] = [v for v in (q.get("enum") or []) if v is not None][:2]
        if not values and str(q.get("type")) == "bool":
            values = [True, False]
        if len(values) < 2:
            continue
        want, other = values
        setup = [
            {**step, "request": {**base, "json": {**base["json"], name: want}}},
            {**step, "request": {**base, "json": {**base["json"], name: other}}},
        ]
        inner = {"minItems": 1, "items": {"type": "object", "properties": {name: {"const": want}}}}
        shape = _list_shape(ep.get("response_schema"), inner)
        if shape is None:
            continue
        req = {"method": "GET", "path": ep["path"], "query": {name: want}}
        out.append(_check(f"{ep['id']}.filter.{name}", "filter", ep, req, 200, actor=actor, setup=setup, schema=shape))
    search = next((q for q in query.values() if q.get("semantics") == "search"), None)
    required = create_schema.get("required") or []
    text_field = next(
        (
            n
            for n in required
            if "string" in _types(create_props.get(n) or {}) and not (create_props.get(n) or {}).get("enum")
        ),
        None,
    )
    if search is not None and text_field is not None:
        needle = "llmlabfind{rand}"
        setup = [{**step, "request": {**base, "json": {**base["json"], text_field: needle}}}, step]
        key = _list_key(ep.get("response_schema"))
        req = {"method": "GET", "path": ep["path"], "query": {search["name"]: needle}}
        if key is not None:  # json_contains needs an object; a bare-list response cannot express "contains"
            contains = {key: [{text_field: needle}]}
            out.append(
                _check(f"{ep['id']}.search", "filter", ep, req, 200, actor=actor, setup=setup, contains=contains),
            )
    return out


def _endpoint_checks(ep: Mapping[str, Any], content: Mapping[str, Any]) -> list[dict[str, Any]]:
    path = str(ep["path"])
    if path.startswith("/api/auth/"):
        if ep["method"] == "POST" and path.endswith(("/register", "/login")):
            check = _auth_endpoint_check(ep, content)
            return [check] if check else []
        return []
    actor = _actor_for(ep)
    owner_actor = "user_a" if actor == "anon" else actor
    setup = _setup_for(path, owner_actor, content)
    if setup is None:
        return []
    req = _request_for(ep, content)
    status = ep.get("success_status", 200)
    auth = str(ep["auth"])
    out = [
        _check(f"{ep['id']}.ok", "functional", ep, req, status, actor=actor, setup=setup, schema=_schema_or_none(ep)),
    ]
    if auth in ("user", "owner", "admin"):
        out.append(_check(f"{ep['id']}.anon_401", "auth", ep, req, 401, actor="anon", setup=setup))
    if auth == "owner" and setup:
        listed = sorted({int(e) for e in ep.get("errors") or []} & {403, 404})
        want: int | list[int] = listed[0] if len(listed) == 1 else [403, 404]
        out.append(_check(f"{ep['id']}.other_user", "ownership", ep, req, want, actor="user_b", setup=setup))
    if auth == "admin":
        out.append(_check(f"{ep['id']}.non_admin_403", "auth", ep, req, 403, actor="user_a", setup=setup))
    if ep["method"] in ("POST", "PUT", "PATCH") and ep.get("request_schema"):
        out += _validation_checks(ep, req, actor, setup)
    errors = {int(e) for e in ep.get("errors") or []}
    if 409 in errors and ep["method"] == "POST" and isinstance(req.get("json"), dict):
        if any(f in req["json"] for f in _unique_fields(content)):
            dup_setup = [*setup, {"actor": actor, "request": req, "expect_status": status}]
            out.append(_check(f"{ep['id']}.duplicate", "negative", ep, req, 409, actor=actor, setup=dup_setup))
    if 413 in errors and ep["method"] in ("POST", "PUT", "PATCH"):
        big = copy.deepcopy(req)
        changed = False
        if big.get("files"):
            first = next(iter(big["files"]))
            big["files"][first] = {**big["files"][first], "content": "{oversize}"}
            changed = True
        elif isinstance(big.get("json"), dict) and big["json"]:
            big["json"][next(iter(big["json"]))] = "{oversize}"
            changed = True
        if changed:
            out.append(_check(f"{ep['id']}.oversize", "negative", ep, big, 413, actor=actor, setup=setup))
    out += _list_checks(ep, content, actor)
    return out


def _persistence_checks(content: Mapping[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for get in content.get("endpoints") or []:
        if get["method"] != "GET" or get["auth"] not in ("user", "owner"):
            continue
        path = str(get["path"])
        found = _collection(path)
        if found is None or not path.rstrip("/").endswith("{" + found[1] + "}"):
            continue
        setup = _setup_for(path, "user_a", content)
        if not setup:
            continue
        created = setup[-1]["request"].get("json") or {}
        text = {k: v for k, v in created.items() if isinstance(v, str) and "{" not in v}
        contains = dict(list(text.items())[:1]) or None
        out.append(
            _check(
                f"{get['id']}.persists",
                "persistence",
                get,
                {"method": "GET", "path": path},
                200,
                actor="user_a",
                setup=setup,
                contains=contains,
                restart=True,
            ),
        )
    return out


def _infra_checks(stack_content: Mapping[str, Any], has_frontend: bool) -> list[dict[str, Any]]:
    contract = stack_content.get("run_contract") or {}
    health = str(contract.get("health_path") or "/api/health")
    out = [_check("health", "infra", None, {"method": "GET", "path": health}, 200, actor="anon", depends=[])]
    if has_frontend and contract.get("static_serving", "backend") == "backend":
        html = {"Content-Type": "text/html"}
        out.append(
            _check("index_served", "infra", None, {"method": "GET", "path": "/"}, 200, actor="anon", headers=html),
        )
        spa = {"method": "GET", "path": "/llmlab/client/route"}
        out.append(_check("spa_fallback", "infra", None, spa, 200, actor="anon", headers=html))
    return out


# =====================================================================================
# Probes: spec section 8.3
# =====================================================================================


def _read_endpoint(endpoint: Mapping[str, Any], content: Mapping[str, Any]) -> dict[str, Any] | None:
    path = str(endpoint["path"])
    return next(
        (ep for ep in content.get("endpoints") or [] if ep["method"] == "GET" and str(ep["path"]) == path),
        None,
    )


def _probe_with_endpoint(
    probe: dict[str, Any],
    params: dict[str, Any],
    endpoint: Mapping[str, Any],
    content: Mapping[str, Any],
    checks: Mapping[str, Any],
) -> tuple[list[str] | None, dict[str, Any] | None]:
    """Defaults of a probe that names an endpoint: ``(requires_checks default, positive control)``."""
    kind = str(probe["kind"])
    endpoints = _endpoint_map(content)
    params.setdefault("setup", _setup_for(str(endpoint["path"]), "user_a", content) or [])
    target = _request_for(endpoint, content)
    target["actor"] = "admin" if endpoint["auth"] == "admin" else "user_a"
    params.setdefault("target", target)
    requires = [cid for cid in (f"{endpoint['id']}.ok",) if cid in checks]
    control: dict[str, Any] | None = {**copy.deepcopy(target), "expect_status": endpoint.get("success_status", 200)}
    read = _read_endpoint(endpoint, content)
    if kind in ("idor_enumeration", "mass_assignment") and read is not None:
        params.setdefault("read", {**_request_for(read, content), "actor": "user_a"})
        if endpoint["method"] in ("DELETE", "PUT", "PATCH"):
            control = {
                **_request_for(read, content),
                "actor": "user_a",
                "expect_status": read.get("success_status", 200),
            }
    if kind in ("sqli_boolean", "sqli_error", "sqli_time", "xss_reflected", "open_redirect", "path_traversal"):
        if not params.get("param") and not params.get("field"):
            sem = (
                ("search", "filter")
                if kind.startswith("sqli") or kind == "xss_reflected"
                else ("other", "search", "filter")
            )
            param = next((q["name"] for q in endpoint.get("query") or [] if q.get("semantics") in sem), None)
            if param:
                params["param"] = param
    if kind == "sqli_boolean" and endpoint["method"] == "GET" and not _PARAM.search(str(endpoint["path"])):
        create = _create_endpoint(str(endpoint["path"]), list(content.get("endpoints") or []))
        if create is not None and create.get("content_type") != "multipart":
            step = {
                "actor": "user_a",
                "request": _request_for(create, content),
                "expect_status": create.get("success_status", 200),
            }
            params["setup"] = [*params["setup"], step]
    if kind == "xss_stored":
        view = endpoints.get(str(params.get("view_endpoint") or ""))
        if view is not None:
            params.setdefault("view", {"method": "GET", "path": view["path"]})
            found = _collection(str(view["path"]))
            if found is not None:
                params.setdefault("capture", {found[1]: _capture_path(found[1], endpoint)})
    if kind == "rate_limit_missing":
        body = {"username": "{user_a_username}", "password": "wrong-{rand}"}
        params["target"] = {"method": "POST", "path": endpoint["path"], "json": body, "actor": "anon"}
        control = {**params["target"], "expect_status": [400, 401, 403, 404]}
        requires = [cid for cid in ("auth.login.ok",) if cid in checks]
    return requires, control


def _probe(
    spec: Mapping[str, Any],
    content: Mapping[str, Any],
    checks: Mapping[str, Mapping[str, Any]],
    auth: AuthDriverConfig,
    *,
    enable_unvalidated: bool = False,
) -> dict[str, Any]:
    """A compiled probe: default ``requires_checks``, setup, positive control and attack target per kind."""
    from backend.generation.evaluation import probes as probe_catalog

    probe = {k: copy.deepcopy(v) for k, v in spec.items()}
    params: dict[str, Any] = dict(probe.get("params") or {})
    endpoint = _endpoint_map(content).get(str(probe.get("endpoint") or ""))
    kind = str(probe["kind"])
    login_checks = [cid for cid in ("auth.login.ok",) if cid in checks]
    requires: list[str] | None
    control: dict[str, Any] | None
    if endpoint is not None:
        requires, control = _probe_with_endpoint(probe, params, endpoint, content, checks)
    else:
        requires, control = login_checks, None
        if kind == "default_credential":
            control = {
                "method": "POST",
                "path": auth.login_path,
                "actor": "anon",
                "json": {"username": "{admin_username}", "password": "{admin_password}"},
                "expect_status": 200,
            }
        elif kind == "jwt_none_alg":
            protected = next(
                (
                    e
                    for e in content.get("endpoints") or []
                    if e["auth"] == "user" and e["method"] == "GET" and not _PARAM.search(str(e["path"]))
                ),
                None,
            )
            if protected is not None:
                params.setdefault("target", {**_request_for(protected, content), "actor": "user_a"})
                control = {
                    **_request_for(protected, content),
                    "actor": "user_a",
                    "expect_status": protected.get("success_status", 200),
                }
                if f"{protected['id']}.ok" in checks:
                    requires = [f"{protected['id']}.ok"]
        elif kind == "rate_limit_missing":
            body = {"username": "{user_a_username}", "password": "wrong-{rand}"}
            params.setdefault("target", {"method": "POST", "path": auth.login_path, "json": body, "actor": "anon"})
            control = {**params["target"], "expect_status": [400, 401, 403, 404]}
    if spec.get("requires_checks") is not None:
        requires = list(spec["requires_checks"])
    if spec.get("positive_control") is not None:
        control = copy.deepcopy(dict(spec["positive_control"]))
    if not enable_unvalidated and not probe_catalog.kind_validated(kind):
        params["disabled"] = True
        params["disabled_reason"] = "probe_unvalidated: no passing mutant pair for this kind"
    probe["params"] = params
    probe["requires_checks"] = requires or []
    probe["positive_control"] = control
    return probe


# =====================================================================================
# Public API
# =====================================================================================


def _statedness(
    task_version: TaskSpecVersion | None,
    content: Mapping[str, Any],
    checks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    from backend.generation.library.lint import tag_checks

    compiled = [c for c in checks if c.get("source") == "compiler"]
    tagged = {c["id"]: c for c in tag_checks(content, compiled)}
    derived = ((getattr(task_version, "derived", None) or {}).get("check_statedness")) or {}
    out: list[dict[str, Any]] = []
    for check in checks:
        if check.get("source") == "compiler":
            out.append(tagged[check["id"]])
        else:
            out.append({**check, "stated_in": list(derived.get(check["id"], check.get("stated_in") or []))})
    return out


def compile_content(
    content: Mapping[str, Any],
    auth_mode: str,
    stack_content: Mapping[str, Any] | None = None,
    *,
    task_version: TaskSpecVersion | None = None,
    probes: str = "task",
    base_url: str = DEFAULT_BASE_URL,
    only: list[str] | None = None,
    enable_unvalidated: bool = False,
) -> TesterPlan:
    """``compile`` over raw content (also used by the API preview and the snapshot tests).

    ``enable_unvalidated`` compiles probe kinds without a passing mutant pair as runnable (only
    ``oracle_validate --kit`` does this, to validate them).
    """
    stack_content = stack_content or {}
    auth = auth_config(content, auth_mode)
    frontend = (stack_content.get("frontend") or {}).get("framework", "none")
    has_frontend = bool(content.get("has_frontend", True)) and frontend not in ("none", None)
    checks: list[dict[str, Any]] = _infra_checks(stack_content, has_frontend)
    for ep in content.get("endpoints") or []:
        checks += _endpoint_checks(ep, content)
    checks += _persistence_checks(content)
    checks = [_with_login_credential(check, auth) for check in checks]
    by_id: dict[str, dict[str, Any]] = {}
    for check in checks:
        by_id.setdefault(check["id"], check)
    for hand in content.get("acceptance") or []:
        merged = {**copy.deepcopy(dict(hand)), "source": "acceptance"}
        if not merged.get("depends_on") and merged.get("category") != "infra":
            merged["depends_on"] = ["health"]
        by_id[str(hand["id"])] = merged  # an id collision: the hand-written check wins
    ordered = _statedness(task_version, content, list(by_id.values()))
    compiled_probes: list[dict[str, Any]] = []
    if probes != "none":
        from backend.generation.evaluation import probes as probe_catalog

        specs = [dict(p) for p in content.get("probes") or []]
        if probes == "all_applicable":
            specs += probe_catalog.applicable(content, existing=specs)
        compiled_probes = [_probe(spec, content, by_id, auth, enable_unvalidated=enable_unvalidated) for spec in specs]
    signed = [
        {"method": str(ep["method"]), "path": str(ep["path"])}
        for ep in content.get("endpoints") or []
        if "webhook" in (ep.get("tags") or [])
    ]
    contract = stack_content.get("run_contract") or {}
    return TesterPlan(
        compiler_version=COMPILER_VERSION,
        base_url=base_url,
        auth=auth,
        actors=["anon", "user_a", "user_b", "admin"],
        checks=[CompiledCheck.model_validate(c) for c in ordered],
        probes=[CompiledProbe.model_validate(p) for p in compiled_probes],
        only=only,
        health_path=str(contract.get("health_path") or "/api/health"),
        signed_routes=signed,
        has_frontend=has_frontend,
    )


def compile(  # noqa: A001 - the spec's name
    task_version: TaskSpecVersion,
    auth_mode: str,
    stack: StackProfile | None,
    *,
    probes: str = "task",
    base_url: str = DEFAULT_BASE_URL,
    only: list[str] | None = None,
) -> TesterPlan:
    """The tester plan of one (task, auth mode, stack)."""
    return compile_content(
        task_version.content or {},
        auth_mode,
        (stack.content if stack is not None else None) or {},
        task_version=task_version,
        probes=probes,
        base_url=base_url,
        only=only,
    )


def plan_json(plan: AcceptancePlan) -> dict[str, Any]:
    """The JSON written to ``/in/plan.json`` (aliases: ``request.json``)."""
    return plan.model_dump(mode="json", by_alias=True)


def checks_for_endpoints(
    task_version: TaskSpecVersion,
    endpoint_ids: Iterable[str],
    *,
    categories: Iterable[str] | None = None,
) -> list[str]:
    """Check ids linked to ``endpoint_ids`` (slot targets): compiled and hand-written, sorted.

    ``categories`` restricts the result (e.g. ``["functional"]``). Infra checks are never linked.
    """
    wanted = {str(e) for e in endpoint_ids}
    allowed = set(categories) if categories is not None else None
    plan = compile_content(task_version.content or {}, "contract_bearer", {}, task_version=task_version, probes="none")
    return sorted(
        {
            check.id
            for check in plan.checks
            if check.endpoint in wanted
            and check.category != "infra"
            and (allowed is None or check.category in allowed)
        },
    )
