"""Library package format v4: export and import of versioned library entities (spec section 9.1).

Ported in spirit from ``_legacy/services/packages`` (visibility-checked export, one-transaction import,
reuse of identical content, rename on conflict); the v1-v3 formats (app specs, blocks, profiles, stacks) have
no v3 equivalent and are refused (clean break, decision D3).

Document (YAML, ``application/x-yaml``)::

    kind: llm-lab-library-package
    package_schema_version: 4
    exported_at: <ISO timestamp>
    entities:                      # dependency order (prompts first, tasks last)
      - {kind, slug, version, name, description, schema_version, content_hash, content,
         derived_from: "slug@v" | null, archived, tier?}
    blobs:                         # every starter file blob, base64
      - {sha256, media_type, data}
    omitted: [{ref, reason}]       # referenced entities the exporter could not see

Export takes ``kind:slug@v`` refs and adds every entity they reference (transitively) and every blob a
starter needs. Import verifies each entity's ``content_hash`` and each blob's sha, then, in one transaction
and in dependency order:

* **reused**: an entity the importer can see with the same kind, slug and content hash (or a system entity
  with the same content hash) is used as is;
* **created**: a new slug starts at v1 owned by the importer; a slug the importer owns gets a new version;
* **conflicts**: a slug owned by someone else (or a system slug) with other content is renamed to
  ``<slug>-imported[-n]`` (``on_conflict=rename``, the default) or refuses the whole import
  (``on_conflict=error``).

Refs inside imported content are rewritten to the rows actually used, so pinned hashes stay true.
``mode=dry_run`` does all of it and rolls the database back.
"""

from __future__ import annotations

import base64
import binascii
import copy
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

import yaml
from django.db import transaction
from django.utils import timezone

from backend.generation import blobs
from backend.generation import identity
from backend.generation.contracts.library import CONTENT_MODELS
from backend.generation.contracts.library import LIBRARY_REF_FIELDS
from backend.generation.contracts.library import SCHEMA_VERSIONS
from backend.generation.contracts.library import iter_content_refs
from backend.generation.library import service
from backend.generation.library.seeding import SEED_ORDER

if TYPE_CHECKING:
    from backend.users.models import User

__all__ = [
    "IMPORT_MODES",
    "MAX_PACKAGE_BYTES",
    "ON_CONFLICT",
    "PACKAGE_KIND",
    "PACKAGE_MEDIA_TYPE",
    "PACKAGE_SCHEMA_VERSION",
    "ImportResult",
    "dump_package",
    "export_package",
    "import_package",
    "parse_package",
]

PACKAGE_KIND: Final = "llm-lab-library-package"
PACKAGE_SCHEMA_VERSION: Final = 4
PACKAGE_MEDIA_TYPE: Final = "application/x-yaml"
IMPORT_MODES: Final = ("dry_run", "apply")
ON_CONFLICT: Final = ("rename", "error")
MAX_PACKAGE_BYTES: Final = 32 * 1024 * 1024
_RENAME_SUFFIX: Final = "-imported"
_OMITTED_REASON: Final = "missing or not visible to you"  # never tells a private row from a missing one


def _bad(message: str) -> service.LibraryError:
    return service.LibraryError(message, status=400, code="invalid")


# =====================================================================================
# Export
# =====================================================================================


def _entity_payload(row: Any) -> dict[str, Any]:
    kind = service.kind_of(row)
    payload: dict[str, Any] = {
        "kind": kind,
        "slug": service.slug_of(row),
        "version": row.version,
        "name": row.name,
        "description": getattr(row, "description", "") or "",
        "schema_version": row.schema_version,
        "content_hash": row.content_hash,
        "content": copy.deepcopy(row.content),
        "derived_from": row.derived_from.ref if row.derived_from_id else None,
        "archived": bool(service._header(row).is_archived),  # noqa: SLF001
    }
    if kind == "resource_policy":
        payload["tier"] = row.tier
    return payload


def export_package(refs: Iterable[str], *, user: User | None, include_dependencies: bool = True) -> dict[str, Any]:
    """A v4 package of ``refs`` (``kind:slug@v[#hash12]``) and, by default, everything they reference.

    A requested ref the user cannot see is a 404; a dependency they cannot see is listed in ``omitted``.
    """
    rows: dict[tuple[str, str, int], Any] = {}
    omitted: list[dict[str, str]] = []
    pending: list[Any] = []
    for ref in refs:
        parsed = service.parse_ref(ref)
        if parsed.kind is None:
            msg = f"export refs name their kind: kind:slug@v ({ref!r})"
            raise service.LibraryError(
                msg,
                status=422,
                code="invalid",
            )
        pending.append(service.resolve_ref(ref, user=user))
    while pending:
        row = pending.pop()
        key = (service.kind_of(row), service.slug_of(row), row.version)
        if key in rows:
            continue
        rows[key] = row
        if not include_dependencies:
            continue
        for _location, target, ref in iter_content_refs(key[0], row.content):
            try:
                pending.append(service.resolve_ref(ref, kind=target, user=user))
            except service.LibraryError:
                entry = {"ref": f"{target}:{ref}", "reason": _OMITTED_REASON}
                if entry not in omitted:
                    omitted.append(entry)
    ordered = sorted(rows.items(), key=lambda item: (SEED_ORDER.index(item[0][0]), item[0][1], item[0][2]))
    entities = [_entity_payload(row) for _, row in ordered]
    shas = sorted(
        {
            str(entry["blob"])
            for item in entities
            if item["kind"] == "starter"
            for entry in (item["content"].get("files") or {}).values()
        },
    )
    blob_rows = []
    for sha in shas:
        try:
            data = blobs.get_bytes(sha)
        except (LookupError, ValueError, OSError):
            omitted.append({"ref": f"blob:{sha}", "reason": "missing from the blob store"})
            continue
        blob_rows.append(
            {"sha256": sha, "media_type": "application/octet-stream", "data": base64.b64encode(data).decode()},
        )
    package: dict[str, Any] = {
        "kind": PACKAGE_KIND,
        "package_schema_version": PACKAGE_SCHEMA_VERSION,
        "exported_at": timezone.now().isoformat(),
        "entities": entities,
        "blobs": blob_rows,
    }
    if omitted:
        package["omitted"] = omitted
    return package


def dump_package(package: Mapping[str, Any]) -> str:
    """The package as YAML text (``PACKAGE_MEDIA_TYPE``)."""
    return yaml.safe_dump(dict(package), sort_keys=False, allow_unicode=True, width=120)


# =====================================================================================
# Parse and validate
# =====================================================================================


def parse_package(text: str | bytes) -> dict[str, Any]:
    """Parse and structurally validate a v4 package (400 ``invalid`` otherwise)."""
    if isinstance(text, bytes):
        if len(text) > MAX_PACKAGE_BYTES:
            msg = f"the package is larger than {MAX_PACKAGE_BYTES} bytes"
            raise _bad(msg)
        text = text.decode("utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = f"the package is not valid YAML: {exc}"
        raise _bad(msg) from exc
    if not isinstance(data, Mapping) or data.get("kind") != PACKAGE_KIND:
        msg = f"not a library package (kind must be {PACKAGE_KIND!r})"
        raise _bad(msg)
    version = data.get("package_schema_version")
    if version != PACKAGE_SCHEMA_VERSION:
        msg = (
            f"unsupported package schema version {version!r}: v3 imports only v{PACKAGE_SCHEMA_VERSION} packages "
            "(template packages v1-v3 describe removed v2 entities)"
        )
        raise _bad(
            msg,
        )
    entities = data.get("entities") or []
    blob_rows = data.get("blobs") or []
    if not isinstance(entities, list) or not isinstance(blob_rows, list):
        msg = "'entities' and 'blobs' must be lists"
        raise _bad(msg)
    for index, entity in enumerate(entities):
        if not isinstance(entity, Mapping):
            msg = f"entities.{index} must be a mapping"
            raise _bad(msg)
        missing = [key for key in ("kind", "slug", "version", "content", "content_hash") if key not in entity]
        if missing:
            msg = f"entities.{index} lacks {', '.join(missing)}"
            raise _bad(msg)
        if entity["kind"] not in CONTENT_MODELS:
            msg = f"entities.{index}: unknown kind {entity['kind']!r}"
            raise _bad(msg)
    return dict(data)


def _decoded_blobs(blob_rows: list[Any]) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for index, item in enumerate(blob_rows):
        if not isinstance(item, Mapping):
            msg = f"blobs.{index} must be a mapping"
            raise _bad(msg)
        try:
            data = base64.b64decode(str(item.get("data", "")), validate=True)
        except (binascii.Error, ValueError) as exc:
            msg = f"blobs.{index}: data is not base64"
            raise _bad(msg) from exc
        sha = str(item.get("sha256", ""))
        if identity.bytes_sha(data) != sha:
            msg = f"blobs.{index}: content does not match sha256 {sha}"
            raise _bad(msg)
        out[sha] = data
    return out


# =====================================================================================
# Import
# =====================================================================================


@dataclass
class ImportResult:
    mode: str
    created: list[dict[str, Any]] = field(default_factory=list)
    reused: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    refused: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "created": self.created, "reused": self.reused, "conflicts": self.conflicts}


def _remap(kind: str, content: Mapping[str, Any], ref_map: Mapping[tuple[str, str], str]) -> dict[str, Any]:
    remapped = copy.deepcopy(dict(content))
    for path, target in LIBRARY_REF_FIELDS.get(kind, ()):
        head, _, tail = path.partition(".")
        if tail == "*":
            mapping = remapped.get(head)
            slots = [(mapping, key) for key in list(mapping)] if isinstance(mapping, dict) else []
        else:
            slots = [(remapped, head)] if head in remapped else []
        for container, key in slots:
            value = container[key]
            if isinstance(value, str) and (target, value) in ref_map:
                container[key] = ref_map[(target, value)]
    return remapped


def _free_slug(kind: str, slug: str) -> str:
    base = f"{slug[: 100 - len(_RENAME_SUFFIX) - 4]}{_RENAME_SUFFIX}"
    candidate = base
    index = 2
    while service.slug_rows(kind, candidate).exists():
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def _reusable(kind: str, slug: str, content_hash: str, user: User | None) -> Any | None:
    same_slug = service.slug_rows(kind, slug).filter(content_hash=content_hash).first()
    if same_slug is not None and service.can_view(same_slug, user):
        return same_slug
    system_q = {"task__is_system": True} if kind == "task" else {"is_system": True}
    return service.model_for(kind).objects.filter(content_hash=content_hash, **system_q).order_by("-version").first()


def _import_entity(
    entity: Mapping[str, Any],
    *,
    user: User | None,
    on_conflict: str,
    ref_map: dict[tuple[str, str], str],
    slug_map: dict[tuple[str, str], str],
    result: ImportResult,
) -> None:
    kind = str(entity["kind"])
    slug = str(entity["slug"])
    version = int(entity["version"])
    schema = int(entity.get("schema_version") or SCHEMA_VERSIONS[kind])
    old_ref = f"{slug}@{version}#{str(entity['content_hash'])[:12]}"
    label = f"{kind}:{slug}@{version}"
    if identity.library_content_hash(kind, schema, entity["content"]) != entity["content_hash"]:
        msg = f"{label}: content does not match its content_hash (edited by hand?)"
        raise _bad(msg)
    content = _remap(kind, entity["content"], ref_map)
    new_hash = service.content_hash_of(kind, content)
    reused = _reusable(kind, slug_map.get((kind, slug), slug), new_hash, user)
    if reused is not None:
        ref_map[(kind, old_ref)] = reused.pinned_ref
        result.reused.append({"kind": kind, "ref": old_ref, "as": reused.pinned_ref})
        return
    target_slug = slug_map.get((kind, slug), slug)
    rows = list(service.slug_rows(kind, target_slug))
    task_header_taken = kind == "task" and not rows and service.TaskSpec.objects.filter(slug=target_slug).exists()
    owned = rows and not service.is_system_row(rows[0]) and user is not None and service.owner_id(rows[0]) == user.pk
    # Imported rows are user-owned, hence never a preset (4.3/4.6): a package equal to a system preset already
    # took the reuse path above, so the package's ``tier`` label is never carried over.
    extra = {"tier": "custom"} if kind == "resource_policy" else None
    common = {
        "name": str(entity.get("name") or target_slug),
        "description": str(entity.get("description") or ""),
        "content": content,
        "user": user,
    }
    if owned:
        row = service.new_version(rows[0], base_version=rows[0].version, extra=extra, **common)
    elif rows or task_header_taken:
        conflict = {"kind": kind, "ref": old_ref, "reason": f"{kind} {target_slug!r} exists and is not yours"}
        if on_conflict == "error":
            result.conflicts.append({**conflict, "resolution": "refused"})
            result.refused = True
            return
        renamed = _free_slug(kind, slug)
        slug_map[(kind, slug)] = renamed
        row = service.create(kind, slug=renamed, extra=extra, **common)
        result.conflicts.append({**conflict, "resolution": f"renamed to {renamed}"})
    else:
        row = service.create(kind, slug=target_slug, extra=extra, **common)
    if entity.get("archived"):
        service.archive(row, archived=True, all_versions=False)
    ref_map[(kind, old_ref)] = row.pinned_ref
    result.created.append({"kind": kind, "ref": old_ref, "as": row.pinned_ref})


def import_package(
    package: Mapping[str, Any] | str | bytes,
    *,
    user: User | None,
    mode: str = "dry_run",
    on_conflict: str = "rename",
) -> ImportResult:
    """Import a v4 package in one transaction (``mode=dry_run`` rolls it back).

    Raises :class:`~backend.generation.library.service.LibraryError` for an invalid package (400), an
    entity refused by validation or lint (422, the whole import is rolled back), and, with
    ``on_conflict=error``, any conflict (409 ``in_use``, nothing imported; the result lists the conflicts).
    """
    if mode not in IMPORT_MODES:
        msg = f"mode must be one of {IMPORT_MODES}"
        raise _bad(msg)
    if on_conflict not in ON_CONFLICT:
        msg = f"on_conflict must be one of {ON_CONFLICT}"
        raise _bad(msg)
    data = parse_package(package) if isinstance(package, (str, bytes)) else parse_package(dump_package(package))
    payload = _decoded_blobs(list(data.get("blobs") or []))
    entities = sorted(
        data.get("entities") or [],
        key=lambda item: (SEED_ORDER.index(item["kind"]), str(item["slug"]), int(item["version"])),
    )
    result = ImportResult(mode=mode)

    class _Rollback(Exception):  # noqa: N818 - control flow only
        pass

    try:
        with transaction.atomic():
            for blob in payload.values():
                blobs.put_bytes(blob)
            ref_map: dict[tuple[str, str], str] = {}
            slug_map: dict[tuple[str, str], str] = {}
            for entity in entities:
                _import_entity(
                    entity,
                    user=user,
                    on_conflict=on_conflict,
                    ref_map=ref_map,
                    slug_map=slug_map,
                    result=result,
                )
            if mode == "dry_run" or result.refused:
                raise _Rollback
    except _Rollback:
        pass
    if result.refused and mode == "apply":
        error = service.LibraryError(
            "the package conflicts with existing entities (on_conflict=error); nothing was imported",
            status=409,
            code="in_use",
        )
        error.result = result  # type: ignore[attr-defined]
        raise error
    return result
