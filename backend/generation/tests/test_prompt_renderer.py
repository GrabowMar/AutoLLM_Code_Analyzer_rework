"""PromptRenderer: context (data models, evaluator contract), API-context
extraction from Flask and FastAPI backends, and the shipped stage prompts."""

from __future__ import annotations

import json
from unittest import mock

import pytest

from backend.generation.seeding import PROMPT_STAGE_SEEDS
from backend.generation.seeding import PROMPTS_DIR
from backend.generation.services.prompt_renderer import PromptRenderer
from backend.generation.services.prompt_renderer import templates_state_contract

BOOK = {"name": "Book", "fields": {"id": "integer", "title": "string"}}
REVIEW = {"name": "Review", "fields": {"id": "integer", "rating": "integer"}}

APP = {
    "name": "Book Library",
    "description": "Track books",
    "backend_requirements": ["Access control: public reads; require auth for create/update/delete"],
    "frontend_requirements": ["Book list"],
    "admin_requirements": ["Admin overview"],
    "api_endpoints": [{"method": "GET", "path": "/api/books", "description": "List", "request": None}],
    "admin_api_endpoints": [{"method": "GET", "path": "/api/admin/books", "description": "Overview"}],
    "data_model": BOOK,
}

FLASK_BACKEND = """
from flask import Flask
app = Flask(__name__)
bp = Blueprint("books", __name__, url_prefix="/api/books")

class Book(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    title = db.Column(db.String(200))

@app.route("/api/health")
def health(): ...

@bp.get("/<int:book_id>")
def get_book(book_id): ...

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def _serve_spa(path): ...
"""

FASTAPI_BACKEND = """
from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class Book(Base):
    __tablename__ = "books"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]

class Review(Book):
    rating: Mapped[int]

class BookIn(BaseModel):
    title: str

class BookOut(BookIn):
    model_config = ConfigDict(from_attributes=True)
    id: int

app = FastAPI(lifespan=lifespan)
router = APIRouter(prefix="/api/books")

@router.get("/{book_id}", response_model=BookOut)
async def get_book(book_id: int): ...

@app.post("/api/auth/login")
def login(body: BookIn): ...

app.include_router(router)

@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str): ...
"""


def _stage_file(slug: str) -> str:
    seed = next(s for s in PROMPT_STAGE_SEEDS if s["slug"] == slug)
    return (PROMPTS_DIR / seed["path"]).read_text(encoding="utf-8")


def _snapshot(app: dict, *, backend: str, frontend: str) -> dict:
    return {
        "app_requirement": app,
        "prompts": {},
        "prompt_templates": {
            "backend": {
                "system": _stage_file(f"{backend}-backend-system"),
                "user": _stage_file(f"{backend}-backend-user"),
            },
            "frontend": {
                "system": _stage_file(f"{frontend}-frontend-system"),
                "user": _stage_file(f"{frontend}-frontend-user"),
            },
        },
    }


class TestDataModels:
    def test_single_legacy_model_renders_as_before(self):
        ctx = PromptRenderer()._build_context_from_dict(APP)
        assert ctx["data_model"] == json.dumps(BOOK, indent=2)

    def test_single_item_list_renders_the_model(self):
        ctx = PromptRenderer()._build_context_from_dict({**APP, "data_models": [BOOK], "data_model": BOOK})
        assert ctx["data_model"] == json.dumps(BOOK, indent=2)

    def test_several_models_render_as_a_list(self):
        ctx = PromptRenderer()._build_context_from_dict({**APP, "data_models": [BOOK, REVIEW], "data_model": BOOK})
        assert json.loads(ctx["data_model"]) == [BOOK, REVIEW]

    def test_no_model(self):
        ctx = PromptRenderer()._build_context_from_dict({**APP, "data_model": {}})
        assert ctx["data_model"] == "Not specified"


class TestExtractApiContext:
    def test_flask_routes_and_models_without_spa_catch_all(self):
        text = PromptRenderer._extract_api_context(FLASK_BACKEND)
        assert '`@app.route("/api/health")`' in text
        assert '`@bp.get("/<int:book_id>")`' in text
        assert "Blueprint(" in text
        assert "<path:path>" not in text
        assert 'defaults={"path": ""}' not in text
        assert "- `class Book(db.Model):` fields: `id`, `title`" in text

    def test_fastapi_routes_models_and_schemas(self):
        text = PromptRenderer._extract_api_context(FASTAPI_BACKEND)
        models, _, rest = text.partition("### Request/Response Schemas")
        assert "### Database Models" in models
        assert "`class Book(Base):` fields: `id`, `title`" in models
        assert "`class Review(Book):` fields: `rating`" in models
        assert "DeclarativeBase" not in models
        assert "`class BookIn(BaseModel):` fields: `title`" in rest
        assert "`class BookOut(BookIn):` fields: `id`" in rest  # model_config is not a field
        assert '`@router.get("/{book_id}", response_model=BookOut)`' in rest
        assert '`@app.post("/api/auth/login")`' in rest
        assert '`router = APIRouter(prefix="/api/books")`' in rest
        assert "`app.include_router(router)`" in rest
        assert "full_path" not in rest

    def test_unparseable_code_falls_back_to_line_matching(self):
        code = "class Book(db.Model):\n    id = db.Column(\n@app.route('/api/books')\ndef broken(:\n"
        text = PromptRenderer._extract_api_context(code)
        assert "- `class Book(db.Model):`" in text
        assert "- `@app.route('/api/books')`" in text

    def test_nothing_recognised(self):
        assert PromptRenderer._extract_api_context("x = 1\n").startswith("Backend code generated (2 lines)")
        assert PromptRenderer._extract_api_context("") == "No backend API context available."

    def test_snapshot_without_context_version_keeps_the_version_1_extractor(self):
        # Pending and pinned pre-versioning snapshots render what they were frozen with.
        snapshot = {"app_requirement": APP, "prompt_templates": {}}
        old = PromptRenderer().render_messages_from_snapshot(snapshot, stage="frontend", backend_code=FASTAPI_BACKEND)
        new = PromptRenderer().render_messages_from_snapshot(
            {**snapshot, "frontend_context_version": 2},
            stage="frontend",
            backend_code=FASTAPI_BACKEND,
        )
        v1 = PromptRenderer._extract_api_context_v1(FASTAPI_BACKEND)
        assert v1.startswith("Backend code generated (")
        assert v1 in old[1]["content"]
        assert "### Request/Response Schemas" in new[1]["content"]


class TestShippedPrompts:
    @pytest.mark.parametrize(
        ("backend", "frontend"),
        [("base", "base"), ("fastapi", "vue"), ("fastapi", "base")],
    )
    def test_render_states_the_contract(self, backend, frontend):
        renderer = PromptRenderer()
        snap = _snapshot(APP, backend=backend, frontend=frontend)
        back = renderer.render_messages_from_snapshot(snap, stage="backend")
        front = renderer.render_messages_from_snapshot(snap, stage="frontend", backend_code=FASTAPI_BACKEND)
        for message in (*back, *front):
            assert "{{" not in message["content"]
            assert "{%" not in message["content"]
            assert "\n\n\n" not in message["content"]
        user = back[1]["content"]
        assert "`GET /api/health`" in user
        assert '`POST /api/auth/register` with JSON `{"username": string, "password": string}`' in user
        assert '`{"token": string}`' in user
        assert "Authorization: Bearer <token>" in user
        assert "answer HTTP 401 without a valid token" in user
        assert "admin account `admin`" in user
        assert "`ADMIN_PASSWORD`" in user
        assert "./static" in user
        front_user = front[1]["content"]
        assert "/api/auth/login" in front_user
        assert "### Admin API Endpoints" in front_user
        for padding in ("300+", "400+", "15+", "6+"):
            assert padding not in back[0]["content"] + user + front[0]["content"] + front_user

    def test_spec_defined_email_auth(self):
        app = {
            **APP,
            "api_endpoints": [
                {"method": "POST", "path": "/api/auth/register", "request": {"email": "string", "password": "string"}},
                {"method": "POST", "path": "/api/auth/login", "request": {"email": "string", "password": "string"}},
            ],
        }
        snap = _snapshot(app, backend="base", frontend="base")
        user = PromptRenderer().render_messages_from_snapshot(snap, stage="backend")[1]["content"]
        assert "Implement the register and login endpoints listed above" in user
        assert "admin account `admin@example.com`" in user

    def test_no_accounts_section_without_auth_needs(self):
        app = {**APP, "backend_requirements": ["Book list"], "admin_api_endpoints": []}
        snap = _snapshot(app, backend="fastapi", frontend="vue")
        user = PromptRenderer().render_messages_from_snapshot(snap, stage="backend")[1]["content"]
        assert "### Accounts" not in user
        assert "`GET /api/health`" in user
        assert "\n\n\n" not in user

    def test_shipped_backend_templates_state_contract(self):
        assert templates_state_contract(_snapshot(APP, backend="base", frontend="base")["prompt_templates"])
        assert templates_state_contract(_snapshot(APP, backend="fastapi", frontend="vue")["prompt_templates"])

    def test_templates_without_contract(self):
        assert not templates_state_contract({"backend": {"system": "Build {{ name }}", "user": "{{ description }}"}})
        assert not templates_state_contract({"backend": {"system": "{% if %}", "user": ""}})
        assert not templates_state_contract(None)
        # Only the backend stage counts: that is where the server is built.
        assert not templates_state_contract({"frontend": {"user": "{{ contract.login_path }}"}})


def test_fallback_prompts_are_the_default_profile_files():
    renderer = PromptRenderer()
    assert renderer._default_for_stage("backend", "system") == _stage_file("base-backend-system")
    assert renderer._default_for_stage("frontend", "user") == _stage_file("base-frontend-user")
    messages = renderer.render_messages_from_snapshot({"app_requirement": APP}, stage="backend")
    assert "admin account `admin`" in messages[1]["content"]


@pytest.mark.django_db
def test_fallback_render_from_a_row_without_snapshot():
    from backend.generation.tests.factories import AppRequirementTemplateFactory

    row = AppRequirementTemplateFactory(**{k: v for k, v in APP.items() if k != "name"}, name="Book Library")
    renderer = PromptRenderer()
    backend = renderer.render_backend_messages(row)
    frontend = renderer.render_frontend_messages(row, FLASK_BACKEND)
    assert "# Book Library" in backend[1]["content"]
    assert "`POST /api/auth/register`" in backend[1]["content"]
    assert "class Book(db.Model)" in frontend[1]["content"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("templates", "expected"),
    [
        (None, False),
        ({"backend": {"system": "old prompt", "user": "{{ name }}"}}, False),
        ({"backend": {"system": "", "user": "{{ contract.health_path }}"}}, True),
    ],
)
def test_contract_stated_in_prompt_for_scaffolding_jobs(templates, expected):
    from backend.generation.models import GenerationJob
    from backend.generation.services.evaluation import runner
    from backend.users.tests.factories import UserFactory

    snapshot = {"llm": {}} if templates is None else {"llm": {}, "prompt_templates": templates}
    job = GenerationJob.objects.create(mode="scaffolding", created_by=UserFactory(), resolved_bundle=snapshot)
    outcome = {**runner.tester_identity(), "status": "passed", "summary": {"pass_rate": 1.0}, "checks": []}
    with mock.patch.object(runner.Evaluation, "run", return_value=outcome):
        runner.evaluate_job(job)
    job.refresh_from_db()
    assert job.metrics["functional"]["contract_stated_in_prompt"] is expected
