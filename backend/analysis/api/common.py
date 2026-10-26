"""Shared helpers for the analysis API views: ownership, pagination, errors, throttled background work.

Ownership: every object is owner-scoped; staff may read (and manage) all of
it, via :func:`backend.common.ownership.get_owned_or_staff_or_403`. Objects a
non-staff user does not own are invisible (404), never 403, so ids do not leak.
"""

from __future__ import annotations

import functools
import json
import logging
import math
from typing import TYPE_CHECKING
from typing import Any

from django.core.cache import cache
from django.db import close_old_connections
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from pydantic import ValidationError

from backend.analysis.models import AnalysisProfile
from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalysisStudy
from backend.common.ownership import get_owned_or_staff_or_403
from backend.common.threading import dispatch_in_thread

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Sequence

    from django.db.models import QuerySet

    from backend.analysis.engine.catalog import ConfigError
    from backend.analysis.research.study import StudyError

logger = logging.getLogger(__name__)

RECONCILE_CACHE_KEY = "analysis:api:reconcile"
RECONCILE_INTERVAL_S = 60
IMAGE_STATUS_CACHE_KEY = "analysis:api:image-status"
IMAGE_STATUS_TTL_S = 15
IMAGE_BUILD_LOCK_KEY = "analysis:api:image-build"
# "build requested" is shown until the worker reports building (sandbox.is_building).
IMAGE_BUILD_FLAG_TTL_S = 120
CAPACITY_CACHE_KEY = "analysis:api:capacity"
CAPACITY_TTL_S = 10


def is_staff(user) -> bool:
    return bool(getattr(user, "is_staff", False))


# --------------------------------------------------------------------------
# Visibility
# --------------------------------------------------------------------------


def visible_runs(user, qs: QuerySet | None = None) -> QuerySet:
    qs = AnalysisRun.objects.all() if qs is None else qs
    return qs if is_staff(user) else qs.filter(created_by=user)


def visible_studies(user, qs: QuerySet | None = None) -> QuerySet:
    qs = AnalysisStudy.objects.all() if qs is None else qs
    return qs if is_staff(user) else qs.filter(created_by=user)


def visible_profiles(user, qs: QuerySet | None = None) -> QuerySet:
    qs = AnalysisProfile.objects.all() if qs is None else qs
    return qs if is_staff(user) else qs.filter(Q(is_system=True) | Q(created_by=user))


def visible_jobs(user, qs: QuerySet | None = None) -> QuerySet:
    from backend.generation.models import GenerationJob

    qs = GenerationJob.objects.all() if qs is None else qs
    return qs if is_staff(user) else qs.filter(created_by=user)


def get_run(user, run_id, *, qs: QuerySet | None = None, write: bool = False):
    """A visible run or 404; *write* re-checks owner-or-staff."""
    run = get_object_or_404(visible_runs(user, qs), id=run_id)
    if write:
        get_owned_or_staff_or_403(AnalysisRun, user=user, owner_field="created_by", id=run.id)
    return run


def get_study(user, study_id, *, qs: QuerySet | None = None, write: bool = False):
    study = get_object_or_404(visible_studies(user, qs), id=study_id)
    if write:
        get_owned_or_staff_or_403(AnalysisStudy, user=user, owner_field="created_by", id=study.id)
    return study


def get_profile(user, profile_id, *, qs: QuerySet | None = None):
    return get_object_or_404(visible_profiles(user, qs), id=profile_id)


def default_profile() -> AnalysisProfile | None:
    """The latest active version of the seeded research-default system profile (None when not seeded)."""
    from backend.analysis.engine import plan as plan_mod

    return (
        AnalysisProfile.objects.filter(slug=plan_mod.DEFAULT_PROFILE_SLUG, is_system=True, is_archived=False)
        .order_by("-version")
        .first()
    )


# --------------------------------------------------------------------------
# Pagination (an out-of-range page is empty, never silently clamped: clients
# that page until an empty page must terminate)
# --------------------------------------------------------------------------


def paginate(source: QuerySet | Sequence[Any], page: int, per_page: int) -> tuple[list[Any], dict[str, int]]:
    total = len(source) if isinstance(source, (list, tuple)) else source.count()
    pages = max(1, math.ceil(total / per_page))
    start = (page - 1) * per_page
    items = list(source[start : start + per_page]) if start < total else []
    return items, {"total": total, "page": page, "per_page": per_page, "pages": pages}


def page_payload(items: list[Any], meta: dict[str, int], serialize: Callable[[Any], Any]) -> dict[str, Any]:
    return {"items": [serialize(item) for item in items], **meta}


# --------------------------------------------------------------------------
# Error responses
# --------------------------------------------------------------------------


def validation_error(errors: dict[str, str], *, location: str = "body", prefix: str = "") -> JsonResponse:
    """422 in Ninja's shape (``detail: [{loc, msg}]``) plus ``field_errors`` keyed by dotted path.

    ``loc`` is ``[location, "payload", *path]`` so the frontend's
    ``apiFieldErrors`` strips the request location and argument name and
    gets the dotted payload path back.
    """
    field_errors: dict[str, str] = {}
    detail = []
    for path, msg in errors.items():
        full = f"{prefix}.{path}" if prefix and path else (prefix or path)
        field_errors[full] = msg
        parts = [p for p in full.split(".") if p]
        loc = [location, "payload", *parts] if location == "body" else [location, *parts]
        detail.append({"loc": loc, "msg": msg, "type": "value_error"})
    return JsonResponse({"detail": detail, "field_errors": field_errors}, status=422)


def config_error(exc: ConfigError, *, prefix: str = "") -> JsonResponse:
    return validation_error(exc.field_errors or {"": str(exc)}, prefix=prefix)


def query_error(param: str, msg: str) -> JsonResponse:
    return validation_error({param: msg}, location="query")


def study_error(exc: StudyError) -> JsonResponse:
    return JsonResponse({"detail": exc.message, "code": exc.code, **exc.extra}, status=exc.status)


def conflict(detail: str, **extra: Any) -> JsonResponse:
    return JsonResponse({"detail": detail, **extra}, status=409)


def optional_body_openapi(schema) -> dict[str, Any]:
    """``openapi_extra`` documenting an optional JSON body parsed by :func:`optional_body`."""
    return {
        "requestBody": {"required": False, "content": {"application/json": {"schema": schema.model_json_schema()}}},
    }


def optional_body(request, schema):
    """``(payload, None)`` or ``(None, error response)`` for an *optional* JSON body.

    Ninja rejects an empty body even when the payload argument is optional,
    and clients (``apiFetch``) POST actions without one, so action endpoints
    with optional parameters parse it here: empty means the schema defaults.
    """
    if "json" not in (request.content_type or ""):
        # A bodiless form post (e.g. multipart with no fields) carries no options.
        if request.POST:
            return None, JsonResponse({"detail": "Send options as a JSON object"}, status=415)
        return schema.model_validate({}), None
    raw = (request.body or b"").strip()
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return None, JsonResponse({"detail": "Cannot parse request body"}, status=400)
    if not isinstance(data, dict):
        return None, validation_error({"": "the body must be a JSON object"})
    try:
        return schema.model_validate(data), None
    except ValidationError as exc:
        errors = {".".join(str(p) for p in err["loc"]): err["msg"] for err in exc.errors()}
        return None, validation_error(errors)


def reject_unstorable_query(view: Callable[..., Any]) -> Callable[..., Any]:
    """Router view decorator: a NUL character in the query string (or path) is a 422, not a 500.

    Postgres cannot take NUL in a text parameter, so a filter such as
    ``?q=%00`` would otherwise fail inside the database. Request bodies are
    checked field by field by :class:`backend.analysis.api.schema.InputSchema`.
    """

    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        if "\x00" in request.path:
            return validation_error({"path": "must not contain NUL characters"}, location="path")
        for key, values in request.GET.lists():
            if "\x00" in key or any("\x00" in value for value in values):
                return validation_error(
                    {key.replace("\x00", "") or "query": "must not contain NUL characters"},
                    location="query",
                )
        return view(request, *args, **kwargs)

    return wrapper


def csv_param(value: str | None) -> list[str]:
    """``"a,b, c"`` -> ``["a", "b", "c"]`` (empty items dropped)."""
    return [part.strip() for part in (value or "").split(",") if part.strip()]


# --------------------------------------------------------------------------
# Background work started by requests (never inside them)
# --------------------------------------------------------------------------


def _in_thread(name: str, target: Callable[[], Any]) -> None:
    def run() -> None:
        close_old_connections()
        try:
            target()
        except Exception:
            logger.exception("Background task %s failed", name)
        finally:
            close_old_connections()

    dispatch_in_thread(run, name=name)


def after_commit_in_thread(name: str, target: Callable[[], Any]) -> None:
    """Run *target* on a daemon thread once the request transaction commits."""
    transaction.on_commit(lambda: _in_thread(name, target))


def maybe_reconcile() -> bool:
    """Opportunistic ``executor.reconcile()``, at most once a minute across processes sharing the cache."""
    if not cache.add(RECONCILE_CACHE_KEY, 1, RECONCILE_INTERVAL_S):
        return False

    def reconcile() -> None:
        from backend.analysis.engine import executor

        executor.reconcile()

    after_commit_in_thread("analysis-reconcile", reconcile)
    return True


def image_status(*, refresh: bool = False) -> dict[str, Any]:
    """``sandbox.image_status()`` cached briefly (it talks to the daemon); never raises."""
    from backend.analysis.engine import sandbox

    if not refresh and (cached := cache.get(IMAGE_STATUS_CACHE_KEY)) is not None:
        status = dict(cached)
    else:
        try:
            status = dict(sandbox.image_status())
            status.setdefault("daemon_ok", True)
        except Exception as exc:  # noqa: BLE001 - a dead daemon is reported, not raised
            logger.warning("image_status failed: %s", exc)
            status = {"daemon_ok": False, "built": False, "error": str(exc)}
        cache.set(IMAGE_STATUS_CACHE_KEY, status, IMAGE_STATUS_TTL_S)
    status["build_requested"] = bool(cache.get(IMAGE_BUILD_LOCK_KEY))
    status["building"] = bool(status.get("building")) or status["build_requested"]
    return status


def capacity() -> dict[str, Any]:
    """``sandbox.capacity()`` cached briefly; never raises."""
    from backend.analysis.engine import sandbox

    if (cached := cache.get(CAPACITY_CACHE_KEY)) is not None:
        return cached
    try:
        value = dict(sandbox.capacity())
    except Exception as exc:  # noqa: BLE001 - capacity is a hint
        value = {"error": str(exc)}
    cache.set(CAPACITY_CACHE_KEY, value, CAPACITY_TTL_S)
    return value


def key_present(user) -> bool:
    from backend.credentials.services.resolver import MissingApiKeyError
    from backend.credentials.services.resolver import get_openrouter_key

    try:
        return bool(get_openrouter_key(user))
    except MissingApiKeyError:
        return False
