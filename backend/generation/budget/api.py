"""Budget API (spec section 9.8), mounted under ``/api/generation/`` without a prefix.

Routes declare their full paths; static paths are registered before parameterised ones. Every route needs
an authenticated user (project-wide auth). Studies are visible to their creator and to staff (404
otherwise). Errors are ``{"detail", "code"}`` with 400/403/404/409/422.

Operation ids (published in docs/design/SAMPLE_GEN_V3_CHANGES.md): ``budget_estimate``, ``budget_study``,
``budget_study_ledger``, ``budget_me``, ``budget_header``, ``budget_caps_list``, ``budget_caps_create``,
``budget_caps_update``, ``budget_caps_delete``, ``budget_priors``, ``budget_policy_explain``,
``budget_knobs``.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from decimal import Decimal
from typing import Any
from typing import Literal

from django.db.models import Q
from django.http import JsonResponse
from ninja import Query
from ninja import Router
from ninja import Schema
from pydantic import Field

from backend.generation.budget import estimate as estimate_mod
from backend.generation.budget import policy as policy_mod
from backend.generation.budget import report
from backend.generation.contracts.plan import ResolvedPlan
from backend.generation.contracts.resources import KEY_LIMITS
from backend.generation.contracts.resources import TIERS
from backend.generation.contracts.resources import preset_content
from backend.generation.models import CostPrior
from backend.generation.models import ResourcePolicy
from backend.generation.models import SpendCap
from backend.generation.models import SpendLedgerEntry
from backend.generation.models import Study
from backend.generation.models import StudyPlan
from backend.generation.models.choices import CapScope

router = Router(tags=["generation: budget"])

PAGE_SIZE_MAX = 200


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


def _error(exc: ApiError) -> JsonResponse:
    return JsonResponse({"detail": exc.detail, "code": exc.code}, status=exc.status)


def _user(request: Any) -> Any:
    return getattr(request, "auth", None) or getattr(request, "user", None)


def _is_staff(user: Any) -> bool:
    return bool(getattr(user, "is_staff", False))


def _study(request: Any, study_id: str) -> Study:
    try:
        uuid.UUID(str(study_id))
    except ValueError:
        raise ApiError(404, "not_found", "Study not found") from None
    study = Study.objects.select_related("resource_policy", "active_plan").filter(pk=study_id).first()
    user = _user(request)
    if study is None or (not _is_staff(user) and study.created_by_id != getattr(user, "pk", None)):
        raise ApiError(404, "not_found", "Study not found")
    return study


def _visible_policies(user: Any):
    qs = ResourcePolicy.objects.all()
    if _is_staff(user):
        return qs
    return qs.filter(Q(is_system=True) | Q(visibility="shared") | Q(created_by=user))


def _policy(request: Any, ref: str) -> ResourcePolicy:
    slug, _, rest = ref.partition("@")
    version, _, hash12 = rest.partition("#")
    if not slug or not version.isdigit():
        raise ApiError(400, "bad_ref", f"expected slug@version, got {ref!r}")
    row = _visible_policies(_user(request)).filter(slug=slug, version=int(version)).first()
    if row is None or (hash12 and not row.content_hash.startswith(hash12)):
        raise ApiError(404, "not_found", f"budget preset {ref} not found")
    return row


# =====================================================================================
# Estimate
# =====================================================================================


class EstimateIn(Schema):
    design: dict[str, Any] | None = None
    study: uuid.UUID | None = None
    plan_hash: str | None = None
    cap: float | Literal["auto"] | None = None


@router.post("/budget/estimate/", operation_id="budget_estimate")
def budget_estimate(request, payload: EstimateIn):
    """``Estimate`` for ``{design}``, ``{study}`` (its current design) or ``{study, plan_hash}`` (a frozen plan)."""
    try:
        plan = _plan_for(request, payload)
        try:
            estimate = estimate_mod.estimate_plan(plan, cap_override=payload.cap)
        except ValueError as exc:
            raise ApiError(422, "estimate_unavailable", str(exc)) from None
    except ApiError as exc:
        return _error(exc)
    return estimate.model_dump(mode="json")


def _plan_for(request: Any, payload: EstimateIn) -> ResolvedPlan:
    from backend.generation import blobs

    if payload.study is not None and payload.plan_hash:
        study = _study(request, str(payload.study))
        row = StudyPlan.objects.filter(study=study, plan_hash=payload.plan_hash).first()
        if row is None:
            raise ApiError(404, "not_found", "plan not found")
        return ResolvedPlan.model_validate(blobs.get_json(row.payload_blob))
    if payload.study is not None:
        study = _study(request, str(payload.study))
    elif payload.design is not None:
        study = _draft_study(request, payload.design)
    else:
        raise ApiError(400, "bad_request", "send {design}, {study} or {study, plan_hash}")
    try:
        from backend.generation.study.plan import resolve

        return resolve(study, persist=False)
    except NotImplementedError as exc:
        raise ApiError(422, "resolver_unavailable", str(exc)) from None
    except (ValueError, KeyError) as exc:
        raise ApiError(422, "design_invalid", str(exc)) from None


def _draft_study(request: Any, design: dict[str, Any]) -> Study:
    from backend.generation.contracts.plan import Design

    try:
        parsed = Design.model_validate(design)
    except Exception as exc:  # noqa: BLE001 — pydantic ValidationError and friends
        raise ApiError(422, "design_invalid", str(exc)) from None
    policy = _policy(request, parsed.resource_policy)
    user = _user(request)
    return Study(
        name="estimate",
        slug="estimate",
        design=parsed.model_dump(mode="json"),
        resource_policy=policy,
        created_by=user,
    )


# =====================================================================================
# Study spend
# =====================================================================================


@router.get("/budget/studies/{study_id}/ledger/", operation_id="budget_study_ledger")
def budget_study_ledger(
    request,
    study_id: str,
    run: uuid.UUID | None = None,
    kind: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    try:
        study = _study(request, study_id)
    except ApiError as exc:
        return _error(exc)
    page = max(1, page)
    page_size = min(max(1, page_size), PAGE_SIZE_MAX)
    qs = SpendLedgerEntry.objects.filter(study=study).order_by("-created_at", "-id")
    if run is not None:
        qs = qs.filter(run_id=run)
    if kind:
        qs = qs.filter(kind=kind)
    total = qs.count()
    rows = qs[(page - 1) * page_size : page * page_size]
    return {
        "items": [_entry_out(entry) for entry in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _entry_out(entry: SpendLedgerEntry) -> dict[str, Any]:
    return {
        "id": entry.pk,
        "kind": entry.kind,
        "run": str(entry.run_id) if entry.run_id else None,
        "attempt": entry.attempt,
        "endpoint": entry.endpoint_id,
        "model_slug": entry.model_slug,
        "amount_usd": float(entry.amount_usd),
        "list_cost_usd": float(entry.list_cost_usd),
        "tokens_in": entry.tokens_in,
        "tokens_out": entry.tokens_out,
        "requests": entry.requests,
        "source": entry.source,
        "dedupe_key": entry.dedupe_key,
        "created_at": entry.created_at.isoformat(),
    }


@router.get("/budget/studies/{study_id}/", operation_id="budget_study")
def budget_study(request, study_id: str):
    """The live spend payload of section 4.7."""
    try:
        study = _study(request, study_id)
    except ApiError as exc:
        return _error(exc)
    return report.study_payload(study)


@router.get("/budget/me/", operation_id="budget_me")
def budget_me(request):
    return report.me_payload(_user(request))


@router.get("/budget/header/", operation_id="budget_header")
def budget_header(request):
    return report.header_payload(_user(request))


# =====================================================================================
# Spend caps
# =====================================================================================


class SpendCapIn(Schema):
    scope: Literal["user", "endpoint", "model", "global"]
    user: int | None = None
    endpoint: int | None = None
    model_slug: str = ""
    period: Literal["5h", "day", "week", "month"]
    unit: Literal["usd", "list_usd", "tokens"]
    limit: Decimal = Field(ge=0)
    action: Literal["block", "warn"] = "block"
    on_hit: Literal["pause"] = "pause"
    enabled: bool = True


class SpendCapPatch(Schema):
    limit: Decimal | None = Field(default=None, ge=0)
    action: Literal["block", "warn"] | None = None
    enabled: bool | None = None
    period: Literal["5h", "day", "week", "month"] | None = None
    unit: Literal["usd", "list_usd", "tokens"] | None = None


def _cap_out(cap: SpendCap) -> dict[str, Any]:
    return {
        "id": cap.pk,
        "scope": cap.scope,
        "user": cap.user_id,
        "endpoint": cap.endpoint_id,
        "model_slug": cap.model_slug,
        "period": cap.period,
        "unit": cap.unit,
        "limit": float(cap.limit),
        "action": cap.action,
        "on_hit": cap.on_hit,
        "enabled": cap.enabled,
        "created_by": cap.created_by_id,
        "staff_cap": bool(cap.created_by_id and cap.created_by.is_staff) if cap.created_by_id else True,
    }


def _staff_ceiling(user: Any, period: str, unit: str, *, exclude: int | None = None) -> Decimal | None:
    """The lowest staff-set user cap for ``user`` in (period, unit): a user may only go below it."""
    qs = SpendCap.objects.filter(scope=CapScope.USER, user=user, period=period, unit=unit, enabled=True).filter(
        Q(created_by__is_staff=True) | Q(created_by__isnull=True),
    )
    if exclude is not None:
        qs = qs.exclude(pk=exclude)
    return min((cap.limit for cap in qs), default=None)


def _check_user_cap(user: Any, period: str, unit: str, limit: Decimal, *, exclude: int | None = None) -> None:
    ceiling = _staff_ceiling(user, period, unit, exclude=exclude)
    if ceiling is not None and limit > ceiling:
        raise ApiError(403, "above_staff_cap", f"limit {limit} is above the staff cap {ceiling} for {period}/{unit}")


@router.get("/budget/caps/", operation_id="budget_caps_list")
def budget_caps_list(request):
    user = _user(request)
    qs = SpendCap.objects.select_related("created_by").order_by("scope", "period", "pk")
    if not _is_staff(user):
        qs = qs.filter(Q(scope=CapScope.USER, user=user) | ~Q(scope=CapScope.USER))
    return [_cap_out(cap) for cap in qs]


@router.post("/budget/caps/", operation_id="budget_caps_create")
def budget_caps_create(request, payload: SpendCapIn):
    user = _user(request)
    try:
        if not _is_staff(user):
            if payload.scope != CapScope.USER or (payload.user not in (None, user.pk)):
                raise ApiError(403, "staff_only", "only staff set global, endpoint, model or other users' caps")
            _check_user_cap(user, payload.period, payload.unit, payload.limit)
        target_user = (payload.user or user.pk) if payload.scope == CapScope.USER else None
        if payload.scope == CapScope.ENDPOINT and payload.endpoint is None:
            raise ApiError(400, "endpoint_required", "an endpoint cap needs endpoint")
        if payload.scope == CapScope.MODEL and not payload.model_slug:
            raise ApiError(400, "model_required", "a model cap needs model_slug")
    except ApiError as exc:
        return _error(exc)
    cap = SpendCap.objects.create(
        scope=payload.scope,
        user_id=target_user,
        endpoint_id=payload.endpoint if payload.scope in (CapScope.ENDPOINT, CapScope.MODEL) else None,
        model_slug=payload.model_slug if payload.scope == CapScope.MODEL else "",
        period=payload.period,
        unit=payload.unit,
        limit=payload.limit,
        action=payload.action,
        on_hit=payload.on_hit,
        enabled=payload.enabled,
        created_by=user,
    )
    return _cap_out(SpendCap.objects.select_related("created_by").get(pk=cap.pk))


def _own_cap(request: Any, cap_id: int) -> SpendCap:
    user = _user(request)
    cap = SpendCap.objects.select_related("created_by").filter(pk=cap_id).first()
    if cap is None:
        raise ApiError(404, "not_found", "spend cap not found")
    if _is_staff(user):
        return cap
    if cap.scope != CapScope.USER or cap.user_id != user.pk:
        raise ApiError(404, "not_found", "spend cap not found")
    if cap.created_by_id != user.pk:
        raise ApiError(403, "staff_cap", "a cap set by staff can only be changed by staff")
    return cap


@router.patch("/budget/caps/{cap_id}/", operation_id="budget_caps_update")
def budget_caps_update(request, cap_id: int, payload: SpendCapPatch):
    try:
        cap = _own_cap(request, cap_id)
        changes = payload.dict(exclude_unset=True)
        for name, value in changes.items():
            if value is not None:
                setattr(cap, name, value)
        if not _is_staff(_user(request)):
            _check_user_cap(_user(request), cap.period, cap.unit, cap.limit, exclude=cap.pk)
    except ApiError as exc:
        return _error(exc)
    cap.save()
    return _cap_out(cap)


@router.delete("/budget/caps/{cap_id}/", operation_id="budget_caps_delete")
def budget_caps_delete(request, cap_id: int):
    try:
        cap = _own_cap(request, cap_id)
    except ApiError as exc:
        return _error(exc)
    cap.delete()
    return {"deleted": cap_id}


# =====================================================================================
# Priors, policies, knobs
# =====================================================================================


@router.get("/budget/priors/", operation_id="budget_priors")
def budget_priors(request, harness: str | None = None, model: str | None = None):
    qs = CostPrior.objects.all().order_by("key")
    rows = []
    for prior in qs:
        parts = prior.key.split("|")
        if harness and (len(parts) < 2 or parts[1] != harness):
            continue
        if model and model not in parts:
            continue
        rows.append(
            {
                "key": prior.key,
                "n": prior.n,
                "cost_p10": prior.cost_p10,
                "cost_p50": prior.cost_p50,
                "cost_p90": prior.cost_p90,
                "list_cost_p50": prior.list_cost_p50,
                "requests_p50": prior.requests_p50,
                "prompt_tokens_p50": prior.prompt_tokens_p50,
                "cached_tokens_p50": prior.cached_tokens_p50,
                "completion_tokens_p50": prior.completion_tokens_p50,
                "wall_s_p50": prior.wall_s_p50,
                "wall_s_p90": prior.wall_s_p90,
                "price_at": prior.price_at,
                "updated_at": prior.updated_at.isoformat(),
                "usable": prior.n >= 5,
            },
        )
    return rows


class ExplainQuery(Schema):
    model: str | None = None
    harness: str | None = None
    study: uuid.UUID | None = None
    executor: str | None = None


@router.get("/budget/policies/{ref}/explain/", operation_id="budget_policy_explain")
def budget_policy_explain(request, ref: str, query: Query[ExplainQuery]):
    """Effective values, clamps, notes, lint issues, treatment view and ``generation_policy_hash``."""
    try:
        row = _policy(request, ref)
        model = None
        if query.model:
            from backend.llm_models.models import LLMModel

            model = LLMModel.objects.select_related("endpoint").filter(model_id=query.model).first()
            if model is None:
                raise ApiError(404, "not_found", f"model {query.model} not found")
        ctx = None
        if query.study is not None:
            ctx = policy_mod.context_for_study(_study(request, str(query.study)))
        if query.executor:
            ctx = replace(ctx or policy_mod.StudyPolicyContext(), executor=query.executor)
        payload = policy_mod.explain(row, model=model, harness=query.harness, study_ctx=ctx)
    except ApiError as exc:
        return _error(exc)
    payload["ref"] = row.pinned_ref
    payload["tier"] = row.tier
    payload["summary"] = policy_mod.summary_line(row, name=row.name, version=row.version, tier=row.tier)
    payload["diff_from_tier"] = policy_mod.diff_from_tier(row, row.tier if row.tier in TIERS else None)
    return payload


@router.get("/budget/knobs/", operation_id="budget_knobs")
def budget_knobs(request):
    """``KNOB_META`` for the Budget preset editor, the key limits and the three tier presets."""
    return {
        "knobs": policy_mod.knob_meta_payload(),
        "key_limits": list(KEY_LIMITS),
        "tiers": {tier: preset_content(tier).model_dump(mode="json") for tier in TIERS},
    }
