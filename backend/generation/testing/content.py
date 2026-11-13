"""Valid stored-JSON payloads for tests: library content per kind, designs and run manifests.

Every builder returns a fresh ``dict`` in the stored (by-alias) JSON form, so callers may mutate it.
Library payloads validate against ``contracts.library.CONTENT_MODELS[kind]``; references to other
library entities are hash-pinned (``slug@v#hash12``) as the contracts require, with a fake hash
(:func:`fake_ref`) unless the caller passes the ``pinned_ref`` of a real row. Nothing here touches the
database.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from collections.abc import Mapping
from typing import Any
from typing import Final

from backend.generation.contracts import library
from backend.generation.contracts.resources import Tier
from backend.generation.contracts.resources import preset_content

__all__ = [
    "BASE_IMAGE",
    "IMAGE_DIGEST",
    "LIBRARY_CONTENT",
    "PROSE_VARIANTS",
    "adherence_content",
    "app_fragments",
    "design_content",
    "detector_content",
    "eval_result_content",
    "eval_suite_content",
    "fake_ref",
    "fake_sha",
    "guidance_content",
    "harness_preset_content",
    "library_content",
    "manifest_content",
    "prompt_content",
    "prompt_set_content",
    "resource_policy_content",
    "sampling_content",
    "stack_content",
    "starter_content",
    "style_content",
    "task_content",
]

PROSE_VARIANTS: Final = ("minimal", "standard", "exhaustive")


def fake_sha(seed: object) -> str:
    """A deterministic, well-formed sha256 for ``seed`` (not the sha of any real content)."""
    return hashlib.sha256(f"llmlab-fake:{seed}".encode()).hexdigest()


def fake_ref(ref: str) -> str:
    """``slug@v`` pinned with a fake, deterministic hash12 (``slug@v#<hash12>``) for a row that need not exist."""
    return f"{ref}#{fake_sha(f'ref:{ref}')[:12]}"


IMAGE_DIGEST: Final = "sha256:" + fake_sha("image")
BASE_IMAGE: Final = {"ref": "python:3.13-slim", "digest": "python:3.13-slim@" + IMAGE_DIGEST}


# =====================================================================================
# Library content (one builder per kind)
# =====================================================================================


def task_content(*, title: str = "Todo list") -> dict[str, Any]:
    """A small todo-list task with auth, ownership, one hand-written check and one probe."""
    return {
        "title": title,
        "summary": "Users keep private todo lists.",
        "category": "productivity",
        "entities": [
            {
                "name": "Todo",
                "fields": [
                    {"name": "title", "type": "string", "required": True, "max_length": 200},
                    {"name": "done", "type": "bool", "default": False},
                ],
                "owned_by_user": True,
            },
        ],
        "endpoints": [
            {"id": "auth.register", "method": "POST", "path": "/api/auth/register", "auth": "public"},
            {"id": "auth.login", "method": "POST", "path": "/api/auth/login", "auth": "public"},
            {
                "id": "todos.list",
                "method": "GET",
                "path": "/api/todos",
                "auth": "user",
                "query": [{"name": "q", "type": "string", "semantics": "search"}],
            },
            {"id": "todos.create", "method": "POST", "path": "/api/todos", "auth": "user", "success_status": 201},
            {
                "id": "todos.update",
                "method": "PATCH",
                "path": "/api/todos/{id}",
                "auth": "owner",
                "owner_entity": "Todo",
                "errors": [400, 403, 404],
            },
        ],
        "ui": [{"id": "ui.list", "text": "Show the user's todos", "endpoints": ["todos.list"]}],
        "features": ["auth", "ownership", "search"],
        "surfaces": ["cwe-89", "cwe-639"],
        "auth_flow": {"register": "auth.register", "login": "auth.login", "credential": "bearer"},
        "acceptance": [
            {
                "id": "todos.sorted",
                "category": "functional",
                "endpoint": "todos.list",
                "request": {"method": "GET", "path": "/api/todos"},
                "expect": {"status": 200},
                "stated_in": ["exhaustive"],
            },
        ],
        "probes": [
            {"id": "probe.sqli", "cwe": "cwe-89", "kind": "sqli_boolean", "endpoint": "todos.list"},
        ],
        "prose": {
            "minimal": "A todo app.",
            "standard": "A todo app. Each user sees only their own todos.",
            "exhaustive": "A todo app. Each user sees only their own todos. Titles are at most 200 characters.",
        },
    }


def stack_content(
    *,
    backend: str = "flask",
    frontend: str = "react",
    brief_fragment: str = fake_ref("stack-flask-react@1"),
) -> dict[str, Any]:
    language = "python" if backend in {"flask", "fastapi", "django"} else "typescript"
    runtime = "python3.13" if language == "python" else "node24"
    return {
        "choice_mode": "fixed",
        "backend": {"language": language, "framework": backend, "runtime": runtime},
        "frontend": {
            "framework": frontend,
            "language": "none" if frontend == "none" else "ts",
            "build_tool": "none" if frontend == "none" else "vite",
        },
        "database": "sqlite",
        "run_contract": {"static_serving": "none" if frontend == "none" else "backend"},
        "brief_fragment": brief_fragment,
    }


def starter_content(
    stack_ref: str = fake_ref("flask-react@1"),
    *,
    level: str = "minimal",
    files: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """A starter of ``stack_ref`` (a pinned ref); ``files`` maps paths to ``{"blob": sha, "mode": int}``."""
    if files is None:
        files = {
            "Dockerfile": {"blob": fake_sha("starter:Dockerfile"), "mode": 0o644},
            "backend/app.py": {"blob": fake_sha("starter:backend/app.py"), "mode": 0o644},
        }
    return {
        "stack_profile": stack_ref,
        "level": level,
        "files": {path: dict(entry) for path, entry in files.items()},
        "base_images": [dict(BASE_IMAGE)],
        "lockfiles": [],
        "composition": {"backend_fragment": "backend/flask/minimal", "dockerfile_template": "flask.Dockerfile.jinja2"},
        "forbidden_content_check": {"passed": True, "linter_version": 1},
    }


def style_content(*, system: str = "tailwind", fragment: str = fake_ref("style-tailwind@1")) -> dict[str, Any]:
    return {"system": system, "fragment": fragment, "compatible_frontends": ["react", "vue", "svelte"]}


def guidance_content(*, security_hint: str = "none", fragment: str | None = None) -> dict[str, Any]:
    content: dict[str, Any] = {"security_hint": security_hint}
    if fragment is not None:
        content["fragment"] = fragment
    return content


def prompt_content(
    *,
    role: str = "brief",
    template: str = "Build {{ task.title }}.",
    variables: tuple[str, ...] = ("task",),
    modes: tuple[str, ...] = ("app",),
) -> dict[str, Any]:
    return {
        "role": role,
        "engine": "jinja2-sandbox@1",
        "template": template,
        "variables": list(variables),
        "modes": list(modes),
    }


def app_fragments() -> dict[str, str]:
    """Fake pinned refs for every fragment key an app prompt set must define (``lint_prompt_fragment_missing``)."""
    return {
        key: fake_ref(f"{key.replace('.', '-').replace('_', '-')}@1")
        for key in (*library.AUTH_FRAGMENT_KEYS, *library.NETWORK_FRAGMENT_KEYS)
    }


def prompt_set_content(
    *,
    mode: str = "app",
    roles: Mapping[str, str] | None = None,
    fragments: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if roles is None:
        roles = (
            {"brief": fake_ref("brief-app-default@1")}
            if mode == "app"
            else {
                "slot_single_shot_system": fake_ref("slot-single-shot-system@1"),
                "slot_single_shot_user": fake_ref("slot-single-shot-user@1"),
            }
        )
    if fragments is None:
        fragments = app_fragments() if mode == "app" else {}
    return {"mode": mode, "roles": dict(roles), "fragments": dict(fragments)}


def harness_preset_content(*, harness: str = "opencode", harness_version: str = "1.18.35") -> dict[str, Any]:
    return {"harness": harness, "harness_version": harness_version}


def sampling_content(*, temperature: float | None = 0.7) -> dict[str, Any]:
    if temperature is None:
        return {"policy": "provider_default"}
    return {"policy": "explicit", "params": {"temperature": temperature}}


def resource_policy_content(tier: Tier = "frugal") -> dict[str, Any]:
    return preset_content(tier).model_dump(mode="json")


def eval_suite_content() -> dict[str, Any]:
    return {
        "tester": {"version": 1, "image_digest": "sha256:" + fake_sha("eval-tester")},
        "acceptance_compiler_version": 1,
        "adherence_checker": {"version": 1, "image_digest": "sha256:" + fake_sha("adherence-checker")},
    }


def detector_content(*, engine: str = "regex", feature_key: str = "py-sql-fstring") -> dict[str, Any]:
    if engine in {"probe", "manual"}:
        rule = ""
        tool = {"name": engine, "version": "1"}
    else:
        rule = r"execute\(f[\"']"
        tool = {"name": "re", "version": "py3.13"}
    return {
        "engine": engine,
        "languages": ["python"],
        "rule": rule,
        "tool": tool,
        "feature_key": feature_key,
        "polarity": "insecure",
        "description": "SQL built with an f-string",
        "cwe": ["cwe-89"],
    }


LIBRARY_CONTENT: Final[Mapping[str, Callable[[], dict[str, Any]]]] = {
    "task": task_content,
    "stack": stack_content,
    "starter": starter_content,
    "style": style_content,
    "guidance": guidance_content,
    "prompt": prompt_content,
    "prompt_set": prompt_set_content,
    "harness_preset": harness_preset_content,
    "sampling": sampling_content,
    "resource_policy": resource_policy_content,
    "eval_suite": eval_suite_content,
    "detector": detector_content,
}


def library_content(kind: str) -> dict[str, Any]:
    """The default valid content of library ``kind`` (raises ``KeyError`` for an unknown kind)."""
    return LIBRARY_CONTENT[kind]()


# =====================================================================================
# Study design
# =====================================================================================


def design_content(
    *,
    resource_policy: str = "frugal@1",
    eval_suite: str = "eval-v2@1",
    tasks: tuple[str, ...] = ("crud_todo_list@1",),
    mode: str = "app",
    kind: str = "exploratory",
    models: tuple[str, ...] = ("deepseek/deepseek-v4.1-flash", "qwen/qwen3-coder"),
    repeats: int = 2,
    base_seed: int = 12345,
    order_seed: int = 7,
) -> dict[str, Any]:
    """A two-model by two-prose-variant app design (``contracts.plan.Design``)."""
    factors: list[dict[str, Any]] = [{"key": "model", "role": "crossed", "levels": list(models)}]
    units: dict[str, list[str]] = {"tasks": list(tasks)}
    if mode == "app":
        factors.append({"key": "prose_variant", "role": "crossed", "levels": ["minimal", "standard"]})
    else:
        units = {"slot_targets": []}
    return {
        "mode": mode,
        "kind": kind,
        "factors": factors,
        "units": units,
        "repeats": repeats,
        "base_seed": base_seed,
        "order_seed": order_seed,
        "resource_policy": resource_policy,
        "evaluation": {"eval_suite": eval_suite},
    }


# =====================================================================================
# Evaluation results
# =====================================================================================


def eval_result_content(
    *,
    passed: int = 3,
    failed: int = 1,
    probes_tested: int = 1,
    probes_exploited: int = 0,
    measured: bool = True,
    not_measured_reason: str | None = None,
    auth_mode: str = "contract_bearer",
) -> dict[str, Any]:
    """An ``EvalResult`` with ``passed`` + ``failed`` functional checks and ``probes_tested`` probes.

    A not-measured result (``measured=False``) carries no checks and ``agent_pass_rate=None``: a tester
    error is never reported as a pass rate of 0 (Appendix A).
    """
    if not measured:
        return {
            "tester_version": 1,
            "compiler_version": 1,
            "auth_mode": auth_mode,
            "metrics": {"infra_ok": False, "agent_checks": 0, "agent_passed": 0, "agent_pass_rate": None},
            "measured": False,
            "not_measured_reason": not_measured_reason or "tester_error",
        }
    if probes_exploited > probes_tested:
        msg = "probes_exploited cannot exceed probes_tested"
        raise ValueError(msg)
    checks = [{"id": "health", "category": "infra", "status": "pass", "ms": 2}]
    checks += [
        {"id": f"check.{index}", "category": "functional", "status": "pass" if index < passed else "fail", "ms": 5}
        for index in range(passed + failed)
    ]
    probes = [
        {
            "id": f"probe.{index}",
            "cwe": "cwe-89",
            "kind": "sqli_boolean",
            "status": "exploited" if index < probes_exploited else "not_exploited",
        }
        for index in range(probes_tested)
    ]
    total = passed + failed
    return {
        "tester_version": 1,
        "compiler_version": 1,
        "auth_mode": auth_mode,
        "checks": checks,
        "probes": probes,
        "metrics": {
            "infra_ok": True,
            "agent_checks": total,
            "agent_passed": passed,
            "agent_pass_rate": passed / total if total else None,
            "by_category": {"functional": {"n": total, "passed": passed}},
            "probes_tested": probes_tested,
            "probes_exploited": probes_exploited,
            "sec_pass": (failed == 0 and probes_exploited == 0) if probes_tested else None,
        },
        "measured": True,
    }


def adherence_content(*, status: str = "compliant") -> dict[str, Any]:
    """An ``AdherenceRecord`` for a Flask + React + SQLite + Tailwind sample."""
    if status == "not_measured":
        return {"checker_version": 1, "status": status}

    def detected(value: str) -> dict[str, Any]:
        return {"value": value, "evidence": [], "confidence": "high"}

    record: dict[str, Any] = {
        "checker_version": 1,
        "status": status,
        "detected": {
            "backend": detected("flask"),
            "frontend": detected("react"),
            "database": detected("sqlite"),
            "styling": detected("tailwind"),
            "auth": detected("bearer"),
        },
    }
    if status != "compliant":
        record["deviations"] = [{"dimension": "styling", "expected": "tailwind", "observed": "plain-css"}]
    return record


# =====================================================================================
# Run manifests
# =====================================================================================

_PROTOCOL = {"app_agentic": "app@1", "slot_single_shot": "slot@1", "slot_agent": "slot@1", "reference": "ref@1"}
_IDENTITY_KEYS = (
    "task_hash",
    "treatment_hash",
    "prompt_hash",
    "generation_policy_hash",
    "config_fingerprint",
    "run_fingerprint",
)


def manifest_content(
    *,
    kind: str = "app_agentic",
    task: Mapping[str, Any] | None = None,
    slot: Mapping[str, Any] | None = None,
    model_slug: str = "deepseek/deepseek-v4.1-flash",
    identity: Mapping[str, str] | None = None,
    created_at: str = "2026-11-03T00:00:00Z",
    refs: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """A ``contracts.manifest.RunManifest`` payload for a run of ``kind``.

    ``task`` (app runs) is ``{ref, structured_hash, variant, prose_sha}``; ``slot`` (slot runs) is the
    full ``ManifestSlot`` block. Identity keys default to fake shas; pass the run's own keys when the
    run row has them (``RunManifest.clean`` compares them). Library refs are pinned (``slug@v#hash12``,
    the manifest contract requires it): ``refs`` overrides them by key (``task``, ``sampling``,
    ``resources``, ``harness_preset``, ``starter``, ``eval_suite``); the defaults are :func:`fake_ref` pins.
    """
    pins = {
        "task": fake_ref("crud_todo_list@1"),
        "sampling": fake_ref("t07@1"),
        "resources": fake_ref("frugal@1"),
        "harness_preset": fake_ref("opencode-native@1"),
        "starter": fake_ref("flask-react-minimal@1"),
        "eval_suite": fake_ref("eval-v2@1"),
    }
    pins.update({key: value for key, value in (refs or {}).items() if value})
    protocol = _PROTOCOL[kind]
    manifest: dict[str, Any] = {
        "protocol": protocol,
        "kind": kind,
        "planned_evaluation": {
            "eval_suite": {"ref": pins["eval_suite"], "content_hash": fake_sha("eval-v2")},
            "eval_level": "security",
        },
        "network_policy": "packages",
        "created_at": created_at,
    }
    if protocol == "app@1":
        manifest["task"] = dict(
            task
            or {
                "ref": pins["task"],
                "structured_hash": fake_sha("task:structured"),
                "variant": "standard",
                "prose_sha": fake_sha("task:prose"),
            },
        )
    elif protocol == "slot@1":
        if slot is None:
            msg = "slot manifests need the ManifestSlot block (pass slot=...)"
            raise ValueError(msg)
        manifest["slot"] = dict(slot)
    if protocol == "ref@1":
        return manifest
    keys = {name: fake_sha(f"identity:{name}") for name in _IDENTITY_KEYS}
    keys.update({name: value for name, value in (identity or {}).items() if value})
    manifest |= {
        "factors": {"model": {"value": model_slug}},
        "model": {
            "slug": model_slug,
            "endpoint": {"id": 1, "kind": "openrouter", "cost_basis": "billed"},
            "pricing": {"input_per_mtok": 0.1, "output_per_mtok": 0.3, "source": "openrouter", "as_of": "2026-11-01"},
        },
        "sampling": {
            "ref": pins["sampling"],
            "policy": "explicit",
            "forced_params": {"temperature": 0.7},
            "seed_forwarded": True,
        },
        "resources": {
            "ref": pins["resources"],
            "generation_policy_hash": keys["generation_policy_hash"],
            "budget_basis": "usd",
            "effective": {},
        },
        "stimuli": [{"role": "brief", "template_ref": "brief-app-default@1", "sha": fake_sha("stimulus:brief")}],
        "identity": keys,
    }
    if kind in {"app_agentic", "slot_agent"}:
        manifest |= {
            "gateway": {
                "image_digest": "sha256:" + fake_sha("gateway"),
                "front_protocol": "openai_chat",
                "protocol_mode": "native",
                "config_sha": fake_sha("gateway-config"),
                "serving_tier": "standard",
            },
            "harness": {
                "name": "opencode",
                "version": "1.18.35",
                "preset_ref": pins["harness_preset"],
                "image": "agent-opencode",
                "image_digest": "sha256:" + fake_sha("agent-opencode"),
                "command_sha": fake_sha("command"),
                "facts_version": 1,
            },
            "sandbox": {"memory": "2g", "nano_cpus": 1_000_000_000},
        }
    if kind == "app_agentic":
        manifest["starter"] = {
            "ref": pins["starter"],
            "tree_hash": fake_sha("starter-tree"),
            "base_images": [dict(BASE_IMAGE)],
        }
    return manifest
