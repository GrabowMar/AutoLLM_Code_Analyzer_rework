"""Library entity lifecycle (spec sections 3.3, 3.6 and 9.1): create, version, archive, fork, override, usage.

Every library entity is immutable per ``(slug, version)`` (decision D8). Editing creates version+1 with
``derived_from`` = the base, archiving replaces deleting, and studies pin exact versions. The models normalize
and hash content and pin ``slug@v`` refs inside it (``VersionedEntity.derive_fields``); this module adds the
lifecycle rules on top:

* **Ownership.** A slug belongs to the creator of its latest version; system slugs (``is_system``) belong to
  the seeder (``seed_library``) only. Users version their own slugs; anyone may fork what they can see.
* **Visibility.** A user sees system rows, shared rows and their own rows; staff see everything.
* **Lint.** A create or version whose content has ``error`` lint issues is refused with 422 and the issues.
* **Tasks.** ``task`` maps to the ``TaskSpec`` header plus ``TaskSpecVersion`` rows; ownership, visibility
  and archiving live on the header, ``derived`` and ``lint`` are computed on every new version.

Errors are :class:`LibraryError` (an ``APIError``) carrying ``status_code``, ``code`` and ``issues``; the API
turns them into ``{"detail", "code", "issues"?}`` bodies.
"""

from __future__ import annotations

import copy
import difflib
import json
import math
import re
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

import yaml
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.db import transaction
from django.db.models import Max
from django.db.models import Q
from django.db.models import QuerySet

from backend.common.exceptions import APIError
from backend.generation import identity
from backend.generation.contracts.issues import Issue
from backend.generation.contracts.issues import make_issue
from backend.generation.contracts.library import CONTENT_MODELS
from backend.generation.contracts.library import LIBRARY_REF_FIELDS
from backend.generation.contracts.library import OVERRIDE_SWAPS
from backend.generation.contracts.library import auth_fragment_key
from backend.generation.contracts.library import iter_content_refs
from backend.generation.contracts.library import pin_content_refs
from backend.generation.contracts.library import swap_prompt_set_fragment
from backend.generation.library import lint as library_lint
from backend.generation.models import LIBRARY_MODELS
from backend.generation.models import TaskSpec
from backend.generation.models import TaskSpecVersion
from backend.generation.models.choices import Visibility
from backend.generation.prompts.engine import find_variables

if TYPE_CHECKING:
    from backend.users.models import User

__all__ = [
    "API_KINDS",
    "DERIVED_VERSION",
    "FACTOR_FOR_KIND",
    "KIND_API_NAMES",
    "LibraryError",
    "OverrideResult",
    "archive",
    "can_edit",
    "can_view",
    "compute_task_derived",
    "content_diff",
    "content_hash_of",
    "create",
    "fork",
    "fork_for_override",
    "is_system_row",
    "kind_from_api",
    "kind_of",
    "latest",
    "lint_entity",
    "model_for",
    "new_version",
    "owner_id",
    "parse_ref",
    "pin_refs",
    "resolve_ref",
    "set_visibility",
    "slug_of",
    "slug_rows",
    "task_lint_payload",
    "usage",
    "validate_content",
    "visibility_q",
    "visible",
]

# URL name -> library kind (section 9.1).
API_KINDS: Final[Mapping[str, str]] = {
    "tasks": "task",
    "stacks": "stack",
    "starters": "starter",
    "styles": "style",
    "guidance": "guidance",
    "prompts": "prompt",
    "prompt-sets": "prompt_set",
    "harness-presets": "harness_preset",
    "sampling-presets": "sampling",
    "resource-policies": "resource_policy",
    "eval-suites": "eval_suite",
    "detectors": "detector",
}
KIND_API_NAMES: Final[Mapping[str, str]] = {kind: name for name, kind in API_KINDS.items()}
# Library kind -> the design factor whose levels name it (section 9.4).
FACTOR_FOR_KIND: Final[Mapping[str, str]] = {
    "stack": "stack_profile",
    "style": "style",
    "guidance": "guidance",
    "prompt_set": "prompt_set",
    "harness_preset": "harness_preset",
    "sampling": "sampling_preset",
    "resource_policy": "resource_policy",
}
# GenerationRun FK per kind (the runs a version produced).
_RUN_FK: Final[Mapping[str, str]] = {
    "task": "task_version",
    "stack": "stack_profile",
    "starter": "starter",
    "style": "style",
    "guidance": "guidance",
    "prompt_set": "prompt_set",
    "harness_preset": "harness_preset",
    "sampling": "sampling_preset",
    "resource_policy": "resource_policy",
}
# TaskSpecVersion.derived algorithm version (difficulty score, size bucket).
DERIVED_VERSION: Final = 1
_SLUG_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_REF_RE: Final = re.compile(
    r"^(?:(?P<kind>[a-z_]+):)?(?P<slug>[A-Za-z0-9][A-Za-z0-9_-]*)@(?P<version>[1-9][0-9]*)(?:#(?P<hash>[0-9a-f]{12}))?$",
)
_SLUG_MAX: Final = 100


class LibraryError(APIError):
    """A refused library operation: ``status_code``, a machine ``code`` and optional ``issues``."""

    def __init__(self, message: str, *, status: int, code: str, issues: Iterable[Issue] = ()) -> None:
        super().__init__(message, status_code=status)
        self.code = code
        self.issues = list(issues)

    def payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"detail": self.message, "code": self.code}
        if self.issues:
            body["issues"] = [issue.model_dump(mode="json") for issue in self.issues]
        return body


def _not_found(message: str) -> LibraryError:
    return LibraryError(message, status=404, code="not_found")


def _denied(message: str) -> LibraryError:
    return LibraryError(message, status=403, code="permission_denied")


def _invalid(message: str, issues: Iterable[Issue] = ()) -> LibraryError:
    return LibraryError(message, status=422, code="invalid", issues=issues)


# =====================================================================================
# Kinds, rows and refs
# =====================================================================================


def kind_from_api(name: str) -> str:
    """The library kind of a URL ``{kind}`` (``prompt-sets`` -> ``prompt_set``); 404 when unknown."""
    try:
        return API_KINDS[name]
    except KeyError:
        msg = f"unknown library kind {name!r}"
        raise _not_found(msg) from None


def model_for(kind: str) -> type[Any]:
    """The model of ``kind`` (``task`` -> ``TaskSpecVersion``)."""
    try:
        return LIBRARY_MODELS[kind]
    except KeyError:
        msg = f"unknown library kind {kind!r}"
        raise _not_found(msg) from None


def _is_task(kind: str) -> bool:
    return kind == "task"


def _slug_field(kind: str) -> str:
    return "task__slug" if _is_task(kind) else "slug"


def slug_of(row: Any) -> str:
    return row.task.slug if isinstance(row, TaskSpecVersion) else row.slug


def kind_of(row: Any) -> str:
    return "task" if isinstance(row, TaskSpecVersion) else row.KIND


def _header(row: Any) -> Any:
    """The row that carries ownership, visibility and archiving (the task header for tasks)."""
    return row.task if isinstance(row, TaskSpecVersion) else row


def is_system_row(row: Any) -> bool:
    return bool(_header(row).is_system)


def owner_id(row: Any) -> int | None:
    return _header(row).created_by_id


def slug_rows(kind: str, slug: str) -> QuerySet:
    """Every version of ``slug`` (newest first), archived ones included."""
    model = model_for(kind)
    qs = model.objects.filter(**{_slug_field(kind): slug})
    if _is_task(kind):
        qs = qs.select_related("task")
    return qs.order_by("-version")


def _latest_row(kind: str, slug: str) -> Any | None:
    return slug_rows(kind, slug).first()


def visibility_q(kind: str, user: User | None) -> Q:
    """Rows ``user`` may see: system, shared and their own (everything for staff)."""
    if user is not None and getattr(user, "is_staff", False):
        return Q()
    prefix = "task__" if _is_task(kind) else ""
    q = Q(**{f"{prefix}is_system": True}) | Q(**{f"{prefix}visibility": Visibility.SHARED})
    if user is not None and getattr(user, "is_authenticated", False):
        q |= Q(**{f"{prefix}created_by": user})
    return q


def visible(kind: str, user: User | None) -> QuerySet:
    """Every version of ``kind`` visible to ``user``."""
    qs = model_for(kind).objects.filter(visibility_q(kind, user))
    return qs.select_related("task") if _is_task(kind) else qs


def can_view(row: Any, user: User | None) -> bool:
    if user is not None and getattr(user, "is_staff", False):
        return True
    header = _header(row)
    if header.is_system or header.visibility == Visibility.SHARED:
        return True
    return user is not None and header.created_by_id is not None and header.created_by_id == getattr(user, "pk", None)


def can_edit(row: Any, user: User | None) -> bool:
    """``user`` may create versions of the slug: its owner (or staff), never for system slugs."""
    if user is None or is_system_row(row):
        return False
    return owner_id(row) == user.pk or bool(getattr(user, "is_staff", False))


@dataclass(frozen=True)
class ParsedRef:
    kind: str | None
    slug: str
    version: int
    hash12: str | None

    @property
    def unpinned(self) -> str:
        return f"{self.slug}@{self.version}"


def parse_ref(ref: str) -> ParsedRef:
    """``[kind:]slug@v[#hash12]`` (422 ``invalid`` for anything else)."""
    match = _REF_RE.match(str(ref or "").strip())
    if match is None:
        msg = f"not a library ref ([kind:]slug@version[#hash12]): {ref!r}"
        raise _invalid(msg)
    return ParsedRef(match["kind"], match["slug"], int(match["version"]), match["hash"])


def resolve_ref(ref: str, *, kind: str | None = None, user: User | None = None) -> Any:
    """The entity named by ``kind:slug@v`` / ``slug@v[#hash12]``.

    ``kind`` is required unless the ref carries it. A pinned ref whose hash disagrees with the stored
    version is 409 ``hash_mismatch``; with ``user`` the row must be visible to them (else 404).
    """
    parsed = parse_ref(ref)
    target = parsed.kind or kind
    if target is None:
        msg = f"{ref!r} names no kind; use kind:slug@version"
        raise _invalid(msg)
    if kind is not None and parsed.kind is not None and parsed.kind != kind:
        msg = f"{ref!r} is not a {kind}"
        raise LibraryError(msg, status=422, code="invalid")
    row = slug_rows(target, parsed.slug).filter(version=parsed.version).first()
    if row is None or (user is not None and not can_view(row, user)):
        msg = f"{target} {parsed.unpinned} does not exist"
        raise _not_found(msg)
    if parsed.hash12 is not None and row.content_hash[:12] != parsed.hash12:
        msg = f"{ref} does not match {target} {parsed.unpinned} (content hash {row.content_hash[:12]})"
        raise LibraryError(
            msg,
            status=409,
            code="hash_mismatch",
        )
    return row


def latest(kind: str, slug: str, *, user: User | None = None) -> Any:
    """The latest non-archived version of ``slug`` (visible to ``user`` when given); 404 when none."""
    qs = slug_rows(kind, slug)
    if _is_task(kind):
        qs = qs.filter(task__is_archived=False)
    else:
        qs = qs.filter(is_archived=False)
    if user is not None:
        qs = qs.filter(visibility_q(kind, user))
    row = qs.first()
    if row is None:
        msg = f"no {kind} {slug!r}"
        raise _not_found(msg)
    return row


def _db_hash(kind: str, slug: str, version: int) -> str | None:
    row = (
        slug_rows(kind, slug).filter(version=version).only("content_hash").first() if kind in LIBRARY_MODELS else None
    )
    return row.content_hash if row is not None else None


def pin_refs(kind: str, content: Mapping[str, Any]) -> dict[str, Any]:
    """``content`` with every ``slug@v`` library ref pinned from the database (422 when one is missing)."""
    pinned, errors = pin_content_refs(kind, content, _db_hash)
    if errors:
        issues = [
            make_issue("lint_unknown_library_ref", message=f"{location}: {message}", path=location)
            for location, messages in errors.items()
            for message in messages
        ]
        msg = "content names library versions that do not exist"
        raise _invalid(msg, issues)
    return pinned


def _db_content(kind: str, ref: str) -> Mapping[str, Any] | None:
    try:
        return resolve_ref(ref, kind=kind).content
    except LibraryError:
        return None


def _normalized(kind: str, content: Mapping[str, Any]) -> dict[str, Any]:
    from pydantic import ValidationError

    try:
        return CONTENT_MODELS[kind].model_validate(content).model_dump(mode="json")
    except ValidationError:
        issues = library_lint.lint(kind, content, resolve=False)
        msg = f"the {kind} content does not validate"
        raise _invalid(msg, issues) from None


def content_hash_of(kind: str, content: Mapping[str, Any]) -> str:
    """The content hash the model would store for ``content`` (refs pinned, content normalized)."""
    normalized = _normalized(kind, pin_refs(kind, content))
    return identity.library_content_hash(kind, _schema(kind), normalized)


def _schema(kind: str) -> int:
    from backend.generation.contracts.library import SCHEMA_VERSIONS

    return SCHEMA_VERSIONS[kind]


# =====================================================================================
# Lint and derived task fields
# =====================================================================================


def lint_entity(kind: str, content: Mapping[str, Any], *, system: bool = False) -> list[Issue]:
    """Lint ``content`` (refs pinned already) against the database."""
    return library_lint.lint(kind, content, system=system, resolve=_db_content)


def compute_task_derived(content: Mapping[str, Any]) -> dict[str, Any]:
    """``TaskDerived`` (computed, never hashed; ``DERIVED_VERSION`` 1).

    ``difficulty_score = endpoints + 1.5 x entities + 0.25 x fields + relations + 0.75 x features
    + 0.5 x probes``, rounded to two decimals; ``size_bucket`` by endpoint count: S <= 4 < M <= 8 < L <= 14 < XL.
    """
    entities = content.get("entities") or []
    endpoints = content.get("endpoints") or []
    fields_ = [entity_field for entity in entities for entity_field in entity.get("fields") or []]
    relations = [entity_field for entity_field in fields_ if entity_field.get("type") == "ref"]
    features = list(content.get("features") or [])
    score = (
        len(endpoints)
        + 1.5 * len(entities)
        + 0.25 * len(fields_)
        + len(relations)
        + 0.75 * len(features)
        + 0.5 * len(content.get("probes") or [])
    )
    count = len(endpoints)
    bucket = "S" if count <= 4 else "M" if count <= 8 else "L" if count <= 14 else "XL"
    return {
        "endpoint_count": count,
        "entity_count": len(entities),
        "field_count": len(fields_),
        "relation_count": len(relations),
        "features": features,
        "surfaces": list(content.get("surfaces") or []),
        "difficulty_score": math.floor(score * 100 + 0.5) / 100,
        "size_bucket": bucket,
        "check_statedness": library_lint.check_statedness(content),
    }


def task_lint_payload(issues: list[Issue]) -> dict[str, Any]:
    return {
        "status": library_lint.status_of(issues),
        "issues": [issue.model_dump(mode="json") for issue in issues],
        "linter_version": library_lint.LINTER_VERSION,
    }


def validate_content(kind: str, content: Mapping[str, Any], *, system: bool = False) -> dict[str, Any]:
    """``{ok, issues, content_hash, derived?}`` for a draft (``POST /library/{kind}/validate/``).

    Bare ``slug@v`` refs are pinned first; ``ok`` means no ``error`` issue.
    """
    pinned, errors = pin_content_refs(kind, content, _db_hash)
    issues = [
        make_issue("lint_unknown_library_ref", message=f"{location}: {message}", path=location)
        for location, messages in errors.items()
        for message in messages
    ]
    issues += library_lint.lint(kind, pinned, system=system, resolve=_db_content)
    result: dict[str, Any] = {"ok": not library_lint.has_errors(issues), "issues": issues, "content_hash": None}
    try:
        normalized = CONTENT_MODELS[kind].model_validate(pinned).model_dump(mode="json")
    except Exception:  # noqa: BLE001 - reported through the issues already
        return result
    result["content_hash"] = identity.library_content_hash(kind, _schema(kind), normalized)
    result["content"] = normalized
    if _is_task(kind):
        result["derived"] = compute_task_derived(normalized)
    return result


# =====================================================================================
# Create, version, archive, visibility, fork
# =====================================================================================


def _check_slug(slug: str) -> None:
    if not slug or len(slug) > _SLUG_MAX or not _SLUG_RE.match(slug):
        msg = f"invalid slug {slug!r} (letters, digits, '-' and '_'; at most {_SLUG_MAX} characters)"
        raise _invalid(msg)


def _prepare(
    kind: str,
    content: Mapping[str, Any],
    *,
    system: bool,
    allow_lint_errors: bool = False,
) -> tuple[dict, list[Issue]]:
    pinned = pin_refs(kind, copy.deepcopy(dict(content)))
    normalized = _normalized(kind, pinned)
    issues = lint_entity(kind, normalized, system=system)
    if library_lint.has_errors(issues) and not allow_lint_errors:
        msg = f"the {kind} content has lint errors"
        raise _invalid(msg, issues)
    return normalized, issues


def _save(row: Any) -> Any:
    try:
        row.full_clean(validate_unique=False, validate_constraints=False)
        row.save()
    except DjangoValidationError as exc:
        msg = f"invalid {kind_of(row)}: {'; '.join(exc.messages)}"
        raise _invalid(msg) from exc
    return row


def _visibility_value(visibility: str | None, *, system: bool, default: str = Visibility.PRIVATE) -> str:
    if system:
        return Visibility.SYSTEM
    value = visibility or default
    if value not in {Visibility.PRIVATE, Visibility.SHARED}:
        msg = f"visibility must be private or shared, not {value!r}"
        raise _invalid(msg)
    return value


def _starter_profile(content: Mapping[str, Any]) -> Any:
    ref = str(content.get("stack_profile", ""))
    return resolve_ref(ref, kind="stack")


def _new_row(
    kind: str,
    *,
    slug: str,
    version: int,
    name: str,
    description: str,
    content: dict[str, Any],
    user: User | None,
    visibility: str,
    system: bool,
    derived_from: Any,
    issues: list[Issue],
    extra: Mapping[str, Any] | None = None,
) -> Any:
    if _is_task(kind):
        header = TaskSpec.objects.filter(slug=slug).first()
        if header is None:
            header = TaskSpec.objects.create(
                slug=slug,
                is_system=system,
                visibility=visibility,
                created_by=user,
                latest_version=0,
            )
        row = TaskSpecVersion(
            task=header,
            version=version,
            name=name,
            content=content,
            created_by=user,
            derived_from=derived_from,
            derived=compute_task_derived(content),
            lint=task_lint_payload(issues),
        )
        _save(row)
        if header.latest_version < version:
            header.latest_version = version
            header.save(update_fields=["latest_version"])
        return row
    model = model_for(kind)
    fields: dict[str, Any] = {
        "slug": slug,
        "version": version,
        "name": name,
        "description": description,
        "content": content,
        "is_system": system,
        "visibility": visibility,
        "created_by": user,
        "derived_from": derived_from,
        **dict(extra or {}),
    }
    if kind == "starter" and "stack_profile" not in fields:
        fields["stack_profile"] = _starter_profile(content)
    return _save(model(**fields))


@transaction.atomic
def create(
    kind: str,
    *,
    slug: str,
    name: str,
    content: Mapping[str, Any],
    description: str = "",
    user: User | None,
    visibility: str | None = None,
    system: bool = False,
    derived_from: Any = None,
    extra: Mapping[str, Any] | None = None,
    allow_lint_errors: bool = False,
) -> Any:
    """Create version 1 of an entity of ``kind`` (409 ``slug_exists`` when the slug is taken).

    ``content`` may name other entities as ``slug@v``; they are pinned. ``system=True`` is for the seeder.
    ``extra`` sets non-content columns (``ResourcePolicy.tier``, ``Starter.stack_profile``). Content with
    ``error`` lint issues is refused (422) unless ``allow_lint_errors`` (the seeder, which reports them).
    """
    _check_slug(slug)
    model_for(kind)
    if slug_rows(kind, slug).exists() or (_is_task(kind) and TaskSpec.objects.filter(slug=slug).exists()):
        msg = f"{kind} {slug!r} already exists"
        raise LibraryError(msg, status=409, code="slug_exists")
    normalized, issues = _prepare(kind, content, system=system, allow_lint_errors=allow_lint_errors)
    try:
        with transaction.atomic():
            return _new_row(
                kind,
                slug=slug,
                version=1,
                name=name or slug,
                description=description,
                content=normalized,
                user=user,
                visibility=_visibility_value(visibility, system=system),
                system=system,
                derived_from=derived_from,
                issues=issues,
                extra=extra,
            )
    except IntegrityError as exc:
        msg = f"{kind} {slug!r} already exists"
        raise LibraryError(msg, status=409, code="slug_exists") from exc


def _max_version(kind: str, slug: str) -> int:
    return slug_rows(kind, slug).aggregate(top=Max("version"))["top"] or 0


@transaction.atomic
def new_version(
    entity: Any,
    *,
    base_version: int,
    content: Mapping[str, Any],
    name: str | None = None,
    description: str | None = None,
    user: User | None,
    visibility: str | None = None,
    system: bool = False,
    extra: Mapping[str, Any] | None = None,
    allow_lint_errors: bool = False,
) -> Any:
    """Create version+1 of ``entity``'s slug from ``content``.

    A ``base_version`` that is not the slug's latest version is 409 ``stale_base``; content whose hash equals
    the base's returns the base unchanged (compare ``result.version == base_version``). Only the slug's owner
    (or staff) may version a user slug; system slugs are versioned by the seeder (``system=True``) only.
    """
    kind = kind_of(entity)
    slug = slug_of(entity)
    latest_row = _latest_row(kind, slug)
    if latest_row is None:
        msg = f"no {kind} {slug!r}"
        raise _not_found(msg)
    if system != is_system_row(latest_row):
        msg = f"{kind} {slug} is {'a system' if is_system_row(latest_row) else 'a user'} entity; fork it"
        raise _denied(msg)
    if not system and not can_edit(latest_row, user):
        msg = f"only the owner of {kind} {slug} can create versions; fork it instead"
        raise _denied(msg)
    if int(base_version) != latest_row.version:
        msg = f"base version {base_version} is stale: the latest {kind} {slug} version is {latest_row.version}"
        raise LibraryError(
            msg,
            status=409,
            code="stale_base",
        )
    pinned = pin_refs(kind, copy.deepcopy(dict(content)))
    normalized = _normalized(kind, pinned)
    if identity.library_content_hash(kind, _schema(kind), normalized) == latest_row.content_hash:
        return latest_row
    normalized, issues = _prepare(kind, normalized, system=system, allow_lint_errors=allow_lint_errors)
    header = _header(latest_row)
    version = latest_row.version + 1
    try:
        with transaction.atomic():
            return _new_row(
                kind,
                slug=slug,
                version=version,
                name=name if name is not None else latest_row.name,
                description=(description if description is not None else getattr(latest_row, "description", "")),
                content=normalized,
                user=user if not system else (user or header.created_by),
                visibility=_visibility_value(visibility, system=system, default=header.visibility),
                system=system,
                derived_from=latest_row,
                issues=issues,
                extra=extra,
            )
    except IntegrityError as exc:
        msg = f"{kind} {slug} v{version} was created concurrently"
        raise LibraryError(
            msg,
            status=409,
            code="stale_base",
        ) from exc


def archive(entity: Any, *, archived: bool = True, user: User | None = None, all_versions: bool = True) -> Any:
    """Archive (or un-archive) ``entity``'s slug (every version, or only ``entity`` with ``all_versions=False``).

    Versions are never deleted. Tasks archive their header. With ``user``, only the owner or staff may
    archive (system slugs: staff only). Returns the refreshed ``entity``.
    """
    if user is not None:
        staff = bool(getattr(user, "is_staff", False))
        if is_system_row(entity) and not staff:
            msg = "only staff can archive system entities"
            raise _denied(msg)
        if not is_system_row(entity) and owner_id(entity) != user.pk and not staff:
            msg = "only the owner can archive this entity"
            raise _denied(msg)
    if isinstance(entity, TaskSpecVersion):
        header = entity.task
        header.is_archived = archived
        header.save(update_fields=["is_archived"])
        entity.refresh_from_db()
        return entity
    rows = slug_rows(entity.KIND, entity.slug) if all_versions else [entity]
    for row in rows:
        if row.is_archived != archived:
            row.is_archived = archived
            row.save(update_fields=["is_archived"])
    entity.refresh_from_db()
    return entity


def set_visibility(entity: Any, visibility: str, *, user: User | None) -> Any:
    """Share (``shared``) or unshare (``private``) every version of a user slug (its owner or staff)."""
    if is_system_row(entity):
        msg = "system entities are always visible"
        raise _denied(msg)
    if user is None or (owner_id(entity) != user.pk and not getattr(user, "is_staff", False)):
        msg = "only the owner can change the visibility"
        raise _denied(msg)
    value = _visibility_value(visibility, system=False)
    if isinstance(entity, TaskSpecVersion):
        entity.task.visibility = value
        entity.task.save(update_fields=["visibility"])
    else:
        for row in slug_rows(entity.KIND, entity.slug):
            if row.visibility != value:
                row.visibility = value
                row.save(update_fields=["visibility"])
    entity.refresh_from_db()
    return entity


def _extra_columns(entity: Any) -> dict[str, Any]:
    """Non-content columns of a fork: a forked or edited budget preset is ``tier="custom"`` (section 4.3)."""
    return {"tier": "custom"} if kind_of(entity) == "resource_policy" else {}


def fork(
    entity: Any,
    *,
    new_slug: str,
    name: str,
    user: User | None,
    description: str | None = None,
    visibility: str | None = None,
) -> Any:
    """Copy ``entity`` as v1 of a new user-owned slug (``derived_from`` set). A forked budget preset is
    ``tier="custom"`` (section 4.2)."""
    if user is not None and not can_view(entity, user):
        msg = "no such entity"
        raise _not_found(msg)
    kind = kind_of(entity)
    return create(
        kind,
        slug=new_slug,
        name=name or new_slug,
        content=entity.content,
        description=description if description is not None else getattr(entity, "description", ""),
        user=user,
        visibility=visibility,
        derived_from=entity,
        extra=_extra_columns(entity),
    )


# =====================================================================================
# Overrides (section 3.6 StudyPromptOverride; OVERRIDE_SWAPS)
# =====================================================================================


@dataclass(frozen=True)
class OverrideResult:
    """The outcome of :func:`fork_for_override`: what was forked and which factor level to swap.

    ``owner_ref_base``/``owner_ref_new`` name the entity that owns the edited text (the fragment template for
    ``auth_fragment``), ``level_base``/``level_new`` the swapped ``swapped_factor`` level (pinned refs).
    ``fields()`` gives the ``StudyPromptOverride`` columns.
    """

    owner_kind: str
    owner_ref_base: str
    owner_ref_new: str
    role: str
    swapped_factor: str
    level_base: str
    level_new: str
    diff: str
    created: tuple[str, ...] = field(default_factory=tuple)

    @property
    def level_ref_base(self) -> str:
        return self.level_base

    @property
    def level_ref_new(self) -> str:
        return self.level_new

    def fields(self) -> dict[str, str]:
        return {
            "owner_kind": self.owner_kind,
            "owner_ref_base": self.owner_ref_base,
            "owner_ref_new": self.owner_ref_new,
            "role": self.role,
            "diff": self.diff,
        }


def _fork_reusable(row: Any, user: User | None) -> bool:
    """A fork row may be handed to ``user``: their own, or (anonymous callers) an unowned one."""
    if is_system_row(row):
        return False
    owner = owner_id(row)
    return owner is None if user is None else owner == user.pk


def _fork_slug(kind: str, slug: str, content_hash: str, user: User | None) -> str:
    """The slug of ``user``'s fork of ``slug`` holding ``content_hash``.

    ``<slug>--<hash8>`` when it is free or already ``user``'s; otherwise (another user owns it, even a shared
    row: a study must pin only entities its owner owns, so archiving stays in their hands) the user-scoped
    ``<slug>--<hash8>-u<pk>``, then ``-u<pk>-2``, ``-3``... until a free or own slug is found.
    """
    base_slug = f"{slug[: _SLUG_MAX - 10]}--{content_hash[:8]}"
    candidates = [base_slug]
    if user is not None:
        scoped = f"{slug[: _SLUG_MAX - 12 - len(str(user.pk)) - 4]}--{content_hash[:8]}-u{user.pk}"
        candidates += [scoped, *(f"{scoped}-{n}" for n in range(2, 1000))]
    for candidate in candidates:
        rows = list(slug_rows(kind, candidate)[:1])
        header_taken = _is_task(kind) and not rows and TaskSpec.objects.filter(slug=candidate).exists()
        if (not rows and not header_taken) or (rows and _fork_reusable(rows[0], user)):
            return candidate
    msg = f"no free fork slug for {kind} {slug!r}"
    raise LibraryError(msg)


def _derive(base: Any, content: Mapping[str, Any], *, user: User | None, created: list[str]) -> Any:
    """A user-owned entity holding ``content``, derived from ``base``.

    The user's own slug gets a new version (an existing version with the same hash is reused); anything else
    (system, shared or another user's) is forked to ``<slug>--<hash8>``, reused when it already exists.
    """
    kind = kind_of(base)
    pinned = pin_refs(kind, copy.deepcopy(dict(content)))
    normalized = _normalized(kind, pinned)
    content_hash = identity.library_content_hash(kind, _schema(kind), normalized)
    if content_hash == base.content_hash:
        return base
    slug = slug_of(base)
    if user is not None and not is_system_row(base) and owner_id(base) == user.pk:
        same = slug_rows(kind, slug).filter(content_hash=content_hash).first()
        if same is not None:
            return same
        latest_row = _latest_row(kind, slug)
        normalized, issues = _prepare(kind, normalized, system=False)
        row = _new_row(
            kind,
            slug=slug,
            version=latest_row.version + 1,
            name=latest_row.name,
            description=getattr(latest_row, "description", ""),
            content=normalized,
            user=user,
            visibility=_header(latest_row).visibility,
            system=False,
            derived_from=base,
            issues=issues,
            extra=_extra_columns(base) if kind == "resource_policy" else None,
        )
        created.append(row.pinned_ref)
        return row
    fork_slug = _fork_slug(kind, slug, content_hash, user)
    existing = slug_rows(kind, fork_slug).filter(content_hash=content_hash).first()
    if existing is not None:
        return existing
    row = create(
        kind,
        slug=fork_slug,
        name=f"{base.name} (edited)"[:200],
        content=normalized,
        description=getattr(base, "description", ""),
        user=user,
        derived_from=base,
        extra=_extra_columns(base),
    )
    created.append(row.pinned_ref)
    return row


def _unified(a: str, b: str, a_name: str, b_name: str) -> str:
    return "".join(
        difflib.unified_diff(
            a.splitlines(keepends=True),
            b.splitlines(keepends=True),
            fromfile=f"a/{a_name}",
            tofile=f"b/{b_name}",
        ),
    )


def _fork_template(template_ref: str, new_template: str, *, user: User | None, created: list[str]) -> tuple[Any, Any]:
    base = resolve_ref(template_ref, kind="prompt", user=user)
    content = {**base.content, "template": new_template}
    used = sorted(find_variables(new_template)) if new_template else []
    # Keep the declared variables in step with the edit (lint refuses undeclared reads).
    content["variables"] = [name for name in base.content.get("variables", []) if name in used] + [
        name for name in used if name not in base.content.get("variables", [])
    ]
    return base, _derive(base, content, user=user, created=created)


def _single_prompt_set_using(template: Any, *, user: User | None) -> Any:
    """The one visible, non-archived prompt set whose roles or fragments name ``template`` (else 422)."""
    pinned = template.pinned_ref
    candidates = [
        row
        for row in visible("prompt_set", user).filter(is_archived=False).order_by("slug", "-version")
        if pinned in (row.content.get("roles") or {}).values()
        or pinned in (row.content.get("fragments") or {}).values()
    ]
    if len(candidates) != 1:
        found = ", ".join(row.pinned_ref for row in candidates[:5]) or "none"
        msg = f"pass prompt_set_ref: {len(candidates)} prompt sets use {pinned} ({found})"
        raise _invalid(msg)
    return candidates[0]


def _replace_ref(content: Any, old: str, new: str) -> tuple[Any, int]:
    """``content`` with every string equal to ``old`` replaced by ``new`` inside the ref fields."""
    count = 0
    copied = copy.deepcopy(content)
    for section in ("roles", "fragments"):
        mapping = copied.get(section) or {}
        for key, value in list(mapping.items()):
            if value == old:
                mapping[key] = new
                count += 1
    return copied, count


@transaction.atomic
def fork_for_override(
    owner_kind: str,
    owner_ref: str,
    new_template: str,
    *,
    user: User | None,
    prompt_set_ref: str | None = None,
    role: str | None = None,
) -> OverrideResult:
    """Save an inline edit as a new user-owned version of the entity that owns the text (section 3.6).

    ``OVERRIDE_SWAPS[owner_kind]`` names the forks (innermost first) and the swapped factor:

    * ``prompt``: ``owner_ref`` is a PromptTemplate named by ``prompt_set_ref`` (roles or fragments; when
      omitted, the one visible prompt set that uses the template); the template and the prompt set are
      forked; the ``prompt_set`` level is swapped;
    * ``auth_fragment``: ``role`` is the auth mode (inferred when the template is exactly one mode's
      fragment); ``owner_ref`` must be the set's ``auth_contract.<role>`` fragment; the template and the
      prompt set are forked;
    * ``stack`` / ``style`` / ``guidance``: ``owner_ref`` is the entity; its fragment template and the entity
      are forked and its own factor level is swapped;
    * ``prompt_set``: ``new_template`` is the new ``PromptSetContent`` as JSON or YAML.

    A fork is a new version of the user's own slug, or a ``<slug>--<hash8>`` fork of anyone else's; both have
    ``derived_from`` = the base and are reused when the same edit is made again.
    """
    if owner_kind not in OVERRIDE_SWAPS:
        msg = f"unknown owner kind {owner_kind!r}; expected one of {sorted(OVERRIDE_SWAPS)}"
        raise _invalid(msg)
    created: list[str] = []
    swapped_factor = OVERRIDE_SWAPS[owner_kind][1]
    if owner_kind in {"prompt", "auth_fragment"}:
        base_template = resolve_ref(owner_ref, kind="prompt", user=user)
        if prompt_set_ref:
            prompt_set = resolve_ref(prompt_set_ref, kind="prompt_set", user=user)
        else:
            prompt_set = _single_prompt_set_using(base_template, user=user)
        if owner_kind == "auth_fragment":
            if not role:
                # The auth mode whose fragment this template is, when exactly one is.
                modes = [
                    key.split(".", 1)[1]
                    for key, value in (prompt_set.content.get("fragments") or {}).items()
                    if key.startswith("auth_contract.") and value == base_template.pinned_ref
                ]
                if len(modes) != 1:
                    msg = "an auth_fragment override names its auth mode (role)"
                    raise _invalid(msg)
                role = modes[0]
            key = auth_fragment_key(role)
            if (prompt_set.content.get("fragments") or {}).get(key) != base_template.pinned_ref:
                msg = f"{owner_ref} is not the {key} fragment of {prompt_set.pinned_ref}"
                raise _invalid(msg)
        _, new_template_row = _fork_template(base_template.pinned_ref, new_template, user=user, created=created)
        if owner_kind == "auth_fragment":
            set_content = swap_prompt_set_fragment(prompt_set.content, key, new_template_row.pinned_ref)
        else:
            set_content, count = _replace_ref(
                prompt_set.content,
                base_template.pinned_ref,
                new_template_row.pinned_ref,
            )
            if not count:
                msg = f"{prompt_set.pinned_ref} does not use {base_template.pinned_ref}"
                raise _invalid(msg)
        new_set = _derive(prompt_set, set_content, user=user, created=created)
        return OverrideResult(
            owner_kind=owner_kind,
            owner_ref_base=base_template.pinned_ref,
            owner_ref_new=new_template_row.pinned_ref,
            role=role or str(base_template.content.get("role", "")),
            swapped_factor=swapped_factor,
            level_base=prompt_set.pinned_ref,
            level_new=new_set.pinned_ref,
            diff=_unified(base_template.content["template"], new_template, base_template.ref, new_template_row.ref),
            created=tuple(created),
        )
    if owner_kind == "prompt_set":
        base = resolve_ref(owner_ref, kind="prompt_set", user=user)
        try:
            new_content = yaml.safe_load(new_template)
        except yaml.YAMLError as exc:
            msg = f"the prompt set content is not JSON or YAML: {exc}"
            raise _invalid(msg) from exc
        if not isinstance(new_content, Mapping):
            msg = "the prompt set content must be a mapping"
            raise _invalid(msg)
        new_set = _derive(base, new_content, user=user, created=created)
        old_text = json.dumps(base.content, indent=2, sort_keys=True) + "\n"
        new_text = json.dumps(new_set.content, indent=2, sort_keys=True) + "\n"
        return OverrideResult(
            owner_kind=owner_kind,
            owner_ref_base=base.pinned_ref,
            owner_ref_new=new_set.pinned_ref,
            role=role or "",
            swapped_factor=swapped_factor,
            level_base=base.pinned_ref,
            level_new=new_set.pinned_ref,
            diff=_unified(old_text, new_text, base.ref, new_set.ref),
            created=tuple(created),
        )
    # stack, style, guidance: the entity owns its fragment template.
    field_name = "brief_fragment" if owner_kind == "stack" else "fragment"
    base = resolve_ref(owner_ref, kind=owner_kind, user=user)
    fragment_ref = base.content.get(field_name)
    if not fragment_ref:
        msg = f"{base.pinned_ref} has no fragment to edit"
        raise _invalid(msg)
    base_template, new_template_row = _fork_template(fragment_ref, new_template, user=user, created=created)
    new_owner = _derive(base, {**base.content, field_name: new_template_row.pinned_ref}, user=user, created=created)
    return OverrideResult(
        owner_kind=owner_kind,
        owner_ref_base=base.pinned_ref,
        owner_ref_new=new_owner.pinned_ref,
        role=role or "fragment",
        swapped_factor=swapped_factor,
        level_base=base.pinned_ref,
        level_new=new_owner.pinned_ref,
        diff=_unified(base_template.content["template"], new_template, base_template.ref, new_template_row.ref),
        created=tuple(created),
    )


# =====================================================================================
# Usage and diff
# =====================================================================================


def _refs_in_design(design: Mapping[str, Any]) -> Iterator[tuple[str | None, str]]:
    """``(factor key or section, level)`` for every ref-like string of a ``Design``."""
    for factor in design.get("factors") or []:
        for level in factor.get("levels") or []:
            yield str(factor.get("key")), str(level)
    for ref in (design.get("units") or {}).get("tasks") or []:
        yield "task", str(ref)
    if design.get("resource_policy"):
        yield "resource_policy", str(design["resource_policy"])
    evaluation = design.get("evaluation") or {}
    if evaluation.get("eval_suite"):
        yield "eval_suite", str(evaluation["eval_suite"])
    for ref in design.get("slot_primary_features") or []:
        yield "detector", str(ref)


def _section_for(kind: str) -> str:
    return FACTOR_FOR_KIND.get(kind, kind)


def _dependents(kind: str, slug: str) -> list[tuple[str, Any, int]]:
    """``(kind, row, referenced version)`` for every entity whose content references ``kind`` ``slug``."""
    out = []
    for source_kind, paths in LIBRARY_REF_FIELDS.items():
        if kind not in {target for _, target in paths}:
            continue
        for row in model_for(source_kind).objects.all().only("id", "slug", "version", "content"):
            for _, target, ref in iter_content_refs(source_kind, row.content):
                if target == kind and ref.split("@", 1)[0] == slug:
                    out.append((source_kind, row, int(ref.split("@", 1)[1].split("#", 1)[0])))
    return out


def usage(entity: Any, *, user: User | None = None) -> dict[str, Any]:
    """``{studies: [{id, name, status, version}], runs: int}`` for every version of ``entity``'s slug.

    Studies are those visible to ``user`` (their own; staff: all) whose design names a version of the slug,
    directly or (prompt templates and stacks) through an entity that references it. ``runs`` counts every
    run produced with a version of the slug (or of a referencing entity).
    """
    from backend.generation.models import GenerationRun
    from backend.generation.models import PatternHit
    from backend.generation.models import RunEvaluation
    from backend.generation.models import Study

    kind = kind_of(entity)
    slug = slug_of(entity)
    # (section, slug) -> version of *this* slug that the reference stands for
    targets: dict[tuple[str, str, int], int] = {}
    for row in slug_rows(kind, slug):
        targets[(_section_for(kind), slug, row.version)] = row.version
    run_filters: list[Q] = []
    if kind in _RUN_FK:
        field_name = _RUN_FK[kind]
        lookup = f"{field_name}__task__slug" if _is_task(kind) else f"{field_name}__slug"
        run_filters.append(Q(**{lookup: slug}))
    for dep_kind, dep_row, version in _dependents(kind, slug):
        targets[(_section_for(dep_kind), dep_row.slug, dep_row.version)] = version
        if dep_kind in _RUN_FK:
            run_filters.append(Q(**{_RUN_FK[dep_kind]: dep_row.pk}))
    studies_qs = Study.objects.all()
    if user is not None and not getattr(user, "is_staff", False):
        studies_qs = studies_qs.filter(created_by=user)
    studies: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for study in studies_qs.only("id", "name", "status", "design").order_by("-created_at").iterator():
        for section, level in _refs_in_design(study.design or {}):
            match = _REF_RE.match(level)
            if match is None or section is None:
                continue
            version = targets.get((section, match["slug"], int(match["version"])))
            if version is not None and (str(study.id), version) not in seen:
                seen.add((str(study.id), version))
                studies.append({"id": str(study.id), "name": study.name, "status": study.status, "version": version})
    runs = 0
    if run_filters:
        combined = Q()
        for item in run_filters:
            combined |= item
        runs = GenerationRun.objects.filter(combined).distinct().count()
    elif kind == "eval_suite":
        runs = RunEvaluation.objects.filter(eval_suite__slug=slug).values("run_id").distinct().count()
    elif kind == "detector":
        runs = PatternHit.objects.filter(detector__slug=slug).values("run_id").distinct().count()
    return {"studies": studies, "runs": runs}


def _leaves(value: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(value, Mapping):
        if not value:
            yield path, {}
        for key in sorted(value):
            yield from _leaves(value[key], f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        if not value:
            yield path, []
        for index, item in enumerate(value):
            yield from _leaves(item, f"{path}.{index}" if path else str(index))
    else:
        yield path, value


def _display(kind: str, content: Mapping[str, Any]) -> str:
    """Readable text of ``content`` for the unified diff (templates and prose as blocks)."""
    data = copy.deepcopy(dict(content))
    blocks = []
    if kind == "prompt":
        blocks.append(("template", str(data.pop("template", ""))))
    if kind == "task":
        for variant, text in sorted((data.pop("prose", None) or {}).items()):
            blocks.append((f"prose.{variant}", str(text)))
    if kind == "detector":
        blocks.append(("rule", str(data.pop("rule", ""))))
    text = yaml.safe_dump(data, sort_keys=True, allow_unicode=True, width=120)
    for name, body in blocks:
        text += f"\n# --- {name} ---\n{body}\n"
    return text


def content_diff(a: Any, b: Any) -> dict[str, Any]:
    """``{unified, structured: [{path, a, b}]}`` between two versions (``GET .../diff/``)."""
    kind = kind_of(a)
    unified = _unified(_display(kind, a.content), _display(kind, b.content), a.ref, b.ref)
    left = dict(_leaves(a.content))
    right = dict(_leaves(b.content))
    structured = [
        {"path": path, "a": left.get(path), "b": right.get(path)}
        for path in sorted(set(left) | set(right))
        if left.get(path) != right.get(path) or (path in left) != (path in right)
    ]
    return {"unified": unified, "structured": structured}
