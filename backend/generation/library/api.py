"""Library API (spec section 9.1), mounted at ``/api/generation/`` without a prefix.

``{kind}`` is one of ``tasks``, ``stacks``, ``starters``, ``styles``, ``guidance``, ``prompts``,
``prompt-sets``, ``harness-presets``, ``sampling-presets``, ``resource-policies``, ``eval-suites`` or
``detectors``. Static paths (``blobs``, ``packages``, the task oracle and starter tree routes and
``{kind}/validate/``) are registered before the parameterised ones, so ``validate`` is not a usable slug.

Every list is filtered by visibility (own, shared and system rows; staff see everything); errors are
``{"detail", "code", "issues"?}``. The operation ids (``library_*``) are published in the CHANGES file.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from datetime import datetime
from typing import Any
from typing import Final

from django.http import HttpRequest
from django.http import HttpResponse
from django.utils.text import slugify
from ninja import File
from ninja import Router
from ninja import Schema
from ninja.files import UploadedFile  # noqa: TC002 - ninja reads the annotation at runtime
from ninja.responses import Status
from pydantic import Field

from backend.generation.contracts import PAGE_SIZE_MAX
from backend.generation.contracts import ErrorOut
from backend.generation.library import packages
from backend.generation.library import service
from backend.generation.models import Starter
from backend.generation.models import TaskSpecVersion

router = Router(tags=["generation: library"])

ERRORS: Final = {400: ErrorOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut}


# =====================================================================================
# Schemas
# =====================================================================================


class UserRefOut(Schema):
    id: int
    username: str


class LibraryItemOut(Schema):
    kind: str
    slug: str
    version: int
    name: str
    description: str
    schema_version: int
    content: dict[str, Any]
    content_hash: str
    pinned_ref: str
    derived_from: str | None = None
    is_system: bool
    is_archived: bool
    visibility: str
    created_by: UserRefOut | None = None
    created_at: datetime
    tags: list[str] = []
    columns: dict[str, Any] = {}
    derived: dict[str, Any] | None = None
    lint: dict[str, Any] | None = None
    unchanged: bool | None = None


class LibraryPageOut(Schema):
    items: list[LibraryItemOut]
    total: int
    page: int
    page_size: int


class VersionSummaryOut(Schema):
    version: int
    name: str
    content_hash: str
    pinned_ref: str
    derived_from: str | None = None
    is_archived: bool
    created_by: UserRefOut | None = None
    created_at: datetime


class LibraryDetailOut(Schema):
    latest: LibraryItemOut
    versions: list[VersionSummaryOut]


class LibraryCreateIn(Schema):
    slug: str
    name: str = ""
    description: str = ""
    content: dict[str, Any]
    visibility: str = "private"


class LibraryVersionIn(Schema):
    base_version: int
    content: dict[str, Any]
    name: str | None = None
    description: str | None = None
    visibility: str | None = None


class ArchiveIn(Schema):
    archived: bool = True
    version: int | None = None  # only this version (default: every version of the slug)


class VisibilityIn(Schema):
    visibility: str


class ValidateIn(Schema):
    content: dict[str, Any]
    system: bool = False


class ValidateOut(Schema):
    ok: bool
    issues: list[dict[str, Any]]
    content_hash: str | None = None
    content: dict[str, Any] | None = None
    derived: dict[str, Any] | None = None


class StudyUsageOut(Schema):
    id: str
    name: str
    status: str
    version: int


class UsageOut(Schema):
    studies: list[StudyUsageOut]
    runs: int


class DiffEntryOut(Schema):
    path: str
    a: Any = None
    b: Any = None


class DiffOut(Schema):
    unified: str
    structured: list[DiffEntryOut]


class ForkIn(Schema):
    new_slug: str
    name: str = ""
    version: int | None = None  # the version to fork (default: the latest visible one)
    description: str | None = None


class OracleQueueIn(Schema):
    stacks: list[str] = Field(min_length=1)


class OracleQueueOut(Schema):
    queued_runs: list[str]


class TreeFileOut(Schema):
    path: str
    size: int | None = None
    blob: str
    mode: int


class StarterTreeOut(Schema):
    files: list[TreeFileOut]
    dockerfile: str


class ExportIn(Schema):
    refs: list[str] = Field(min_length=1)
    include_dependencies: bool = True


class ImportOut(Schema):
    mode: str
    created: list[dict[str, Any]]
    reused: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]


# =====================================================================================
# Helpers
# =====================================================================================


def _errors(view: Callable[..., Any]) -> Callable[..., Any]:
    """Turn a :class:`service.LibraryError` into ``Status(code, {"detail", "code", "issues"?})``."""

    @functools.wraps(view)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return view(*args, **kwargs)
        except service.LibraryError as exc:
            return Status(exc.status_code, exc.payload())

    return wrapper


def _user(request: HttpRequest) -> Any:
    return getattr(request, "auth", None) or request.user


def _user_out(user: Any) -> dict[str, Any] | None:
    if user is None:
        return None
    return {"id": user.pk, "username": user.get_username()}


def entity_tags(kind: str, row: Any) -> list[str]:
    """Filterable tags of a row (``?tag=``): derived from its content, never free text."""
    content = row.content or {}
    tags: list[str] = []
    if kind == "task":
        tags += [str(content.get("category", "")), *(content.get("features") or []), *(content.get("surfaces") or [])]
    elif kind == "prompt":
        tags += [str(content.get("role", "")), *(content.get("modes") or []), *(content.get("tags") or [])]
    elif kind == "prompt_set":
        tags += [str(content.get("mode", "")), str(content.get("system_prompt_mode", ""))]
    elif kind == "stack":
        tags += [
            str((content.get("backend") or {}).get("framework", "")),
            str((content.get("frontend") or {}).get("framework", "")),
            str(content.get("database", "")),
            str(content.get("choice_mode", "")),
        ]
    elif kind == "starter":
        tags += [str(content.get("level", ""))]
    elif kind == "style":
        tags += [str(content.get("system", "")), *(content.get("compatible_frontends") or [])]
    elif kind == "guidance":
        tags += [str(content.get("security_hint", ""))]
    elif kind == "harness_preset":
        tags += [str(content.get("harness", "")), "controlled" if content.get("controlled") else "native"]
    elif kind == "sampling":
        tags += [str(content.get("policy", ""))]
    elif kind == "resource_policy":
        tags += [str(row.tier)]
    elif kind == "detector":
        tags += [str(content.get("engine", "")), str(content.get("polarity", ""))]
        tags += list(content.get("languages") or []) + list(content.get("cwe") or [])
    return sorted({tag for tag in tags if tag})


def _columns(kind: str, row: Any) -> dict[str, Any]:
    if kind == "task":
        return {"structured_hash": row.structured_hash, "prose_hashes": row.prose_hashes}
    columns: dict[str, Any] = {}
    for name in type(row).CONTENT_COLUMNS:
        columns[name] = getattr(row, name)
    if kind == "starter":
        columns["tree_hash"] = row.tree_hash
        columns["stack_profile"] = row.stack_profile.pinned_ref
    if kind == "resource_policy":
        columns["tier"] = row.tier
    return columns


def item_out(row: Any, *, with_lint: bool = False, unchanged: bool | None = None) -> dict[str, Any]:
    """``LibraryItemOut`` of one version."""
    kind = service.kind_of(row)
    header = row.task if isinstance(row, TaskSpecVersion) else row
    out: dict[str, Any] = {
        "kind": kind,
        "slug": service.slug_of(row),
        "version": row.version,
        "name": row.name,
        "description": (row.content or {}).get("summary", "") if kind == "task" else row.description,
        "schema_version": row.schema_version,
        "content": row.content,
        "content_hash": row.content_hash,
        "pinned_ref": row.pinned_ref,
        "derived_from": row.derived_from.pinned_ref if row.derived_from_id else None,
        "is_system": header.is_system,
        "is_archived": header.is_archived,
        "visibility": header.visibility,
        "created_by": _user_out(row.created_by),
        "created_at": row.created_at,
        "tags": entity_tags(kind, row),
        "columns": _columns(kind, row),
        "unchanged": unchanged,
    }
    if kind == "task":
        out["derived"] = row.derived
        out["lint"] = row.lint
    elif with_lint:
        out["lint"] = service.task_lint_payload(service.lint_entity(kind, row.content, system=header.is_system))
    return out


def _summary(row: Any) -> dict[str, Any]:
    header = row.task if isinstance(row, TaskSpecVersion) else row
    return {
        "version": row.version,
        "name": row.name,
        "content_hash": row.content_hash,
        "pinned_ref": row.pinned_ref,
        "derived_from": row.derived_from.pinned_ref if row.derived_from_id else None,
        "is_archived": header.is_archived,
        "created_by": _user_out(row.created_by),
        "created_at": row.created_at,
    }


def _visible_slug_rows(kind: str, slug: str, user: Any) -> list[Any]:
    rows = [row for row in service.slug_rows(kind, slug).select_related("created_by") if service.can_view(row, user)]
    if not rows:
        msg = f"no {kind} {slug!r}"
        raise service.LibraryError(msg, status=404, code="not_found")
    return rows


def _visible_version(kind: str, slug: str, version: int, user: Any) -> Any:
    row = service.slug_rows(kind, slug).filter(version=version).first()
    if row is None or not service.can_view(row, user):
        msg = f"no {kind} {slug}@{version}"
        raise service.LibraryError(msg, status=404, code="not_found")
    return row


def _latest_visible(kind: str, slug: str, user: Any) -> Any:
    rows = _visible_slug_rows(kind, slug, user)
    active = [row for row in rows if not service._header(row).is_archived]  # noqa: SLF001
    return (active or rows)[0]


def _page_args(page: int, page_size: int) -> tuple[int, int]:
    if page < 1:
        msg = "page must be >= 1"
        raise service.LibraryError(msg, status=400, code="invalid")
    if not 1 <= page_size <= PAGE_SIZE_MAX:
        msg = f"page_size must be 1..{PAGE_SIZE_MAX}"
        raise service.LibraryError(msg, status=400, code="invalid")
    return page, page_size


# =====================================================================================
# Static routes first: blobs, packages, task oracle, starter tree
# =====================================================================================


@router.get("/library/blobs/{sha}/", response=ERRORS, operation_id="library_blob")
@_errors
def library_blob(request: HttpRequest, sha: str) -> Any:
    """Raw text of a blob referenced by a starter the caller can see."""
    from backend.generation import blobs

    user = _user(request)
    starters = service.visible("starter", user).only("content", "is_system", "visibility", "created_by")
    if not any(
        (entry or {}).get("blob") == sha
        for row in starters.iterator()
        for entry in ((row.content or {}).get("files") or {}).values()
    ):
        msg = "no such blob"
        raise service.LibraryError(msg, status=404, code="not_found")
    try:
        data = blobs.get_bytes(sha)
    except (LookupError, ValueError, OSError) as exc:
        msg = "the blob is missing from the store"
        raise service.LibraryError(msg, status=404, code="not_found") from exc
    return HttpResponse(data.decode("utf-8", errors="replace"), content_type="text/plain; charset=utf-8")


@router.post("/library/packages/export/", response=ERRORS, operation_id="library_package_export")
@_errors
def library_package_export(request: HttpRequest, payload: ExportIn) -> Any:
    """A v4 package (YAML) of the given ``kind:slug@v`` refs and their dependencies."""
    package = packages.export_package(
        payload.refs,
        user=_user(request),
        include_dependencies=payload.include_dependencies,
    )
    response = HttpResponse(packages.dump_package(package), content_type=packages.PACKAGE_MEDIA_TYPE)
    first = slugify(payload.refs[0].split(":", 1)[-1].split("#", 1)[0].replace("@", "-v")) or "library"
    response["Content-Disposition"] = f'attachment; filename="{first}.library.yaml"'
    return response


@router.post(
    "/library/packages/import/",
    response={200: ImportOut, 400: ErrorOut, 403: ErrorOut, 404: ErrorOut, 409: dict, 422: ErrorOut},
    operation_id="library_package_import",
)
@_errors
def library_package_import(
    request: HttpRequest,
    mode: str = "dry_run",
    on_conflict: str = "rename",
    file: UploadedFile | None = File(None),  # noqa: B008 - ninja's declaration style
) -> Any:
    """Import a v4 package (multipart ``file``, or the raw YAML body); ``mode=dry_run`` changes nothing."""
    if file is not None:
        if file.size and file.size > packages.MAX_PACKAGE_BYTES:
            msg = "the package is too large"
            raise service.LibraryError(msg, status=400, code="invalid")
        text = file.read()
    else:
        text = request.body
    if not text:
        msg = "no package: send a multipart 'file' or a YAML body"
        raise service.LibraryError(msg, status=400, code="invalid")
    try:
        result = packages.import_package(text, user=_user(request), mode=mode, on_conflict=on_conflict)
    except service.LibraryError as exc:
        refused = getattr(exc, "result", None)
        if refused is not None:
            return Status(409, {**exc.payload(), **refused.as_dict()})
        raise
    return result.as_dict()


@router.get(
    "/library/tasks/{slug}/versions/{version}/oracle/",
    response={200: dict, **ERRORS},
    operation_id="library_task_oracle_get",
)
@_errors
def library_task_oracle_get(request: HttpRequest, slug: str, version: int) -> Any:
    """``TaskSpecVersion.oracle``: ``{stack_ref: {status, run_id, eval_suite_ref, at}}``."""
    return _visible_version("task", slug, version, _user(request)).oracle or {}


@router.post(
    "/library/tasks/{slug}/versions/{version}/oracle/",
    response={200: OracleQueueOut, 501: ErrorOut, **ERRORS},
    operation_id="library_task_oracle_queue",
)
@_errors
def library_task_oracle_queue(request: HttpRequest, slug: str, version: int, payload: OracleQueueIn) -> Any:
    """Queue oracle validation runs of a task version on the given stacks (staff; ``evaluation/baseline.py``)."""
    user = _user(request)
    if not getattr(user, "is_staff", False):
        msg = "only staff can queue oracle runs"
        raise service.LibraryError(msg, status=403, code="permission_denied")
    task_version = _visible_version("task", slug, version, user)
    stacks = [service.resolve_ref(ref, kind="stack", user=user) for ref in payload.stacks]
    try:
        from backend.generation.evaluation import baseline

        queued = baseline.queue_oracle_runs(task_version, stacks, user=user)  # type: ignore[attr-defined]
    except (AttributeError, NotImplementedError) as exc:
        return Status(501, {"detail": f"oracle validation is not available yet: {exc}", "code": "not_implemented"})
    return {"queued_runs": [str(run_id) for run_id in queued]}


@router.get(
    "/library/starters/{slug}/versions/{version}/tree/",
    response={200: StarterTreeOut, **ERRORS},
    operation_id="library_starter_tree",
)
@_errors
def library_starter_tree(request: HttpRequest, slug: str, version: int) -> Any:
    """The starter's files (path, size, blob, mode) and its Dockerfile text."""
    from backend.generation import blobs
    from backend.generation.models import Blob

    row: Starter = _visible_version("starter", slug, version, _user(request))
    files = row.content.get("files") or {}
    sizes = dict(
        Blob.objects.filter(sha256__in=[entry["blob"] for entry in files.values()]).values_list("sha256", "size"),
    )
    dockerfile_entry = files.get(row.content.get("dockerfile", "Dockerfile"))
    dockerfile = ""
    if dockerfile_entry:
        try:
            dockerfile = blobs.get_text(dockerfile_entry["blob"])
        except (LookupError, ValueError, OSError):
            dockerfile = ""
    return {
        "files": [
            {"path": path, "size": sizes.get(entry["blob"]), "blob": entry["blob"], "mode": entry["mode"]}
            for path, entry in sorted(files.items())
        ],
        "dockerfile": dockerfile,
    }


# =====================================================================================
# /library/{kind}/
# =====================================================================================


@router.get("/library/{kind}/", response={200: LibraryPageOut, **ERRORS}, operation_id="library_list")
@_errors
def library_list(
    request: HttpRequest,
    kind: str,
    q: str = "",
    tag: str = "",
    archived: bool = False,
    mine: bool = False,
    page: int = 1,
    page_size: int = 50,
) -> Any:
    """The latest visible version per slug (``archived=true`` includes archived slugs)."""
    from django.db.models import Q

    page, page_size = _page_args(page, page_size)
    lib_kind = service.kind_from_api(kind)
    user = _user(request)
    qs = service.visible(lib_kind, user)
    is_task = lib_kind == "task"
    prefix = "task__" if is_task else ""
    if not archived:
        qs = qs.filter(**{f"{prefix}is_archived": False})
    if mine:
        qs = qs.filter(**{f"{prefix}created_by": user})
    if q:
        text = Q(**{f"{prefix}slug__icontains": q}) | Q(name__icontains=q)
        if not is_task:
            text |= Q(description__icontains=q)
        qs = qs.filter(text)
    slug_field = "task__slug" if is_task else "slug"
    latest_ids = qs.order_by(slug_field, "-version").distinct(slug_field).values("id")
    model = service.model_for(lib_kind)
    rows = model.objects.filter(id__in=latest_ids).select_related("created_by", "derived_from")
    if is_task:
        rows = rows.select_related("task")
    rows = list(rows.order_by(slug_field))
    if tag:
        rows = [row for row in rows if tag in entity_tags(lib_kind, row)]
    start = (page - 1) * page_size
    return {
        "items": [item_out(row) for row in rows[start : start + page_size]],
        "total": len(rows),
        "page": page,
        "page_size": page_size,
    }


@router.post("/library/{kind}/validate/", response={200: ValidateOut, **ERRORS}, operation_id="library_validate")
@_errors
def library_validate(request: HttpRequest, kind: str, payload: ValidateIn) -> Any:
    """``{ok, issues, content_hash, content, derived?}`` for draft content (refs may be ``slug@v``)."""
    result = service.validate_content(service.kind_from_api(kind), payload.content, system=payload.system)
    result["issues"] = [issue.model_dump(mode="json") for issue in result["issues"]]
    return result


@router.post("/library/{kind}/", response={200: LibraryItemOut, **ERRORS}, operation_id="library_create")
@_errors
def library_create(request: HttpRequest, kind: str, payload: LibraryCreateIn) -> Any:
    """Create v1 of a user-owned entity (409 ``slug_exists`` when the slug is taken)."""
    row = service.create(
        service.kind_from_api(kind),
        slug=payload.slug,
        name=payload.name or payload.slug,
        content=payload.content,
        description=payload.description,
        user=_user(request),
        visibility=payload.visibility,
    )
    return item_out(row, with_lint=True)


@router.get("/library/{kind}/{slug}/", response={200: LibraryDetailOut, **ERRORS}, operation_id="library_detail")
@_errors
def library_detail(request: HttpRequest, kind: str, slug: str) -> Any:
    """The latest version (with lint) and every visible version of the slug."""
    lib_kind = service.kind_from_api(kind)
    user = _user(request)
    rows = _visible_slug_rows(lib_kind, slug, user)
    latest = _latest_visible(lib_kind, slug, user)
    return {"latest": item_out(latest, with_lint=True), "versions": [_summary(row) for row in rows]}


@router.get(
    "/library/{kind}/{slug}/versions/{version}/",
    response={200: LibraryItemOut, **ERRORS},
    operation_id="library_version_get",
)
@_errors
def library_version_get(request: HttpRequest, kind: str, slug: str, version: int) -> Any:
    """One version."""
    lib_kind = service.kind_from_api(kind)
    return item_out(_visible_version(lib_kind, slug, version, _user(request)), with_lint=True)


@router.post(
    "/library/{kind}/{slug}/versions/",
    response={200: LibraryItemOut, **ERRORS},
    operation_id="library_version_create",
)
@_errors
def library_version_create(request: HttpRequest, kind: str, slug: str, payload: LibraryVersionIn) -> Any:
    """Create version+1 (409 ``stale_base`` if ``base_version`` is not the latest; ``unchanged`` when equal)."""
    lib_kind = service.kind_from_api(kind)
    user = _user(request)
    base = _visible_version(lib_kind, slug, payload.base_version, user)
    row = service.new_version(
        base,
        base_version=payload.base_version,
        content=payload.content,
        name=payload.name,
        description=payload.description,
        user=user,
        visibility=payload.visibility,
    )
    return item_out(row, with_lint=True, unchanged=row.version == payload.base_version)


@router.post(
    "/library/{kind}/{slug}/archive/",
    response={200: LibraryItemOut, **ERRORS},
    operation_id="library_archive",
)
@_errors
def library_archive(request: HttpRequest, kind: str, slug: str, payload: ArchiveIn) -> Any:
    """Archive or un-archive the slug (or one ``version``); versions are never deleted."""
    lib_kind = service.kind_from_api(kind)
    user = _user(request)
    row = (
        _visible_version(lib_kind, slug, payload.version, user)
        if payload.version is not None
        else _visible_slug_rows(lib_kind, slug, user)[0]
    )
    row = service.archive(row, archived=payload.archived, user=user, all_versions=payload.version is None)
    return item_out(row)


@router.post(
    "/library/{kind}/{slug}/visibility/",
    response={200: LibraryItemOut, **ERRORS},
    operation_id="library_visibility",
)
@_errors
def library_visibility(request: HttpRequest, kind: str, slug: str, payload: VisibilityIn) -> Any:
    """Share (``shared``) or unshare (``private``) every version of a user slug."""
    lib_kind = service.kind_from_api(kind)
    user = _user(request)
    row = service.set_visibility(_visible_slug_rows(lib_kind, slug, user)[0], payload.visibility, user=user)
    return item_out(row)


@router.get("/library/{kind}/{slug}/usage/", response={200: UsageOut, **ERRORS}, operation_id="library_usage")
@_errors
def library_usage(request: HttpRequest, kind: str, slug: str) -> Any:
    """Studies (visible to the caller) and runs that use any version of the slug."""
    lib_kind = service.kind_from_api(kind)
    user = _user(request)
    return service.usage(_visible_slug_rows(lib_kind, slug, user)[0], user=user)


@router.get("/library/{kind}/{slug}/diff/", response={200: DiffOut, **ERRORS}, operation_id="library_diff")
@_errors
def library_diff(request: HttpRequest, kind: str, slug: str, a: int, b: int) -> Any:
    """Unified and structured diff between versions ``a`` and ``b``."""
    lib_kind = service.kind_from_api(kind)
    user = _user(request)
    return service.content_diff(
        _visible_version(lib_kind, slug, a, user),
        _visible_version(lib_kind, slug, b, user),
    )


@router.post("/library/{kind}/{slug}/fork/", response={200: LibraryItemOut, **ERRORS}, operation_id="library_fork")
@_errors
def library_fork(request: HttpRequest, kind: str, slug: str, payload: ForkIn) -> Any:
    """Copy a visible version as v1 of a new user-owned slug (``derived_from`` set)."""
    lib_kind = service.kind_from_api(kind)
    user = _user(request)
    source = (
        _visible_version(lib_kind, slug, payload.version, user)
        if payload.version is not None
        else _latest_visible(lib_kind, slug, user)
    )
    row = service.fork(
        source,
        new_slug=payload.new_slug,
        name=payload.name or payload.new_slug,
        user=user,
        description=payload.description,
    )
    return item_out(row, with_lint=True)
