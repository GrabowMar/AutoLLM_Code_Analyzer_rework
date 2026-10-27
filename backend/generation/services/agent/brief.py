"""Render the agent's task brief from an app spec (or free-form description).

The brief is the only task-specific text the agent receives. It states what to
build (the app spec, verbatim) and the runtime contract the platform will
build and run the result under -- nothing model-specific, no length targets.
It is rendered once at job creation and frozen into the snapshot, so its hash
is the prompt identity that groups runs across models and repeats.
"""

from __future__ import annotations

import functools
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment
from jinja2 import StrictUndefined
from jinja2 import select_autoescape

from backend.generation.services.evaluation import contract as contract_mod
from backend.generation.services.spec_models import data_model_for_prompt

# v2: autonomy statement for every harness, accounts/admin contract, the
# automatic-evaluation notice, request budget as the primary limit.
# v3: a task description given alongside an app spec is added as an
# "Additional context" section (it used to replace the spec's description);
# user-authored stacks state their real start command. Briefs without such a
# description, on builtin stacks, are byte-identical to v2.
# v4: a spec with several data models (schema v3 ``data_models``) lists them
# all under "Data models"; an endpoint's access level, query parameters and
# success status are stated when the spec gives them; the builtin stack
# summaries name the current runtimes (Python 3.13, Node 24 toolchains).
# JSON blocks (data models, request and response bodies) are no longer
# HTML-escaped (Jinja's ``tojson`` printed ' < > & as \u0027 \u003c ...).
# Keys stay sorted: a spec read back from the database (jsonb) has lost its
# authoring order, so sorting is what keeps the brief, and its sha256,
# independent of how the spec was loaded.
BRIEF_VERSION = 4

_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "agent"
BRIEF_TEMPLATE_PATH = _DATA_DIR / "brief.md.jinja2"
STACK_CONTRACTS_PATH = _DATA_DIR / "stacks.yaml"

# Files the platform owns: restored from the pinned stack after the run, so
# the agent cannot change how its own output is built and evaluated.
PROTECTED_FILES = ("Dockerfile", ".dockerignore", "docker-compose.yml")

# How the brief states a spec endpoint's ``auth`` level (schema v3).
ACCESS_LABELS = {
    "public": "anyone, without signing in",
    "user": "signed-in users",
    "admin": "admin accounts only",
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@functools.cache
def _stack_contracts() -> dict[str, Any]:
    return yaml.safe_load(STACK_CONTRACTS_PATH.read_text(encoding="utf-8")) or {}


def stack_contract(stack_slug: str, stack_config: dict[str, Any]) -> dict[str, Any]:
    """What the brief states about the stack: ``stacks.yaml`` for builtins.

    Stacks that file does not list (user-authored) take the ``_default``
    summaries and the start command their generated Dockerfile runs.
    """
    from backend.runtime.services.dockerfile_gen import start_command

    contracts = _stack_contracts()
    backend_filename = stack_config.get("backend_filename") or "app.py"
    port = stack_config.get("default_port") or 8000
    contract = {**contracts.get("_default", {}), **contracts.get(stack_slug, {})}
    contract.setdefault(
        "start_command",
        " ".join(
            start_command(
                server_kind=stack_config.get("server_kind") or "python",
                backend_filename=backend_filename,
                port=port,
            ),
        ),
    )
    return {
        "slug": stack_slug,
        "has_frontend": bool(stack_config.get("has_frontend")),
        "backend_filename": backend_filename,
        "port": port,
        **contract,
    }


def _budget_summary(agent_config: dict[str, Any]) -> str:
    minutes = round(agent_config["wall_clock_seconds"] / 60)
    return (
        f"You have at most {agent_config['max_llm_requests']} model requests for this task; "
        f"the run is also stopped after {minutes} minutes."
    )


def _network_summary(policy: str) -> str:
    if policy == "packages":
        return "the Python and npm package registries (pip and npm install work)"
    return "nothing: no package downloads are possible, use only what is installed"


def _brief_json(value: Any, indent: int | None = None) -> str:
    """``tojson`` without HTML escaping: the brief is Markdown for an LLM, never HTML."""
    return json.dumps(value, indent=indent, sort_keys=True)


def render_brief(
    *,
    app: dict[str, Any] | None,
    description: str,
    stack_slug: str,
    stack_config: dict[str, Any],
    agent_config: dict[str, Any],
) -> str:
    """Render the brief text; *app* is a snapshot app_requirement dict or None.

    Without an app spec, *description* is the task. With one, the spec's own
    description leads and *description* (when given) follows as additional
    context, so it never hides the spec text the evaluation checks against.
    """
    extra = (description or "").strip()
    if app:
        task_description = (app.get("description") or "").strip()
        additional_context = extra
    else:
        app = {"name": "the application described below"}
        task_description = extra
        additional_context = ""
    # The brief is Markdown for an LLM, never served as HTML: escaping only applies to .html/.xml.
    env = Environment(
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=select_autoescape(enabled_extensions=("html", "xml"), default_for_string=False),
    )
    env.filters["tojson"] = _brief_json
    template = env.from_string(BRIEF_TEMPLATE_PATH.read_text(encoding="utf-8"))
    endpoints = [*(app.get("api_endpoints") or []), *(app.get("admin_api_endpoints") or [])]
    text = template.render(
        app={
            "name": app.get("name", ""),
            "backend_requirements": app.get("backend_requirements") or [],
            "frontend_requirements": app.get("frontend_requirements") or [],
            "admin_requirements": app.get("admin_requirements") or [],
            # The one model, or the list of all of them (legacy app dicts carry only data_model).
            "data_model": data_model_for_prompt(app),
        },
        description=task_description,
        additional_context=additional_context,
        endpoints=[
            {
                "method": ep.get("method", "GET"),
                "path": ep.get("path", ""),
                "description": ep.get("description", ""),
                "request": ep.get("request"),
                "response": ep.get("response"),
                "auth": ep.get("auth") if ep.get("auth") in ACCESS_LABELS else None,
                "query": ep.get("query") if isinstance(ep.get("query"), dict) else None,
                "success_status": ep.get("success_status"),
            }
            for ep in endpoints
        ],
        access_labels=ACCESS_LABELS,
        stack=stack_contract(stack_slug, stack_config),
        contract=contract_mod.describe(app),
        protected_files=PROTECTED_FILES,
        network_summary=_network_summary(agent_config["network_policy"]),
        budget_summary=_budget_summary(agent_config),
    )
    # Collapse the blank-line runs left by skipped optional sections.
    lines: list[str] = []
    for line in text.strip().splitlines():
        if line.strip() or (lines and lines[-1].strip()):
            lines.append(line.rstrip())
    return "\n".join(lines) + "\n"
