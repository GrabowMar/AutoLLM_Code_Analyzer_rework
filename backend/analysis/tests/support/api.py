"""Fakes and builders for the API / study / preflight / canary tests (owner: API/research stage).

Other stages are faked only at their public module boundary:

* ``catalog`` (tool specs and config validation) — three fake tools,
* ``adapters.get_adapter`` as seen by the preflight (language-based selection),
* ``sandbox`` (image tag/status/manifest, capacity, build requests: no Docker),
* ``executor.enqueue`` (no worker), and the OpenRouter key resolver,
* the study job dispatchers (``tasks.dispatch_study_launch`` /
  ``dispatch_study_report``): they run the job inline and record it, so a
  test that executes on-commit callbacks
  (``django_capture_on_commit_callbacks(execute=True)``) sees the
  background work done; one that does not sees it still pending.

Everything else (plan freezing, sample resolution, submission, measurement,
protocol/report builders) runs for real on synthetic generation jobs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

from backend.analysis.engine import catalog
from backend.analysis.engine import executor
from backend.analysis.engine import plan as plan_mod
from backend.analysis.engine import preflight as preflight_mod
from backend.analysis.engine import sandbox
from backend.analysis.engine.catalog import ConfigError
from backend.analysis.engine.catalog import ToolSpec
from backend.analysis.models import AnalysisProfile
from backend.generation.models import AppRequirementTemplate
from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.tests.factories import LLMModelFactory

if TYPE_CHECKING:
    from backend.llm_models.models import LLMModel

FAKE_TAG = "llmlab/analyzer:apitest00001"
FAKE_IMAGE_ID = "sha256:" + "ab" * 32
FAKE_VERSIONS = {"bandit": "1.8.6", "eslint": "9.30.0", "gitleaks": "8.28.0"}
REVIEW_MODEL = "deepseek/deepseek-v4.1-flash"

APP_PY = (
    "import os\n"
    "import subprocess\n"
    "\n"
    "\n"
    "def run(cmd):\n"
    "    # runs a shell command\n"
    "    return subprocess.call(cmd, shell=True)\n"
    "\n"
    "\n"
    "def home():\n"
    "    return os.environ.get('HOME')\n"
)
APP_JSX = "export default function App() {\n  const title = 'Todo';\n  return <h1>{title}</h1>;\n}\n"
DEFAULT_FILES = {"app.py": APP_PY, "frontend/src/App.jsx": APP_JSX, "README.md": "# Todo\n"}
STATIC_CONFIG: dict[str, Any] = {
    "tools": [{"slug": "bandit"}, {"slug": "eslint"}, {"slug": "gitleaks", "required": False}],
    "reviewer": {"enabled": False},
}


def _spec(slug: str, name: str, category: str, languages: tuple[str, ...], order: int) -> ToolSpec:
    raw = {"slug": slug, "name": name, "version": FAKE_VERSIONS[slug]}
    return ToolSpec(
        slug=slug,
        name=name,
        kind="static",
        category=category,
        languages=languages,
        file_kinds=("source", "config"),
        output_kind="findings",
        description=f"{name} (fake)",
        docs_url=f"https://example.invalid/{slug}",
        display_order=order,
        config_schema=({"name": "strict", "type": "boolean", "default": False},),
        default_config={"strict": False},
        spec_hash=hashlib.sha256(slug.encode()).hexdigest(),
        raw=raw,
    )


FAKE_SPECS: dict[str, ToolSpec] = {
    "bandit": _spec("bandit", "Bandit", "security", ("python",), 10),
    "eslint": _spec("eslint", "ESLint", "lint", ("javascript", "jsx", "typescript", "tsx"), 20),
    "gitleaks": _spec("gitleaks", "Gitleaks", "secrets", ("*",), 30),
}


def _validate_tool_config(slug: str, config: dict[str, Any] | None) -> dict[str, Any]:
    spec = FAKE_SPECS[slug]
    effective = dict(spec.default_config)
    for key, value in (config or {}).items():
        if key not in spec.default_config:
            raise ConfigError({f"config.{key}": "unknown option"})
        if not isinstance(value, bool):
            raise ConfigError({f"config.{key}": "must be a boolean"})
        effective[key] = value
    return effective


def _config_hash(slug: str, config: dict[str, Any], tool_version: str = "") -> str:
    payload = json.dumps([slug, config, tool_version, FAKE_SPECS[slug].spec_hash], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


class FakeAdapter:
    """Selects included files by language like a real adapter would."""

    def __init__(self, spec: ToolSpec) -> None:
        self.slug = spec.slug
        self.languages = set(spec.languages)

    def select_files(self, sample, config):
        return [
            f
            for f in sample.included()
            if "*" in self.languages or (f.language in self.languages and f.kind in ("source", "config"))
        ]


@dataclass
class FakeEngine:
    """Switches for the faked infrastructure (tests flip them)."""

    built: bool = True
    daemon_ok: bool = True
    key: str | None = "sk-or-test"
    tag: str = FAKE_TAG
    build_requests: int = 0
    enqueued: list[str] = field(default_factory=list)
    dispatched: list[tuple[str, ...]] = field(default_factory=list)

    def image_status(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "built": self.built and self.daemon_ok,
            "image_id": FAKE_IMAGE_ID if self.built else "",
            "manifest": {"tools": {k: {"version": v} for k, v in FAKE_VERSIONS.items()}} if self.built else None,
            "building": False,
            "daemon_ok": self.daemon_ok,
            "error": "" if self.daemon_ok else "Docker daemon unavailable: connection refused",
        }


def install_fakes(monkeypatch, settings) -> FakeEngine:
    """Fake catalog, adapters, image and key; the rest of the engine runs for real.

    Test modules wrap this in their own ``engine`` fixture.
    """
    from django.core.cache import cache

    from backend.analysis.api import common
    from backend.credentials.services import resolver

    # The API caches daemon answers briefly; never let one test see another's.
    cache.delete_many(
        [
            common.IMAGE_STATUS_CACHE_KEY,
            common.IMAGE_BUILD_LOCK_KEY,
            common.CAPACITY_CACHE_KEY,
            common.RECONCILE_CACHE_KEY,
        ],
    )
    engine = FakeEngine()
    settings.ANALYSIS_DEFAULT_REVIEW_MODEL = REVIEW_MODEL
    monkeypatch.setattr(catalog, "load_specs", lambda *, refresh=False: dict(FAKE_SPECS))
    monkeypatch.setattr(catalog, "get_spec", lambda slug: FAKE_SPECS[slug])
    monkeypatch.setattr(catalog, "static_specs", lambda: sorted(FAKE_SPECS.values(), key=lambda s: s.display_order))
    monkeypatch.setattr(catalog, "validate_tool_config", _validate_tool_config)
    monkeypatch.setattr(catalog, "config_hash", _config_hash)
    monkeypatch.setattr(preflight_mod, "get_adapter", lambda slug: FakeAdapter(FAKE_SPECS[slug]))
    monkeypatch.setattr(sandbox, "image_tag", lambda: engine.tag)

    def ensure_image(*, expect_tag: str = "", build: bool = True, **_kwargs):
        if not engine.daemon_ok:
            msg = "Docker daemon unavailable"
            raise sandbox.SandboxError(msg)
        if not engine.built:
            msg = f"The analyzer image {engine.tag} is not built"
            raise sandbox.ImageUnavailable(msg)
        return engine.tag, FAKE_IMAGE_ID

    def request_image_build() -> dict[str, Any]:
        engine.build_requests += 1
        return {**engine.image_status(), "building": True}

    monkeypatch.setattr(sandbox, "ensure_image", ensure_image)
    monkeypatch.setattr(sandbox, "image_manifest", lambda ref: engine.image_status()["manifest"] or {})
    monkeypatch.setattr(sandbox, "image_status", engine.image_status)
    monkeypatch.setattr(sandbox, "request_image_build", request_image_build)
    monkeypatch.setattr(sandbox, "capacity", lambda: {"budget_bytes": 1, "used_bytes": 0, "containers": []})
    monkeypatch.setattr(executor, "enqueue", lambda run_id, countdown=0: engine.enqueued.append(str(run_id)))

    from backend.analysis import tasks
    from backend.analysis.research import study as study_mod

    def dispatch_launch(study_id: str) -> str:
        engine.dispatched.append(("launch", str(study_id)))
        study_mod.submit_launch(study_id)
        return "inline"

    def dispatch_report(study_id: str, request_id: str) -> str:
        engine.dispatched.append(("report", str(study_id), request_id))
        study_mod.generate_report(study_id, request_id)
        return "inline"

    monkeypatch.setattr(tasks, "dispatch_study_launch", dispatch_launch)
    monkeypatch.setattr(tasks, "dispatch_study_report", dispatch_report)

    def get_key(user, *, allow_global_fallback=None):
        if engine.key is None:
            msg = "No OpenRouter API key"
            raise resolver.MissingApiKeyError(msg)
        return engine.key

    monkeypatch.setattr(resolver, "get_openrouter_key", get_key)
    return engine


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------


def llm_model(model_id: str = "acme/coder-1", **kwargs) -> LLMModel:
    return LLMModelFactory(model_id=model_id, model_name=kwargs.pop("model_name", model_id), **kwargs)


def review_model() -> LLMModel:
    """The default reviewer model in the catalog (needed to freeze an enabled reviewer)."""
    return LLMModelFactory(
        model_id=REVIEW_MODEL,
        canonical_slug="deepseek-deepseek-v4.1-flash",
        model_name="DeepSeek V4.1 Flash",
        provider="deepseek",
        context_window=128_000,
        max_output_tokens=32_000,
        capabilities_json={"supported_parameters": ["response_format", "structured_outputs", "temperature"]},
    )


def app(slug: str = "todo") -> AppRequirementTemplate:
    template, _ = AppRequirementTemplate.objects.get_or_create(slug=slug, defaults={"name": slug.title()})
    return template


def make_job(user, *, files: dict[str, str] | None = None, model=None, app_slug: str = "todo", **kwargs):
    """A completed scaffolding-mode job whose ``files`` the sample resolver reads as authored code."""
    kwargs.setdefault("mode", "scaffolding")
    kwargs.setdefault("status", "completed")
    kwargs.setdefault("stack_slug", "flask-react")
    return GenerationJobFactory(
        created_by=user,
        model=model if model is not None else llm_model(),
        app_requirement=app(app_slug) if app_slug else None,
        result_data={"files": dict(files if files is not None else DEFAULT_FILES)},
        **kwargs,
    )


def make_profile(user=None, *, config: dict[str, Any] | None = None, slug: str | None = None, system: bool = False):
    return plan_mod.save_profile(
        user=user,
        name=(slug or "profile").replace("-", " ").title(),
        description="",
        config=json.loads(json.dumps(config or STATIC_CONFIG)),
        slug=slug or f"profile-{AnalysisProfile.objects.count() + 1}",
        is_system=system,
    )
