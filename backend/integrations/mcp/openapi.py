"""The platform's own OpenAPI schema, indexed for MCP tools.

Every MCP tool is backed by a real ``/api/`` operation, and its input schema
is derived from that operation's OpenAPI parameters and request body, so the
tools cannot drift from the API they call.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from dataclasses import field
from functools import cache
from typing import Any

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
MAX_REF_DEPTH = 8
MAX_DESCRIPTION_CHARS = 200
# Operations an MCP client must never reach: the MCP endpoint itself, token
# minting (session-only anyway) and first-run bootstrap.
EXCLUDED_PATH_PREFIXES = ("/api/mcp", "/api/tokens/", "/api/users/bootstrap-")


@dataclass(frozen=True)
class Operation:
    operation_id: str
    method: str  # upper-case
    path: str  # e.g. /api/generation/jobs/{job_id}/
    summary: str
    description: str
    tags: tuple[str, ...]
    path_params: tuple[dict[str, Any], ...] = ()
    query_params: tuple[dict[str, Any], ...] = ()
    body_schema: dict[str, Any] | None = None
    body_required: bool = False
    extra: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def read_only(self) -> bool:
        return self.method == "GET"

    @property
    def short_id(self) -> str:
        """``backend_generation_api_views_jobs_get_job`` → ``generation.jobs.get_job``."""
        oid = self.operation_id.removeprefix("backend_")
        return oid.replace("_api_views_", ".").replace("_api_views", "")


@cache
def schema() -> dict[str, Any]:
    from config.api import api

    return api.get_openapi_schema()


def _resolve(node: Any, components: dict[str, Any], depth: int = 0, seen: tuple[str, ...] = ()) -> Any:
    """Inline ``#/components/schemas/*`` refs (bounded; cycles become plain objects)."""
    if isinstance(node, list):
        return [_resolve(item, components, depth, seen) for item in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        name = ref.rsplit("/", 1)[-1]
        if name in seen or depth >= MAX_REF_DEPTH:
            return {"type": "object", "description": f"{name} (see describe_operation)"}
        target = components.get(name, {"type": "object"})
        resolved = _resolve(copy.deepcopy(target), components, depth + 1, (*seen, name))
        siblings = {k: v for k, v in node.items() if k != "$ref"}
        return {**resolved, **siblings} if siblings else resolved
    return {key: _resolve(value, components, depth, seen) for key, value in node.items()}


def _clean(node: Any) -> Any:
    """Drop OpenAPI-only keys JSON Schema clients choke on (``title`` noise, ``example``)."""
    if isinstance(node, list):
        return [_clean(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {k: _clean(v) for k, v in node.items() if k not in {"title", "example", "examples", "x-order"}}
    if isinstance(out.get("description"), str):
        # Schema docstrings carry implementation notes; the first line is what a model needs.
        out["description"] = out["description"].strip().split("\n", 1)[0][:MAX_DESCRIPTION_CHARS]
    return out


@cache
def operations() -> dict[str, Operation]:
    """All callable operations keyed by operation id."""
    spec = schema()
    components = spec.get("components", {}).get("schemas", {})
    out: dict[str, Operation] = {}
    for path, item in spec.get("paths", {}).items():
        if path.startswith(EXCLUDED_PATH_PREFIXES):
            continue
        for method in HTTP_METHODS:
            op = item.get(method)
            if not op:
                continue
            params = [_clean(_resolve(p, components)) for p in op.get("parameters", [])]
            body_schema = None
            body_required = False
            body = op.get("requestBody")
            if body:
                content = body.get("content", {})
                media = content.get("application/json") or next(iter(content.values()), {})
                if media.get("schema"):
                    body_schema = _clean(_resolve(media["schema"], components))
                body_required = bool(body.get("required"))
            operation = Operation(
                operation_id=op.get("operationId") or f"{method}_{path}",
                method=method.upper(),
                path=path,
                summary=op.get("summary") or "",
                description=(op.get("description") or "").strip(),
                tags=tuple(op.get("tags") or ()),
                path_params=tuple(p for p in params if p.get("in") == "path"),
                query_params=tuple(p for p in params if p.get("in") == "query"),
                body_schema=body_schema,
                body_required=body_required,
            )
            out[operation.operation_id] = operation
    return out


def find(identifier: str) -> Operation | None:
    """Look an operation up by full id, short id (``generation.jobs.get_job``) or ``METHOD /path``."""
    ops = operations()
    if identifier in ops:
        return ops[identifier]
    for op in ops.values():
        if identifier in {op.short_id, f"{op.method} {op.path}"}:
            return op
    return None


def search(query: str = "", tag: str = "", limit: int = 40) -> list[Operation]:
    words = [w for w in re.split(r"\s+", query.lower().strip()) if w]
    hits: list[tuple[int, Operation]] = []
    for op in operations().values():
        if tag and tag.lower() not in {t.lower() for t in op.tags}:
            continue
        haystack = f"{op.operation_id} {op.method} {op.path} {op.summary} {op.description}".lower()
        score = sum(haystack.count(w) for w in words) if words else 1
        if words and not all(w in haystack for w in words):
            continue
        hits.append((score, op))
    hits.sort(key=lambda pair: (-pair[0], pair[1].path, pair[1].method))
    return [op for _, op in hits[:limit]]


def input_schema(op: Operation) -> dict[str, Any]:
    """A flat JSON Schema: path and query params as properties, the JSON body under ``body``."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in (*op.path_params, *op.query_params):
        prop = dict(param.get("schema") or {"type": "string"})
        if param.get("description"):
            prop["description"] = param["description"]
        properties[param["name"]] = prop
        if param.get("required") or param.get("in") == "path":
            required.append(param["name"])
    if op.body_schema is not None:
        properties["body"] = {**op.body_schema, "description": "JSON request body."}
        if op.body_required:
            required.append("body")
    result: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        result["required"] = required
    return result


def render_path(op: Operation, arguments: dict[str, Any]) -> str:
    path = op.path
    for param in op.path_params:
        name = param["name"]
        if name not in arguments:
            msg = f"missing path parameter {name!r}"
            raise ValueError(msg)
        value = str(arguments[name])
        if "/" in value or value in {"", ".", ".."}:
            msg = f"invalid path parameter {name!r}"
            raise ValueError(msg)
        path = path.replace("{" + name + "}", value)
    return path


def query_from(op: Operation, arguments: dict[str, Any]) -> dict[str, Any]:
    names = {p["name"] for p in op.query_params}
    return {k: v for k, v in arguments.items() if k in names and v is not None}


def describe(op: Operation) -> dict[str, Any]:
    return {
        "operation_id": op.operation_id,
        "short_id": op.short_id,
        "method": op.method,
        "path": op.path,
        "summary": op.summary,
        "description": op.description,
        "tags": list(op.tags),
        "input_schema": input_schema(op),
    }
