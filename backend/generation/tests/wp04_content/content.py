"""Readers and checks for WP-04's seed content (``backend/generation/data/``), shared by the wp04 tests.

The readers parse the files directly (the layout of section 11.1 and the golden fixture), so the content
tests do not depend on the seeder. Where WP-03's lint, engine and context builder exist they are used; the
``fallback_*`` helpers stand in for them while a part raises ``NotImplementedError`` (spec 12.3: "until it
lands, validate with ``contracts.library.TaskSpecContent.model_validate``").
"""

from __future__ import annotations

import functools
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Final

import yaml

from backend.generation.contracts.library import PROSE_VARIANTS

DATA_ROOT: Final = Path(__file__).resolve().parents[2] / "data"
TASKS_ROOT: Final = DATA_ROOT / "tasks"
PROMPTS_ROOT: Final = DATA_ROOT / "prompts"

# Section 11.2: the 33 v2 tasks (rewritten) and the new ones.
V2_TASKS: Final = (
    "analytics_campaign_monitor",
    "api_url_shortener",
    "api_weather_display",
    "auth_user_login",
    "booking_reservations",
    "collaboration_simple_poll",
    "commerce_subscription_billing",
    "content_recipe_list",
    "crm_customer_list",
    "crud_book_library",
    "crud_todo_list",
    "dataviz_sales_table",
    "ecommerce_cart",
    "education_quiz_app",
    "fileproc_image_upload",
    "finance_expense_list",
    "gaming_leaderboard",
    "geolocation_store_list",
    "healthcare_appointments",
    "inventory_stock_list",
    "iot_sensor_display",
    "learning_flashcards",
    "media_audio_player",
    "messaging_notifications",
    "monitoring_server_stats",
    "operations_incident_center",
    "productivity_notes",
    "realtime_chat_room",
    "scheduling_event_list",
    "social_blog_posts",
    "utility_base64_tool",
    "validation_xml_checker",
    "workflow_task_board",
)
NEW_TASKS: Final = (
    "notes_markdown_render",
    "file_share_multipart",
    "account_password_reset",
    "banking_transfers_cookie",
    "link_preview_fetcher",
    "login_rate_limited",
    "team_roles_multitenant",
    "csv_report_export",
    "webhook_receiver_signed",
    "search_catalog_sql",
)
# Section 8.5: the conformance-kit tasks every phase-A framework needs a reference for (wave 3).
KIT_TASKS: Final = ("crud_todo_list", "team_roles_multitenant", "file_share_multipart")

# Section 11.2 prose rules (the WP-04 test list).
MINIMAL_MAX_WORDS: Final = 120
FORBIDDEN_PHRASES: Final = ("evaluat", "test suite", "graded", "admin/admin", "default password")
# Prompt text never talks about grading, hidden checks or budgets in numbers (Appendix A.2, 11.5).
FORBIDDEN_PROMPT_PHRASES: Final = (*FORBIDDEN_PHRASES, "tested", "test cases", "score", "token budget", "$")

HARNESSES: Final = ("opencode", "mini-swe-agent", "aider", "goose", "qwen-code", "claude-code", "codex", "gemini-cli")
# Enabled in wave 2 as ported adapters (section 5.3), plus claude-code and goose since E2E-19 (they run on
# OpenCode Go); the other three are seeded archived (11.6).
ENABLED_HARNESSES: Final = ("opencode", "mini-swe-agent", "aider", "goose", "claude-code")
STYLE_SLUGS: Final = (
    "model-choice",
    "tailwind",
    "plain-css",
    "css-modules",
    "mui",
    "shadcn",
    "bootstrap",
    "vuetify",
    "primevue",
    "skeleton",
    "daisyui",
)
TONES: Final = ("neutral", "minimal", "playful", "corporate", "editorial", "brutalist", "dark-first", "high-contrast")
GUIDANCE_SLUGS: Final = (
    "none",
    "security-generic",
    "security-cwe",
    "libraries-prescribed-py",
    "libraries-prescribed-js",
)
PROMPT_SETS: Final = (
    "app-default",
    "app-budget-neutral",
    "app-editor",
    "app-paraphrase-a",
    "app-paraphrase-b",
    "app-paraphrase-c",
    "slot-single-shot-default",
    "slot-agent-default",
)
SAMPLING_PRESETS: Final = ("provider-default", "t02", "t07", "t10")
# Prompt templates WP-04 owns (WP-05 owns ``stack-*`` and ``run-contract-*``, section 12.1).
FOREIGN_PROMPT_PREFIXES: Final = ("stack-", "run-contract-")


def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Entry:
    """One seed entity as written (refs ``slug@v``), with its wrapper fields."""

    kind: str
    slug: str
    name: str
    description: str
    content: dict[str, Any]
    archived: bool = False
    path: Path | None = None


# ----------------------------------------------------------------------------------------------- tasks


@functools.cache
def task_slugs() -> tuple[str, ...]:
    return tuple(sorted(p.name for p in TASKS_ROOT.iterdir() if p.is_dir() and (p / "task.yaml").is_file()))


@functools.cache
def _task(slug: str) -> Entry:
    directory = TASKS_ROOT / slug
    content = _yaml(directory / "task.yaml")
    content["prose"] = {
        path.stem: path.read_text(encoding="utf-8").removesuffix("\n")
        for path in sorted((directory / "prose").glob("*.md"))
    }
    return Entry("task", slug, content["title"], content["summary"], content, path=directory)


def task(slug: str) -> dict[str, Any]:
    """A fresh copy of a task's content (``task.yaml`` plus ``prose``)."""
    return yaml.safe_load(yaml.safe_dump(_task(slug).content))


def tasks() -> dict[str, dict[str, Any]]:
    return {slug: task(slug) for slug in task_slugs()}


@functools.cache
def vocabulary() -> dict[str, Any]:
    return _yaml(TASKS_ROOT / "vocab.yaml")


# --------------------------------------------------------------------------------------------- prompts


@functools.cache
def prompts() -> dict[str, Entry]:
    out = {}
    for meta_path in sorted(PROMPTS_ROOT.glob("*.meta.yaml")):
        slug = meta_path.name.removesuffix(".meta.yaml")
        meta = _yaml(meta_path)
        template = (PROMPTS_ROOT / f"{slug}.jinja2").read_text(encoding="utf-8").removesuffix("\n")
        content = {
            "role": meta["role"],
            "engine": meta.get("engine", "jinja2-sandbox@1"),
            "template": template,
            "variables": meta.get("variables", []),
            "modes": meta["modes"],
            "tags": meta.get("tags", []),
        }
        out[slug] = Entry("prompt", slug, meta["name"], meta.get("description", ""), content, path=meta_path)
    return out


def own_prompts() -> dict[str, Entry]:
    return {slug: entry for slug, entry in prompts().items() if not slug.startswith(FOREIGN_PROMPT_PREFIXES)}


# ------------------------------------------------------------------------------------------ yaml kinds

YAML_DIRS: Final = {
    "stack": "stacks",
    "style": "styles",
    "guidance": "guidance",
    "prompt_set": "prompt_sets",
    "harness_preset": "harness_presets",
    "sampling": "sampling_presets",
    "eval_suite": "eval_suites",
}


@functools.cache
def entries(kind: str) -> dict[str, Entry]:
    root = DATA_ROOT / YAML_DIRS[kind]
    if not root.is_dir():
        return {}
    out = {}
    for path in sorted(root.glob("*.yaml")):
        data = _yaml(path)
        out[path.stem] = Entry(
            kind,
            path.stem,
            data["name"],
            data.get("description", ""),
            data["content"],
            archived=bool(data.get("archived", False)),
            path=path,
        )
    return out


@functools.cache
def tones() -> dict[str, dict[str, Any]]:
    return {path.stem: _yaml(path) for path in sorted((DATA_ROOT / "styles" / "tones").glob("*.yaml"))}


def slug_of(ref: str) -> str:
    return ref.split("@", 1)[0]


# ----------------------------------------------------------------------------------- prose consistency

_WORD_RE: Final = re.compile(r"\S+")


def word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def mention(endpoint: Mapping[str, Any]) -> str:
    """The literal form the content uses for an endpoint in standard and exhaustive prose."""
    return f"`{endpoint['method']} {endpoint['path']}`"


def prose_issues(slug: str, content: Mapping[str, Any]) -> list[str]:
    """Section 11.2 prose rules, independent of WP-03's lint (stricter: literal mentions, minimal lists)."""
    issues: list[str] = []
    prose = content["prose"]
    missing = [variant for variant in PROSE_VARIANTS if not prose.get(variant, "").strip()]
    issues += [f"{slug}: no {variant} prose" for variant in missing]
    if missing:
        return issues
    for variant, text in prose.items():
        lowered = text.lower()
        issues += [
            f"{slug}/{variant}: forbidden phrase {phrase!r}" for phrase in FORBIDDEN_PHRASES if phrase in lowered
        ]
        if "{{" in text or "{%" in text:
            issues.append(f"{slug}/{variant}: template syntax in prose")
    words = word_count(prose["minimal"])
    if words > MINIMAL_MAX_WORDS:
        issues.append(f"{slug}/minimal: {words} words (at most {MINIMAL_MAX_WORDS})")
    for endpoint in content["endpoints"]:
        plain = f"{endpoint['method']} {endpoint['path']}"
        if plain not in prose["minimal"]:
            issues.append(f"{slug}/minimal: does not list {plain}")
        for variant in ("standard", "exhaustive"):
            if mention(endpoint) not in prose[variant]:
                issues.append(f"{slug}/{variant}: does not mention {mention(endpoint)}")
        for query in endpoint.get("query") or []:
            for variant in ("standard", "exhaustive"):
                if f"`{query['name']}" not in prose[variant]:
                    issues.append(f"{slug}/{variant}: does not mention query parameter {query['name']!r}")
        for status in endpoint.get("errors") or []:
            if str(status) not in prose["exhaustive"]:
                issues.append(f"{slug}/exhaustive: does not state status {status} of {endpoint['id']}")
        if endpoint.get("success_status", 200) != 200 and str(endpoint["success_status"]) not in prose["exhaustive"]:
            issues.append(f"{slug}/exhaustive: does not state the success status of {endpoint['id']}")
    for entity in content["entities"]:
        for field in entity["fields"]:
            if field.get("max_length") and field.get("type") != "enum" and entity["name"] != "User":
                if str(field["max_length"]) not in prose["exhaustive"]:
                    issues.append(f"{slug}/exhaustive: no limit for {entity['name']}.{field['name']}")
    if re.search(r"\b(?:400|401|403|404|409|413|429)\b", prose["minimal"]):
        issues.append(f"{slug}/minimal: states error codes")
    return issues


def structure_issues(slug: str, content: Mapping[str, Any]) -> list[str]:
    """Cross-field rules WP-03's lint does not cover (vocabulary of probes, admin/auth consistency)."""
    vocab = vocabulary()
    issues: list[str] = []
    features = set(content["features"])
    endpoints = {endpoint["id"]: endpoint for endpoint in content["endpoints"]}
    auths = {endpoint["auth"] for endpoint in content["endpoints"]}
    issues += [f"{slug}: unknown feature {f!r}" for f in features if f not in vocab["features"]]
    issues += [f"{slug}: unknown surface {s!r}" for s in content["surfaces"] if s not in vocab["surfaces"]]
    if len(set(content["surfaces"])) != len(content["surfaces"]):
        issues.append(f"{slug}: duplicate surfaces")
    if ("admin" in auths) != ("admin" in features):
        issues.append(f"{slug}: the admin feature and the admin endpoints disagree")
    if bool(auths & {"user", "owner", "admin"}) != ("auth" in features):
        issues.append(f"{slug}: the auth feature and the authenticated endpoints disagree")
    if "auth" in features:
        for required in ("auth.register", "auth.login"):
            if required not in endpoints:
                issues.append(f"{slug}: an auth task needs {required}")
        if not content.get("auth_flow"):
            issues.append(f"{slug}: an auth task needs an auth_flow (spec_defined)")
    if (
        "ownership" in features
        and "owner" not in auths
        and not any(e.get("owned_by_user") for e in content["entities"])
    ):
        issues.append(f"{slug}: ownership without owned entities")
    for endpoint in content["endpoints"]:
        if endpoint["path"] != "/" and not endpoint["path"].startswith("/api/"):
            issues.append(f"{slug}: {endpoint['id']} is outside /api")
        if endpoint.get("realtime", "none") == "websocket" and "websocket" not in features:
            issues.append(f"{slug}: {endpoint['id']} is a websocket without the websocket feature")
        if ":" in endpoint["path"]:
            issues.append(f"{slug}: {endpoint['id']} uses :param instead of {{param}}")
    for probe in content.get("probes") or []:
        if probe["cwe"] not in vocab["surfaces"]:
            issues.append(f"{slug}: probe {probe['id']} names an unknown CWE")
        if probe["cwe"] not in content["surfaces"]:
            issues.append(f"{slug}: probe {probe['id']} CWE {probe['cwe']} is not among the task's surfaces")
        if probe["kind"] not in vocab["probe_requires"]:
            issues.append(f"{slug}: probe {probe['id']} has an unknown kind")
        requires = vocab["probe_requires"].get(probe["kind"], {})
        if (
            requires.get("features_any")
            and not features & set(requires["features_any"])
            and not set(
                content["surfaces"],
            )
            & set(requires.get("surfaces_any", []))
        ):
            issues.append(f"{slug}: probe {probe['id']} lacks the tags its kind requires")
    for check in content.get("acceptance") or []:
        if not check.get("stated_in"):
            issues.append(f"{slug}: acceptance {check['id']} is stated in no variant")
    return issues


# ---------------------------------------------------------------------------------- fallback rendering

FALLBACK_FILTERS: Final = (
    "indent",
    "join",
    "upper",
    "lower",
    "title",
    "trim",
    "wordwrap",
    "replace",
    "length",
    "sort",
    "default",
    "tojson",
)


@functools.cache
def _fallback_env() -> Any:
    from jinja2 import StrictUndefined
    from jinja2.defaults import DEFAULT_FILTERS
    from jinja2.sandbox import SandboxedEnvironment

    class _ItemOnly(SandboxedEnvironment):
        """Spec 9.2: item lookup only (contexts are pure dicts), whitelisted filters, ``range`` only."""

        def getattr(self, obj: Any, attribute: str) -> Any:
            if isinstance(obj, Mapping) and attribute in obj:
                return obj[attribute]
            if hasattr(obj, "index0") and not attribute.startswith("_"):  # loop.index, loop.first, ...
                return super().getattr(obj, attribute)
            return self.undefined(obj=obj, name=attribute)

    env = _ItemOnly(
        undefined=StrictUndefined,
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters = {name: DEFAULT_FILTERS[name] for name in FALLBACK_FILTERS}
    env.globals = {"range": range}
    return env


def fallback_render(content: Mapping[str, Any], context: Mapping[str, Any]) -> str:
    """The section 9.2 engine contract: only declared variables, NFC/LF, exactly one trailing newline."""
    scoped = {name: context[name] for name in content.get("variables") or () if name in context}
    text = _fallback_env().from_string(content["template"]).render(scoped)
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n"))
    return text.rstrip("\n") + "\n"


def fallback_variables(template: str) -> set[str]:
    from jinja2 import meta

    return set(meta.find_undeclared_variables(_fallback_env().parse(template))) - {"range"}


# ------------------------------------------------------------------------------- render (WP-03 or fallback)


def load_template(ref: str) -> dict[str, Any]:
    """Template content for a ``slug@v[#hash]`` ref, read from the seed files (the render ``loader``)."""
    return dict(prompts()[slug_of(ref)].content)


def _wp03() -> tuple[Any, Any] | None:
    """``(build_app_context, render)`` from WP-03 when they are implemented, else ``None``."""
    try:
        from backend.generation.prompts import context as wp03_context
        from backend.generation.prompts import engine as wp03_engine

        wp03_engine.render(
            {"role": "fragment", "engine": "jinja2-sandbox@1", "template": "x", "variables": [], "modes": ["app"]},
            {},
        )
    except (ImportError, NotImplementedError):
        return None
    return wp03_context, wp03_engine


def _fallback_app_context(
    *,
    task_content: Mapping[str, Any],
    variant: str,
    stack: Mapping[str, Any],
    style: Mapping[str, Any],
    guidance: Mapping[str, Any],
    prompt_set: Mapping[str, Any],
    auth_mode: str,
    network_policy: str,
    starter_level: str,
    allowed_bases: list[str],
) -> dict[str, Any]:
    cwes = (
        []
        if guidance.get("security_hint") == "none"
        else (
            list(guidance.get("cwes") or [])
            if guidance.get("cwe_source") == "explicit"
            else list(task_content["surfaces"])
        )
    )
    base: dict[str, Any] = {
        "task": {
            "title": task_content["title"],
            "summary": task_content["summary"],
            "prose": task_content["prose"][variant],
            "entities": task_content["entities"],
            "endpoints": task_content["endpoints"],
            "ui": task_content.get("ui", []),
            "features": task_content["features"],
            "has_frontend": task_content.get("has_frontend", True),
            "auth_flow": task_content.get("auth_flow"),
        },
        "stack": {key: stack[key] for key in ("backend", "frontend", "database", "run_contract")},
        "starter": {"level": starter_level, "tree": []},
        "style": {key: style.get(key) for key in ("system", "library", "tone", "tone_text")}
        | {"accessibility": style.get("accessibility", "unspecified")},
        "guidance": {
            "security_hint": guidance["security_hint"],
            "cwes": cwes,
            "prescriptions": guidance.get("library_prescriptions", []),
        },
        "harness": {"name": "opencode", "agentic": True},
        "network": {"policy": network_policy, "sentence": ""},
        "allowed_bases": allowed_bases,
    }

    def fragment(ref: str | None) -> str:
        return fallback_render(load_template(ref), base).removesuffix("\n") if ref else ""

    context = dict(base)
    context["stack"] = base["stack"] | {"fragment_text": fragment(stack["brief_fragment"])}
    context["style"] = base["style"] | {"fragment_text": fragment(style["fragment"])}
    context["guidance"] = base["guidance"] | {"fragment_text": fragment(guidance.get("fragment"))}
    fragments = prompt_set.get("fragments") or {}
    context["auth_contract"] = fragment(fragments.get(f"auth_contract.{auth_mode}"))
    context["network"] = base["network"] | {"sentence": fragment(fragments.get(f"network.{network_policy}"))}
    context["budget_neutral"] = fragment(fragments.get("budget_neutral"))
    return context


def render_app_brief(
    *,
    task_slug: str,
    variant: str,
    stack_slug: str,
    style_slug: str,
    guidance_slug: str = "none",
    prompt_set_slug: str = "app-default",
    auth_mode: str = "contract_bearer",
    network_policy: str = "packages",
    starter_level: str = "minimal",
    allowed_bases: list[str] | None = None,
) -> str:
    """The rendered brief of one cell, through WP-03's context and engine when they exist."""
    from backend.generation.contracts.library import normalize_content

    task_content = normalize_content("task", task(task_slug))  # stored content is normalized (defaults filled)
    stack = stacks()[stack_slug]
    style = entries("style")[style_slug].content
    guidance = entries("guidance")[guidance_slug].content
    prompt_set = entries("prompt_set")[prompt_set_slug].content
    brief = load_template(prompt_set["roles"]["brief"])
    bases = (
        allowed_bases
        if allowed_bases is not None
        else (["python:3.13-slim@sha256:" + "a" * 64] if stack["choice_mode"] == "free" else [])
    )
    wp03 = _wp03()
    if wp03 is None:
        context = _fallback_app_context(
            task_content=task_content,
            variant=variant,
            stack=stack,
            style=style,
            guidance=guidance,
            prompt_set=prompt_set,
            auth_mode=auth_mode,
            network_policy=network_policy,
            starter_level=starter_level,
            allowed_bases=bases,
        )
        return fallback_render(brief, context)
    wp03_context, wp03_engine = wp03
    level = wp03_context.Level
    context = wp03_context.build_app_context(
        task=level("task", f"{task_slug}@1", task_content),
        variant=variant,
        stack=level("stack", f"{stack_slug}@1", stack),
        style=level("style", f"{style_slug}@1", style),
        guidance=level("guidance", f"{guidance_slug}@1", guidance),
        prompt_set=level("prompt_set", f"{prompt_set_slug}@1", prompt_set),
        auth_mode=auth_mode,
        starter_level=starter_level,
        harness="opencode",
        network_policy=network_policy,
        allowed_bases=bases,
        loader=load_template,
    )
    return wp03_engine.render(brief, context, template_ref=prompt_set["roles"]["brief"]).text


def render_slot(template_slug: str, slot: Mapping[str, Any], *, stack_slug: str, guidance_slug: str = "none") -> str:
    content = load_template(f"{template_slug}@1")
    stack = stacks()[stack_slug]
    guidance = entries("guidance")[guidance_slug].content
    wp03 = _wp03()
    if wp03 is None:
        context = {
            "slot": dict(slot),
            "stack": {key: stack[key] for key in ("backend", "frontend", "database", "run_contract")}
            | {"fragment_text": fallback_render(load_template(stack["brief_fragment"]), {}).removesuffix("\n")},
            "guidance": {
                "security_hint": guidance["security_hint"],
                "cwes": [],
                "prescriptions": [],
                "fragment_text": "",
            },
            "context": "\n\n".join(f"### `{f['path']}`\n```\n{f['text']}\n```" for f in slot.get("context_files", [])),
        }
        return fallback_render(content, context)
    wp03_context, wp03_engine = wp03
    level = wp03_context.Level
    context = wp03_context.build_slot_context(
        slot=slot,
        stack=level("stack", f"{stack_slug}@1", stack),
        guidance=level("guidance", f"{guidance_slug}@1", guidance),
        network_policy="none",
        loader=load_template,
    )
    return wp03_engine.render(content, context, template_ref=f"{template_slug}@1").text


# ---------------------------------------------------------------------------------------------- stacks

# Phase-A profiles (section 11.4) as a stand-in while WP-05's ``data/stacks`` is absent.
_FALLBACK_BACKENDS: Final = {
    "flask": ("python", "python3.13", "Flask (Python 3.13)"),
    "fastapi": ("python", "python3.13", "FastAPI (Python 3.13)"),
    "django": ("python", "python3.13", "Django 5 (Python 3.13)"),
    "express": ("typescript", "node24", "Express (Node 24, TypeScript)"),
}


@functools.cache
def stacks() -> dict[str, dict[str, Any]]:
    """WP-05's stack profiles when present, else synthetic phase-A profiles with inline fragments."""
    found = {slug: entry.content for slug, entry in entries("stack").items()}
    if found and all(slug_of(c["brief_fragment"]) in prompts() for c in found.values()):
        return found
    synthetic: dict[str, dict[str, Any]] = {}
    for backend, (language, runtime, label) in _FALLBACK_BACKENDS.items():
        for frontend in ("react", "vue", "svelte", "none"):
            slug = f"{backend}-{frontend}"
            synthetic[slug] = {
                "choice_mode": "fixed",
                "backend": {"language": language, "framework": backend, "runtime": runtime},
                "frontend": {
                    "framework": frontend,
                    "language": "none" if frontend == "none" else "ts",
                    "build_tool": "none" if frontend == "none" else "vite",
                },
                "database": "sqlite",
                "run_contract": {
                    "port": 8000,
                    "api_prefix": "/api",
                    "health_path": "/api/health",
                    "static_serving": "backend",
                    "data_dir": "./data",
                    "start": "dockerfile",
                },
                "brief_fragment": f"stack-{slug}@1",
            }
            prompts()[f"stack-{slug}"] = Entry(
                "prompt",
                f"stack-{slug}",
                slug,
                "",
                {
                    "role": "fragment",
                    "engine": "jinja2-sandbox@1",
                    "template": f"Build the API with {label}; the frontend is {frontend}.",
                    "variables": [],
                    "modes": ["app", "slot"],
                    "tags": [],
                },
            )
    return synthetic
