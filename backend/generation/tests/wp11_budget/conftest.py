"""Fixtures for the WP-11 budget tests: captured events, study trees with blocks, and the scheduler."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from dataclasses import field
from datetime import timedelta
from decimal import Decimal
from typing import Any
from unittest import mock

import pytest
from django.utils import timezone

from backend.execution.breaker import breaker
from backend.execution.capacity import EffectiveSlots
from backend.execution.scheduler import Scheduler
from backend.generation.contracts.resources import preset_content
from backend.generation.models import GenerationRun
from backend.generation.models import RunManifest
from backend.generation.models import Study
from backend.generation.models import StudyCell
from backend.generation.models import StudyCondition
from backend.generation.models import StudyUnit
from backend.generation.testing.factories import run_manifest_content
from backend.generation.tests.factories import GenerationRunFactory
from backend.generation.tests.factories import ResourcePolicyFactory
from backend.generation.tests.factories import StudyCellFactory
from backend.generation.tests.factories import StudyConditionFactory
from backend.generation.tests.factories import StudyFactory
from backend.generation.tests.factories import StudyPlanFactory
from backend.generation.tests.factories import StudyUnitFactory
from backend.generation.tests.factories import TaskSpecVersionFactory
from backend.inference.access import system_openrouter
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

OWNER = "exec-wp11:1:abcd"


@dataclass
class Events:
    """Every realtime event published during a test: ``[(channel, payload)]``."""

    items: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def of(self, channel_prefix: str, kind: str | None = None) -> list[dict[str, Any]]:
        return [e for c, e in self.items if c.startswith(channel_prefix) and (kind is None or e.get("type") == kind)]


@pytest.fixture(autouse=True)
def events():
    """No Redis: doorbells are no-ops and published events are captured (validated by the realtime layer)."""
    captured = Events()
    breaker.reset()

    def publish(channel: str, event: dict) -> None:
        captured.items.append((channel, dict(event)))

    with mock.patch("backend.execution.api.wake"), mock.patch("backend.realtime.events.publish", side_effect=publish):
        yield captured
    breaker.reset()


@pytest.fixture
def user(db):
    return UserFactory()


@pytest.fixture
def system_ep(db):
    return system_openrouter()


def slots(**kw: Any) -> EffectiveSlots:
    values = {
        "sandbox_slots": 8,
        "eval_slots": 1,
        "llm_job_threads": 16,
        "post_run_slots": 1,
        "draining": False,
        "defer_post_runs": False,
    }
    values.update(kw)
    return EffectiveSlots(**values)


def free(sandbox: int = 8, eval: int = 4, llm: int = 32) -> dict[str, int]:
    return {"sandbox": sandbox, "eval": eval, "llm": llm}


def scheduler() -> Scheduler:
    return Scheduler(OWNER)


@pytest.fixture
def tick(django_capture_on_commit_callbacks):
    """One scheduler tick with ``n`` sandbox slots; after-commit work (events, block closing) runs."""

    def run(n: int = 2, **slot_kw: Any):
        with django_capture_on_commit_callbacks(execute=True):
            return scheduler().tick(slots(sandbox_slots=n, **slot_kw), free(sandbox=n))

    return run


@pytest.fixture
def on_commit(django_capture_on_commit_callbacks):
    """A context manager that runs the after-commit callbacks of its block."""

    def ctx():
        return django_capture_on_commit_callbacks(execute=True)

    return ctx


def policy_content(tier: str = "frugal", **dotted: Any) -> dict[str, Any]:
    """A preset's content with dotted overrides (``{"run.max_cost_usd": 0.2}``)."""
    content = preset_content(tier).model_dump(mode="json")  # type: ignore[arg-type]
    for key, value in dotted.items():
        node = content
        parts = key.split(".")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = copy.deepcopy(value)
    return content


def model(**kw: Any):
    kw.setdefault("input_price_per_token", 1e-6)
    kw.setdefault("output_price_per_token", 2e-6)
    kw.setdefault("context_window", 128_000)
    kw.setdefault("max_output_tokens", 32_000)
    kw.setdefault("supports_function_calling", True)
    return LLMModelFactory(**kw)


@dataclass
class StudyTree:
    study: Study
    conditions: list[StudyCondition]
    units: list[StudyUnit]
    cells: list[StudyCell]
    runs: list[GenerationRun]

    def runs_of(self, *, condition: int | None = None, unit: int | None = None) -> list[GenerationRun]:
        out = []
        for run in self.runs:
            cell = run.cell
            if condition is not None and run.condition_id != self.conditions[condition].pk:
                continue
            if unit is not None and cell.unit_id != self.units[unit].pk:
                continue
            out.append(run)
        return out


def make_study(
    user,
    *,
    n_conditions: int = 2,
    n_units: int = 2,
    repeats: int = 5,
    cap: float | str = 10.0,
    run_cap: float = 0.30,
    cell_cap: float = 0.60,
    cap_usd: float = 0.15,
    estimate_usd: float = 0.10,
    estimate_list_usd: float | None = None,
    cap_basis: str = "billed",
    on_exhausted: str = "stop_balanced",
    status: str = "running",
    stage: str = "main",
    kind: str = "exploratory",
    mode: str = "app",
    models: list | None = None,
    manifests: bool = False,
    policy_overrides: dict[str, Any] | None = None,
    pricing: tuple[float, float] = (1.0, 2.0),
) -> StudyTree:
    """A launched study: conditions x units x repeats cells in repeat-major blocks, one pending run per cell."""
    content = policy_content(
        "frugal",
        **{
            "study.max_cost_usd": cap,
            "run.max_cost_usd": run_cap,
            "cell.max_cost_usd": cell_cap,
            "study.on_budget_exhausted": on_exhausted,
            "concurrency.study_max_parallel": 64,
            **(policy_overrides or {}),
        },
    )
    policy = ResourcePolicyFactory(content=content)
    study = StudyFactory(created_by=user, resource_policy=policy, status=status, launched_at=timezone.now())
    Study.objects.filter(pk=study.pk).update(
        on_budget_exhausted=on_exhausted,
        kind=kind,
        mode=mode,
        max_parallel=64,
        cap_basis=cap_basis,
    )
    study.refresh_from_db()
    plan = StudyPlanFactory(
        study=study,
        n_conditions=n_conditions,
        n_units=n_units,
        n_cells=n_conditions * n_units * repeats,
    )
    Study.objects.filter(pk=study.pk).update(active_plan=plan)
    study.refresh_from_db()
    models = models or [model() for _ in range(n_conditions)]
    conditions = [
        StudyConditionFactory(
            plan=plan,
            index=i,
            model=models[i % len(models)],
            levels={"model": models[i % len(models)].model_id},
        )
        for i in range(n_conditions)
    ]
    task_version = TaskSpecVersionFactory()
    units = [
        StudyUnitFactory(
            plan=plan,
            index=u,
            unit_order=u,
            task_version=task_version if u == 0 else TaskSpecVersionFactory(),
        )
        for u in range(n_units)
    ]
    cells: list[StudyCell] = []
    runs: list[GenerationRun] = []
    now = timezone.now()
    for r in range(repeats):
        for u, unit in enumerate(units):
            block = r * n_units + u
            for c, condition in enumerate(conditions):
                sequence = block * n_conditions + c
                cell = StudyCellFactory(
                    condition=condition,
                    unit=unit,
                    repeat_index=r,
                    block_index=block,
                    sequence=sequence,
                    stage=stage,
                    cap_usd=Decimal(str(cap_usd)),
                    cell_cap_usd=Decimal(str(cell_cap)),
                    estimate_usd=Decimal(str(estimate_usd)),
                    estimate_list_usd=Decimal(str(estimate_usd if estimate_list_usd is None else estimate_list_usd)),
                    state="queued",
                )
                run = GenerationRunFactory(
                    created_by=user,
                    study=study,
                    condition=condition,
                    cell=cell,
                    stage=stage,
                    repeat_index=r,
                    sequence=sequence,
                    model=condition.model,
                    task_version=unit.task_version,
                    resource_policy=policy,
                    queued_at=now - timedelta(seconds=10) + timedelta(milliseconds=sequence),
                )
                if manifests:
                    attach_manifest(run, pricing=pricing)
                cells.append(cell)
                runs.append(run)
    return StudyTree(study, conditions, units, cells, runs)


def attach_manifest(
    run: GenerationRun,
    *,
    pricing: tuple[float, float] = (1.0, 2.0),
    effective: dict | None = None,
    cost_basis: str = "billed",
):
    """A RunManifest whose resources.effective is the run's preset (as an EffectivePolicy dump)."""
    manifest = run_manifest_content(run)
    manifest["model"]["endpoint"]["cost_basis"] = cost_basis
    manifest["model"]["pricing"] = {
        "input_per_mtok": pricing[0],
        "output_per_mtok": pricing[1],
        "source": "test",
        "as_of": "2026-11-01",
    }
    manifest["resources"]["effective"] = effective or {
        "content": run.resource_policy.content,
        "budget_basis": "usd",
        "cap_basis": "billed",
        "serving_tier": "standard",
        "clamps": [],
        "notes": [],
        "issues": [],
    }
    return RunManifest.objects.create(run=run, manifest=manifest)


def backdate(entry, delta: timedelta) -> None:
    """Move a ledger row back in time (the ledger is write-once, so through SQL)."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE generation_spendledgerentry SET created_at = %s WHERE id = %s",
            [timezone.now() - delta, entry.pk],
        )


def claimed_ids(result) -> set[str]:
    return {str(c.run_id) for c in result.claimed}


def finish(run: GenerationRun, *, outcome: str = "tested", cost: float | None = None, **fields: Any) -> GenerationRun:
    """End a claimed run's generation like a worker: optional usage, then a terminal finish_stage."""
    from backend.execution import transitions
    from backend.generation.budget import ledger

    run.refresh_from_db()
    if cost is not None:
        ledger.record_usage(
            run,
            attempt=ledger.attempt_no(run),
            source="gateway_live",
            amount_usd=cost,
            list_cost_usd=cost,
            tokens_in=1000,
            tokens_out=100,
            requests=1,
            dedupe_key=f"live:{run.pk}:{ledger.attempt_no(run)}:1",
        )
    run.outcome = outcome
    for name, value in fields.items():
        setattr(run, name, value)
    run.metrics = {**(run.metrics or {}), **({"cost_usd": cost} if cost is not None else {})}
    assert transitions.finish_stage(run, transitions.OUTCOME_DONE)
    run.refresh_from_db()
    return run
