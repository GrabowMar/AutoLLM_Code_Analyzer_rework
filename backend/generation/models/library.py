"""Library models (spec section 3.3): versioned instruments, task specs and the blob index.

Every library entity is **immutable per ``(slug, version)``** (decision D8): editing creates version+1
(``library.service.new_version``, WP-03), archiving replaces deleting, and studies pin exact versions.
``content`` is validated by the kind's contract and stored in normalized form
(``contracts.library.normalize_content``: validated, defaults filled, JSON types), so equal content
always gets the same ``content_hash = H({"kind", "schema", "content"})``. Slug, name, description,
version, ids and owner are never hashed.
"""

from __future__ import annotations

from typing import Any
from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from backend.generation import identity
from backend.generation.contracts._base import Sha256
from backend.generation.contracts.library import CONTENT_MODELS
from backend.generation.contracts.library import SCHEMA_VERSIONS
from backend.generation.contracts.library import ProseVariant
from backend.generation.contracts.library import TaskDerived
from backend.generation.contracts.library import TaskLint
from backend.generation.contracts.library import TaskOracleEntry
from backend.generation.contracts.library import pin_content_refs
from backend.generation.models.base import Contract
from backend.generation.models.base import Errors
from backend.generation.models.base import FrozenFieldsQuerySet
from backend.generation.models.base import ImmutableVersionError
from backend.generation.models.base import ValidatedModel
from backend.generation.models.base import fill_or_check
from backend.generation.models.base import sha_field
from backend.generation.models.base import sha_validator
from backend.generation.models.choices import DetectorEngine
from backend.generation.models.choices import Mode
from backend.generation.models.choices import PolicyTier
from backend.generation.models.choices import PromptRole
from backend.generation.models.choices import SecurityHint
from backend.generation.models.choices import StarterLevel
from backend.generation.models.choices import Visibility

__all__ = [
    "Blob",
    "EvalSuite",
    "Guidance",
    "HarnessPreset",
    "ImmutableVersionError",
    "PatternDetector",
    "PromptSet",
    "PromptTemplate",
    "ResourcePolicy",
    "SamplingPreset",
    "StackProfile",
    "Starter",
    "StyleDirective",
    "TaskSpec",
    "TaskSpecVersion",
    "VersionedEntity",
]

_SYSTEM_VISIBILITY = Q(is_system=True, visibility=Visibility.SYSTEM) | (
    Q(is_system=False) & ~Q(visibility=Visibility.SYSTEM)
)
# Columns of every versioned row that never change after creation. Each concrete kind adds its
# ``CONTENT_COLUMNS`` and its extras (``immutable_fields``); only name, description, is_archived and
# visibility (and the audit columns created_by / created_at) may change (section 3.3).
VERSION_IMMUTABLE_FIELDS = (
    "slug",
    "version",
    "schema_version",
    "content",
    "content_hash",
    "derived_from",
    "is_system",
)
MUTABLE_VERSION_FIELDS = ("name", "description", "is_archived", "visibility")


def immutable_fields(content_columns: dict[str, str], *extras: str) -> tuple[str, ...]:
    """``VERSION_IMMUTABLE_FIELDS`` + the kind's content-derived columns + ``extras``."""
    return (*VERSION_IMMUTABLE_FIELDS, *content_columns, *extras)


# kind -> concrete VersionedEntity model (filled by __init_subclass__; resolves refs inside content).
_KIND_MODELS: dict[str, type[VersionedEntity]] = {}


class VersionedQuerySet(FrozenFieldsQuerySet):
    def latest_versions(self) -> VersionedQuerySet:
        """One row per slug: the highest non-archived version."""
        return self.filter(is_archived=False).order_by("slug", "-version").distinct("slug")


class VersionedManager(models.Manager.from_queryset(VersionedQuerySet)):  # type: ignore[misc]
    def latest(self, slug: str) -> Any:  # type: ignore[override]
        """The latest non-archived version of ``slug`` (raises ``DoesNotExist`` when there is none).

        Overrides ``Manager.latest(*fields)`` on purpose (section 3.3: ``objects.latest(slug)``); use
        ``.all().latest(field)`` for Django's field-ordered variant.
        """
        row = self.filter(slug=slug, is_archived=False).order_by("-version").first()
        if row is None:
            msg = f"no non-archived {self.model.__name__} version of {slug!r}"
            raise self.model.DoesNotExist(msg)
        return row


class VersionedEntity(ValidatedModel):
    """Abstract versioned library instrument (section 3.3)."""

    KIND: ClassVar[str]
    IMMUTABLE_FIELDS: ClassVar[tuple[str, ...]] = VERSION_IMMUTABLE_FIELDS
    # column -> dotted path into the normalized content; filled on create, verified afterwards.
    CONTENT_COLUMNS: ClassVar[dict[str, str]] = {}
    CREATE_ONLY_CONTRACTS = frozenset({"content"})
    DERIVED_FROM: ClassVar[dict[str, tuple[str, ...]]] = {}

    slug = models.SlugField(_("slug"), max_length=100)
    version = models.PositiveIntegerField(_("version"))
    name = models.CharField(_("name"), max_length=200)
    description = models.TextField(_("description"), blank=True, default="")
    schema_version = models.PositiveSmallIntegerField(_("schema version"))
    content = models.JSONField(_("content"))
    content_hash = sha_field(_("content hash"), db_index=True)
    derived_from = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="derivatives",
        help_text="The version this one was edited or forked from",
    )
    is_system = models.BooleanField(_("system entity"), default=False)
    is_archived = models.BooleanField(_("archived"), default=False)
    visibility = models.CharField(
        _("visibility"),
        max_length=8,
        choices=Visibility.choices,
        default=Visibility.PRIVATE,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = VersionedManager()

    class Meta:
        abstract = True
        ordering = ["slug", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["slug", "version"], name="%(app_label)s_%(class)s_slug_version_uniq"),
            models.CheckConstraint(condition=~Q(content_hash=""), name="%(app_label)s_%(class)s_content_hash_set"),
            models.CheckConstraint(condition=Q(version__gte=1), name="%(app_label)s_%(class)s_version_gte_1"),
            models.CheckConstraint(condition=_SYSTEM_VISIBILITY, name="%(app_label)s_%(class)s_system_visibility"),
        ]
        indexes = [models.Index(fields=["slug", "-version"], name="%(class)s_slug_ver")]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        kind = cls.__dict__.get("KIND")
        if kind is not None:
            cls.CONTRACT_FIELDS = {"content": Contract(CONTENT_MODELS[kind])}
            if "IMMUTABLE_FIELDS" not in cls.__dict__:
                cls.IMMUTABLE_FIELDS = immutable_fields(cls.CONTENT_COLUMNS)
            _KIND_MODELS[kind] = cls  # only the concrete kinds set KIND

    def __str__(self) -> str:
        return self.ref

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            self.refuse_changes(
                self.IMMUTABLE_FIELDS,
                f"{self.ref} is immutable: create a new version instead",
                update_fields=kwargs.get("update_fields"),
            )
        super().save(*args, **kwargs)

    # -- identity --------------------------------------------------------------------------------

    @property
    def ref(self) -> str:
        """``"<slug>@<version>"``."""
        return f"{self.slug}@{self.version}"

    @property
    def pinned_ref(self) -> str:
        """``"<slug>@<version>#<content_hash[:12]>"``."""
        return identity.pinned_ref(self.slug, self.version, self.content_hash)

    @classmethod
    def normalize(cls, content: Any) -> dict[str, Any]:
        """``content`` validated by the kind's contract and dumped with every default filled."""
        return Contract(CONTENT_MODELS[cls.KIND]).dump(content)

    @classmethod
    def compute_content_hash(cls, content: Any, schema_version: int | None = None) -> str:
        """``H({"kind", "schema", "content"})`` over the normalized content."""
        schema = SCHEMA_VERSIONS[cls.KIND] if schema_version is None else schema_version
        return identity.library_content_hash(cls.KIND, schema, cls.normalize(content))

    # -- validation ------------------------------------------------------------------------------

    def derive_fields(self) -> Errors:
        errors = super().derive_fields()
        if not self._state.adding:
            return errors  # content and its hash are frozen; refuse_changes guards them
        if self.schema_version is None:
            self.schema_version = SCHEMA_VERSIONS[self.KIND]
        if self.schema_version != SCHEMA_VERSIONS[self.KIND]:
            errors["schema_version"] = [
                f"schema version {self.schema_version} is not the current {self.KIND} schema "
                f"({SCHEMA_VERSIONS[self.KIND]}); migrate the content first",
            ]
            return errors
        self.content, ref_errors = pin_content_refs(self.KIND, self.content, _resolve_library_ref)
        for location, messages in ref_errors.items():
            errors.setdefault("content", []).extend(f"{location}: {message}" for message in messages)
        normalized = Contract(CONTENT_MODELS[self.KIND]).try_validate(self.content)
        if normalized is None:
            return errors  # contract_errors reports the content itself
        self.content = normalized.model_dump(mode="json")
        fill_or_check(
            self,
            "content_hash",
            identity.library_content_hash(self.KIND, self.schema_version, self.content),
            errors,
        )
        for column, path in self.CONTENT_COLUMNS.items():
            fill_or_check(self, column, _dig(self.content, path), errors)
        return errors

    def clean(self) -> None:
        super().clean()
        if (self.visibility == Visibility.SYSTEM) != bool(self.is_system):
            raise ValidationError({"visibility": _("Visibility 'system' is used exactly for system entities.")})


def _resolve_library_ref(kind: str, slug: str, version: int) -> str | None:
    """The stored ``content_hash`` of ``kind`` ``slug@version`` (``None`` when there is no such row)."""
    model = _KIND_MODELS.get(kind)
    if model is None:
        return None
    return model._base_manager.filter(slug=slug, version=version).values_list("content_hash", flat=True).first()  # noqa: SLF001


def _dig(content: dict[str, Any], path: str) -> Any:
    value: Any = content
    for part in path.split("."):
        value = value[part] if isinstance(value, dict) else None
    return value


# ---------------------------------------------------------------------------
# Concrete kinds
# ---------------------------------------------------------------------------


class StackProfile(VersionedEntity):
    KIND = "stack"
    CONTENT_COLUMNS: ClassVar[dict[str, str]] = {
        "backend_framework": "backend.framework",
        "frontend_framework": "frontend.framework",
        "database": "database",
    }

    backend_framework = models.CharField(_("backend framework"), max_length=24, db_index=True)
    frontend_framework = models.CharField(_("frontend framework"), max_length=16, db_index=True)
    database = models.CharField(_("database"), max_length=16)

    class Meta(VersionedEntity.Meta):
        verbose_name = _("Stack profile")
        verbose_name_plural = _("Stack profiles")


class Starter(VersionedEntity):
    """A composed, digest-pinned starter tree for one stack profile and level."""

    KIND = "starter"
    CONTENT_COLUMNS: ClassVar[dict[str, str]] = {"level": "level"}
    IMMUTABLE_FIELDS = immutable_fields(CONTENT_COLUMNS, "stack_profile", "tree_hash")

    stack_profile = models.ForeignKey(StackProfile, on_delete=models.PROTECT, related_name="starters")
    level = models.CharField(_("level"), max_length=8, choices=StarterLevel.choices)
    tree_hash = sha_field(_("tree hash"))

    class Meta(VersionedEntity.Meta):
        verbose_name = _("Starter")
        verbose_name_plural = _("Starters")

    @classmethod
    def iter_blob_refs(cls) -> Any:
        """The file blobs of every starter version (``content.files[*].blob``)."""
        for files in cls._base_manager.values_list("content__files", flat=True).iterator():
            for entry in (files or {}).values():
                if isinstance(entry, dict) and entry.get("blob"):
                    yield entry["blob"]

    def derive_fields(self) -> Errors:
        errors = super().derive_fields()
        if self._state.adding and "content" not in errors and isinstance(self.content, dict):
            files = self.content.get("files") or {}
            if all(isinstance(entry, dict) for entry in files.values()):
                try:
                    expected = identity.tree_hash(
                        (path, int(entry["mode"]), entry["blob"]) for path, entry in files.items()
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    errors.setdefault("content", []).append(f"files: {exc}")
                else:
                    fill_or_check(self, "tree_hash", expected, errors)
        return errors

    def clean(self) -> None:
        super().clean()
        ref = (self.content or {}).get("stack_profile") if isinstance(self.content, dict) else None
        if self.stack_profile_id is not None and ref:
            profile = self.stack_profile
            if ref != profile.pinned_ref:
                raise ValidationError(
                    {"stack_profile": _("content.stack_profile %(ref)s is not %(profile)s.")},
                    params={"ref": ref, "profile": profile.pinned_ref},
                )


class StyleDirective(VersionedEntity):
    KIND = "style"
    CONTENT_COLUMNS: ClassVar[dict[str, str]] = {"system": "system"}

    system = models.CharField(_("styling system"), max_length=24, db_index=True)

    class Meta(VersionedEntity.Meta):
        verbose_name = _("Style directive")
        verbose_name_plural = _("Style directives")


class Guidance(VersionedEntity):
    KIND = "guidance"
    CONTENT_COLUMNS: ClassVar[dict[str, str]] = {"security_hint": "security_hint"}

    security_hint = models.CharField(_("security hint"), max_length=16, choices=SecurityHint.choices)

    class Meta(VersionedEntity.Meta):
        verbose_name = _("Guidance")
        verbose_name_plural = _("Guidance")


class PromptTemplate(VersionedEntity):
    KIND = "prompt"
    CONTENT_COLUMNS: ClassVar[dict[str, str]] = {"role": "role"}

    role = models.CharField(_("role"), max_length=24, choices=PromptRole.choices, db_index=True)

    class Meta(VersionedEntity.Meta):
        verbose_name = _("Prompt template")
        verbose_name_plural = _("Prompt templates")


class PromptSet(VersionedEntity):
    KIND = "prompt_set"
    CONTENT_COLUMNS: ClassVar[dict[str, str]] = {"mode": "mode"}

    mode = models.CharField(_("mode"), max_length=8, choices=Mode.choices)

    class Meta(VersionedEntity.Meta):
        verbose_name = _("Prompt set")
        verbose_name_plural = _("Prompt sets")


class HarnessPreset(VersionedEntity):
    KIND = "harness_preset"
    CONTENT_COLUMNS: ClassVar[dict[str, str]] = {"harness": "harness", "harness_version": "harness_version"}

    harness = models.CharField(_("harness"), max_length=32, db_index=True)
    harness_version = models.CharField(_("harness version"), max_length=32)

    class Meta(VersionedEntity.Meta):
        verbose_name = _("Harness preset")
        verbose_name_plural = _("Harness presets")


class SamplingPreset(VersionedEntity):
    KIND = "sampling"

    class Meta(VersionedEntity.Meta):
        verbose_name = _("Sampling preset")
        verbose_name_plural = _("Sampling presets")


class ResourcePolicy(VersionedEntity):
    """A budget preset (UI name **Budget preset**); content is ``ResourcePolicyContent`` (section 4.2)."""

    KIND = "resource_policy"
    IMMUTABLE_FIELDS = immutable_fields(VersionedEntity.CONTENT_COLUMNS, "tier")

    tier = models.CharField(_("tier"), max_length=12, choices=PolicyTier.choices, default=PolicyTier.CUSTOM)

    class Meta(VersionedEntity.Meta):
        verbose_name = _("Budget preset")
        verbose_name_plural = _("Budget presets")


class EvalSuite(VersionedEntity):
    KIND = "eval_suite"
    CONTENT_COLUMNS: ClassVar[dict[str, str]] = {"tester_version": "tester.version"}

    tester_version = models.PositiveSmallIntegerField(_("tester version"))

    class Meta(VersionedEntity.Meta):
        verbose_name = _("Evaluation suite")
        verbose_name_plural = _("Evaluation suites")


class PatternDetector(VersionedEntity):
    KIND = "detector"
    CONTENT_COLUMNS: ClassVar[dict[str, str]] = {"engine": "engine", "feature_key": "feature_key"}

    engine = models.CharField(_("engine"), max_length=16, choices=DetectorEngine.choices)
    feature_key = models.SlugField(_("feature key"), max_length=100, db_index=True)

    class Meta(VersionedEntity.Meta):
        verbose_name = _("Pattern detector")
        verbose_name_plural = _("Pattern detectors")


# ---------------------------------------------------------------------------
# Tasks: a header row (the blocking unit, with per-version oracle status) plus versions
# ---------------------------------------------------------------------------


class TaskSpec(models.Model):
    """Task header: the slug, ownership and the number of the latest version."""

    slug = models.SlugField(_("slug"), max_length=100, unique=True)
    is_system = models.BooleanField(_("system task"), default=False)
    is_archived = models.BooleanField(_("archived"), default=False)
    visibility = models.CharField(
        _("visibility"),
        max_length=8,
        choices=Visibility.choices,
        default=Visibility.PRIVATE,
    )
    latest_version = models.PositiveIntegerField(_("latest version"), default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Task spec")
        verbose_name_plural = _("Task specs")
        ordering = ["slug"]
        constraints = [
            models.CheckConstraint(condition=_SYSTEM_VISIBILITY, name="generation_taskspec_system_visibility"),
        ]

    def __str__(self) -> str:
        return self.slug


class TaskSpecVersion(ValidatedModel):
    """One immutable version of a task (``TaskSpecContent``, schema 4).

    ``content``, ``content_hash``, ``structured_hash`` and ``prose_hashes`` are frozen at creation;
    ``derived``, ``lint`` and ``oracle`` stay mutable.
    """

    KIND: ClassVar[str] = "task"
    IMMUTABLE_FIELDS: ClassVar[tuple[str, ...]] = (
        "task",
        "version",
        "schema_version",
        "content",
        "content_hash",
        "structured_hash",
        "prose_hashes",
        "derived_from",
    )
    CONTRACT_FIELDS: ClassVar[dict[str, Contract]] = {
        "content": Contract(CONTENT_MODELS["task"]),
        "prose_hashes": Contract(dict[ProseVariant, Sha256]),
        "derived": Contract(TaskDerived),
        "lint": Contract(TaskLint),
        "oracle": Contract(dict[str, TaskOracleEntry]),
    }
    CREATE_ONLY_CONTRACTS = frozenset({"content", "prose_hashes"})

    task = models.ForeignKey(TaskSpec, on_delete=models.PROTECT, related_name="versions")
    version = models.PositiveIntegerField(_("version"))
    name = models.CharField(_("name"), max_length=200)
    schema_version = models.PositiveSmallIntegerField(_("schema version"), default=SCHEMA_VERSIONS["task"])
    content = models.JSONField(_("content"))
    content_hash = sha_field(_("content hash"), db_index=True)
    structured_hash = sha_field(_("structured hash"), help_text='H(content minus "prose")')
    prose_hashes = models.JSONField(_("prose hashes"), default=dict, help_text="{variant: text_sha}")
    derived = models.JSONField(_("derived"), null=True, blank=True, help_text="TaskDerived; computed, not hashed")
    lint = models.JSONField(_("lint"), null=True, blank=True)
    oracle = models.JSONField(
        _("oracle status"),
        default=dict,
        blank=True,
        help_text="{stack_ref: {status, run_id, eval_suite_ref, at}}; written only by evaluation/baseline.py",
    )
    derived_from = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="derivatives",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = FrozenFieldsQuerySet.as_manager()

    class Meta:
        verbose_name = _("Task spec version")
        verbose_name_plural = _("Task spec versions")
        ordering = ["task", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["task", "version"], name="generation_taskspecversion_task_version_uniq"),
            models.CheckConstraint(condition=~Q(content_hash=""), name="generation_taskspecversion_content_hash_set"),
            models.CheckConstraint(condition=Q(version__gte=1), name="generation_taskspecversion_version_gte_1"),
        ]
        indexes = [models.Index(fields=["task", "-version"], name="taskspecversion_task_ver")]

    def __str__(self) -> str:
        return self.ref

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            self.refuse_changes(
                self.IMMUTABLE_FIELDS,
                f"task version {self.pk} is immutable: create a new version instead",
                update_fields=kwargs.get("update_fields"),
            )
        super().save(*args, **kwargs)

    @property
    def slug(self) -> str:
        return self.task.slug

    @property
    def ref(self) -> str:
        return f"{self.task.slug}@{self.version}"

    @property
    def pinned_ref(self) -> str:
        return identity.pinned_ref(self.task.slug, self.version, self.content_hash)

    @property
    def unit_key(self) -> str:
        """The app-mode study unit key ``task:<slug>@<v>`` (the prose variant is a factor, not a unit)."""
        return f"task:{self.ref}"

    def derive_fields(self) -> Errors:
        errors = super().derive_fields()
        if not self._state.adding:
            return errors
        if self.schema_version != SCHEMA_VERSIONS["task"]:
            errors["schema_version"] = [f"task content schema must be {SCHEMA_VERSIONS['task']}"]
            return errors
        normalized = Contract(CONTENT_MODELS["task"]).try_validate(self.content)
        if normalized is None:
            return errors
        self.content = normalized.model_dump(mode="json")
        fill_or_check(
            self,
            "content_hash",
            identity.library_content_hash("task", self.schema_version, self.content),
            errors,
        )
        fill_or_check(self, "structured_hash", identity.task_structured_hash(self.content), errors)
        expected_prose = identity.task_prose_hashes(self.content)
        if not self.prose_hashes:
            self.prose_hashes = expected_prose
        elif self.prose_hashes != expected_prose:
            errors.setdefault("prose_hashes", []).append("does not match text_sha of content.prose")
        return errors


# ---------------------------------------------------------------------------
# Blob index (section 3.2): the files live under settings.GENERATION_BLOBS_ROOT
# ---------------------------------------------------------------------------


class Blob(models.Model):
    """One content-addressed blob (``<root>/sha256/<aa>/<bb>/<sha>.<codec>``); written by ``blobs.py``."""

    class Codec(models.TextChoices):
        ZSTD = "zst", "zstd"
        GZIP = "gz", "gzip"

    sha256 = models.CharField(_("sha256"), max_length=64, primary_key=True, validators=[sha_validator])
    size = models.PositiveBigIntegerField(_("size (uncompressed bytes)"))
    codec = models.CharField(_("codec"), max_length=8, choices=Codec.choices)
    media_type = models.CharField(_("media type"), max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Blob")
        verbose_name_plural = _("Blobs")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.sha256[:12]} ({self.media_type}, {self.size} B)"
