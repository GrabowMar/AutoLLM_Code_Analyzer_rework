"""Slots API (spec section 9.6).

Mounted by ``backend.generation.api`` without a prefix, so routes declare their full path under
``/api/generation/`` (as in the spec's tables). Static paths are registered before parameterised ones.

Visibility: a user sees the parents they created and the slot sets they or the parent's owner created; staff
see everything. Errors are ``{"detail", "code", "data"?}`` with 400/403/404/409/422 (``address_stale`` and
``in_use`` are 409). Mutations run in a transaction; validation runs are enqueued on commit.

Operation ids (published for the frontend, WP-16): ``slots_parents_list``, ``slots_parents_draw``,
``slots_sets_create``, ``slots_sets_list``, ``slots_sets_get``, ``slots_sets_tree``, ``slots_sets_symbols``,
``slots_sets_endpoints``, ``slots_sets_components``, ``slots_sets_file``, ``slots_sets_preview_target``,
``slots_sets_create_target``, ``slots_targets_match``, ``slots_targets_revalidate``, ``slots_targets_delete``,
``slots_targets_get``, ``slots_targets_render``, ``slots_targets_variants``, ``slots_variants_diff``.
"""

import difflib
from collections import Counter
from functools import wraps
from typing import Any
from typing import Literal

from django.db import transaction
from django.db.models import Count
from django.db.models import Q
from django.db.models import QuerySet
from ninja import Query
from ninja import Router
from ninja import Schema
from ninja import Status
from pydantic import Field

from backend.generation import blobs
from backend.generation.contracts import PAGE_SIZE_MAX
from backend.generation.contracts import Ref
from backend.generation.contracts.slot import ContextPolicy
from backend.generation.contracts.slot import ParentSelection
from backend.generation.contracts.slot import SlotAddress
from backend.generation.models import GenerationRun
from backend.generation.models import Guidance
from backend.generation.models import PatternHit
from backend.generation.models import PatternRun
from backend.generation.models import PromptSet
from backend.generation.models import RunEvaluation
from backend.generation.models import SlotSet
from backend.generation.models import SlotTarget
from backend.generation.models import SlotVariantResult
from backend.generation.models import StudyUnit
from backend.generation.models import StyleDirective
from backend.generation.models import VariantClusterMember
from backend.generation.models.choices import RunKind
from backend.generation.models.choices import WorkStatus
from backend.generation.slot import parents as slot_parents
from backend.generation.slot import selection as slot_selection
from backend.generation.slot import validate as slot_validate
from backend.generation.slot.errors import InUseError
from backend.generation.slot.errors import SlotError
from backend.generation.slot.errors import SlotInvalidError
from backend.generation.slot.errors import SlotNotFoundError
from backend.generation.slot.evaluate import slot_pass_rate
from backend.generation.slot.freeze import freeze
from backend.generation.slot.part import ParentContext
from backend.generation.slot.prompt import render_stimuli
from backend.generation.slot.types import SlotTargetCreateIn
from backend.generation.slot.types import SlotTargetIn

router = Router(tags=["generation: slots"])

_SLOT_RUN_KINDS = (RunKind.SLOT_SINGLE_SHOT, RunKind.SLOT_AGENT)


class ErrorBody(Schema):
    detail: str
    code: str
    data: dict[str, Any] | None = None


_ERRORS: dict[int, Any] = {400: ErrorBody, 403: ErrorBody, 404: ErrorBody, 409: ErrorBody, 422: ErrorBody}


def _guard(view: Any) -> Any:
    """Map service errors to ``(status, {"detail", "code"})`` and run the view in a transaction."""

    @wraps(view)
    def wrapper(request: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            with transaction.atomic():
                return view(request, *args, **kwargs)
        except SlotError as exc:
            return Status(exc.status_code, exc.payload())
        except (ValueError, LookupError) as exc:
            status = 404 if isinstance(exc, LookupError) else 422
            return Status(status, {"detail": str(exc), "code": "not_found" if status == 404 else "invalid"})

    return wrapper


# ---------------------------------------------------------------------------
# Visibility and pagination
# ---------------------------------------------------------------------------


def _user(request: Any) -> Any:
    return request.auth


def _runs_visible(user: Any) -> QuerySet[GenerationRun]:
    qs = GenerationRun.objects.all()
    return qs if getattr(user, "is_staff", False) else qs.filter(created_by=user)


def _sets_visible(user: Any) -> QuerySet[SlotSet]:
    qs = SlotSet.objects.select_related(
        "parent_run__model",
        "parent_run__stack_profile",
        "parent_run__starter",
        "task_version__task",
        "index",
        "parent_eval",
    )
    if getattr(user, "is_staff", False):
        return qs
    return qs.filter(Q(created_by=user) | Q(parent_run__created_by=user))


def _slot_set(user: Any, slot_set_id: Any) -> SlotSet:
    slot_set = _sets_visible(user).filter(pk=slot_set_id).first()
    if slot_set is None:
        msg = "slot set not found"
        raise SlotNotFoundError(msg)
    return slot_set


def _target(user: Any, target_id: Any) -> SlotTarget:
    target = (
        SlotTarget.objects.select_related("slot_set__parent_run", "slot_set__task_version", "slot_set__index")
        .filter(pk=target_id, slot_set__in=_sets_visible(user))
        .first()
    )
    if target is None:
        msg = "slot target not found"
        raise SlotNotFoundError(msg)
    return target


def _page(items: list[Any], total: int, page: int, page_size: int) -> dict[str, Any]:
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def _bounds(page: int, page_size: int) -> tuple[int, int, int, int]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), PAGE_SIZE_MAX)
    return page, page_size, (page - 1) * page_size, page * page_size


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _ref(entity: Any) -> str | None:
    return entity.ref if entity is not None else None


def _eval_metrics(run: GenerationRun) -> dict[str, Any]:
    return ((run.metrics or {}).get("eval")) or {}


def _parent_row(run: GenerationRun, slot_sets: int) -> dict[str, Any]:
    metrics = _eval_metrics(run)
    return {
        "run_id": str(run.pk),
        "task": _ref(run.task_version),
        "stack": _ref(run.stack_profile),
        "model": run.model.model_id if run.model is not None else None,
        "harness": run.harness_preset.harness if run.harness_preset is not None else None,
        "outcome": run.outcome,
        "agent_pass_rate": metrics.get("agent_pass_rate"),
        "probes_exploited": metrics.get("probes_exploited"),
        "cost_usd": (run.metrics or {}).get("cost_usd"),
        "frozen": slot_sets > 0,
        "slot_sets": slot_sets,
        "created_at": run.created_at.isoformat(),
    }


def parent_levels(parent: GenerationRun) -> dict[str, str | None]:
    """The levels a slot study cannot vary: fixed by the parent (pinned refs / enum values; ``None`` when unset)."""
    return {
        "stack_profile": parent.stack_profile.pinned_ref if parent.stack_profile is not None else None,
        "starter_level": parent.starter.level if parent.starter is not None else None,
        "auth_mode": parent.auth_mode or None,
        "prose_variant": parent.prose_variant or None,
    }


def _slot_set_out(slot_set: SlotSet) -> dict[str, Any]:
    parent_eval = slot_set.parent_eval
    return {
        "id": str(slot_set.pk),
        "name": slot_set.name,
        "parent_run": str(slot_set.parent_run_id),
        "task": _ref(slot_set.task_version),
        "parent_model": slot_set.parent_model,
        "parent_harness": slot_set.parent_harness,
        "parent_levels": parent_levels(slot_set.parent_run),
        "frozen_tree_hash": slot_set.frozen_tree_hash,
        "parent_manifest_hash": slot_set.parent_manifest_hash,
        "index_stats": slot_set.index.stats,
        "parent_eval": (
            {
                "id": parent_eval.pk,
                "status": parent_eval.status,
                "eval_suite": parent_eval.eval_suite.pinned_ref,
                "agent_pass_rate": ((parent_eval.result or {}).get("metrics") or {}).get("agent_pass_rate"),
            }
            if parent_eval is not None
            else None
        ),
        "selection": slot_set.selection,
        "notes": slot_set.notes,
        "created_at": slot_set.created_at.isoformat(),
    }


def _target_out(target: SlotTarget, *, study_kind: str = "exploratory") -> dict[str, Any]:
    usable, reason = slot_validate.target_usable(target)
    return {
        "id": str(target.pk),
        "slot_set": str(target.slot_set_id),
        "label": target.label,
        "kind": target.kind,
        "address": target.address,
        "match_key": target.match_key,
        "language": target.language,
        "original_sha": target.original_sha,
        "default_hole_mode": target.default_hole_mode,
        "default_keep_docstring": target.default_keep_docstring,
        "default_context_policy": target.default_context_policy,
        "edit_policy": target.edit_policy,
        "allowlist": target.allowlist,
        "interface": target.interface,
        "linked_endpoints": target.linked_endpoints,
        "linked_checks": target.linked_checks,
        "linked_checks_source": target.linked_checks_source,
        "linked_checks_diff": target.linked_checks_diff,
        "linked_probes": target.linked_probes,
        "validation": target.validation,
        "leak": target.leak,
        "target_hash": target.target_hash,
        "original_run": str(target.original_run_id) if target.original_run_id else None,
        "usable": usable,
        "unusable_reason": reason,
        "issues": [i.model_dump(mode="json") for i in slot_selection.target_issues(target, study_kind)],
        "created_at": target.created_at.isoformat(),
    }


def _variants_summary(target: SlotTarget) -> dict[str, Any]:
    runs = GenerationRun.objects.filter(slot_target=target, kind__in=_SLOT_RUN_KINDS)
    outcomes = Counter(runs.values_list("outcome", flat=True))
    parse = Counter(SlotVariantResult.objects.filter(slot_target=target).values_list("parse_status", flat=True))
    return {"n": sum(outcomes.values()), "by_outcome": dict(outcomes), "by_parse_status": dict(parse)}


# ---------------------------------------------------------------------------
# Parents
# ---------------------------------------------------------------------------


class ParentsQuery(Schema):
    task: str | None = None
    stack: str | None = None
    model: str | None = None
    harness: str | None = None
    outcome: Literal["tested", "build_fail", "start_fail"] = "tested"
    study: str | None = None
    page: int = 1
    page_size: int = 50


@router.get("/slots/parents/", response={200: dict, **_ERRORS}, operation_id="slots_parents_list")
@_guard
def list_parents(request: Any, filters: Query[ParentsQuery]) -> Any:
    page, size, start, end = _bounds(filters.page, filters.page_size)
    qs = slot_parents.pool_for(
        task=filters.task,
        stack=filters.stack,
        models=[filters.model] if filters.model else None,
        harnesses=[filters.harness] if filters.harness else None,
        studies=[filters.study] if filters.study else None,
        outcome=filters.outcome,
        base=_runs_visible(_user(request)),
    )
    qs = qs.annotate(n_slot_sets=Count("slot_sets"))
    total = qs.count()
    items = [_parent_row(run, run.n_slot_sets) for run in qs[start:end]]
    return Status(200, _page(items, total, page, size))


@router.post("/slots/parents/draw/", response={200: dict, **_ERRORS}, operation_id="slots_parents_draw")
@_guard
def draw_parents(request: Any, payload: ParentSelection) -> Any:
    # The pool is the user's visible runs before ranking: k, pool_size and the seeded order hold for them.
    result = slot_parents.draw_with_record(payload, base=_runs_visible(_user(request)))
    return Status(
        200,
        {
            "parents": [str(r.pk) for r in result.parents],
            "seed": result.seed,
            "pool_size": result.pool_size,
        },
    )


# ---------------------------------------------------------------------------
# Slot sets
# ---------------------------------------------------------------------------


class SlotSetIn(Schema):
    parent_run: str
    name: str = Field(default="", max_length=200)
    allow_unbuilt_parent: bool = False
    selection: ParentSelection | None = None
    pool_size: int | None = None


@router.post("/slots/sets/", response={201: dict, **_ERRORS}, operation_id="slots_sets_create")
@_guard
def create_slot_set(request: Any, payload: SlotSetIn) -> Any:
    parent = _runs_visible(_user(request)).filter(pk=payload.parent_run).first()
    if parent is None:
        msg = "parent run not found"
        raise SlotNotFoundError(msg)
    slot_set = freeze(
        parent,
        name=payload.name,
        user=_user(request),
        selection=payload.selection,
        allow_unbuilt_parent=payload.allow_unbuilt_parent,
        pool_size=payload.pool_size,
    )
    return Status(201, _slot_set_out(_sets_visible(_user(request)).get(pk=slot_set.pk)))


class SlotSetsQuery(Schema):
    parent_run: str | None = None
    task: str | None = None
    page: int = 1
    page_size: int = 50


@router.get("/slots/sets/", response={200: dict, **_ERRORS}, operation_id="slots_sets_list")
@_guard
def list_slot_sets(request: Any, filters: Query[SlotSetsQuery]) -> Any:
    page, size, start, end = _bounds(filters.page, filters.page_size)
    qs = _sets_visible(_user(request))
    if filters.parent_run:
        qs = qs.filter(parent_run_id=filters.parent_run)
    if filters.task:
        parsed = Ref.parse(filters.task)
        qs = qs.filter(task_version__task__slug=parsed.slug, task_version__version=parsed.version)
    qs = qs.order_by("-created_at", "id")
    return Status(200, _page([_slot_set_out(s) for s in qs[start:end]], qs.count(), page, size))


@router.get("/slots/sets/{slot_set_id}/", response={200: dict, **_ERRORS}, operation_id="slots_sets_get")
@_guard
def get_slot_set(request: Any, slot_set_id: str) -> Any:
    slot_set = _slot_set(_user(request), slot_set_id)
    targets = [_target_out(t) for t in slot_set.targets.select_related("slot_set").order_by("created_at", "id")]
    return Status(200, {**_slot_set_out(slot_set), "targets": targets})


@router.get("/slots/sets/{slot_set_id}/tree/", response={200: list, **_ERRORS}, operation_id="slots_sets_tree")
@_guard
def slot_set_tree(request: Any, slot_set_id: str) -> Any:
    parent = ParentContext(_slot_set(_user(request), slot_set_id))
    counts = Counter(symbol.path for symbol in parent.index.symbols if symbol.kind != "module")
    indexed = {f.path: f for f in parent.index.files}
    rows = []
    for entry in parent.tree:
        info = indexed.get(entry.path)
        rows.append(
            {
                "path": entry.path,
                "language": entry.language,
                "loc": info.loc if info is not None else None,
                "origin": entry.origin,
                "is_test": parent.tree.is_test(entry.path),
                "excluded": entry.excluded,
                "parsed": info.parsed if info is not None else False,
                "symbols": counts.get(entry.path, 0),
            },
        )
    return Status(200, rows)


class SymbolsQuery(Schema):
    path: str | None = None
    kind: str | None = None


@router.get("/slots/sets/{slot_set_id}/symbols/", response={200: list, **_ERRORS}, operation_id="slots_sets_symbols")
@_guard
def slot_set_symbols(request: Any, slot_set_id: str, filters: Query[SymbolsQuery]) -> Any:
    index = ParentContext(_slot_set(_user(request), slot_set_id)).index
    symbols = [
        s.model_dump(mode="json")
        for s in index.symbols
        if (not filters.path or s.path == filters.path) and (not filters.kind or s.kind == filters.kind)
    ]
    return Status(200, symbols)


@router.get(
    "/slots/sets/{slot_set_id}/endpoints/",
    response={200: list, **_ERRORS},
    operation_id="slots_sets_endpoints",
)
@_guard
def slot_set_endpoints(request: Any, slot_set_id: str) -> Any:
    from backend.generation.evaluation import acceptance

    slot_set = _slot_set(_user(request), slot_set_id)
    parent = ParentContext(slot_set)
    probes = Counter(p.get("endpoint") for p in parent.task_content.get("probes", []))
    rows = []
    for endpoint in parent.index.endpoints:
        spec_id = endpoint.spec_endpoint_id
        checks = acceptance.checks_for_endpoints(slot_set.task_version, [spec_id]) if spec_id else []
        rows.append(
            {
                **endpoint.model_dump(mode="json"),
                "linked_checks": len(checks),
                "linked_probes": probes.get(spec_id, 0),
            },
        )
    return Status(200, rows)


@router.get(
    "/slots/sets/{slot_set_id}/components/",
    response={200: list, **_ERRORS},
    operation_id="slots_sets_components",
)
@_guard
def slot_set_components(request: Any, slot_set_id: str) -> Any:
    index = ParentContext(_slot_set(_user(request), slot_set_id)).index
    return Status(200, [c.model_dump(mode="json") for c in index.components])


class FileQuery(Schema):
    path: str


@router.get("/slots/sets/{slot_set_id}/file/", response={200: dict, **_ERRORS}, operation_id="slots_sets_file")
@_guard
def slot_set_file(request: Any, slot_set_id: str, filters: Query[FileQuery]) -> Any:
    parent = ParentContext(_slot_set(_user(request), slot_set_id))
    if filters.path not in parent.tree:
        msg = f"{filters.path} is not in the parent tree"
        raise SlotNotFoundError(msg)
    data = parent.tree.read(filters.path)
    if b"\x00" in data:
        msg = f"{filters.path} is a binary file"
        raise SlotInvalidError(msg)
    symbols = [s.model_dump(mode="json") for s in parent.index.symbols if s.path == filters.path]
    return Status(
        200,
        {
            "path": filters.path,
            "text": data.decode("utf-8", errors="replace"),
            "language": parent.tree.files[filters.path].language,
            "symbols": symbols,
        },
    )


@router.post(
    "/slots/sets/{slot_set_id}/preview-target/",
    response={200: dict, **_ERRORS},
    operation_id="slots_sets_preview_target",
)
@_guard
def preview_target(request: Any, slot_set_id: str, payload: SlotTargetIn) -> Any:
    return Status(200, slot_selection.preview(_slot_set(_user(request), slot_set_id), payload))


@router.post(
    "/slots/sets/{slot_set_id}/targets/",
    response={201: dict, 200: dict, **_ERRORS},
    operation_id="slots_sets_create_target",
)
@_guard
def create_target(request: Any, slot_set_id: str, payload: SlotTargetCreateIn) -> Any:
    slot_set = _slot_set(_user(request), slot_set_id)
    before = set(slot_set.targets.values_list("pk", flat=True))
    target = slot_selection.create_target(slot_set, payload, user=_user(request))
    return Status((200 if target.pk in before else 201), _target_out(target))


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------


class MatchIn(Schema):
    slot_sets: list[str] | Literal["same_task"] = "same_task"


@router.post("/slots/targets/{target_id}/match/", response={200: dict, **_ERRORS}, operation_id="slots_targets_match")
@_guard
def match_target(request: Any, target_id: str, payload: MatchIn) -> Any:
    target = _target(_user(request), target_id)
    if payload.slot_sets == "same_task":
        sets: list[SlotSet] | str = list(
            _sets_visible(_user(request))
            .filter(task_version=target.slot_set.task_version)
            .exclude(pk=target.slot_set_id)
            .order_by("created_at", "id"),
        )
    else:
        sets = list(_sets_visible(_user(request)).filter(pk__in=payload.slot_sets).order_by("created_at", "id"))
        missing = set(payload.slot_sets) - {str(s.pk) for s in sets}
        if missing:
            msg = f"slot sets not found: {', '.join(sorted(missing))}"
            raise SlotNotFoundError(msg)
    result = slot_selection.match(target, sets, user=_user(request))
    return Status(200, {"created": [_target_out(t) for t in result["created"]], "unmatched": result["unmatched"]})


@router.post(
    "/slots/targets/{target_id}/revalidate/",
    response={200: dict, **_ERRORS},
    operation_id="slots_targets_revalidate",
)
@_guard
def revalidate_target(request: Any, target_id: str) -> Any:
    target = _target(_user(request), target_id)
    if GenerationRun.objects.filter(slot_target=target, kind__in=_SLOT_RUN_KINDS).exists():
        msg = "the target already has variants; create a new target to validate again"
        raise InUseError(msg)
    return Status(200, _target_out(slot_validate.revalidate(target, user=_user(request))))


@router.delete("/slots/targets/{target_id}/", response={204: None, **_ERRORS}, operation_id="slots_targets_delete")
@_guard
def delete_target(request: Any, target_id: str) -> Any:
    target = _target(_user(request), target_id)
    if StudyUnit.objects.filter(slot_target=target).exists():
        msg = "a study uses this target"
        raise InUseError(msg)
    if GenerationRun.objects.filter(slot_target=target, kind__in=_SLOT_RUN_KINDS).exists():
        msg = "the target has variants"
        raise InUseError(msg)
    references = GenerationRun.objects.filter(slot_target=target, kind=RunKind.REFERENCE)
    if references.filter(lease_owner__gt="").exists():
        msg = "a validation run of the target is being evaluated"
        raise InUseError(msg)
    target.original_run = None
    target.save(update_fields=["original_run"])
    references.delete()
    target.delete()
    return Status(204, None)


@router.get("/slots/targets/{target_id}/", response={200: dict, **_ERRORS}, operation_id="slots_targets_get")
@_guard
def get_target(request: Any, target_id: str) -> Any:
    target = _target(_user(request), target_id)
    if (target.validation or {}).get("status") == "pending":
        slot_validate.validate_target(target)
    original = None
    if target.original_run_id:
        latest = (
            RunEvaluation.objects.filter(run_id=target.original_run_id, retest_of__isnull=True)
            .order_by("-created_at")
            .first()
        )
        original = {
            "run_id": str(target.original_run_id),
            "evaluation": latest.pk if latest is not None else None,
            "slot_pass_rate": slot_pass_rate(latest, target),
        }
    return Status(
        200,
        {
            **_target_out(target),
            "original_text": blobs.get_text(target.original_blob) if blobs.exists(target.original_blob) else None,
            "original": original,
            "variants_summary": _variants_summary(target),
        },
    )


class RenderIn(Schema):
    prompt_set: str
    executor: Literal["single_shot", "restricted_agent"] = "single_shot"
    hole_mode: Literal["stub", "remove", "rewrite"] | None = None
    keep_docstring: bool | None = None
    context_policy: ContextPolicy | None = None
    guidance: str | None = None
    style: str | None = None


def _library(model: Any, ref: str | None) -> Any:
    if not ref:
        return None
    parsed = Ref.parse(ref)
    qs = model.objects.filter(slug=parsed.slug, version=parsed.version)
    if parsed.hash12:
        qs = qs.filter(content_hash__startswith=parsed.hash12)
    entity = qs.first()
    if entity is None:
        msg = f"{ref} not found"
        raise SlotNotFoundError(msg)
    return entity


@router.post(
    "/slots/targets/{target_id}/render/",
    response={200: dict, **_ERRORS},
    operation_id="slots_targets_render",
)
@_guard
def render_target(request: Any, target_id: str, payload: RenderIn) -> Any:
    target = _target(_user(request), target_id)
    prompt_set = _library(PromptSet, payload.prompt_set)
    if prompt_set.mode != "slot":
        msg = f"{payload.prompt_set} is not a slot prompt set"
        raise SlotInvalidError(msg)
    return Status(
        200,
        render_stimuli(
            target,
            prompt_set,
            executor=payload.executor,
            hole_mode=payload.hole_mode,
            keep_docstring=payload.keep_docstring,
            context_policy=payload.context_policy,
            guidance=_library(Guidance, payload.guidance),
            style=_library(StyleDirective, payload.style),
        ),
    )


class VariantsQuery(Schema):
    study: str | None = None
    condition: int | None = None
    cluster: int | None = None
    feature: str | None = None
    page: int = 1
    page_size: int = 50


def _pattern_run(target: SlotTarget, study: str | None) -> PatternRun | None:
    qs = PatternRun.objects.filter(status=WorkStatus.COMPLETED, is_live=False, clusters__slot_target=target)
    if study:
        qs = qs.filter(study_id=study)
    return qs.order_by("-completed_at", "-created_at").first()


@router.get(
    "/slots/targets/{target_id}/variants/",
    response={200: dict, **_ERRORS},
    operation_id="slots_targets_variants",
)
@_guard
def target_variants(request: Any, target_id: str, filters: Query[VariantsQuery]) -> Any:
    target = _target(_user(request), target_id)
    page, size, start, end = _bounds(filters.page, filters.page_size)
    runs = GenerationRun.objects.filter(slot_target=target, kind__in=_SLOT_RUN_KINDS)
    if filters.study:
        runs = runs.filter(study_id=filters.study)
    if filters.condition is not None:
        runs = runs.filter(condition_id=filters.condition)
    pattern_run = _pattern_run(target, filters.study)
    if filters.cluster is not None:
        runs = runs.filter(pk__in=VariantClusterMember.objects.filter(cluster_id=filters.cluster).values("run_id"))
    if filters.feature and pattern_run is not None:
        present = PatternHit.objects.filter(pattern_run=pattern_run, feature_key=filters.feature, status="present")
        runs = runs.filter(pk__in=present.values("run_id"))
    runs = runs.order_by("created_at", "id")
    total = runs.count()
    page_runs = list(runs[start:end])
    results = {r.run_id: r for r in SlotVariantResult.objects.filter(run__in=page_runs)}
    clusters: dict[Any, int] = {}
    features: dict[Any, dict[str, str]] = {}
    if pattern_run is not None:
        for member in VariantClusterMember.objects.filter(cluster__pattern_run=pattern_run, run__in=page_runs):
            clusters[member.run_id] = member.cluster_id
        for hit in PatternHit.objects.filter(pattern_run=pattern_run, run__in=page_runs):
            features.setdefault(hit.run_id, {})[f"{hit.feature_key}:{hit.scope}"] = hit.status
    latest: dict[Any, RunEvaluation] = {}
    for evaluation in RunEvaluation.objects.filter(run__in=page_runs, retest_of__isnull=True).order_by("created_at"):
        latest[evaluation.run_id] = evaluation
    items = []
    for run in page_runs:
        result = results.get(run.pk)
        items.append(
            {
                "run_id": str(run.pk),
                "kind": run.kind,
                "condition": run.condition_id,
                "repeat_index": run.repeat_index,
                "outcome": run.outcome,
                "outcome_reason": run.outcome_reason,
                "parse_status": result.parse_status if result is not None else None,
                "normalized_sha": (result.normalized_sha or None) if result is not None else None,
                "cluster_id": clusters.get(run.pk),
                "features": features.get(run.pk, {}),
                "slot_pass_rate": slot_pass_rate(latest.get(run.pk), target),
                "diff_vs_original": result.diff_vs_original if result is not None else None,
                "edit_violation": result.edit_violation if result is not None else None,
            },
        )
    return Status(200, _page(items, total, page, size))


class DiffQuery(Schema):
    against: str = "original"


def _replacement_text(result: SlotVariantResult) -> str:
    from backend.generation.slot.assemble import recorded_replacement

    replacement = recorded_replacement(result.run)
    if replacement is None:
        return ""
    if isinstance(replacement, dict):
        return "\n".join(replacement[path] for path in sorted(replacement))
    return replacement


@router.get("/slots/variants/{run_id}/diff/", response={200: dict, **_ERRORS}, operation_id="slots_variants_diff")
@_guard
def variant_diff(request: Any, run_id: str, filters: Query[DiffQuery]) -> Any:
    run = _runs_visible(_user(request)).filter(pk=run_id, kind__in=_SLOT_RUN_KINDS).first()
    result = SlotVariantResult.objects.select_related("slot_target", "run").filter(run=run).first() if run else None
    if result is None:
        msg = "variant not found"
        raise SlotNotFoundError(msg)
    target = result.slot_target
    replacement = _replacement_text(result)
    if filters.against == "original":
        other, label = blobs.get_text(target.original_blob), "original"
    else:
        other_result = (
            SlotVariantResult.objects.select_related("run").filter(run_id=filters.against, slot_target=target).first()
        )
        if other_result is None:
            msg = "the other variant is not a variant of the same target"
            raise SlotNotFoundError(msg)
        other, label = _replacement_text(other_result), filters.against
    unified = "".join(
        difflib.unified_diff(
            other.splitlines(keepends=True),
            replacement.splitlines(keepends=True),
            fromfile=label,
            tofile=str(run.pk),  # type: ignore[union-attr]
        ),
    )
    path = SlotAddress.model_validate(target.address).path
    return Status(200, {"unified": unified, "replacement": replacement, "original": other, "path": path})
