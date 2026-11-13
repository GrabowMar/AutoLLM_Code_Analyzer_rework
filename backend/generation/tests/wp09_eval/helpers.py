"""Shared helpers of the WP-09 tests: the bundled reference app, its plans and a local runtime."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from typing import Any

from backend.generation.evaluation import acceptance
from backend.generation.evaluation import conformance
from backend.generation.evaluation.runtime import LocalProcessRuntime

KIT_STACK: dict[str, Any] = {
    "run_contract": {"health_path": "/api/health", "static_serving": "backend", "data_dir": "./data"},
    "frontend": {"framework": "react"},
}
ADMIN_PASSWORD = "test-admin-pw-42"  # noqa: S105 - a test value


def kit_plan(
    mode: str = "contract_bearer",
    *,
    probes: str = "task",
    content: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan = acceptance.compile_content(content or conformance.kit_task_content(), mode, KIT_STACK, probes=probes)
    return acceptance.plan_json(plan)


def env(mode: str = "contract_bearer", **extra: str) -> dict[str, str]:
    return {"ADMIN_PASSWORD": ADMIN_PASSWORD, "SECRET_KEY": "test-secret", "AUTH_MODE": mode, **extra}


@contextlib.contextmanager
def app(mode: str = "contract_bearer", **extra: str) -> Iterator[tuple[LocalProcessRuntime, Any, dict[str, str]]]:
    runtime = LocalProcessRuntime()
    variables = env(mode, **extra)
    try:
        handle = runtime.start(str(conformance.PROBE_MUTANTS), variables)
        yield runtime, handle, variables
    finally:
        runtime.close()


def run_tester(mode: str = "contract_bearer", plan: dict[str, Any] | None = None, **extra: str) -> dict[str, Any]:
    with app(mode, **extra) as (runtime, handle, variables):
        assert runtime.wait_healthy(handle, "/api/health", 15)
        return runtime.run_tester(handle, plan or kit_plan(mode), variables, 120)


def statuses(result: dict[str, Any]) -> dict[str, str]:
    return {c["id"]: c["status"] for c in result["checks"]}


def kit_tree(**env_overrides: str) -> dict[str, tuple[bytes, int]]:
    """The reference app as a run tree (``app.py`` + ``llmlab-local.json``, optional mutant env)."""
    marker: dict[str, Any] = {"local_entry": "app.py"}
    if env_overrides:
        marker["env"] = env_overrides
    return {
        "app.py": ((conformance.PROBE_MUTANTS / "app.py").read_bytes(), 0o644),
        "llmlab-local.json": (json.dumps(marker).encode(), 0o644),
    }
