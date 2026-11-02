"""factory_boy factories for the human-evaluation models, plus :func:`make_open_task`.

Shared by every human-eval test module. Later agents may APPEND new factories at
the end of this file (short, uniquely named block); never edit the existing ones.

:func:`make_open_task` builds a realistic *open* (frozen) task directly in the
database — completed jobs with an app spec and ``metrics.functional``, task apps
with snapshots shaped like the spec (§5.2 + R1), items and the design hash —
without calling the task / pool / items services, so tests of any area can use it.
"""

from __future__ import annotations

import hashlib
import json
import random
from typing import Any

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from backend.generation.models import GenerationJob
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import ExperimentConditionFactory
from backend.generation.tests.factories import ExperimentFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.human_eval.models import Assignment
from backend.human_eval.models import Attachment
from backend.human_eval.models import EvalInvite
from backend.human_eval.models import EvalItem
from backend.human_eval.models import EvalTask
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.models import EvaluatorProfile
from backend.human_eval.models import FindingVerdict
from backend.human_eval.models import HttpExchange
from backend.human_eval.models import Observation
from backend.human_eval.models import Participant
from backend.human_eval.models import Response
from backend.human_eval.services import design as design_service
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

# A valid 1x1 transparent PNG (magic bytes recognised by the upload sniffer).
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da636460f85f0f0002870180eb47ba920000000049454e44ae426082",
)


class EvalTaskFactory(DjangoModelFactory):
    class Meta:
        model = EvalTask

    title = factory.Sequence(lambda n: f"Human eval task {n}")
    description = "Rate the apps."
    instructions = "Use each app, then answer the questions."
    mode = EvalTask.Mode.SINGLE
    status = EvalTask.Status.DRAFT
    design = factory.LazyAttribute(lambda o: design_service.default_design(o.mode))
    settings = factory.LazyFunction(design_service.default_settings)
    created_by = factory.SubFactory(UserFactory)


class EvalTaskAppFactory(DjangoModelFactory):
    class Meta:
        model = EvalTaskApp

    task = factory.SubFactory(EvalTaskFactory)
    job = None
    label = factory.Sequence(lambda n: f"App {n + 1:02d}")
    group_key = "todo-app:000000000000"
    position = factory.Sequence(lambda n: n)
    snapshot = factory.LazyFunction(dict)


class EvalItemFactory(DjangoModelFactory):
    class Meta:
        model = EvalItem

    task = factory.SubFactory(EvalTaskFactory)
    app_a = factory.SubFactory(EvalTaskAppFactory, task=factory.SelfAttribute("..task"))
    app_b = None
    group_key = factory.LazyAttribute(lambda o: o.app_a.group_key)
    position = factory.Sequence(lambda n: n)


class EvalInviteFactory(DjangoModelFactory):
    class Meta:
        model = EvalInvite

    task = factory.SubFactory(EvalTaskFactory)
    label = factory.Sequence(lambda n: f"Invite {n}")


class ParticipantFactory(DjangoModelFactory):
    class Meta:
        model = Participant

    task = factory.SubFactory(EvalTaskFactory)
    number = factory.Sequence(lambda n: n + 1)
    kind = Participant.Kind.INVITE
    status = Participant.Status.ACTIVE
    joined_at = factory.LazyFunction(timezone.now)


class AssignmentFactory(DjangoModelFactory):
    class Meta:
        model = Assignment

    participant = factory.SubFactory(ParticipantFactory)
    task = factory.LazyAttribute(lambda o: o.participant.task)
    item = factory.SubFactory(EvalItemFactory, task=factory.SelfAttribute("..task"))
    status = Assignment.Status.IN_PROGRESS
    position = factory.Sequence(lambda n: n + 1)
    started_at = factory.LazyFunction(timezone.now)
    last_activity_at = factory.LazyFunction(timezone.now)


class ResponseFactory(DjangoModelFactory):
    class Meta:
        model = Response

    assignment = factory.SubFactory(AssignmentFactory)
    block_id = "ratings"
    answers = factory.LazyFunction(dict)


class ObservationFactory(DjangoModelFactory):
    class Meta:
        model = Observation

    assignment = factory.SubFactory(AssignmentFactory)
    task_app = factory.LazyAttribute(lambda o: o.assignment.item.app_a)
    kind = Observation.Kind.BUG
    title = factory.Sequence(lambda n: f"Bug {n}")
    description = "The save button does nothing."


class AttachmentFactory(DjangoModelFactory):
    class Meta:
        model = Attachment

    observation = factory.SubFactory(ObservationFactory)
    file = factory.django.FileField(filename="upload.png", data=PNG_BYTES)
    content_type = "image/png"
    size = len(PNG_BYTES)
    original_name = "screenshot.png"


class FindingVerdictFactory(DjangoModelFactory):
    class Meta:
        model = FindingVerdict

    assignment = factory.SubFactory(AssignmentFactory)
    task_app = factory.LazyAttribute(lambda o: o.assignment.item.app_a)
    finding = None
    finding_key = factory.Sequence(lambda n: f"finding-{n}")
    verdict = FindingVerdict.Verdict.TP


class HttpExchangeFactory(DjangoModelFactory):
    class Meta:
        model = HttpExchange

    assignment = factory.SubFactory(AssignmentFactory)
    task_app = factory.LazyAttribute(lambda o: o.assignment.item.app_a)
    method = "GET"
    path = "/api/items"
    status_code = 200
    elapsed_ms = 12


class EvaluatorProfileFactory(DjangoModelFactory):
    class Meta:
        model = EvaluatorProfile

    user = factory.SubFactory(UserFactory)
    is_restricted = True
    created_by = None


# ---------------------------------------------------------------------------
# make_open_task
# ---------------------------------------------------------------------------

SPEC_API_ENDPOINTS = [
    {"method": "GET", "path": "/api/items", "description": "List the user's items", "auth": "user"},
    {"method": "POST", "path": "/api/items", "description": "Create an item", "auth": "user"},
    {"method": "DELETE", "path": "/api/items/<id>", "description": "Delete an item", "auth": "user"},
]
SPEC_ADMIN_API_ENDPOINTS = [
    {"method": "GET", "path": "/api/admin/users", "description": "List all users", "auth": "admin"},
]


def _spec_dict(spec) -> dict[str, Any]:
    from backend.generation.services.profile_resolver import app_requirement_to_dict

    return app_requirement_to_dict(spec)


def _checks(index: int) -> list[dict[str, Any]]:
    """Conformance api/admin checks named "METHOD /path" (one failing check per odd app)."""
    checks = [
        {"name": f"{endpoint['method']} {endpoint['path']}", "category": "api", "passed": True}
        for endpoint in SPEC_API_ENDPOINTS
    ]
    checks += [
        {"name": f"{endpoint['method']} {endpoint['path']}", "category": "admin", "passed": True}
        for endpoint in SPEC_ADMIN_API_ENDPOINTS
    ]
    if index % 2:
        checks[-1]["passed"] = False
    return checks


def _functional(checks: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(1 for check in checks if check["passed"])
    return {
        "source": "conformance",
        "status": "passed" if passed == len(checks) else "failed",
        "passed": passed == len(checks),
        "pass_rate": round(passed / len(checks), 4),
        "deployed": True,
        "evaluator_version": "3",
        "tester_sha256": "f" * 64,
        "endpoints_ok": passed,
        "endpoints_checked": len(checks),
    }


def _by_category(checks: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for check in checks:
        entry = summary.setdefault(check["category"], {"passed": 0, "total": 0})
        entry["total"] += 1
        entry["passed"] += int(check["passed"])
    return summary


def _snapshot(job, *, condition, spec_hash: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    """A task-app snapshot with the keys of spec §5.2 + R1 (taken at first open)."""
    model = job.model
    experiment = job.experiment
    functional = job.metrics["functional"]
    spec = job.app_requirement
    return {
        "model_id": model.model_id,
        "model_name": model.model_name,
        "provider": model.provider,
        "condition_id": condition.id if condition is not None else None,
        "condition_label": (condition.label or f"{model.model_name} / {condition.method_label()}")
        if condition
        else "",
        "experiment_id": str(experiment.id) if experiment is not None else None,
        "experiment_name": experiment.name if experiment is not None else "",
        "app_slug": spec.slug,
        "app_name": spec.name,
        "repeat_index": job.repeat_index,
        "mode": job.mode,
        "functional": {
            "status": functional["status"],
            "pass_rate": functional["pass_rate"],
            "deployed": functional["deployed"],
            "source": "conformance",
            "by_category": _by_category(checks),
        },
        "evaluator_version": functional["evaluator_version"],
        "tester_sha256": functional["tester_sha256"],
        "created_at": job.created_at.isoformat(),
        "checks": [{"name": c["name"], "category": c["category"], "passed": c["passed"]} for c in checks],
        "spec_content_hash": spec_hash,
        "static_findings": {"count": 0, "loc": 120, "tools": []},
    }


def _pairs(apps: list[EvalTaskApp], snapshots: dict[str, dict[str, Any]], settings: dict[str, Any]):
    """All unordered pairs within a group (cross-condition only when configured), app ids ordered as strings."""
    cross_only = settings["pairing"]["cross_condition_only"]
    ordered = sorted(apps, key=lambda app: str(app.id))
    pairs = []
    for i, first in enumerate(ordered):
        for second in ordered[i + 1 :]:
            if first.group_key != second.group_key:
                continue
            a, b = snapshots[str(first.id)], snapshots[str(second.id)]
            if cross_only and (a["condition_id"] or a["model_id"]) == (b["condition_id"] or b["model_id"]):
                continue
            pairs.append((first, second))
    return pairs


def make_open_task(
    owner=None,
    n_apps: int = 3,
    mode: str = "single",
    design: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    conditions: int = 2,
) -> EvalTask:
    """An OPEN (frozen) task with ``n_apps`` completed jobs, task apps, snapshots and items.

    - ``conditions`` experiment conditions (each its own LLM model); app ``i`` uses condition
      ``i % conditions`` and ``repeat_index = i // conditions``. ``conditions=0`` builds model-only
      jobs without an experiment.
    - every job implements the same app spec, so all apps share one ``group_key``
      (``f"{slug}:{spec_content_hash[:12]}"``); labels are ``App 01``..; ``metrics.functional`` is a
      conformance verdict (odd apps fail one admin check).
    - ``design`` / ``settings`` default to ``design.default_design(mode)`` / defaults; ``settings`` is
      deep-merged over the defaults. Both are validated (ValueError when invalid).
    - single: one item per app; pairwise: all pairs (``pairing.cross_condition_only`` honoured).
    """
    owner = owner or UserFactory()
    normalized_design, design_errors = design_service.validate_design(
        mode,
        design if design is not None else design_service.default_design(mode),
    )
    normalized_settings, settings_errors = design_service.validate_settings(settings or {})
    if design_errors or settings_errors:
        msg = f"make_open_task: invalid design/settings: {design_errors} {settings_errors}"
        raise ValueError(msg)

    experiment = ExperimentFactory(created_by=owner) if conditions else None
    condition_rows = []
    for index in range(conditions):
        condition_rows.append(
            ExperimentConditionFactory(experiment=experiment, model=LLMModelFactory(), label=f"Condition {index + 1}"),
        )
    if experiment is not None:
        experiment.baseline_condition = condition_rows[0]
        experiment.save(update_fields=["baseline_condition"])
    model_only = [] if conditions else [LLMModelFactory(), LLMModelFactory()]

    spec = AppRequirementTemplateFactory(
        backend_requirements=["Users can register and log in", "Users can create, edit and delete items"],
        frontend_requirements=["A list page shows the user's items"],
        admin_requirements=["Admins can see all users"],
        api_endpoints=SPEC_API_ENDPOINTS,
        admin_api_endpoints=SPEC_ADMIN_API_ENDPOINTS,
    )
    spec_dict = _spec_dict(spec)
    spec_hash = hashlib.sha256(
        json.dumps(spec_dict, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"),
    ).hexdigest()
    group_key = f"{spec.slug}:{spec_hash[:12]}"

    now = timezone.now()
    task = EvalTaskFactory(
        created_by=owner,
        mode=mode,
        status=EvalTask.Status.OPEN,
        design=normalized_design,
        settings=normalized_settings,
        experiment=experiment,
        frozen_at=now,
        opened_at=now,
    )
    task.design_hash = design_service.design_hash(mode, normalized_design, normalized_settings)
    task.save(update_fields=["design_hash"])

    apps: list[EvalTaskApp] = []
    snapshots: dict[str, dict[str, Any]] = {}
    width = max(2, len(str(n_apps)))
    for index in range(n_apps):
        condition = condition_rows[index % conditions] if conditions else None
        model = condition.model if condition is not None else model_only[index % len(model_only)]
        checks = _checks(index)
        job = GenerationJobFactory(
            created_by=owner,
            mode=GenerationJob.Mode.AGENT,
            status=GenerationJob.Status.COMPLETED,
            model=model,
            experiment=experiment,
            condition=condition,
            repeat_index=index // conditions if conditions else None,
            app_requirement=spec,
            resolved_bundle={"app_requirement": spec_dict},
            metrics={"functional": _functional(checks)},
            result_data={
                "files": {
                    "backend/app.py": "from flask import Flask\n\napp = Flask(__name__)\n",
                    "frontend/src/App.jsx": "export default function App() { return <h1>Items</h1>; }\n",
                },
                "evaluation": {"checks": checks, "summary": {"by_category": _by_category(checks)}},
            },
            completed_at=now,
        )
        snapshot = _snapshot(job, condition=condition, spec_hash=spec_hash, checks=checks)
        app = EvalTaskAppFactory(
            task=task,
            job=job,
            label=f"App {index + 1:0{width}d}",
            group_key=group_key,
            position=index,
            snapshot=snapshot,
        )
        apps.append(app)
        snapshots[str(app.id)] = snapshot

    rng = random.Random(task.seed)  # noqa: S311 - seeded study order, not a secret
    if mode == EvalTask.Mode.PAIRWISE:
        units: list[tuple[EvalTaskApp, EvalTaskApp | None]] = list(_pairs(apps, snapshots, normalized_settings))
    else:
        units = [(app, None) for app in apps]
    positions = list(range(len(units)))
    rng.shuffle(positions)
    for (app_a, app_b), position in zip(units, positions, strict=True):
        EvalItem.objects.create(task=task, app_a=app_a, app_b=app_b, group_key=app_a.group_key, position=position)
    return task


# ---------------------------------------------------------------------------
# Workbench (B2) helpers: a design with every single-mode block, analysis findings, assignments
# ---------------------------------------------------------------------------


def workbench_design(*, finding_review: bool = True, max_findings: int = 5, min_reports: int = 0) -> dict[str, Any]:
    """A raw single-mode design with one block of every type (validated by :func:`make_open_task`)."""
    scale7 = {"min": 1, "max": 7}
    return {
        "version": 1,
        "blocks": [
            {
                "id": "ratings",
                "type": "rating",
                "title": "Ratings",
                "criteria": [
                    {
                        "id": "functionality",
                        "label": "Functionality",
                        "scale": scale7,
                        "required": True,
                        "allow_na": True,
                    },
                    {"id": "looks", "label": "Looks", "scale": {"min": 0, "max": 10, "step": 2}, "required": False},
                ],
                "comment": "required",
            },
            {
                "id": "reqs",
                "type": "requirements",
                "title": "Requirements",
                "sources": ["backend", "api"],
                "custom": [{"id": "readme", "text": "The app has a README"}],
                "note": "required_unless_met",
            },
            {
                "id": "sec",
                "type": "security",
                "title": "Security",
                "checklist": [{"id": "xss", "label": "XSS"}, {"id": "idor", "label": "IDOR"}],
                "reports": True,
                "finding_review": {"enabled": finding_review, "max_findings": max_findings},
                "http_console": True,
            },
            {
                "id": "tasks",
                "type": "scenarios",
                "title": "Tasks",
                "scenarios": [{"id": "create", "title": "Create an item"}, {"id": "share", "title": "Share it"}],
            },
            {"id": "sus", "type": "questionnaire", "title": "SUS", "instrument": "sus"},
            {
                "id": "extra",
                "type": "questionnaire",
                "title": "Extra",
                "instrument": "custom",
                "questions": [
                    {
                        "id": "liked",
                        "kind": "single",
                        "label": "Liked it?",
                        "options": [{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}],
                    },
                    {"id": "notes", "kind": "textarea", "label": "Notes", "required": False},
                    {
                        "id": "tags",
                        "kind": "multi",
                        "label": "Tags",
                        "options": [{"value": "fast", "label": "Fast"}, {"value": "ugly", "label": "Ugly"}],
                    },
                    {"id": "age", "kind": "number", "label": "Age", "min": 18, "max": 99},
                    {
                        "id": "attn",
                        "kind": "attention",
                        "label": "Pick B",
                        "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}],
                        "expected": "b",
                    },
                ],
            },
            {"id": "bugs", "type": "bugs", "title": "Bugs", "min_reports": min_reports},
        ],
        "analysis": {"primary": None},
        "consent": {"enabled": False, "text": ""},
        "pre_questions": [],
        "post_questions": [],
    }


def workbench_findings(
    job,
    tool_slug: str,
    count: int,
    *,
    kind: str = "container",
    analysis_mode: str = "static",
    category: str = "security",
    result_status: str = "completed",
    **fields,
) -> list:
    """``count`` findings of one tool for ``job`` (a completed analysis run with one tool result)."""
    from backend.analysis.models import AnalysisRun
    from backend.analysis.tests.factories import AnalysisRunFactory
    from backend.analysis.tests.factories import AnalyzerToolFactory
    from backend.analysis.tests.factories import FindingFactory
    from backend.analysis.tests.factories import ToolResultFactory

    AnalyzerToolFactory(slug=tool_slug, name=f"Tool {tool_slug}", kind=kind, analysis_mode=analysis_mode)
    run = AnalysisRunFactory(generation_job=job, created_by=job.created_by, status=AnalysisRun.Status.COMPLETED)
    result = ToolResultFactory(run=run, tool_slug=tool_slug, status=result_status)
    defaults = {"category": category, "severity": "high", "tool_severity": "ERROR", "rule_id": "rule-1"}
    defaults.update(fields)
    return [
        FindingFactory(result=result, file_path=f"backend/file_{index}.py", line_number=index + 1, **defaults)
        for index in range(count)
    ]


def workbench_assignment(task: EvalTask, *, participant=None, item=None, status: str = "assigned", **fields):
    """An assignment of ``participant`` (a new invite participant by default) on ``item`` (the first item)."""
    participant = participant or ParticipantFactory(task=task)
    item = item or EvalItem.objects.filter(task=task).order_by("position").first()
    position = Assignment.objects.filter(participant=participant).count() + 1
    return Assignment.objects.create(
        task=task,
        participant=participant,
        item=item,
        status=status,
        position=position,
        **fields,
    )


# ---------------------------------------------------------------------------
# Task / pool (A) helpers: completed jobs and a DRAFT task whose pool was filled by services.pool
# ---------------------------------------------------------------------------


def make_eval_jobs(owner=None, n_jobs: int = 3, conditions: int = 2, *, spec=None, experiment=None):
    """``(experiment, condition_rows, jobs)``: completed agent jobs shaped like :func:`make_open_task`'s.

    Job ``i`` uses condition ``i % conditions`` (each condition its own model) and ``repeat_index =
    i // conditions``; ``conditions=0`` builds model-only jobs without an experiment. Every job
    implements ``spec`` (a new one by default) and carries a conformance verdict + checks.
    """
    owner = owner or UserFactory()
    if conditions and experiment is None:
        experiment = ExperimentFactory(created_by=owner)
    condition_rows = [
        ExperimentConditionFactory(experiment=experiment, model=LLMModelFactory(), label=f"Arm {index + 1}")
        for index in range(conditions)
    ]
    if experiment is not None and condition_rows and experiment.baseline_condition_id is None:
        experiment.baseline_condition = condition_rows[0]
        experiment.save(update_fields=["baseline_condition"])
    models = [] if conditions else [LLMModelFactory(), LLMModelFactory()]
    spec = spec or AppRequirementTemplateFactory(
        backend_requirements=["Users can register and log in"],
        frontend_requirements=["A list page shows the user's items"],
        api_endpoints=SPEC_API_ENDPOINTS,
        admin_api_endpoints=SPEC_ADMIN_API_ENDPOINTS,
    )
    spec_payload = _spec_dict(spec)
    jobs = []
    for index in range(n_jobs):
        condition = condition_rows[index % conditions] if conditions else None
        checks = _checks(index)
        jobs.append(
            GenerationJobFactory(
                created_by=owner,
                mode=GenerationJob.Mode.AGENT,
                status=GenerationJob.Status.COMPLETED,
                model=condition.model if condition is not None else models[index % len(models)],
                experiment=experiment if conditions else None,
                condition=condition,
                repeat_index=index // conditions if conditions else None,
                app_requirement=spec,
                resolved_bundle={"app_requirement": spec_payload, "harness": {"name": "opencode"}},
                metrics={"functional": _functional(checks)},
                result_data={
                    "files": {"backend/app.py": "from flask import Flask\n\napp = Flask(__name__)\n"},
                    "evaluation": {"checks": checks, "summary": {"by_category": _by_category(checks)}},
                },
                completed_at=timezone.now(),
            ),
        )
    return experiment, condition_rows, jobs


def make_draft_task(
    owner=None,
    n_apps: int = 3,
    mode: str = "single",
    conditions: int = 2,
    design: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> EvalTask:
    """A DRAFT task (validated design/settings) whose pool holds ``n_apps`` jobs added via ``services.pool``."""
    from backend.human_eval.services import pool as pool_service

    owner = owner or UserFactory()
    normalized_design, design_errors = design_service.validate_design(
        mode,
        design if design is not None else design_service.default_design(mode),
    )
    normalized_settings, settings_errors = design_service.validate_settings(settings or {})
    if design_errors or settings_errors:
        msg = f"make_draft_task: invalid design/settings: {design_errors} {settings_errors}"
        raise ValueError(msg)
    experiment, _, jobs = make_eval_jobs(owner, n_apps, conditions)
    task = EvalTaskFactory(
        created_by=owner,
        mode=mode,
        design=normalized_design,
        settings=normalized_settings,
        experiment=experiment,
    )
    pool_service.create_task_apps(task, jobs)
    return task


# ---------------------------------------------------------------------------
# Hosting (owner C)
# ---------------------------------------------------------------------------


class HostingContainerFactory(DjangoModelFactory):
    """A sample-app ``runtime.ContainerInstance`` (``llm-<hex>``, running by default). Pass ``generation_job``."""

    class Meta:
        model = "runtime.ContainerInstance"

    generation_job = None
    name = factory.Sequence(lambda n: f"llm-{n + 0xA0000:08x}")
    image = factory.LazyAttribute(lambda o: f"backend/{o.name}:latest")
    container_id = factory.Sequence(lambda n: f"docker{n:010d}")
    status = "running"
    app_port = factory.Sequence(lambda n: 9600 + n)
