"""Jinja2-based prompt renderer for scaffolding mode generation."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING
from typing import Any

import jinja2

if TYPE_CHECKING:
    from backend.generation.models import AppRequirementTemplate

logger = logging.getLogger(__name__)

# Legacy fallbacks when no snapshot and DB empty (deprecated after bundle seeding)
DEFAULT_BACKEND_SYSTEM = """\
You are a senior Flask backend developer. Generate a **comprehensive, feature-rich** API.

## Output
Return ONE code block:
```python:app.py
[complete application - aim for 300+ lines]
```

## Stack
- Flask 3.x, Flask-SQLAlchemy, Flask-CORS
- PyJWT for authentication, bcrypt for passwords
- SQLite database

## Forbidden
- `@app.before_first_request` (removed in Flask 2.3)
- `Model.query.get(id)` (use `db.session.get()`)
"""

DEFAULT_BACKEND_USER = """\
# {{ name }}

{{ description }}

## Task
Generate `app.py` - a **production-quality** Flask API.

## Requirements

### Features
{% for req in backend_requirements %}
- {{ req }}
{% endfor %}

### Admin Features
{% for req in admin_requirements %}
- {{ req }}
{% endfor %}

### Data Model Context
{{ data_model }}

### API Endpoints
{{ api_endpoints }}

{{ admin_api_endpoints }}

**START GENERATION NOW.**
"""

DEFAULT_FRONTEND_SYSTEM = """\
You are a senior React developer. Generate a **comprehensive** single-page application.

## Output
Return ONE code block:
```jsx:App.jsx
[complete application]
```

## Stack
- React 18 with Hooks, react-router-dom v6, axios, react-hot-toast, lucide-react, Tailwind CSS

## Forbidden
- `BrowserRouter` in App.jsx (already wrapped externally)
- `process.env` (use `import.meta.env`)
"""

DEFAULT_FRONTEND_USER = """\
# {{ name }}

{{ description }}

## Task
Generate `App.jsx` matching the requirements below.

## Backend API Context
{{ backend_api_context }}

## Requirements

### Features
{% for req in frontend_requirements %}
- {{ req }}
{% endfor %}

### Admin Features
{% for req in admin_requirements %}
- {{ req }}
{% endfor %}

**GENERATE COMPLETE APP.JSX NOW.**
"""


class PromptRenderer:
    """Renders Jinja2 prompt templates with app requirement context."""

    def __init__(self) -> None:
        self.env = jinja2.Environment(
            autoescape=False,
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
        return self._build_context_from_dict(
            {
                "name": app_req.name,
                "description": app_req.description,
                "backend_requirements": app_req.backend_requirements or [],
                "frontend_requirements": app_req.frontend_requirements or [],
                "admin_requirements": app_req.admin_requirements or [],
                "api_endpoints": app_req.api_endpoints,
                "data_model": app_req.data_model,
                "admin_api_endpoints": app_req.admin_api_endpoints,
            },
        )

    def _build_context_from_dict(self, app_dict: dict[str, Any]) -> dict:
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
            "data_model": self._format_json(app_dict.get("data_model")),
            "admin_api_endpoints": admin_section,
        }

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
            context["backend_api_context"] = api_context_override or self._extract_api_context(
                backend_code,
            )
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
            {"role": "system", "content": self.render_template(DEFAULT_BACKEND_SYSTEM, context)},
            {"role": "user", "content": self.render_template(DEFAULT_BACKEND_USER, context)},
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
            {"role": "system", "content": self.render_template(DEFAULT_FRONTEND_SYSTEM, context)},
            {"role": "user", "content": self.render_template(DEFAULT_FRONTEND_USER, context)},
        ]

    @staticmethod
    def _default_for_stage(stage: str, role: str) -> str:
        if stage == "backend" and role == "system":
            return DEFAULT_BACKEND_SYSTEM
        if stage == "backend" and role == "user":
            return DEFAULT_BACKEND_USER
        if stage == "frontend" and role == "system":
            return DEFAULT_FRONTEND_SYSTEM
        return DEFAULT_FRONTEND_USER

    @staticmethod
    def _extract_api_context(backend_code: str) -> str:
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
