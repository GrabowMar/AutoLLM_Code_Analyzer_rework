"""JSON-RPC 2.0 core of the MCP server (stateless Streamable HTTP).

Each POST carries one message (or a legacy batch) and gets a plain
``application/json`` answer — no session ids, no server-initiated stream —
which every MCP client supports and keeps the server horizontally trivial.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.integrations.mcp import tools
from backend.integrations.mcp.dispatch import DispatchError

logger = logging.getLogger(__name__)

SERVER_NAME = "llm-lab"
SERVER_VERSION = "1.0.0"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

INSTRUCTIONS = """\
LLM Lab runs studies: factorial designs that have LLMs build applications (or fill slots in
existing ones) from versioned tasks, evaluate them in a sandbox and score the code with
static-analysis tools; rankings and study results compare models on measured outcomes.

Typical flows:
- Rigorous comparison: list_studies / get_study → resolve_study (free dry-run with a cost
  estimate) → launch_study(plan_hash) → list_runs(study=…) → get_run.
- Analyse a sample: analyze (generation_job_id = the run id, tool_slugs from list_analyzers)
  → get_analysis_run → list_findings / rankings.
- Anything else the web UI can do: find_api_operations → describe_api_operation → call_api.
Some study and run tools appear only once their API is published; until then use
find_api_operations with the query "studies" or "runs".
Generation spends the user's LLM credits — confirm scope with the user first and prefer the free
resolve/preview tools. Docs: search_docs / read_doc, or the llmlab://docs/* resources.
"""

PROMPTS: dict[str, dict[str, Any]] = {
    "compare_models": {
        "description": "Benchmark a few models on one task with a study and summarise which writes the best code.",
        "arguments": [
            {"name": "task", "description": "What the app should do, or a task name.", "required": True},
            {"name": "models", "description": "Comma-separated model names or slugs.", "required": True},
        ],
        "template": (
            "Use the LLM Lab tools to compare these models: {models}.\n"
            "Task: {task}\n\n"
            "1. Find the models (search_models) and the closest task (find_api_operations 'tasks').\n"
            "2. Set up a study and dry-run it (resolve_study); tell me the plan and estimated cost before "
            "launching anything that spends credits.\n"
            "3. Launch it (launch_study), wait for the runs (list_runs), then analyze each one with the "
            "default analyzers.\n"
            "4. Summarise per model: outcome (build/start/tested), findings as each tool labelled them, "
            "notable issues, and a verdict. Report runs without measurements as not measured, never as clean."
        ),
    },
    "triage_findings": {
        "description": "Triage an analysis run's findings into real issues vs noise, with fixes.",
        "arguments": [{"name": "run_id", "description": "Analysis run id.", "required": True}],
        "template": (
            "Fetch analysis run {run_id} (get_analysis_run) and its findings (list_findings, all pages). "
            "Group them by root cause, separate likely false positives, rank the real issues by "
            "impact and propose a concrete fix for the top five."
        ),
    },
    "study_report": {
        "description": "Write an executive summary of a study's results.",
        "arguments": [{"name": "study_id", "description": "Study id.", "required": True}],
        "template": (
            "Read study {study_id} (get_study, then its results through find_api_operations 'study results'). "
            "Write a one-page summary: hypothesis, design, which conditions won with effect sizes and "
            "confidence, caveats (attrition, excluded or not-measured runs, small n), and a recommendation."
        ),
    },
    "platform_health": {
        "description": "Check what is running, queued or stuck, and whether anything needs attention.",
        "arguments": [],
        "template": (
            "Check LLM Lab's health: execution_queue, list_runs (status=running and "
            "status=failed, newest first), list_containers and list_endpoints. Report anything stuck, "
            "failing repeatedly or offline, and suggest the next action."
        ),
    },
}


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _negotiate(params: dict[str, Any]) -> str:
    requested = str(params.get("protocolVersion") or "")
    return requested if requested in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION


def _initialize(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocolVersion": _negotiate(params),
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"listChanged": False, "subscribe": False},
            "prompts": {"listChanged": False},
        },
        "serverInfo": {"name": SERVER_NAME, "title": "LLM Lab", "version": SERVER_VERSION},
        "instructions": INSTRUCTIONS,
    }


def _doc_entries(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in nodes:
        if node.get("children"):
            out.extend(_doc_entries(node["children"]))
        else:
            out.append(node)
    return out


def _list_resources() -> dict[str, Any]:
    from backend.docs import services as docs

    resources = [
        {
            "uri": f"llmlab://docs/{node['slug']}",
            "name": node["slug"],
            "title": node.get("title") or node["slug"],
            "description": node.get("description") or "",
            "mimeType": "text/markdown",
        }
        for node in _doc_entries(docs.list_docs())
    ]
    resources.append(
        {
            "uri": "llmlab://openapi.json",
            "name": "openapi.json",
            "title": "LLM Lab OpenAPI schema",
            "description": "Every REST operation, for call_api.",
            "mimeType": "application/json",
        },
    )
    return {"resources": resources}


def _read_resource(params: dict[str, Any]) -> dict[str, Any]:
    import json

    from django.core.serializers.json import DjangoJSONEncoder

    from backend.docs import services as docs
    from backend.integrations.mcp import openapi

    uri = str(params.get("uri") or "")
    if uri == "llmlab://openapi.json":
        text = json.dumps(openapi.schema(), cls=DjangoJSONEncoder)
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}
    if uri.startswith("llmlab://docs/"):
        page = docs.get_doc(uri.removeprefix("llmlab://docs/"))
        if page:
            return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": page["raw"]}]}
    raise RpcError(INVALID_PARAMS, f"resource not found: {uri}")


def _list_prompts() -> dict[str, Any]:
    return {
        "prompts": [
            {"name": name, "description": spec["description"], "arguments": spec["arguments"]}
            for name, spec in PROMPTS.items()
        ],
    }


def _get_prompt(params: dict[str, Any]) -> dict[str, Any]:
    name = str(params.get("name") or "")
    spec = PROMPTS.get(name)
    if spec is None:
        raise RpcError(INVALID_PARAMS, f"unknown prompt {name}")
    arguments = params.get("arguments") or {}
    values = {a["name"]: str(arguments.get(a["name"], "")) for a in spec["arguments"]}
    missing = [a["name"] for a in spec["arguments"] if a.get("required") and not values[a["name"]]]
    if missing:
        raise RpcError(INVALID_PARAMS, f"missing prompt arguments: {', '.join(missing)}")
    return {
        "description": spec["description"],
        "messages": [{"role": "user", "content": {"type": "text", "text": spec["template"].format(**values)}}],
    }


def _call_tool(caller: tools.Caller, params: dict[str, Any]) -> dict[str, Any]:
    name = str(params.get("name") or "")
    try:
        return tools.call_tool(caller, name, params.get("arguments"))
    except (tools.ToolError, DispatchError) as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}


def handle_message(caller: tools.Caller, message: Any) -> dict[str, Any] | None:  # noqa: PLR0911
    """Handle one JSON-RPC message; ``None`` for notifications and responses."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, INVALID_REQUEST, "invalid JSON-RPC message")
    method = message.get("method")
    msg_id = message.get("id")
    if method is None:  # a response to a server request we never send
        return None
    is_notification = "id" not in message
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return None if is_notification else _error(msg_id, INVALID_PARAMS, "params must be an object")
    try:
        result = _dispatch(caller, str(method), params)
    except RpcError as exc:
        return None if is_notification else _error(msg_id, exc.code, exc.message, exc.data)
    except Exception:
        # The method name is caller-supplied: no line breaks may reach the log.
        safe_method = str(method)[:80].replace("\r", " ").replace("\n", " ")
        logger.exception("MCP %s failed", safe_method)
        return None if is_notification else _error(msg_id, INTERNAL_ERROR, "internal error")
    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _dispatch(caller: tools.Caller, method: str, params: dict[str, Any]) -> dict[str, Any]:
    handlers = {
        "initialize": lambda: _initialize(params),
        "ping": dict,
        "tools/list": lambda: {"tools": tools.list_tools(caller)},
        "tools/call": lambda: _call_tool(caller, params),
        "resources/list": _list_resources,
        "resources/templates/list": lambda: {"resourceTemplates": []},
        "resources/read": lambda: _read_resource(params),
        "prompts/list": _list_prompts,
        "prompts/get": lambda: _get_prompt(params),
        "logging/setLevel": dict,
        "completion/complete": lambda: {"completion": {"values": [], "hasMore": False}},
    }
    if method.startswith("notifications/"):
        return {}
    handler = handlers.get(method)
    if handler is None:
        raise RpcError(METHOD_NOT_FOUND, f"method not found: {method}")
    return handler()


def _error(msg_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": error}
