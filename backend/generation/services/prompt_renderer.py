"""Jinja2-based prompt renderer for scaffolding mode generation."""

from __future__ import annotations

import ast
import functools
import json
import logging
import re
from typing import TYPE_CHECKING
from typing import Any

import jinja2
from jinja2 import meta as jinja2_meta

from backend.generation.services.spec_models import normalize_data_models

if TYPE_CHECKING:
    from backend.generation.models import AppRequirementTemplate

logger = logging.getLogger(__name__)

# Stage prompts used when neither a job snapshot nor the DB supplies one (a
# first boot before seeding): the same files the seeder turns into the
# default profile's ``base-*`` prompt-stage blocks, read from data/prompts/.
_FALLBACK_STAGE_SLUGS = {
    ("backend", "system"): "base-backend-system",
    ("backend", "user"): "base-backend-user",
    ("frontend", "system"): "base-frontend-system",
    ("frontend", "user"): "base-frontend-user",
}

# Decorators that declare an HTTP route: Flask (``@app.route``, ``@bp.get``)
# and FastAPI (``@app.post``, ``@router.websocket``).
_ROUTE_DECORATOR_RE = re.compile(
    r"^@[A-Za-z_]\w*(?:\.\w+)*\.(?:route|get|post|put|patch|delete|websocket|api_route)\(",
)
# Router objects whose prefix is part of every path they declare.
_ROUTER_RE = re.compile(r"(?:\bAPIRouter\(|\bBlueprint\(|\.include_router\(|\.register_blueprint\()")
# The SPA catch-all on the app itself (``@app.route("/<path:path>")``,
# ``@app.get("/{full_path:path}")``) is not part of the API.
_SPA_ROUTE_RE = re.compile(r"""^@app\.\w+\(\s*["'](?:/|/<path:\w+>|/\{\w+:path\})["']""")
# Version of the backend summary the frontend stage gets when the backend
# scanner finds no endpoints (``_extract_api_context``). It is rendered at run
# time from the generated backend, outside ``prompt_hash``, so snapshots
# record the version they were frozen with (``frontend_context_version``,
# which enters ``run_fingerprint``); snapshots without it get version 1.
# 1: ``@app.route(`` lines and ``(db.Model)`` class lines only.
# 2: Flask/FastAPI/blueprint/router decorators without the SPA catch-all,
#    ORM models and Pydantic schemas with their fields.
FRONTEND_CONTEXT_VERSION = 2

_ORM_BASES = frozenset({"db.Model", "Model", "Base", "DeclarativeBase", "SQLModel"})
_SCHEMA_BASES = frozenset({"BaseModel", "pydantic.BaseModel"})


@functools.cache
def _fallback_template(stage: str, role: str) -> str:
    from backend.generation.seeding import PROMPT_STAGE_SEEDS
    from backend.generation.seeding import PROMPTS_DIR

    slug = _FALLBACK_STAGE_SLUGS[(stage, role)]
    seed = next(s for s in PROMPT_STAGE_SEEDS if s["slug"] == slug)
    return (PROMPTS_DIR / seed["path"]).read_text(encoding="utf-8")


def templates_state_contract(prompt_templates: dict[str, Any] | None) -> bool:
    """True when the backend stage templates render the evaluator contract.

    A scaffolding prompt states the runtime contract only if its template
    uses the ``contract`` context variable (the current stage prompts do;
    those from before it existed, and user prompts that leave it out, do
    not). Read from the job's frozen templates, so it holds for the prompt
    the job was actually given.
    """
    stage = (prompt_templates or {}).get("backend") or {}
    env = jinja2.Environment(autoescape=jinja2.select_autoescape(enabled_extensions=(), default_for_string=False))
    for role in ("system", "user"):
        source = stage.get(role) or ""
        if not source:
            continue
        try:
            if "contract" in jinja2_meta.find_undeclared_variables(env.parse(source)):
                return True
        except jinja2.TemplateSyntaxError:
            continue
    return False


class PromptRenderer:
    """Renders Jinja2 prompt templates with app requirement context."""

    def __init__(self) -> None:
        self.env = jinja2.Environment(
            # Prompts are Markdown, not HTML: never escape.
            autoescape=jinja2.select_autoescape(enabled_extensions=(), default_for_string=False),
            undefined=jinja2.StrictUndefined,
        )

    def render_template(self, template_str: str, context: dict) -> str:
        try:
            template = self.env.from_string(template_str)
            return template.render(**context)
        except jinja2.TemplateError:
            logger.exception("Template rendering failed")
            raise

    def _build_context(self, app_req: AppRequirementTemplate) -> dict:
        from backend.generation.services.profile_resolver import app_requirement_to_dict

        return self._build_context_from_dict(app_requirement_to_dict(app_req))

    def _build_context_from_dict(self, app_dict: dict[str, Any]) -> dict:
        from backend.generation.services.evaluation import contract as contract_mod

        admin_eps = app_dict.get("admin_api_endpoints") or []
        admin_section = ""
        if admin_eps:
            formatted = self._format_json(admin_eps)
            admin_section = f"### Admin API Endpoints\n{formatted}"

        return {
            "name": app_dict.get("name", ""),
            "description": app_dict.get("description", ""),
            "backend_requirements": app_dict.get("backend_requirements") or [],
            "frontend_requirements": app_dict.get("frontend_requirements") or [],
            "admin_requirements": app_dict.get("admin_requirements") or [],
            "api_endpoints": self._format_json(app_dict.get("api_endpoints")),
            "data_model": self._format_data_models(app_dict),
            "admin_api_endpoints": admin_section,
            # The evaluator's runtime contract (the same one the agent brief
            # states and the conformance tester checks), derived from the spec.
            "contract": contract_mod.describe(app_dict),
        }

    @classmethod
    def _format_data_models(cls, app_dict: dict[str, Any]) -> str:
        """``data_model`` text: the single model as before, or a JSON list of all of them."""
        models = normalize_data_models(app_dict)
        if len(models) > 1:
            return cls._format_json(models)
        return cls._format_json(app_dict.get("data_model") or (models[0] if models else None))

    def render_messages_from_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        stage: str,
        api_context_override: str | None = None,
        backend_code: str = "",
    ) -> list[dict[str, str]]:
        """Render system+user messages from a job ``resolved_bundle`` snapshot."""
        pre_rendered = (snapshot.get("prompts") or {}).get(stage)
        if stage == "backend" and pre_rendered:
            return [
                {"role": "system", "content": pre_rendered["system"]},
                {"role": "user", "content": pre_rendered["user"]},
            ]

        templates = snapshot.get("prompt_templates") or {}
        stage_templates = templates.get(stage) or {}
        app_dict = snapshot.get("app_requirement") or {}
        context = self._build_context_from_dict(app_dict)

        if stage == "frontend":
            extract = (
                self._extract_api_context
                if snapshot.get("frontend_context_version") == FRONTEND_CONTEXT_VERSION
                else self._extract_api_context_v1
            )
            context["backend_api_context"] = api_context_override or extract(backend_code)
            if not api_context_override and app_dict.get("api_endpoints"):
                spec = self._format_json(app_dict.get("api_endpoints"))
                context["backend_api_context"] = (
                    f"### Specified API endpoints\n{spec}\n\n{context['backend_api_context']}"
                )

        system_raw = stage_templates.get("system") or self._default_for_stage(stage, "system")
        user_raw = stage_templates.get("user") or self._default_for_stage(stage, "user")

        return [
            {"role": "system", "content": self.render_template(system_raw, context)},
            {"role": "user", "content": self.render_template(user_raw, context)},
        ]

    def render_backend_messages(
        self,
        app_requirement: AppRequirementTemplate,
        *,
        resolved_bundle: dict[str, Any] | None = None,
    ) -> list[dict]:
        if resolved_bundle:
            return self.render_messages_from_snapshot(resolved_bundle, stage="backend")

        context = self._build_context(app_requirement)
        return [
            {"role": "system", "content": self.render_template(_fallback_template("backend", "system"), context)},
            {"role": "user", "content": self.render_template(_fallback_template("backend", "user"), context)},
        ]

    def render_frontend_messages(
        self,
        app_requirement: AppRequirementTemplate,
        backend_code: str,
        api_context_override: str | None = None,
        *,
        resolved_bundle: dict[str, Any] | None = None,
    ) -> list[dict]:
        if resolved_bundle:
            return self.render_messages_from_snapshot(
                resolved_bundle,
                stage="frontend",
                api_context_override=api_context_override,
                backend_code=backend_code,
            )

        context = self._build_context(app_requirement)
        context["backend_api_context"] = api_context_override or self._extract_api_context(
            backend_code,
        )
        return [
            {"role": "system", "content": self.render_template(_fallback_template("frontend", "system"), context)},
            {"role": "user", "content": self.render_template(_fallback_template("frontend", "user"), context)},
        ]

    @staticmethod
    def _default_for_stage(stage: str, role: str) -> str:
        return _fallback_template("backend" if stage == "backend" else "frontend", role)

    @staticmethod
    def _extract_api_context(backend_code: str) -> str:
        """Summarize a generated backend for the frontend stage.

        Lists the route decorators (Flask ``@app.route``/``@bp.get``, FastAPI
        ``@app.get``/``@router.post``) and router prefixes as written, the ORM
        models (``db.Model``, SQLAlchemy ``Base``/``Mapped`` classes) and the
        Pydantic request/response schemas with their field names.
        """
        if not backend_code:
            return "No backend API context available."

        lines = backend_code.split("\n")
        routes = []
        for line in lines:
            stripped = line.strip()
            if _SPA_ROUTE_RE.match(stripped):
                continue
            if _ROUTE_DECORATOR_RE.match(stripped) or (_ROUTER_RE.search(stripped) and not stripped.startswith("#")):
                routes.append(stripped)
        models, schemas = _classify_classes(backend_code, lines)

        context_parts = []
        if models:
            context_parts.append("### Database Models\n" + "\n".join(models))
        if schemas:
            context_parts.append("### Request/Response Schemas\n" + "\n".join(schemas))
        if routes:
            context_parts.append(
                "### API Routes\n" + "\n".join(f"- `{r}`" for r in routes),
            )

        if not context_parts:
            return f"Backend code generated ({len(lines)} lines). Use `/api/` prefix for all API calls."

        return "\n\n".join(context_parts)

    @staticmethod
    def _extract_api_context_v1(backend_code: str) -> str:
        """``frontend_context_version`` 1: what snapshots frozen without the key were rendered with."""
        if not backend_code:
            return "No backend API context available."

        lines = backend_code.split("\n")
        routes = []
        models = []
        for line in lines:
            stripped = line.strip()
            if "@app.route(" in stripped:
                routes.append(stripped)
            elif "class " in stripped and "(db.Model)" in stripped:
                models.append(stripped)

        context_parts = []
        if models:
            context_parts.append(
                "### Database Models\n" + "\n".join(f"- `{m}`" for m in models),
            )
        if routes:
            context_parts.append(
                "### API Routes\n" + "\n".join(f"- `{r}`" for r in routes),
            )

        if not context_parts:
            return f"Backend code generated ({len(lines)} lines). Use `/api/` prefix for all API calls."

        return "\n\n".join(context_parts)

    @staticmethod
    def _format_json(data: dict | list | None) -> str:
        if not data:
            return "Not specified"
        if isinstance(data, str):
            return data
        return json.dumps(data, indent=2)


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return f"{_base_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Subscript):  # e.g. Generic[T]
        return _base_name(node.value)
    return ""


def _class_fields(node: ast.ClassDef) -> list[str]:
    """Field names declared in a class body (annotations and ``Column`` assignments)."""
    fields = []
    for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            name = stmt.target.id
        elif (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and isinstance(stmt.value, ast.Call)
            and _base_name(stmt.value.func).rsplit(".", 1)[-1] in ("Column", "mapped_column", "relationship")
        ):
            name = stmt.targets[0].id
        else:
            continue
        if not name.startswith("_") and name != "model_config":
            fields.append(name)
    return fields


def _classify_classes(source: str, lines: list[str]) -> tuple[list[str], list[str]]:
    """``(models, schemas)`` as Markdown bullets: the class line as written plus its fields.

    A class is an ORM model when it derives from ``db.Model``, a declarative
    base, or a model class of the same file, or declares ``__tablename__``;
    a schema when it derives (directly or through the file's own schemas)
    from Pydantic's ``BaseModel``. Unparseable code falls back to matching
    ``(db.Model)`` class lines.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        models = [f"- `{ln.strip()}`" for ln in lines if "class " in ln and "(db.Model)" in ln]
        return models, []

    orm_roots = set(_ORM_BASES)
    schema_roots = set(_SCHEMA_BASES)
    models: list[str] = []
    schemas: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {_base_name(b) for b in node.bases}
        has_table = any(
            isinstance(stmt, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in stmt.targets)
            for stmt in node.body
        )
        fields = _class_fields(node)
        header = lines[node.lineno - 1].strip() if 0 < node.lineno <= len(lines) else f"class {node.name}"
        bullet = f"- `{header}`" + (f" fields: {', '.join(f'`{f}`' for f in fields)}" if fields else "")
        if bases & schema_roots:
            schema_roots.add(node.name)
            schemas.append(bullet)
        elif bases & orm_roots or has_table:
            orm_roots.add(node.name)
            if "DeclarativeBase" in bases and not has_table and not fields:
                continue  # the declarative base itself, not a table
            models.append(bullet)
    return models, schemas
