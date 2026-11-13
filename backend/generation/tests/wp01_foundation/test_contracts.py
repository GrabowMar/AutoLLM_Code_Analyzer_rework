"""Contract round trips, extra-key rejection, aliases and the checked-in task schema (no database)."""

from __future__ import annotations

import copy
import dataclasses
import json

import pytest
from pydantic import BaseModel
from pydantic import ValidationError

from backend.generation import identity
from backend.generation.contracts import ErrorOut
from backend.generation.contracts import Issue
from backend.generation.contracts import Page
from backend.generation.contracts import Ref
from backend.generation.contracts import evaluation
from backend.generation.contracts import events
from backend.generation.contracts import harness
from backend.generation.contracts import library
from backend.generation.contracts import manifest
from backend.generation.contracts import outcome
from backend.generation.contracts import plan
from backend.generation.contracts import resources
from backend.generation.contracts import slot

SHA = "a" * 64
SHA2 = "b" * 64
DIGEST = "python@sha256:" + "c" * 64
PIN = "x@1#aaaaaaaaaaaa"


# =====================================================================================
# Fixture payloads (stored-JSON form)
# =====================================================================================


def task_content() -> dict:
    return {
        "title": "Todo list",
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
            {
                "id": "todos.list",
                "method": "GET",
                "path": "/api/todos",
                "auth": "user",
                "query": [{"name": "q", "type": "string", "semantics": "search"}],
            },
            {
                "id": "todos.update",
                "method": "PATCH",
                "path": "/api/todos/{id}",
                "auth": "owner",
                "owner_entity": "Todo",
                "errors": [400, 403, 404],
            },
            {"id": "auth.register", "method": "POST", "path": "/api/auth/register", "auth": "public"},
            {"id": "auth.login", "method": "POST", "path": "/api/auth/login", "auth": "public"},
        ],
        "ui": [{"id": "ui.list", "text": "Show the todos", "endpoints": ["todos.list"]}],
        "features": ["auth", "ownership", "search"],
        "surfaces": ["cwe-89", "cwe-639"],
        "auth_flow": {"register": "auth.register", "login": "auth.login", "credential": "bearer"},
        "acceptance": [
            {
                "id": "todos.sorted",
                "category": "functional",
                "endpoint": "todos.list",
                "setup": [
                    {
                        "request": {"method": "POST", "path": "/api/todos", "json": {"title": "b"}},
                        "expect_status": 201,
                        "capture": {"id": "$.id"},
                    },
                ],
                "request": {"method": "GET", "path": "/api/todos", "query": {"sort": "title"}},
                "expect": {"status": 200, "json_contains": {"items": []}},
                "stated_in": ["exhaustive"],
            },
        ],
        "probes": [
            {
                "id": "probe.sqli",
                "cwe": "cwe-89",
                "kind": "sqli_boolean",
                "endpoint": "todos.list",
                "positive_control": {"method": "GET", "path": "/api/todos", "query": {"q": "a"}, "expect_status": 200},
            },
        ],
        "prose": {"minimal": "A todo app.", "standard": "A todo app\r\nwith users.", "exhaustive": "Long."},
    }


def stack_content() -> dict:
    return {
        "choice_mode": "fixed",
        "backend": {"language": "python", "framework": "flask", "runtime": "python3.13"},
        "frontend": {"framework": "react", "language": "ts", "build_tool": "vite"},
        "database": "sqlite",
        "run_contract": {"static_serving": "backend"},
        "brief_fragment": "stack-flask-react@1#0123456789ab",
    }


def starter_content() -> dict:
    return {
        "stack_profile": PIN,
        "level": "minimal",
        "files": {"Dockerfile": {"blob": SHA, "mode": 0o644}, "app/main.py": {"blob": SHA2, "mode": 0o644}},
        "base_images": [{"ref": "python:3.13-slim", "digest": DIGEST}],
        "lockfiles": ["requirements.lock"],
        "composition": {"backend_fragment": "backend/flask/minimal", "dockerfile_template": "flask.Dockerfile.jinja2"},
        "forbidden_content_check": {"passed": True, "linter_version": 1},
    }


LIBRARY_SAMPLES: dict[str, dict] = {
    "task": task_content(),
    "stack": stack_content(),
    "starter": starter_content(),
    "style": {
        "system": "tailwind",
        "fragment": "style-tailwind@1#0123456789ab",
        "compatible_frontends": ["react", "vue"],
    },
    "guidance": {"security_hint": "none"},
    "prompt": {"role": "brief", "engine": "jinja2-sandbox@1", "template": "{{ task.title }}", "modes": ["app"]},
    "prompt_set": {"mode": "app", "roles": {"brief": "brief-app-default@1#0123456789ab"}},
    "harness_preset": {"harness": "opencode", "harness_version": "1.18.35"},
    "sampling": {"policy": "explicit", "params": {"temperature": 0.7}},
    "resource_policy": resources.preset_content("frugal").model_dump(mode="json"),
    "eval_suite": {
        "tester": {"version": 1, "image_digest": "sha256:" + "d" * 64},
        "acceptance_compiler_version": 1,
        "adherence_checker": {"version": 1, "image_digest": "sha256:" + "e" * 64},
    },
    "detector": {
        "engine": "regex",
        "languages": ["python"],
        "rule": r"execute\(f\"",
        "tool": {"name": "re", "version": "py3.13"},
        "feature_key": "py-sql-fstring",
        "polarity": "insecure",
        "description": "SQL built with an f-string",
        "cwe": ["cwe-89"],
    },
}


def manifest_payload() -> dict:
    return {
        "protocol": "app@1",
        "kind": "app_agentic",
        "study": {
            "id": "7b2d",
            "plan_hash": SHA,
            "condition_hash": SHA2,
            "cell": [0, 1, 2],
            "block_index": 3,
            "stage": "main",
        },
        "factors": {
            "model": {"value": "deepseek/deepseek-v4.1-flash"},
            "stack_profile": {"ref": PIN, "content_hash": SHA},
        },
        "task": {
            "ref": "crud_todo_list@1#" + "c" * 12,
            "structured_hash": SHA,
            "variant": "standard",
            "prose_sha": SHA2,
        },
        "model": {
            "slug": "deepseek/deepseek-v4.1-flash",
            "endpoint": {"id": 1, "kind": "openrouter", "cost_basis": "billed"},
            "pricing": {"input_per_mtok": 0.1, "output_per_mtok": 0.3, "source": "openrouter", "as_of": "2026-11-01"},
        },
        "sampling": {
            "ref": "t07@1#" + "d" * 12,
            "policy": "explicit",
            "forced_params": {"temperature": 0.7},
            "seed_forwarded": True,
        },
        "resources": {
            "ref": "frugal@1#" + "e" * 12,
            "generation_policy_hash": SHA,
            "budget_basis": "usd",
            "effective": {},
        },
        "gateway": {
            "image_digest": "sha256:" + "1" * 64,
            "front_protocol": "openai_chat",
            "protocol_mode": "native",
            "config_sha": SHA,
            "serving_tier": "standard",
        },
        "harness": {
            "name": "opencode",
            "version": "1.18.35",
            "preset_ref": "opencode-native@1#" + "f" * 12,
            "image": "agent-opencode",
            "image_digest": "sha256:" + "2" * 64,
            "command_sha": SHA,
            "facts_version": 1,
        },
        "starter": {"ref": PIN, "tree_hash": SHA, "base_images": [{"ref": "python:3.13-slim", "digest": DIGEST}]},
        "stimuli": [{"role": "brief", "template_ref": "brief-app-default@1", "sha": SHA}],
        "identity": dict.fromkeys(
            [
                "task_hash",
                "treatment_hash",
                "prompt_hash",
                "generation_policy_hash",
                "config_fingerprint",
                "run_fingerprint",
            ],
            SHA,
        ),
        "planned_evaluation": {
            "eval_suite": {"ref": "eval-v2@1#" + "0" * 12, "content_hash": SHA},
            "eval_level": "security",
        },
        "network_policy": "packages",
        "sandbox": {"memory": "2g", "nano_cpus": 1_000_000_000},
        "created_at": "2026-11-03T00:00:00Z",
    }


def design_payload() -> dict:
    return {
        "mode": "app",
        "kind": "exploratory",
        "factors": [
            {"key": "model", "role": "crossed", "levels": ["deepseek/deepseek-v4.1-flash", "qwen/qwen3-coder"]},
            {"key": "prose_variant", "role": "crossed", "levels": ["minimal", "standard"]},
        ],
        "units": {"tasks": ["crud_todo_list@1"]},
        "repeats": 2,
        "base_seed": 12345,
        "order_seed": 7,
        "resource_policy": "frugal@1",
        "evaluation": {"eval_suite": "eval-v2@1#aaaaaaaaaaaa"},
    }


def eval_result_payload() -> dict:
    return {
        "tester_version": 1,
        "compiler_version": 1,
        "auth_mode": "contract_bearer",
        "checks": [
            {"id": "health", "category": "infra", "status": "pass", "ms": 3},
            {"id": "todos.list.ok", "category": "functional", "status": "skipped", "skipped_due_to": ["login"]},
        ],
        "probes": [
            {
                "id": "probe.sqli",
                "cwe": "cwe-89",
                "kind": "sqli_boolean",
                "status": "skipped",
                "skip_reason": "precondition_failed",
            },
        ],
        "metrics": {"infra_ok": True, "agent_checks": 1, "agent_passed": 0, "agent_pass_rate": 0.0},
        "measured": True,
    }


def adherence_payload() -> dict:
    detected = {"value": "flask", "evidence": ["app.py:1"], "confidence": "high"}
    return {
        "checker_version": 1,
        "status": "partial",
        "detected": dict.fromkeys(["backend", "frontend", "database", "styling", "auth"], detected),
        "deviations": [{"dimension": "styling", "expected": "tailwind", "observed": "plain-css"}],
        "packages": [
            {"ecosystem": "pypi", "name": "flask", "version": "3.1.0", "pinned": True, "declared_in": "r.txt"},
        ],
    }


def plan_payload() -> dict:
    cell = {
        "cell_key": plan.cell_key(0, 0, 0, "main"),
        "condition_index": 0,
        "unit_index": 0,
        "repeat_index": 0,
        "block_index": 0,
        "sequence": 0,
        "stage": "main",
        "seed": 99,
        "unit_key": "task:crud_todo_list@1",
        "levels": {"model": "deepseek/deepseek-v4.1-flash"},
        "manifest": {k: v for k, v in manifest_payload().items() if k not in {"study", "created_at"}},
        "stimuli": [
            {
                "role": "brief",
                "template_ref": "brief-app-default@1",
                "sha": SHA,
                "blob": SHA,
                "chars": 10,
                "est_tokens": 3,
                "sources": [
                    {
                        "template_ref": "brief-app-default@1",
                        "owner_kind": "prompt",
                        "owner_ref": "brief-app-default@1",
                        "char_start": 0,
                        "char_end": 10,
                    },
                ],
            },
        ],
        "estimate": {
            "expected_usd": 0.1,
            "expected_list_usd": 0.1,
            "p10_usd": 0.04,
            "p90_usd": 0.22,
            "cap_usd": 0.3,
            "expected_tokens": 1000,
            "expected_wall_s": 600,
            "p90_wall_s": 900,
            "basis": "heuristic",
        },
    }
    return {
        "resolver_version": 1,
        "design_hash": SHA,
        "plan_hash": SHA2,
        "conditions": [{"index": 0, "label": "c0", "condition_hash": SHA, "levels": {"model": "m"}}],
        "units": [{"index": 0, "unit_key": "task:crud_todo_list@1", "unit_order": 0}],
        "cells": [cell],
        "n_blocks": 1,
        "prompt_hashes": {cell["cell_key"]: SHA},
        "estimate": {"expected_total": 0.1, "p90_total": 0.22, "worst_case_total": 0.3},
        "issues": [{"code": "aliasing", "severity": "warn"}],
    }


def gateway_config_payload() -> dict:
    policy = resources.preset_content("frugal")
    return {
        "run_id": "r1",
        "attempt": 1,
        "session_id": "r1",
        "front_protocol": "openai_chat",
        "protocol_mode": "native",
        "upstream": {"kind": "openrouter", "base_url": "https://openrouter.ai/api/v1"},
        "model": {"upstream_model": "deepseek/deepseek-v4.1-flash", "presented_names": ["deepseek-v4.1-flash"]},
        "pricing": {"input_per_mtok": 0.1, "output_per_mtok": 0.3, "source": "openrouter", "as_of": "2026-11-01"},
        "reasoning": policy.reasoning.model_dump(mode="json"),
        "budget": {
            "max_cost_usd": 0.3,
            "cost_basis": "billed",
            "max_total_tokens": 3_000_000,
            "max_llm_requests": 40,
            "max_aux_requests": 10,
        },
        "call": {"max_output_tokens": 8192, "max_input_tokens": 72_000},
        "caching": {"prompt_cache": "on", "ttl": "5m", "sticky_session": True},
        "retries": {"upstream_retries": 2, "backoff_s": 2, "retry_on": ["5xx", "429"], "llm_call_timeout_s": 300},
    }


ROUND_TRIPS: list[tuple[type[BaseModel], dict]] = [
    *[(library.CONTENT_MODELS[kind], payload) for kind, payload in LIBRARY_SAMPLES.items()],
    (manifest.RunManifest, manifest_payload()),
    (plan.Design, design_payload()),
    (plan.ResolvedPlan, plan_payload()),
    (evaluation.EvalResult, eval_result_payload()),
    (evaluation.AdherenceRecord, adherence_payload()),
    (
        evaluation.ResolvedLock,
        {"ecosystems": {"pip": [{"name": "flask", "version": "3.1.0"}]}, "files": {"r.txt": SHA}, "resolved_at": "t"},
    ),
    (
        evaluation.AcceptancePlan,
        {
            "compiler_version": 1,
            "base_url": "http://app:8000",
            "auth": {"mode": "cookie_session", "credential": "cookie", "csrf": "double_submit_cookie"},
            "actors": ["anon", "user_a", "user_b"],
            "checks": [
                {
                    "id": "c1",
                    "category": "auth",
                    "actor": "anon",
                    "request": {"method": "GET", "path": "/api/todos"},
                    "expect": {"status": 401},
                },
            ],
            "probes": [{"id": "p1", "cwe": "cwe-352", "kind": "csrf_missing", "requires_checks": ["c1"]}],
        },
    ),
    (
        evaluation.EvalStages,
        {"lock": {"status": "ok", "seconds": 1.5}, "build": {"status": "fail", "detail": {"reason": "base"}}},
    ),
    (
        slot.SlotAddress,
        {
            "kind": "endpoint",
            "path": "app/routes.py",
            "span": {"start_line": 3, "end_line": 9, "start_byte": 40, "end_byte": 210},
            "symbol_id": "app/routes.py::update_todo",
            "endpoint": {"method": "PATCH", "path": "/api/todos/{id}", "spec_endpoint_id": "todos.update"},
            "content_sha": SHA,
            "anchors": {"before_sha": SHA2, "after_sha": ""},
        },
    ),
    (slot.ContextPolicy, {"mode": "full_app", "include_tests": True}),
    (
        slot.TargetValidation,
        {
            "status": "ok",
            "original_pass": {"c1": [True, True, True]},
            "holed_fail": {"c1": True},
            "discriminating_checks": ["c1"],
            "probe_stability": {"p1": ["not_exploited"] * 3},
        },
    ),
    (slot.LeakReport, {"score": 0.12, "matches": [{"path": "a.py", "shingles": 4}], "scope": ["context"]}),
    (
        slot.SymbolIndex,
        {
            "files": [{"path": "a.py", "language": "python", "loc": 10, "parsed": True}],
            "symbols": [
                {
                    "id": "a.py::f",
                    "path": "a.py",
                    "language": "python",
                    "kind": "function",
                    "qualname": "f",
                    "span": {"start_line": 1, "end_line": 2, "start_byte": 0, "end_byte": 20},
                    "content_sha": SHA,
                },
            ],
            "endpoints": [
                {"method": "GET", "path": "/x", "framework": "flask", "handler": "a.py::f", "confidence": "exact"},
            ],
            "components": [{"name": "TodoList", "path": "src/TodoList.tsx", "framework": "react", "whole_file": True}],
        },
    ),
    (slot.SlotFilesOutput, {"files": {"a.py": "print(1)\n"}}),
    (
        slot.ParentSelection,
        {"rule": "random", "pool_query": {"task": "crud_todo_list@1", "models": ["a", "b"]}, "k": 3, "seed": 5},
    ),
    (
        harness.Trajectory,
        {
            "steps": [{"index": 0, "kind": "shell", "ok": True, "tokens_in": 10, "command_sha": SHA}],
            "tool_calls": 1,
            "parser": "opencode",
            "parser_version": 2,
        },
    ),
    (harness.ObservedInputs, {"system_prompt_sha": SHA, "tool_names": ["bash"], "calls_by_class": {"main": 3}}),
    (
        outcome.Validity,
        {
            "termination": "budget_exhausted",
            "truncated": True,
            "notes": [{"code": "budget_exhausted:cost", "level": "warn"}, {"code": "call_truncations:2"}],
        },
    ),
    (outcome.RunResult, {"seed_tree_hash": SHA, "collection": {"files": 3, "by_origin": {"authored": 2}}}),
    (
        outcome.RunMetrics,
        {
            "cost_usd": 0.12,
            "requests_by_class": {"main": 12},
            "eval": {"eval_suite": "eval-v2@1", "eval_level": "security", "status": "measured", "measured": True},
        },
    ),
    (outcome.AttemptUsage, {"requests_by_class": {"main": 3, "auxiliary": 1}, "prompt_tokens": 100}),
    (resources.GatewayConfig, gateway_config_payload()),
    (
        resources.EffectivePolicy,
        {
            "content": resources.preset_content("standard").model_dump(mode="json"),
            "budget_basis": "usd",
            "cap_basis": "billed",
            "serving_tier": "standard",
            "clamps": [{"key": "call.max_output_tokens", "from": 16384, "to": 8192, "reason": "model max_output"}],
            "notes": ["reasoning_unsupported"],
        },
    ),
    (Issue, {"code": "estimate_exceeds_cap", "severity": "error", "fix": [{"label": "raise cap", "action": "x"}]}),
    (ErrorOut, {"detail": "stale", "code": "plan_stale"}),
]


def _ids(cases):
    return [model.__name__ for model, _ in cases]


@pytest.mark.parametrize(("model", "payload"), ROUND_TRIPS, ids=_ids(ROUND_TRIPS))
def test_round_trip(model, payload):
    obj = model.model_validate(payload)
    dumped = obj.model_dump(mode="json")
    again = model.model_validate(dumped)
    assert again == obj
    assert again.model_dump(mode="json") == dumped
    assert model.model_validate_json(obj.model_dump_json()) == obj
    # The stored (dumped) form is hash-stable.
    assert identity.H(dumped) == identity.H(model.model_validate(dumped).model_dump(mode="json"))


@pytest.mark.parametrize(("model", "payload"), ROUND_TRIPS, ids=_ids(ROUND_TRIPS))
def test_unknown_keys_rejected(model, payload):
    bad = copy.deepcopy(payload)
    bad["unexpected_key"] = 1
    with pytest.raises(ValidationError, match="unexpected_key"):
        model.model_validate(bad)


def test_every_contract_model_forbids_extra():
    modules = [library, manifest, plan, outcome, evaluation, slot, resources, harness, events]
    checked = 0
    for module in modules:
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, BaseModel)
                and value.__module__.startswith(
                    "backend.generation.contracts",
                )
            ):
                assert value.model_config.get("extra") == "forbid", value
                checked += 1
    assert checked > 80


# =====================================================================================
# Library specifics
# =====================================================================================


def test_library_registry_covers_every_kind():
    assert set(library.LIBRARY_KINDS) == {
        "task",
        "stack",
        "starter",
        "style",
        "guidance",
        "prompt",
        "prompt_set",
        "harness_preset",
        "sampling",
        "resource_policy",
        "eval_suite",
        "detector",
    }
    assert library.SCHEMA_VERSIONS["task"] == 4
    assert set(LIBRARY_SAMPLES) == set(library.LIBRARY_KINDS)


def test_normalize_content_fills_defaults_and_is_idempotent():
    normalized = library.normalize_content("harness_preset", LIBRARY_SAMPLES["harness_preset"])
    assert normalized["tool_policy"] == {"deny": [], "allow": None}
    assert normalized["subagents"] is False
    assert library.normalize_content("harness_preset", normalized) == normalized


def test_unknown_kind():
    with pytest.raises(KeyError, match="unknown library kind"):
        library.validate_content("no_such_kind", {})


def test_aliases_keep_spec_key_names():
    task = library.TaskSpecContent.model_validate(task_content())
    dumped = task.model_dump(mode="json")
    assert dumped["auth_flow"]["register"] == "auth.register"
    assert "register_endpoint" not in dumped["auth_flow"]
    assert dumped["acceptance"][0]["setup"][0]["request"]["json"] == {"title": "b"}
    assert task.auth_flow is not None
    assert task.auth_flow.register_endpoint == "auth.register"
    # Population by field name also works.
    flow = library.AuthFlow(register_endpoint="a.reg", login="a.login", credential="cookie")
    assert flow.model_dump()["register"] == "a.reg"


def test_version_refs_must_be_pinned_to_a_version():
    bad = {**stack_content(), "brief_fragment": "stack-flask-react"}
    with pytest.raises(ValidationError, match="brief_fragment"):
        library.StackProfileContent.model_validate(bad)
    ok = {**stack_content(), "brief_fragment": "stack-flask-react@2#0123456789ab"}
    library.StackProfileContent.model_validate(ok)


@pytest.mark.parametrize(
    ("kind", "path"),
    [
        ("stack", ("brief_fragment",)),
        ("starter", ("stack_profile",)),
        ("style", ("fragment",)),
        ("guidance", ("fragment",)),
        ("prompt_set", ("roles", "brief")),
    ],
)
def test_library_refs_in_content_must_be_hash_pinned(kind, path):
    """Section 3.4 "pinned ref" / A.1: a bare ``slug@v`` would let equal entities hash differently."""
    content = copy.deepcopy(LIBRARY_SAMPLES[kind])
    if kind == "guidance":
        content["fragment"] = "guidance-generic@1#0123456789ab"
    library.validate_content(kind, content)
    node = content
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = node[path[-1]].split("#", 1)[0]
    with pytest.raises(ValidationError):
        library.validate_content(kind, content)
    assert {location for location, _, _ in library.iter_content_refs(kind, content)} == {".".join(path)}


def test_ref_fields_cover_every_versionref_in_the_contracts():
    """LIBRARY_REF_FIELDS lists each content field typed as a library ref (pinning reaches all of them)."""
    declared = {
        (kind, path.split(".", 1)[0]) for kind, fields in library.LIBRARY_REF_FIELDS.items() for path, _ in fields
    }
    typed = set()
    for kind, model in library.CONTENT_MODELS.items():
        for name, field in model.model_fields.items():
            if "#[0-9a-f]{12}" in repr(field.metadata) + repr(field.annotation):
                typed.add((kind, name))
    assert typed == declared


def test_pin_content_refs_pins_bare_refs_and_checks_pinned_ones():
    hashes = {("prompt", "brief-app-default", 1): "a" * 64, ("prompt", "other", 2): "b" * 64}

    def resolve(kind, slug, version):
        return hashes.get((kind, slug, version))

    raw = {"mode": "app", "roles": {"brief": "brief-app-default@1", "system": "other@2#" + "b" * 12}}
    pinned, errors = library.pin_content_refs("prompt_set", raw, resolve)
    assert errors == {}
    assert pinned["roles"] == {"brief": "brief-app-default@1#" + "a" * 12, "system": "other@2#" + "b" * 12}
    assert raw["roles"]["brief"] == "brief-app-default@1"  # the input is not modified
    library.validate_content("prompt_set", pinned)
    # Two spellings of the same entity normalize to the same stored content (and so the same hash).
    again, _ = library.pin_content_refs("prompt_set", pinned, resolve)
    assert again == pinned

    _, errors = library.pin_content_refs("prompt_set", {"mode": "app", "roles": {"brief": "missing@1"}}, resolve)
    assert errors == {"roles.brief": ["prompt missing@1 does not exist"]}
    wrong = {"mode": "app", "roles": {"brief": "brief-app-default@1#" + "c" * 12}}
    _, errors = library.pin_content_refs("prompt_set", wrong, resolve)
    assert "does not match" in errors["roles.brief"][0]
    # Unknown pinned refs are accepted (lint reports them); kinds without refs pass through.
    unknown = {"mode": "app", "roles": {"brief": "elsewhere@3#" + "d" * 12}}
    assert library.pin_content_refs("prompt_set", unknown, resolve) == (unknown, {})
    assert library.pin_content_refs("sampling", {"policy": "provider_default"}, resolve)[1] == {}


def test_starter_requires_digest_pinned_bases_and_relative_paths():
    bad = starter_content()
    bad["base_images"] = [{"ref": "python:3.13-slim", "digest": "latest"}]
    with pytest.raises(ValidationError, match="digest"):
        library.StarterContent.model_validate(bad)
    for path in ["/etc/passwd", "../escape.py", "a/./b.py", "a//b.py", "a\\b.py", "dir/"]:
        bad = starter_content()
        bad["files"] = {path: {"blob": SHA, "mode": 0o644}}
        with pytest.raises(ValidationError):
            library.StarterContent.model_validate(bad)


def test_task_prose_requires_known_variants():
    bad = {**task_content(), "prose": {"verbose": "x"}}
    with pytest.raises(ValidationError, match="prose"):
        library.TaskSpecContent.model_validate(bad)
    with pytest.raises(ValidationError, match="prose"):
        library.TaskSpecContent.model_validate({**task_content(), "prose": {}})


def test_endpoint_rules():
    endpoint = task_content()["endpoints"][0]
    with pytest.raises(ValidationError):
        library.Endpoint.model_validate({**endpoint, "path": "api/no-slash"})
    with pytest.raises(ValidationError):
        library.Endpoint.model_validate({**endpoint, "success_status": 404})
    with pytest.raises(ValidationError):
        library.Endpoint.model_validate({**endpoint, "method": "OPTIONS"})


def test_task_structured_hash_from_content_ignores_prose():
    content = library.normalize_content("task", task_content())
    other = copy.deepcopy(content)
    other["prose"]["minimal"] = "Totally different wording."
    assert identity.task_structured_hash(content) == identity.task_structured_hash(other)
    assert identity.task_prose_hashes(content)["standard"] == identity.text_sha("A todo app\nwith users.")


def test_task_schema_json_matches_checked_in_file():
    """``data/tasks/schema.json`` is generated from TaskSpecContent; regenerate with write_task_schema()."""
    assert library.TASK_SCHEMA_PATH.is_file(), "missing data/tasks/schema.json: run write_task_schema()"
    checked_in = library.TASK_SCHEMA_PATH.read_text(encoding="utf-8")
    assert checked_in == library.render_task_schema(), (
        "data/tasks/schema.json is stale: python -c 'from backend.generation.contracts.library import "
        "write_task_schema; write_task_schema()'"
    )


def test_task_schema_describes_stored_form():
    schema = json.loads(library.render_task_schema())
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert "register" in schema["$defs"]["AuthFlow"]["properties"]
    assert "json" in schema["$defs"]["CheckRequest"]["properties"]
    assert set(schema["required"]) >= {"title", "entities", "endpoints", "features", "surfaces", "prose"}


# =====================================================================================
# Manifest / plan / slot specifics
# =====================================================================================


def test_manifest_protocol_shape_rules():
    payload = manifest_payload()
    manifest.RunManifest.model_validate(payload)
    with pytest.raises(ValidationError, match="runs under"):
        manifest.RunManifest.model_validate({**payload, "kind": "slot_agent"})
    with pytest.raises(ValidationError, match="carry task"):
        manifest.RunManifest.model_validate({**payload, "task": None})
    with pytest.raises(ValidationError, match="missing: harness"):
        manifest.RunManifest.model_validate({**payload, "harness": None})


def test_manifest_reference_runs_need_no_model():
    payload = manifest_payload()
    for key in ("model", "sampling", "resources", "gateway", "harness", "identity", "sandbox", "study"):
        payload[key] = None
    payload.update(protocol="ref@1", kind="reference")
    manifest.RunManifest.model_validate(payload)


# Follow-up #4: every library ref of a manifest is pinned (slug@v#hash12); a bare slug@v is refused.
MANIFEST_REF_PATHS = (
    ("task", "ref"),
    ("sampling", "ref"),
    ("resources", "ref"),
    ("harness", "preset_ref"),
    ("starter", "ref"),
)


@pytest.mark.parametrize(("section", "field"), MANIFEST_REF_PATHS)
def test_manifest_library_refs_must_be_pinned(section, field):
    payload = manifest_payload()
    manifest.RunManifest.model_validate(payload)
    payload[section] = {**payload[section], field: "x@1"}
    with pytest.raises(ValidationError, match=field):
        manifest.RunManifest.model_validate(payload)


def test_manifest_factor_and_eval_suite_refs_must_be_pinned():
    with pytest.raises(ValidationError):
        manifest.FactorRecord.model_validate({"ref": "x@1", "content_hash": SHA})
    with pytest.raises(ValidationError):
        manifest.ManifestEvalSuite.model_validate({"ref": "eval-v2@1", "content_hash": SHA})
    payload = manifest_payload()
    payload["planned_evaluation"]["eval_suite"]["ref"] = "eval-v2@1"
    with pytest.raises(ValidationError):
        manifest.RunManifest.model_validate(payload)


def test_manifest_endpoint_freezes_local_serving_settings():
    endpoint = manifest.ManifestEndpoint.model_validate(
        {
            "id": 3,
            "kind": "ollama",
            "served_model_name": "qwen3:8b",
            "cost_basis": "free",
            "num_ctx": 32768,
            "keep_alive": "30m",
            "api_flavor": "native",
            "capabilities": ["completion", "tools"],
        },
    )
    assert endpoint.num_ctx == 32768
    assert (
        manifest.ManifestEndpoint.model_validate({"id": 1, "kind": "openrouter", "cost_basis": "billed"}).num_ctx
        is None
    )
    with pytest.raises(ValidationError):
        manifest.ManifestEndpoint.model_validate({"id": 1, "kind": "ollama", "cost_basis": "free", "num_ctx": 0})


def test_factor_record_has_one_form():
    with pytest.raises(ValidationError, match="exactly one"):
        manifest.FactorRecord.model_validate({"ref": PIN, "content_hash": SHA, "value": "x"})
    with pytest.raises(ValidationError, match="content_hash"):
        manifest.FactorRecord.model_validate({"ref": PIN})


def test_design_rejects_duplicate_factors_and_levels():
    payload = design_payload()
    payload["factors"].append({"key": "model", "role": "fixed", "levels": ["x"]})
    with pytest.raises(ValidationError, match="duplicate factor keys"):
        plan.Design.model_validate(payload)
    with pytest.raises(ValidationError, match="duplicate factor levels"):
        plan.FactorSpec.model_validate({"key": "model", "role": "crossed", "levels": ["a", "a"]})
    with pytest.raises(ValidationError):
        plan.Design.model_validate({**design_payload(), "repeats": plan.MAX_REPEATS + 1})


def test_design_hash_is_stable_over_the_dump():
    design = plan.Design.model_validate(design_payload())
    assert identity.design_hash(design.model_dump(mode="json")) == identity.design_hash(
        plan.Design.model_validate(design.model_dump(mode="json")).model_dump(mode="json"),
    )


def test_factor_mode_vocabulary():
    assert set(plan.FACTOR_KEYS) >= plan.SLOT_FACTORS
    assert "stack_profile" not in plan.SLOT_FACTORS
    assert "hole_mode" not in plan.APP_FACTORS
    assert "prose_variant" in plan.APP_FACTORS


def test_slot_address_rules_and_identity():
    with pytest.raises(ValidationError, match="requires a span"):
        slot.SlotAddress.model_validate({"kind": "region", "path": "a.py", "content_sha": SHA})
    with pytest.raises(ValidationError, match="must not carry a span"):
        slot.SlotAddress.model_validate(
            {
                "kind": "file",
                "path": "a.py",
                "content_sha": SHA,
                "span": {"start_line": 1, "end_line": 1, "start_byte": 0, "end_byte": 1},
            },
        )
    with pytest.raises(ValidationError, match="ends before"):
        slot.Span(start_line=3, end_line=2, start_byte=0, end_byte=0)
    address = slot.SlotAddress.model_validate(dict(ROUND_TRIPS_BY_NAME["SlotAddress"]))
    ident = address.identity()
    assert set(ident) == {"kind", "path", "span", "content_sha"}
    assert ident["span"]["start_byte"] == 40


def test_slot_policy_identity_matches_identity_inputs_shape():
    policy = slot.SlotPolicy(hole_mode="stub", context_policy=slot.ContextPolicy(), edit_policy="revert")
    ident = policy.identity(SHA)
    assert tuple(sorted(ident)) == tuple(sorted(identity.SLOT_POLICY_KEYS))
    assert ident["context_policy_hash"] == identity.H(slot.ContextPolicy().model_dump(mode="json"))
    changed = slot.SlotPolicy(
        hole_mode="stub",
        context_policy=slot.ContextPolicy(max_context_tokens=1000),
        edit_policy="revert",
    )
    assert changed.identity(SHA)["context_policy_hash"] != ident["context_policy_hash"]


def test_slot_output_models():
    assert slot.slot_output_model("file") is slot.SlotFilesOutput
    assert slot.slot_output_model("symbol") is slot.SlotCodeOutput
    assert slot.slot_output_model("component", whole_file_component=True) is slot.SlotFilesOutput
    with pytest.raises(ValidationError):
        slot.SlotCodeOutput.model_validate({"code": "x", "explanation": "y"})


# =====================================================================================
# Outcome table
# =====================================================================================


def test_termination_outcome_table_covers_every_termination():
    assert set(outcome.TERMINATION_OUTCOME) == set(outcome.Termination)
    table = outcome.TERMINATION_OUTCOME
    for term in outcome.TRUNCATING_TERMINATIONS:
        assert table[term].truncated is True
        assert table[term].usable is True
        assert table[term].final_outcome == outcome.EVALUATION_DECIDES
    assert table[outcome.Termination.COMPLETED].truncated is False
    assert table[outcome.Termination.HARNESS_CRASH].generation == "retry_once_then_evaluate"
    assert table[outcome.Termination.LLM_ERROR].final_outcome == outcome.Outcome.INFRA_ERROR
    assert table[outcome.Termination.INFRA].retryable is True
    assert table[outcome.Termination.OOM].final_outcome == outcome.Outcome.EXCLUDED
    assert table[outcome.Termination.OOM].reason == "resource_limit"
    assert table[outcome.Termination.OOM].retryable is False
    assert table[outcome.Termination.CANCELLED].reason == "cancelled"
    for rule in table.values():
        if rule.final_outcome != outcome.EVALUATION_DECIDES:
            assert rule.usable is outcome.is_usable(rule.final_outcome)
        if rule.note:
            assert outcome.note_code_valid(rule.note) or rule.note in outcome.PARAMETRIC_NOTE_CODES


def test_usable_outcomes():
    assert outcome.is_usable("tested") is True
    assert outcome.is_usable("output_invalid") is True
    assert outcome.is_usable("infra_error") is False
    assert outcome.is_usable("excluded") is False
    assert outcome.is_usable("") is None
    with pytest.raises(ValueError, match="failed"):
        outcome.is_usable("failed")


def test_termination_values_match_spec():
    assert {t.value for t in outcome.Termination} == {
        "completed",
        "wall_clock",
        "idle_timeout",
        "budget_exhausted",
        "harness_error",
        "harness_crash",
        "llm_error",
        "infra_interrupted",
        "cancelled",
        "max_turns",
        "oom_killed",
    }


@pytest.mark.parametrize(
    "code",
    ["budget_exhausted", "call_truncations:0", "call_truncations:x", "adherence:clean", "made_up", "lock_source:"],
)
def test_invalid_note_codes_rejected(code):
    with pytest.raises(ValidationError, match="unregistered validity note"):
        outcome.Note(code=code)


def test_validity_has_note():
    validity = outcome.Validity.model_validate({"notes": [{"code": "budget_exhausted:cost"}]})
    assert validity.has_note("budget_exhausted")
    assert not validity.has_note("wall_clock")


# =====================================================================================
# Shared API shapes and events
# =====================================================================================


def test_ref_parse_and_str():
    pinned = Ref.parse("flask-react@3#0123456789ab")
    assert (pinned.slug, pinned.version, pinned.hash12) == ("flask-react", 3, "0123456789ab")
    assert str(pinned) == "flask-react@3#0123456789ab"
    assert pinned.is_pinned
    assert pinned.unpinned == "flask-react@3"
    plain = Ref.parse("t07@1")
    assert str(plain) == "t07@1"
    assert not plain.is_pinned
    assert hash(plain) == hash(Ref.parse("t07@1"))
    for bad in ["flask", "a@0", "a@1#short", "@1", "a@b"]:
        with pytest.raises(ValueError, match="not a ref"):
            Ref.parse(bad)


def test_page_bounds():
    page = Page[int](items=[1, 2], total=2, page=1, page_size=200)
    assert page.model_dump() == {"items": [1, 2], "total": 2, "page": 1, "page_size": 200}
    with pytest.raises(ValidationError):
        Page[int](items=[], total=0, page=1, page_size=201)
    with pytest.raises(ValidationError):
        Page[int](items=["x"], total=1, page=1, page_size=10)


def test_events_parse_by_type():
    run_event = events.parse_run_event(
        {"type": "step", "run_id": "r", "step": "collect", "status": "ok", "channel": "run:r", "published_at": "t"},
    )
    assert isinstance(run_event, events.RunStepEvent)
    study_event = events.parse_study_event({"type": "paused", "reason": "budget", "condition_id": 3})
    assert isinstance(study_event, events.PausedEvent)
    with pytest.raises(ValidationError):
        events.parse_run_event({"type": "spend", "spent": 1})
    assert events.run_channel("abc") == "run:abc"
    assert events.study_channel("abc") == "study:abc"


def test_event_type_lists_match_models():
    run_types = {m.model_fields["type"].default for m in events.RunEvent.__args__[0].__args__}
    study_types = {m.model_fields["type"].default for m in events.StudyEvent.__args__[0].__args__}
    assert run_types == set(events.RUN_EVENT_TYPES)
    assert study_types == set(events.STUDY_EVENT_TYPES)


def test_harness_dataclasses():
    facts_kwargs = dict.fromkeys(harness.HarnessFacts.__dataclass_fields__)
    facts_kwargs.update(
        facts_version=1,
        exit_codes={53: "max_turns"},
        crash_codes=(139,),
        protocol_modes=("native",),
        open_questions=("stream-json schema",),
        system_prompt_normalizers=(),
        unsupported_knobs=(),
    )
    facts = harness.HarnessFacts(**facts_kwargs)
    assert facts.enabled is False
    assert facts.to_dict()["exit_codes"] == {"53": "max_turns"}
    wiring = harness.GatewayWiring(protocol="openai_chat", protocol_mode="native", base_url="http://gw:8080/v1")
    ctx = harness.HarnessContext(
        run_id="r1",
        brief="Build it.",
        model_name="m",
        gateway=wiring,
        preset=library.HarnessPresetContent(harness="opencode", harness_version="1"),
        policy=resources.EffectivePolicy(
            content=resources.preset_content("frugal"),
            budget_basis="usd",
            cap_basis="billed",
            serving_tier="standard",
        ),
    )
    assert ctx.workspace == "/workspace"
    assert ctx.brief_path == "/run/brief.md"
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.run_id = "x"  # type: ignore[misc]


ROUND_TRIPS_BY_NAME = {model.__name__: payload for model, payload in ROUND_TRIPS}
