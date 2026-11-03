"""factory_boy factories for the human-evaluation models, plus :func:`make_open_task`.

Shared by every human-eval test module. Later agents may APPEND new factories at
the end of this file (short, uniquely named block); never edit the existing ones.

:func:`make_open_task` builds a realistic *open* (frozen) task directly in the
database — completed v3 ``GenerationRun`` rows of one task version (with collected
``RunFile`` trees and a measured ``RunEvaluation``), task apps with snapshots taken by
``snapshot.full_snapshots`` (§5.2 + R1), items and the design hash — without calling
the task / pool / items services, so tests of any area can use it.
"""

from __future__ import annotations

import itertools
import random
from typing import Any

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from backend.generation import blobs
from backend.generation.models import choices
from backend.generation.testing import content as samples
from backend.generation.testing.content import fake_sha
from backend.generation.tests.factories import EvalSuiteFactory
from backend.generation.tests.factories import GenerationRunFactory
from backend.generation.tests.factories import HarnessPresetFactory
from backend.generation.tests.factories import RunEvaluationFactory
from backend.generation.tests.factories import RunFileFactory
from backend.generation.tests.factories import StudyCellFactory
from backend.generation.tests.factories import StudyConditionFactory
from backend.generation.tests.factories import StudyFactory
from backend.generation.tests.factories import StudyPlanFactory
from backend.generation.tests.factories import StudyUnitFactory
from backend.generation.tests.factories import TaskSpecVersionFactory
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

#: The task's endpoints (``contracts.library.Endpoint``); participants see non-admin and admin ones apart.
TASK_ENDPOINTS: list[dict[str, Any]] = [
    {"id": "auth.register", "method": "POST", "path": "/api/auth/register", "auth": "public"},
    {"id": "auth.login", "method": "POST", "path": "/api/auth/login", "auth": "public"},
    {"id": "items.list", "method": "GET", "path": "/api/items", "auth": "user"},
    {"id": "items.create", "method": "POST", "path": "/api/items", "auth": "user", "success_status": 201},
    {"id": "items.delete", "method": "DELETE", "path": "/api/items/{id}", "auth": "owner", "owner_entity": "Item"},
    {"id": "admin.users", "method": "GET", "path": "/api/admin/users", "auth": "admin"},
]
SPEC_API_ENDPOINTS = [endpoint for endpoint in TASK_ENDPOINTS if endpoint["auth"] != "admin"]
SPEC_ADMIN_API_ENDPOINTS = [endpoint for endpoint in TASK_ENDPOINTS if endpoint["auth"] == "admin"]
#: The task's ``ui`` requirements (the participants' ``frontend`` requirement items).
UI_REQUIREMENTS = ["A list page shows the user's items", "Users can create, edit and delete items"]
#: Files of every generated app: ``{path: (text, stored source LOC)}`` (app-scope LOC 120).
APP_FILES: dict[str, tuple[str, int]] = {
    "backend/app.py": ("from flask import Flask\n\napp = Flask(__name__)\n", 100),
    "frontend/src/App.jsx": ("export default function App() { return <h1>Items</h1>; }\n", 20),
}
PROSE_VARIANT = "standard"


def eval_task_content(*, title: str = "Item tracker") -> dict[str, Any]:
    """A valid ``TaskSpecContent`` with :data:`TASK_ENDPOINTS` and :data:`UI_REQUIREMENTS`."""
    content = samples.task_content(title=title)
    content["entities"] = [
        {
            "name": "Item",
            "fields": [{"name": "title", "type": "string", "required": True, "max_length": 200}],
            "owned_by_user": True,
        },
    ]
    content["endpoints"] = [dict(endpoint) for endpoint in TASK_ENDPOINTS]
    content["ui"] = [
        {"id": f"ui.{index}", "text": text, "endpoints": ["items.list"]} for index, text in enumerate(UI_REQUIREMENTS)
    ]
    content["acceptance"] = []
    content["probes"] = []
    return content


def eval_task_version(**kwargs):
    """A ``TaskSpecVersion`` of :func:`eval_task_content`."""
    kwargs.setdefault("content", eval_task_content())
    return TaskSpecVersionFactory(**kwargs)


def _passed(index: int) -> dict[str, bool]:
    """Per endpoint id, whether the tester's check of it passed (odd apps fail the admin endpoint)."""
    verdicts = {endpoint["id"]: True for endpoint in TASK_ENDPOINTS}
    if index % 2:
        verdicts[SPEC_ADMIN_API_ENDPOINTS[-1]["id"]] = False
    return verdicts


def eval_result(index: int) -> dict[str, Any]:
    """A measured ``EvalResult``: one functional check per task endpoint (see :func:`_passed`)."""
    verdicts = _passed(index)
    checks = [
        {
            "id": f"chk.{endpoint_id}",
            "category": "functional",
            "endpoint": endpoint_id,
            "status": "pass" if ok else "fail",
        }
        for endpoint_id, ok in verdicts.items()
    ]
    passed = sum(verdicts.values())
    total = len(verdicts)
    return {
        "tester_version": 1,
        "compiler_version": 1,
        "auth_mode": "contract_bearer",
        "checks": checks,
        "metrics": {
            "infra_ok": True,
            "agent_checks": total,
            "agent_passed": passed,
            "agent_pass_rate": round(passed / total, 4),
            "by_category": {"functional": {"n": total, "passed": passed}},
        },
        "measured": True,
    }


def add_app_files(run, files: dict[str, tuple[str, int]] | None = None) -> None:
    """Store ``files`` (default :data:`APP_FILES`) as the run's collected tree (blobs + ``RunFile`` rows)."""
    for path, (text, loc) in (APP_FILES if files is None else files).items():
        sha = blobs.put_text(text)
        RunFileFactory(
            run=run,
            path=path,
            blob=sha,
            size=len(text.encode("utf-8")),
            loc=loc,
            language="python" if path.endswith(".py") else "javascript",
        )


def evaluate_run(run, index: int = 0, *, suite=None, **fields):
    """A measured ``RunEvaluation`` of ``run`` with :func:`eval_result` (``fields`` override)."""
    values = {
        "status": choices.EvaluationStatus.MEASURED,
        "result": eval_result(index),
        "stages": {stage: {"status": "ok"} for stage in ("lock", "build", "start", "tester")},
    }
    values.update(fields)
    if suite is not None:
        values["eval_suite"] = suite
    return RunEvaluationFactory(run=run, **values)


def eval_run(owner, *, task_version, model, study=None, condition=None, cell=None, repeat_index=None, **fields):
    """A completed, tested ``app_agentic`` run of ``task_version`` (opencode harness, files not added)."""
    values = {
        "created_by": owner,
        "task_version": task_version,
        "model": model,
        "study": study,
        "condition": condition,
        "cell": cell,
        "repeat_index": repeat_index,
        "stage": choices.Stage.MAIN if cell is not None else "",
        "prose_variant": PROSE_VARIANT,
        "auth_mode": choices.AuthMode.CONTRACT_BEARER,
        "task_hash": fake_sha(f"task:{task_version.pk}:{PROSE_VARIANT}"),
        "harness_preset": HarnessPresetFactory(),
        "tested": True,
    }
    values.update(fields)
    return GenerationRunFactory(**values)


_EVAL_MODEL_SEQ = itertools.count(1)


def eval_model(**overrides):
    """An :class:`LLMModel` with pinned, source-free provider/id/name.

    ``LLMModelFactory`` draws them from Faker (``catch_phrase``/``slug``/``company``), whose words
    (``function``, ``interface``...) become blinding-scan model tokens that match ordinary source and
    make ``blinding_scan`` assertions order-dependent.
    """
    n = next(_EVAL_MODEL_SEQ)
    values = {
        "model_id": f"zqvendor/zqmodel{n}",
        "provider": "Zqvendor",
        "model_name": f"Zqvendor: Zqmodel {n}",
    }
    values.update(overrides)
    return LLMModelFactory(**values)


def make_study(owner, conditions: int, *, label: str = "Condition", task_version=None, study=None):
    """``(study, condition_rows, unit)``: a study with ``conditions`` conditions (each its own model).

    The first condition is the baseline (``is_baseline``); ``unit`` is the study unit of ``task_version``.
    """
    study = study or StudyFactory(created_by=owner)
    plan = StudyPlanFactory(study=study)
    condition_rows = [
        StudyConditionFactory(plan=plan, model=eval_model(), label=f"{label} {index + 1}", is_baseline=index == 0)
        for index in range(conditions)
    ]
    unit = StudyUnitFactory(plan=plan, task_version=task_version) if task_version is not None else None
    return study, condition_rows, unit


def make_eval_runs(owner=None, n_runs: int = 3, conditions: int = 2, *, task_version=None, study=None, label="Arm"):
    """``(study, condition_rows, runs)``: completed, evaluated app runs of one task version.

    Run ``i`` uses condition ``i % conditions`` (each condition its own model) in its own study cell with
    ``repeat_index = i // conditions``; ``conditions=0`` builds model-only runs without a study. Every
    run carries :data:`APP_FILES` and a measured evaluation (odd runs fail the admin endpoint).
    """
    owner = owner or UserFactory()
    task_version = task_version or eval_task_version()
    condition_rows: list[Any] = []
    unit = None
    if conditions:
        study, condition_rows, unit = make_study(
            owner,
            conditions,
            label=label,
            task_version=task_version,
            study=study,
        )
    else:
        study = None
    models = [] if conditions else [eval_model(), eval_model()]
    suite = EvalSuiteFactory()
    runs = []
    for index in range(n_runs):
        condition = condition_rows[index % conditions] if conditions else None
        repeat = index // conditions if conditions else None
        cell = StudyCellFactory(condition=condition, unit=unit, repeat_index=repeat) if condition else None
        run = eval_run(
            owner,
            task_version=task_version,
            model=condition.model if condition is not None else models[index % len(models)],
            study=study,
            condition=condition,
            cell=cell,
            repeat_index=repeat,
        )
        add_app_files(run)
        evaluate_run(run, index, suite=suite)
        runs.append(run)
    return study, condition_rows, runs


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
    """An OPEN (frozen) task with ``n_apps`` completed runs, task apps, snapshots and items.

    - ``conditions`` study conditions (each its own LLM model; the first is the baseline); app ``i``
      uses condition ``i % conditions`` and ``repeat_index = i // conditions``. ``conditions=0``
      builds model-only runs without a study.
    - every run implements the same task version, so all apps share one ``group_key``
      (``f"{slug}:{spec_content_hash[:12]}"``); labels are ``App 01``..; each run has a measured
      evaluation (odd apps fail the admin endpoint check).
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

    from backend.human_eval.services import snapshot as snapshot_service

    study, _, runs = make_eval_runs(owner, n_apps, conditions, label="Condition")
    snapshots_by_run = snapshot_service.full_snapshots(runs)

    now = timezone.now()
    task = EvalTaskFactory(
        created_by=owner,
        mode=mode,
        status=EvalTask.Status.OPEN,
        design=normalized_design,
        settings=normalized_settings,
        frozen_at=now,
        opened_at=now,
    )
    task.study_id = study.pk if study is not None else None
    task.design_hash = design_service.design_hash(mode, normalized_design, normalized_settings)
    task.save(update_fields=["design_hash", "study_id"])

    apps: list[EvalTaskApp] = []
    snapshots: dict[str, dict[str, Any]] = {}
    width = max(2, len(str(n_apps)))
    for index, run in enumerate(runs):
        snapshot = snapshots_by_run[str(run.pk)]
        app = EvalTaskAppFactory(
            task=task,
            job=run,
            label=f"App {index + 1:0{width}d}",
            group_key=snapshot_service.group_key(snapshot, None),
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
                "sources": ["frontend", "api"],
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
    run,
    tool_slug: str,
    count: int,
    *,
    kind: str = "container",
    analysis_mode: str = "static",
    category: str = "security",
    result_status: str = "completed",
    **fields,
) -> list:
    """``count`` findings of one tool for the GenerationRun ``run`` (a completed analysis run, one tool result)."""
    from backend.analysis.models import AnalysisRun
    from backend.analysis.tests.factories import AnalysisRunFactory
    from backend.analysis.tests.factories import AnalyzerToolFactory
    from backend.analysis.tests.factories import FindingFactory
    from backend.analysis.tests.factories import ToolResultFactory

    AnalyzerToolFactory(slug=tool_slug, name=f"Tool {tool_slug}", kind=kind, analysis_mode=analysis_mode)
    analysis = AnalysisRunFactory(generation_job=run, created_by=run.created_by, status=AnalysisRun.Status.COMPLETED)
    result = ToolResultFactory(run=analysis, tool_slug=tool_slug, status=result_status)
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
# Task / pool (A) helpers: a DRAFT task whose pool was filled by services.pool
# ---------------------------------------------------------------------------


def make_draft_task(
    owner=None,
    n_apps: int = 3,
    mode: str = "single",
    conditions: int = 2,
    design: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> EvalTask:
    """A DRAFT task (validated design/settings) whose pool holds ``n_apps`` runs added via ``services.pool``."""
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
    study, _, runs = make_eval_runs(owner, n_apps, conditions)
    task = EvalTaskFactory(
        created_by=owner,
        mode=mode,
        design=normalized_design,
        settings=normalized_settings,
    )
    if study is not None:
        task.study_id = study.pk
        task.save(update_fields=["study_id"])
    pool_service.create_task_apps(task, runs)
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
