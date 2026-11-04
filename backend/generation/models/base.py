"""Shared model plumbing: sha columns, contract-validated JSON columns and derived columns.

Section 3 conventions implemented here:

* every JSON column holding a typed payload names its contract in ``CONTRACT_FIELDS``;
  ``full_clean()`` (through ``clean_fields``) and ``save()`` validate it with the contract's
  ``model_validate``, and unknown keys are rejected (every contract uses ``extra="forbid"``);
* derived columns (content hashes, denormalized query columns) are filled by ``derive_fields()``
  before validation and before every save; a supplied value that disagrees is a validation error;
* ``BLOB_FIELDS`` lists the sha columns that reference the blob store (read by
  ``blobs.referenced_shas``); a model whose JSON embeds blob shas also defines ``iter_blob_refs()``.

``QuerySet.update()`` and raw SQL bypass derivation and contract validation, as they bypass ``save()``
everywhere in Django. Frozen columns are also guarded on the bulk path: models with ``IMMUTABLE_FIELDS``
use :class:`FrozenFieldsQuerySet` and write-once rows use :class:`WriteOnceQuerySet`; raw SQL is not guarded.
"""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Mapping
from typing import Any
from typing import ClassVar

from django.core.exceptions import FieldDoesNotExist
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from backend.common.exceptions import APIError

SHA_LENGTH = 64
REF_LENGTH = 140
_EMPTY = ("", None)

Errors = dict[str, list[str]]

sha_validator = RegexValidator(r"^[0-9a-f]{64}$", _("Enter a lowercase hex sha256."), code="invalid_sha")


def validate_rel_path(value: str) -> None:
    """A relative POSIX path: no leading slash, backslash or NUL, no empty, ``.`` or ``..`` segment."""
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or "\x00" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValidationError(
            _("%(value)r is not a relative POSIX path without empty, '.' or '..' segments."),
            code="invalid_path",
            params={"value": value},
        )


def sha_field(verbose_name: str, *, blank: bool = False, db_index: bool = False, **kwargs: Any) -> models.CharField:
    """A ``CharField(max_length=64)`` holding a lowercase hex sha256 (``""`` when ``blank`` and unset)."""
    if blank:
        kwargs.setdefault("default", "")
    return models.CharField(
        verbose_name,
        max_length=SHA_LENGTH,
        blank=blank,
        db_index=db_index,
        validators=[sha_validator],
        **kwargs,
    )


class ImmutableVersionError(APIError):
    """A write to a frozen row: library version content, a run manifest, a finished evaluation, ...

    It is an :class:`~backend.common.exceptions.APIError` with status 409, so a view that lets it escape
    answers ``409 {"detail": ...}``.
    """

    status_code = 409
    code = "locked"


class Contract:
    """How one JSON column is validated: a pydantic model, or any type through a ``TypeAdapter``."""

    __slots__ = ("adapter", "label")

    def __init__(self, schema: Any, *, many: bool = False) -> None:
        target = list[schema] if many else schema
        self.adapter: TypeAdapter[Any] = TypeAdapter(target)
        name = getattr(schema, "__name__", None) or repr(schema)
        self.label = f"list[{name}]" if many else name

    def __repr__(self) -> str:
        return f"Contract({self.label})"

    def validate(self, value: Any) -> Any:
        """The validated value; raises ``pydantic.ValidationError``."""
        return self.adapter.validate_python(value)

    def dump(self, value: Any) -> Any:
        """``value`` validated and dumped back to JSON types (defaults filled, aliases applied)."""
        return self.adapter.dump_python(self.validate(value), mode="json")

    def try_validate(self, value: Any) -> Any | None:
        """The validated value, or ``None`` when ``value`` does not validate (for derivations)."""
        try:
            return self.validate(value)
        except (PydanticValidationError, TypeError, ValueError):
            return None


def format_pydantic_errors(exc: PydanticValidationError, *, limit: int = 10) -> list[str]:
    errors = exc.errors()
    messages = []
    for error in errors[:limit]:
        location = ".".join(str(part) for part in error["loc"]) or "(root)"
        messages.append(f"{location}: {error['msg']}")
    if len(errors) > limit:
        messages.append(f"... and {len(errors) - limit} more errors")
    return messages


def fill_or_check(instance: models.Model, field: str, expected: Any, errors: Errors) -> None:
    """Set ``field`` to ``expected`` when it is empty; record an error when it holds another value."""
    current = getattr(instance, field)
    if current in _EMPTY:
        setattr(instance, field, expected)
    elif current != expected:
        errors.setdefault(field, []).append(
            f"{current!r} does not match the value derived from the row ({expected!r})",
        )


def merge_errors(target: Errors, more: Mapping[str, Iterable[str]]) -> Errors:
    for field, messages in more.items():
        target.setdefault(field, []).extend(messages)
    return target


def field_names(model: type[models.Model], keys: Iterable[str]) -> set[str]:
    """Field names for ``QuerySet.update()`` keywords, which may also be attnames (``stack_profile_id``)."""
    names = set()
    for key in keys:
        try:
            names.add(model._meta.get_field(key).name)  # noqa: SLF001
        except FieldDoesNotExist:
            names.add(key)
    return names


class FrozenFieldsQuerySet(models.QuerySet):
    """Refuses ``update()`` of the model's ``IMMUTABLE_FIELDS`` (the bulk path that bypasses ``save()``)."""

    def update(self, **kwargs: Any) -> int:
        frozen = sorted(field_names(self.model, kwargs) & set(getattr(self.model, "IMMUTABLE_FIELDS", ())))
        if frozen:
            msg = f"{self.model.__name__} rows are immutable; refused bulk update of {', '.join(frozen)}"
            raise ImmutableVersionError(msg)
        return super().update(**kwargs)


class WriteOnceQuerySet(models.QuerySet):
    """Refuses every ``update()``: write-once and append-only rows are never changed in bulk either."""

    def update(self, **kwargs: Any) -> int:
        msg = f"{self.model.__name__} rows are write-once; refused bulk update of {', '.join(sorted(kwargs))}"
        raise ImmutableVersionError(msg)


class ValidatedModel(models.Model):
    """Abstract base: contract-validated JSON columns, derived columns and blob references."""

    # field name -> Contract. ``None`` is skipped for nullable columns.
    CONTRACT_FIELDS: ClassVar[Mapping[str, Contract]] = {}
    # Contract fields validated only when the row is created (immutable afterwards).
    CREATE_ONLY_CONTRACTS: ClassVar[frozenset[str]] = frozenset()
    # sha columns that point into the blob store.
    BLOB_FIELDS: ClassVar[tuple[str, ...]] = ()
    # source field -> derived fields written together with it (keeps ``save(update_fields=...)`` honest).
    DERIVED_FROM: ClassVar[Mapping[str, tuple[str, ...]]] = {}

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        adding = self._state.adding
        errors = self.derive_fields()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            fields = set(update_fields)
            for source, derived in self.DERIVED_FROM.items():
                if source in fields:
                    fields.update(derived)
            kwargs["update_fields"] = sorted(fields)
            checked: Iterable[str] = fields
        else:
            checked = self.CONTRACT_FIELDS.keys()
        if not adding:
            checked = [name for name in checked if name not in self.CREATE_ONLY_CONTRACTS]
        merge_errors(errors, self.contract_errors(checked))
        if errors:
            raise ValidationError(errors)
        super().save(*args, **kwargs)

    def clean_fields(self, exclude: Iterable[str] | None = None) -> None:
        excluded = set(exclude or ())
        errors = {name: messages for name, messages in self.derive_fields().items() if name not in excluded}
        try:
            super().clean_fields(exclude=exclude)
        except ValidationError as exc:
            errors = exc.update_error_dict(errors)
        names = [name for name in self.CONTRACT_FIELDS if name not in excluded and name not in errors]
        merge_errors(errors, self.contract_errors(names))
        if errors:
            raise ValidationError(errors)

    # -- hooks -------------------------------------------------------------------------------------

    def derive_fields(self) -> Errors:
        """Fill derived columns from their sources and return ``{field: [mismatch messages]}``.

        Must be idempotent. Subclasses call ``super().derive_fields()`` and merge.
        """
        return {}

    def contract_errors(self, fields: Iterable[str] | None = None) -> Errors:
        """``{field: [messages]}`` for every listed contract column whose value does not validate."""
        names = list(self.CONTRACT_FIELDS) if fields is None else [f for f in fields if f in self.CONTRACT_FIELDS]
        errors: Errors = {}
        for name in names:
            value = getattr(self, name)
            if value is None and self._meta.get_field(name).null:
                continue
            try:
                self.CONTRACT_FIELDS[name].validate(value)
            except PydanticValidationError as exc:
                errors[name] = format_pydantic_errors(exc)
        return errors

    def validate_contracts(self, fields: Iterable[str] | None = None) -> None:
        """Raise ``ValidationError`` when a contract column (every one, or ``fields``) does not validate."""
        errors = self.contract_errors(fields)
        if errors:
            raise ValidationError(errors)

    # -- helpers for subclasses --------------------------------------------------------------------

    def stored_values(self, *fields: str) -> dict[str, Any] | None:
        """The stored values of ``fields`` for this row, or ``None`` when it is not in the database yet."""
        if self._state.adding or self.pk is None:
            return None
        return type(self)._base_manager.filter(pk=self.pk).values(*fields).first()  # noqa: SLF001

    def refuse_changes(self, fields: Iterable[str], reason: str, *, update_fields: Iterable[str] | None) -> None:
        """Raise :class:`ImmutableVersionError` when this save would change any of ``fields``."""
        frozen = list(fields)
        if update_fields is not None:
            frozen = [name for name in frozen if name in set(update_fields)]
        if not frozen:
            return
        stored = self.stored_values(*(self._meta.get_field(name).attname for name in frozen))
        if stored is None:
            return
        changed = [
            name for name in frozen if stored[self._meta.get_field(name).attname] != self.serializable_value(name)
        ]
        if changed:
            msg = f"{type(self).__name__} {self.pk}: {reason}; refused change to {', '.join(sorted(changed))}"
            raise ImmutableVersionError(msg)
