"""Jinja2-based prompt renderer for scaffolding mode generation."""

import json
import logging

import jinja2

from llm_lab.generation.models import AppRequirementTemplate
from llm_lab.generation.models import PromptTemplate

logger = logging.getLogger(__name__)

# ── Default prompt templates (ported from ThesisAppRework v2) ─────────

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

## Required Features
- Rich data models with 6+ fields per model
- Comprehensive CRUD endpoints with search, filtering, sorting, pagination
- JWT authentication with token_required and admin_required decorators
- Public endpoints for stats and featured items
- Seed data with 15+ diverse, realistic records
- Comprehensive error handling

## Forbidden
- `@app.before_first_request` (removed in Flask 2.3)
- `Model.query.get(id)` (use `db.session.get()`)
- Empty functions or stub implementations
"""

DEFAULT_BACKEND_USER = """\
# {{ name }}

{{ description }}

## Task
Generate `app.py` - a **comprehensive, production-quality** Flask API. Aim for **300+ lines**.

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

## Implementation Requirements
1. Rich data models with 6+ meaningful fields each
2. Multi-field search, 2+ filters, flexible sorting, pagination
3. Realistic seed data with 15-20 records
4. JWT auth with decorator-based protection
5. Admin dashboard with aggregation metrics

**START GENERATION NOW.**
"""

DEFAULT_FRONTEND_SYSTEM = """\
You are a senior React developer. Generate a **comprehensive, feature-rich** single-page application.

## Output
Return ONE code block:
```jsx:App.jsx
[complete application - aim for 400+ lines]
```

## Stack
- React 18 with Hooks
- react-router-dom v6 (Routes, Route, Link, useNavigate, useParams)
- axios for API calls
- react-hot-toast for notifications
- lucide-react for icons
- Tailwind CSS (dark theme)
- clsx for conditional classes
- date-fns for date formatting

## Required Pages (6+ distinct views)
1. Landing Page (/) - Hero, stats, featured items
2. Browse Page (/browse) - Search, filters, sorting, pagination
3. Detail Page (/item/:id) - Full item view
4. Login Page (/login) - Auth form
5. Dashboard (/dashboard) - User overview
6. Admin Panel (/admin) - Stats, user management

## Design
Dark theme, glassmorphism, gradient accents, smooth transitions.

## Forbidden
- `BrowserRouter` in App.jsx (already wrapped externally)
- `process.env` (use `import.meta.env`)
- Empty function bodies or stub implementations
"""

DEFAULT_FRONTEND_USER = """\
# {{ name }}

{{ description }}

## Task
Generate `App.jsx` - a **comprehensive, production-quality** React application. \
Aim for **400+ lines** with **6+ distinct pages**.

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

## Design Guidelines
- Unique visual identity matching the app's brand
- Impressive landing page with dynamic stats
- Comprehensive filtering and search on browse page
- Responsive design (mobile + desktop)

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
        """Render a Jinja2 template string with the given context."""
        try:
            template = self.env.from_string(template_str)
            return template.render(**context)
        except jinja2.TemplateError:
            logger.exception("Template rendering failed")
            raise

    def _build_context(self, app_req: AppRequirementTemplate) -> dict:
        """Build template context from an app requirement template."""
        return {
            "name": app_req.name,
            "description": app_req.description,
            "backend_requirements": app_req.backend_requirements or [],
            "frontend_requirements": app_req.frontend_requirements or [],
            "admin_requirements": app_req.admin_requirements or [],
            "api_endpoints": self._format_json(app_req.api_endpoints),
            "data_model": self._format_json(app_req.data_model),
            "admin_api_endpoints": "",
        }

    def render_backend_messages(
        self,
        app_requirement: AppRequirementTemplate,
        prompt_template_system: PromptTemplate | None = None,
        prompt_template_user: PromptTemplate | None = None,
    ) -> list[dict]:
        """Render system + user messages for backend generation."""
        context = self._build_context(app_requirement)

        system_content = self._get_template_content(
            prompt_template_system,
            "backend",
            "system",
            DEFAULT_BACKEND_SYSTEM,
        )
        user_content = self._get_template_content(
            prompt_template_user,
            "backend",
            "user",
            DEFAULT_BACKEND_USER,
        )

        system_rendered = self.render_template(system_content, context)
        user_rendered = self.render_template(user_content, context)

        return [
            {"role": "system", "content": system_rendered},
            {"role": "user", "content": user_rendered},
        ]

    def render_frontend_messages(
        self,
        app_requirement: AppRequirementTemplate,
        backend_code: str,
        prompt_template_system: PromptTemplate | None = None,
        prompt_template_user: PromptTemplate | None = None,
        api_context_override: str | None = None,
    ) -> list[dict]:
        """Render system + user messages for frontend generation."""
        context = self._build_context(app_requirement)
        # Use scanner output if available, otherwise fall back to regex extraction
        context["backend_api_context"] = api_context_override or self._extract_api_context(backend_code)

        system_content = self._get_template_content(
            prompt_template_system,
            "frontend",
            "system",
            DEFAULT_FRONTEND_SYSTEM,
        )
        user_content = self._get_template_content(
            prompt_template_user,
            "frontend",
            "user",
            DEFAULT_FRONTEND_USER,
        )

        system_rendered = self.render_template(system_content, context)
        user_rendered = self.render_template(user_content, context)

        return [
            {"role": "system", "content": system_rendered},
            {"role": "user", "content": user_rendered},
        ]

    @staticmethod
    def _get_template_content(
        prompt_template: PromptTemplate | None,
        stage: str,
        role: str,
        default: str,
    ) -> str:
        """Get template content from DB or use default."""
        if prompt_template and prompt_template.content:
            return prompt_template.content
        # Try to find a default in DB
        db_default = PromptTemplate.objects.filter(
            stage=stage,
            role=role,
            is_default=True,
        ).first()
        if db_default:
            return db_default.content
        return default

    @staticmethod
    def _extract_api_context(backend_code: str) -> str:
        """Extract API route information from generated backend code."""
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
            # Fallback: include a summary of the code
            return f"Backend code generated ({len(lines)} lines). Use `/api/` prefix for all API calls."

        return "\n\n".join(context_parts)

    @staticmethod
    def _format_json(data: dict | list) -> str:
        """Format JSON data as readable string for prompt injection."""
        if not data:
            return "Not specified"
        if isinstance(data, str):
            return data
        return json.dumps(data, indent=2)
