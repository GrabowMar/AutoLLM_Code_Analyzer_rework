"""Task lifecycle (owner A): create, update (protocol log), status transitions incl. first open, duplicate, delete.

Spec §5.1 + R1/R3:

- **Create**: a preset fills design/settings (explicit ones win; settings deep-merge over
  the preset's); both documents must validate (``design.validate_design`` /
  ``validate_settings``; error paths prefixed ``design.`` / ``settings.``). A
  ``study_id`` must be the user's (404); ``run_ids`` add apps (pool rules). A
  study brings its current completed runs in addition to ``run_ids`` (union, like
  ``pool.add_runs``); an explicit empty ``run_ids: []`` links the study without adding apps.
- **PATCH**: title / description / instructions / opens_at / closes_at always; after the
  first open, changes to title, description and instructions are appended to
  ``protocol_log`` (sha256 of old and new text). ``mode``, ``design``, ``study_id`` and
  the frozen settings keys only while not frozen (409 ``design_frozen``); the
  ``design.editable_after_freeze`` settings keys stay editable. Settings patches deep-merge
  over the stored settings.
- **Status** (``allowed_actions``): draft → open; open → pause | close; paused → open | close;
  closed → reopen (→ open) | archive; archived → reopen (→ closed). The first open validates
  the open blockers, re-takes every app's snapshot, relabels the apps, builds the items,
  draws the finding samples (``context.draw_finding_samples``), scans sampled findings for
  model names and stores ``frozen_at`` / ``opened_at`` / ``design_hash`` — all in one atomic
  block, so a failure leaves the draft untouched. Close sets ``closed_at`` (and
  ``first_closed_at`` once), unpins every app and stops the task-started ones.
- **Duplicate**: a new draft "Copy of …" with the same design, settings and apps (fresh
  snapshots, new labels and seed), no participants or invites. **Delete**: any status; the
  task's started apps are stopped first.

``open_blockers`` (draft only) and ``open_warnings`` (validity warnings: ``shared_app_state``,
``mixed_spec_versions``, ``model_name_in_source``, ``layout_reveals_condition``) are part of
``TaskDetail``.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
from collections import defaultdict
from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from backend.generation.models import GenerationRun
from backend.human_eval.api.schemas.common import paginate
from backend.human_eval.models import EvalTask
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.services import context as context_service
from backend.human_eval.services import counts
from backend.human_eval.services import design as design_service
from backend.human_eval.services import hosting
from backend.human_eval.services import items as items_service
from backend.human_eval.services import pool
from backend.human_eval.services import snapshot as snapshot_service
from backend.human_eval.services import studies
from backend.human_eval.services.access import researcher_tasks_qs
from backend.human_eval.services.errors import HumanEvalServiceError
from backend.human_eval.services.errors import conflict
from backend.human_eval.services.errors import invalid
from backend.human_eval.services.presets import get_preset

if TYPE_CHECKING:
    from collections.abc import Iterable

TITLE_MAX = 200
TEXT_MAX = design_service.MAX_TEXT
LOGGED_FIELDS = ("title", "description", "instructions")

ACTIONS = ("open", "pause", "close", "reopen", "archive")
#: status → {action: new status}
TRANSITIONS: dict[str, dict[str, str]] = {
    EvalTask.Status.DRAFT: {"open": EvalTask.Status.OPEN},
    EvalTask.Status.OPEN: {"pause": EvalTask.Status.PAUSED, "close": EvalTask.Status.CLOSED},
    EvalTask.Status.PAUSED: {"open": EvalTask.Status.OPEN, "close": EvalTask.Status.CLOSED},
    EvalTask.Status.CLOSED: {"reopen": EvalTask.Status.OPEN, "archive": EvalTask.Status.ARCHIVED},
    EvalTask.Status.ARCHIVED: {"reopen": EvalTask.Status.CLOSED},
}

WARNING_SHARED_APP_STATE = "shared_app_state"
WARNING_MIXED_SPEC_VERSIONS = "mixed_spec_versions"
WARNING_MODEL_NAME_IN_SOURCE = "model_name_in_source"
WARNING_LAYOUT_REVEALS_CONDITION = "layout_reveals_condition"

_APP_RUN_RELATIONS = tuple(f"job__{relation}" for relation in pool.RUN_RELATIONS)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _prefixed(prefix: str, errors: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {"path": f"{prefix}.{error['path']}" if error.get("path") else prefix, "message": str(error["message"])}
        for error in errors
    ]


def validate_documents(mode: Any, design: Any, settings: Any) -> dict[str, Any]:
    """TS ``DesignValidation`` for ``{mode, design, settings}`` (paths prefixed ``design.`` / ``settings.``)."""
    errors: list[dict[str, str]] = []
    normalized_design = None
    if mode not in design_service.MODES:
        errors.append({"path": "mode", "message": "Choose single or pairwise"})
    else:
        normalized_design, design_errors = design_service.validate_design(mode, design)
        errors += _prefixed("design", design_errors)
    normalized_settings, settings_errors = design_service.validate_settings(settings)
    errors += _prefixed("settings", settings_errors)
    return {"ok": not errors, "errors": errors, "design": normalized_design, "settings": normalized_settings}


def _text(data: dict[str, Any], key: str, errors: list[dict[str, str]], *, limit: int) -> str:
    value = data.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        errors.append({"path": key, "message": "Expected text"})
        return ""
    if len(value) > limit:
        errors.append({"path": key, "message": f"At most {limit} characters"})
    return value


def _title(data: dict[str, Any], errors: list[dict[str, str]]) -> str:
    title = _text(data, "title", errors, limit=TITLE_MAX).strip()
    if not title and not any(error["path"] == "title" for error in errors):
        errors.append({"path": "title", "message": "A title is required"})
    return title


def _datetime(value: Any, path: str, errors: list[dict[str, str]]) -> dt.datetime | None:
    if value is None or value == "":
        return None
    parsed = value if isinstance(value, dt.datetime) else parse_datetime(value) if isinstance(value, str) else None
    if parsed is None:
        errors.append({"path": path, "message": "Expected an ISO 8601 date-time"})
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, dt.UTC)
    return parsed


def _check_window(opens_at: dt.datetime | None, closes_at: dt.datetime | None, errors: list[dict[str, str]]) -> None:
    if opens_at is not None and closes_at is not None and closes_at <= opens_at:
        errors.append({"path": "closes_at", "message": "The closing time must be after the opening time"})


def _raise_if(errors: list[dict[str, str]], detail: str = "The task is invalid") -> None:
    if errors:
        raise invalid(detail, errors)


# ---------------------------------------------------------------------------
# Create / update / duplicate / delete
# ---------------------------------------------------------------------------


def create_task(user: Any, data: dict[str, Any]) -> EvalTask:
    """Create a draft task from TS ``TaskCreateInput`` (validated before anything is written)."""
    errors: list[dict[str, str]] = []
    title = _title(data, errors)
    description = _text(data, "description", errors, limit=TEXT_MAX)
    instructions = _text(data, "instructions", errors, limit=TEXT_MAX)
    mode = data.get("mode")
    if mode not in design_service.MODES:
        errors.append({"path": "mode", "message": "Choose single or pairwise"})
    preset = None
    if data.get("preset"):
        preset = get_preset(str(data["preset"]))
        if preset is None:
            errors.append({"path": "preset", "message": "Unknown preset"})
        elif data.get("design") is None and mode in design_service.MODES and preset["mode"] != mode:
            errors.append({"path": "preset", "message": f"This preset is for {preset['mode']} tasks"})
    raw_settings = data.get("settings")
    if raw_settings is not None and not isinstance(raw_settings, dict):
        errors.append({"path": "settings", "message": "The settings must be an object"})
        raw_settings = None
    normalized_design = normalized_settings = None
    if mode in design_service.MODES and not any(error["path"] == "preset" for error in errors):
        if data.get("design") is not None:
            raw_design = data["design"]
        elif preset is not None:
            raw_design = preset["design"]
        else:
            raw_design = design_service.default_design(mode)
        normalized_design, design_errors = design_service.validate_design(mode, raw_design)
        errors += _prefixed("design", design_errors)
        merged = design_service.deep_merge(preset["settings"] if preset else {}, raw_settings or {})
        normalized_settings, settings_errors = design_service.validate_settings(merged)
        errors += _prefixed("settings", settings_errors)
    opens_at = _datetime(data.get("opens_at"), "opens_at", errors)
    closes_at = _datetime(data.get("closes_at"), "closes_at", errors)
    _check_window(opens_at, closes_at, errors)
    _raise_if(errors)

    study = pool.owned_study(user, data["study_id"]) if data.get("study_id") else None
    run_ids = data.get("run_ids")
    runs = pool.resolve_run_ids(user, run_ids) if run_ids else []
    if study is not None and run_ids != []:
        known = {run.pk for run in runs}
        runs += [run for run in pool.study_runs(user, study) if run.pk not in known]
    with transaction.atomic():
        task = EvalTask(
            title=title,
            description=description,
            instructions=instructions,
            mode=mode,
            design=normalized_design,
            settings=normalized_settings,
            opens_at=opens_at,
            closes_at=closes_at,
            created_by=user,
        )
        studies.link_study(task, study)
        task.save()
        pool.create_task_apps(task, runs)
    return task


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _frozen(detail: str, field: str) -> HumanEvalServiceError:
    return HumanEvalServiceError(409, detail, "design_frozen", [{"path": field, "message": detail}])


def update_task(task: EvalTask, user: Any, data: dict[str, Any]) -> EvalTask:
    """PATCH a task with TS ``TaskUpdateInput`` (only the keys present in ``data`` change)."""
    errors: list[dict[str, str]] = []
    changes: dict[str, Any] = {}
    if "title" in data:
        changes["title"] = _title(data, errors)
    for key in ("description", "instructions"):
        if key in data:
            changes[key] = _text(data, key, errors, limit=TEXT_MAX)
    for key in ("opens_at", "closes_at"):
        if key in data:
            changes[key] = _datetime(data[key], key, errors)
    _check_window(changes.get("opens_at", task.opens_at), changes.get("closes_at", task.closes_at), errors)

    study_changed = False
    if "study_id" in data:
        study = pool.owned_study(user, data["study_id"]) if data["study_id"] else None
        study_changed = (study.pk if study else None) != task.study_id
        if study_changed:
            changes["study"] = study

    mode = data.get("mode") or task.mode
    mode_changed = mode != task.mode
    design_given = "design" in data and data["design"] is not None
    if task.is_frozen:
        if mode_changed:
            msg = "The mode cannot change once the task has been opened"
            raise _frozen(msg, "mode")
        if design_given:
            normalized, design_errors = design_service.validate_design(task.mode, data["design"])
            if design_errors or normalized != task.design:
                msg = "The design cannot change once the task has been opened"
                raise _frozen(msg, "design")
        if study_changed:
            msg = "The study cannot change once the task has been opened"
            raise _frozen(msg, "study_id")
    if mode_changed:
        if mode not in design_service.MODES:
            errors.append({"path": "mode", "message": "Choose single or pairwise"})
        else:
            changes["mode"] = mode
    if (design_given or mode_changed) and not task.is_frozen and mode in design_service.MODES:
        raw_design = data["design"] if design_given else task.design
        normalized, design_errors = design_service.validate_design(mode, raw_design)
        errors += _prefixed("design", design_errors)
        if normalized is not None:
            changes["design"] = normalized

    if "settings" in data and data["settings"] is not None:
        if not isinstance(data["settings"], dict):
            errors.append({"path": "settings", "message": "The settings must be an object"})
        else:
            merged = design_service.deep_merge(design_service.merged_settings(task.settings), data["settings"])
            if task.is_frozen:
                locked = design_service.changed_frozen_keys(task.settings, merged, design=task.design)
                if locked:
                    message = "Frozen once the task has been opened"
                    raise HumanEvalServiceError(
                        409,
                        f"These settings cannot change once the task has been opened: {', '.join(locked)}",
                        "design_frozen",
                        [{"path": f"settings.{key}", "message": message} for key in locked],
                    )
            normalized_settings, settings_errors = design_service.validate_settings(merged)
            errors += _prefixed("settings", settings_errors)
            if normalized_settings is not None:
                changes["settings"] = normalized_settings
    _raise_if(errors)

    with transaction.atomic():
        if task.opened_at is not None:
            log = list(task.protocol_log or [])
            now = timezone.now().isoformat()
            for field in LOGGED_FIELDS:
                if field in changes and changes[field] != getattr(task, field):
                    log.append(
                        {
                            "at": now,
                            "by": getattr(user, "email", "") or "",
                            "field": field,
                            "old_sha256": _sha256(getattr(task, field)),
                            "new_sha256": _sha256(changes[field]),
                        },
                    )
            changes["protocol_log"] = log
        from backend.human_eval.services import access
        from backend.human_eval.services import allocation

        was_accepting = access.is_accepting(task)
        for field, value in changes.items():
            setattr(task, field, value)
        task.save()
        if task.status == EvalTask.Status.OPEN and not was_accepting and access.is_accepting(task):
            # A window change re-opened the task for work: same idle-clock restart as a reopen.
            allocation.resume_idle_clocks(task)
    return task


def duplicate_task(task: EvalTask, user: Any) -> EvalTask:
    """A new draft copy: same design, settings and apps (fresh snapshots, provisional labels, new seed)."""
    source_apps = list(
        EvalTaskApp.objects.filter(task=task, job__isnull=False)
        .order_by("position", "id")
        .values("job_id", "is_active"),
    )
    runs_by_id = {
        run.pk: run
        for run in GenerationRun.objects.filter(pk__in=[row["job_id"] for row in source_apps]).select_related(
            *pool.RUN_RELATIONS,
        )
    }
    ordered = [row for row in source_apps if row["job_id"] in runs_by_id]
    with transaction.atomic():
        copy_task = EvalTask(
            title=f"Copy of {task.title}"[:TITLE_MAX],
            description=task.description,
            instructions=task.instructions,
            mode=task.mode,
            design=copy.deepcopy(task.design),
            settings=copy.deepcopy(task.settings),
            created_by=user,
        )
        copy_task.study_id = task.study_id
        copy_task.save()
        created = pool.create_task_apps(copy_task, [runs_by_id[row["job_id"]] for row in ordered])
        inactive = [app.pk for app, row in zip(created, ordered, strict=True) if not row["is_active"]]
        if inactive:
            EvalTaskApp.objects.filter(pk__in=inactive).update(is_active=False)
    return copy_task


def delete_task(task: EvalTask) -> None:
    """Delete a task in any status (participants' data included); its started apps are stopped first."""
    with transaction.atomic():
        # Started, pinned and in-flight containers (unless another open task uses them): once the rows
        # are gone neither the reaper nor the capacity count would see them.
        hosting.release_apps(EvalTaskApp.objects.filter(task=task))
        task.delete()


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


def _apps(task: EvalTask) -> list[EvalTaskApp]:
    """The task's apps without their runs (blockers and warnings only need ``job_id`` and the snapshot)."""
    return list(EvalTaskApp.objects.filter(task=task).order_by("position", "id"))


def _first_open(task: EvalTask, now: dt.datetime) -> None:
    blockers = open_blockers(task, _apps(task))
    if blockers:
        _raise_blocked(blockers)
    # Re-take every snapshot (what the app is at the moment the study starts).
    apps = list(
        EvalTaskApp.objects.filter(task=task).select_related(*_APP_RUN_RELATIONS).order_by("position", "id"),
    )
    snapshots = snapshot_service.full_snapshots([app.job for app in apps if app.job_id])
    for app in apps:
        if app.job_id:
            app.snapshot = snapshots[str(app.job_id)]
            app.group_key = snapshot_service.group_key(app.snapshot, app.id)
            # Freeze the task the requirement items come from (context.frozen_spec): later raters of the
            # app get the same items even if the run is deleted (EvalTaskApp.job is SET_NULL).
            app.snapshot = {**app.snapshot, "spec": context_service.spec_dict(app.job)}
    EvalTaskApp.objects.bulk_update(apps, ["snapshot", "group_key"])
    blockers = open_blockers(task, apps)
    if blockers:
        _raise_blocked(blockers)
    items_service.relabel_apps(task)
    items_service.build_items(task)
    context_service.draw_finding_samples(task)
    sampled = list(EvalTaskApp.objects.filter(task=task).exclude(finding_sample=[]))
    for app in sampled:
        snap = dict(app.snapshot or {})
        scan = dict(snap.get("blinding_scan") or {"source": []})
        scan["findings"] = snapshot_service.scan_findings(app)
        snap["blinding_scan"] = scan
        app.snapshot = snap
    if sampled:
        EvalTaskApp.objects.bulk_update(sampled, ["snapshot"])
    task.frozen_at = now
    task.opened_at = now
    task.design_hash = design_service.design_hash(task.mode, task.design, task.settings)


def _raise_blocked(blockers: list[str]) -> None:
    msg = "The task cannot be opened yet"
    raise HumanEvalServiceError(
        409,
        msg,
        "open_blocked",
        [{"path": "open_blockers", "message": blocker} for blocker in blockers],
    )


def transition(task: EvalTask, user: Any, action: Any) -> EvalTask:
    """Apply a status action (TS ``TaskAction``); 409 ``invalid_transition`` when the status does not allow it."""
    if action not in ACTIONS:
        msg = "Unknown action"
        raise invalid(msg, [{"path": "action", "message": f"Choose one of: {', '.join(ACTIONS)}"}])
    with transaction.atomic():
        task = EvalTask.objects.select_for_update(of=("self",)).select_related("study", "created_by").get(pk=task.pk)
        target = TRANSITIONS.get(task.status, {}).get(action)
        if target is None:
            msg = f"A {task.status} task cannot {action}"
            raise conflict(msg, "invalid_transition")
        now = timezone.now()
        previous = task.status
        if previous == EvalTask.Status.DRAFT:
            _first_open(task, now)
        if target == EvalTask.Status.CLOSED and previous in (EvalTask.Status.OPEN, EvalTask.Status.PAUSED):
            task.closed_at = now
            if task.first_closed_at is None:
                task.first_closed_at = now
            EvalTaskApp.objects.filter(task=task, pinned=True).update(pinned=False)
            hosting.stop_task_apps(task)
        if previous == EvalTask.Status.CLOSED and target == EvalTask.Status.OPEN:
            task.closed_at = None
        if target == EvalTask.Status.OPEN and previous in (EvalTask.Status.PAUSED, EvalTask.Status.CLOSED):
            # Heartbeats/autosaves were refused while not accepting: restart the idle clocks so the
            # first `next` after a long pause does not expire every assignment that was open.
            from backend.human_eval.services import allocation

            allocation.resume_idle_clocks(task, now)
        task.status = target
        task.save()
    return task


def allowed_actions(task: EvalTask, blockers: list[str] | None = None) -> list[str]:
    """The status actions allowed now (``open`` on a draft only without open blockers)."""
    actions = list(TRANSITIONS.get(task.status, {}))
    if task.status == EvalTask.Status.DRAFT and blockers:
        actions.remove("open")
    return actions


# ---------------------------------------------------------------------------
# Blockers and warnings
# ---------------------------------------------------------------------------


def open_blockers(task: EvalTask, apps: Iterable[EvalTaskApp]) -> list[str]:
    """Problems that keep a draft from opening (TS ``TaskDetail.open_blockers``); [] once frozen."""
    if task.is_frozen or task.status != EvalTask.Status.DRAFT:
        return []
    blockers: list[str] = []
    _, design_errors = design_service.validate_design(task.mode, task.design)
    if design_errors:
        first = design_errors[0]
        blockers.append(f"The design is invalid ({first['path'] or 'design'}: {first['message']})")
    else:
        blockers += design_service.design_open_blockers(task.mode, task.design)
    _, settings_errors = design_service.validate_settings(task.settings)
    if settings_errors:
        first = settings_errors[0]
        blockers.append(f"The settings are invalid ({first['path'] or 'settings'}: {first['message']})")
    blockers += design_service.settings_open_blockers(task.settings)
    active = [app for app in apps if app.is_active]
    if not active:
        blockers.append("Add at least one active app")
        return blockers
    orphaned = [app.label for app in active if app.job_id is None]
    if orphaned:
        blockers.append(f"Deactivate or remove apps whose generation run was deleted: {', '.join(orphaned)}")
    if task.mode == EvalTask.Mode.PAIRWISE:
        pairing = design_service.merged_settings(task.settings)["pairing"]
        if pairing.get("strategy") == "vs_baseline" and items_service.baseline_condition_id(task) is None:
            blockers.append("Choose a baseline condition for vs-baseline pairing (or link a study with one)")
        elif not items_service.plan_pairs(task, active):
            blockers.append(
                "No pair of apps can be formed: pairs need two active apps built from the same task version"
                + (" in different conditions" if pairing.get("cross_condition_only") else ""),
            )
    return blockers


def _source_mentions(app: EvalTaskApp) -> list[str]:
    """Model tokens in the app's generated source (scanned once, cached in the snapshot)."""
    snap = app.snapshot or {}
    scan = snap.get("blinding_scan")
    if isinstance(scan, dict) and isinstance(scan.get("source"), list):
        return scan["source"]
    if app.job_id is None:
        return []
    run = GenerationRun.objects.filter(pk=app.job_id).first()
    if run is None:
        return []
    found = snapshot_service.scan_text(snapshot_service.source_texts(run), snapshot_service.model_tokens(snap))
    app.snapshot = {**snap, "blinding_scan": {"source": found, "findings": []}}
    EvalTaskApp.objects.filter(pk=app.pk).update(snapshot=app.snapshot)
    return found


def open_warnings(task: EvalTask, apps: Iterable[EvalTaskApp]) -> list[str]:
    """Non-blocking validity warnings (TS ``TaskDetail.open_warnings``), in a fixed order."""
    settings = design_service.merged_settings(task.settings)
    design = task.design or {}
    active = [app for app in apps if app.is_active]
    warnings: list[str] = []
    if design_service.has_security_testing(design) and settings["hosting"].get("reset") != "per_assignment":
        warnings.append(WARNING_SHARED_APP_STATE)
    hashes: dict[str, set[str]] = defaultdict(set)
    for app in active:
        snap = app.snapshot or {}
        if snap.get("app_slug") and snap.get("spec_content_hash"):
            hashes[snap["app_slug"]].add(snap["spec_content_hash"])
    if any(len(values) > 1 for values in hashes.values()):
        warnings.append(WARNING_MIXED_SPEC_VERSIONS)
    show_source = bool(settings["blinding"].get("show_source_code"))
    reviews_findings = design_service.finding_review_block(design) is not None
    leaks = False
    for app in active:
        if show_source and _source_mentions(app):
            leaks = True
            break
        scan = (app.snapshot or {}).get("blinding_scan") or {}
        if reviews_findings and scan.get("findings"):
            leaks = True
            break
    if leaks:
        warnings.append(WARNING_MODEL_NAME_IN_SOURCE)
    if show_source:
        kinds = {(app.snapshot or {}).get("kind") or "" for app in active}
        harnesses = {(app.snapshot or {}).get("harness") or "" for app in active}
        stacks = {(app.snapshot or {}).get("stack") or "" for app in active}
        if len(kinds) > 1 or len(harnesses) > 1 or len(stacks) > 1:
            warnings.append(WARNING_LAYOUT_REVEALS_CONDITION)
    return warnings


# ---------------------------------------------------------------------------
# Serialization and listing
# ---------------------------------------------------------------------------


def serialize_task_list_item(task: EvalTask) -> dict[str, Any]:
    """TS ``TaskListItem``."""
    return {
        "id": str(task.id),
        "title": task.title,
        "status": task.status,
        "mode": task.mode,
        "counts": counts.task_counts(task),
        "study": studies.study_ref(task),
        "opens_at": task.opens_at,
        "closes_at": task.closes_at,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def serialize_task_detail(task: EvalTask) -> dict[str, Any]:
    """TS ``TaskDetail`` (blockers, warnings and allowed actions computed now)."""
    apps = _apps(task)
    blockers = open_blockers(task, apps)
    owner = task.created_by
    return {
        **serialize_task_list_item(task),
        "description": task.description,
        "instructions": task.instructions,
        "design": task.design,
        "settings": design_service.merged_settings(task.settings),
        "seed": task.seed,
        "design_hash": task.design_hash,
        "frozen_at": task.frozen_at,
        "opened_at": task.opened_at,
        "closed_at": task.closed_at,
        "allowed_actions": allowed_actions(task, blockers),
        "open_blockers": blockers,
        "open_warnings": open_warnings(task, apps),
        "first_closed_at": task.first_closed_at,
        "protocol_log": list(task.protocol_log or []),
        "owner": {"email": owner.email, "name": owner.name or ""} if owner is not None else None,
    }


def list_tasks(
    user: Any,
    *,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> dict[str, Any]:
    """TS ``Paginated<TaskListItem>`` of the tasks the user manages, newest first."""
    qs = researcher_tasks_qs(user)
    if status:
        qs = qs.filter(status=status) if status in EvalTask.Status.values else qs.none()
    if search and search.strip():
        term = search.strip()
        qs = qs.filter(Q(title__icontains=term) | Q(description__icontains=term))
    return paginate(qs.order_by("-created_at", "-id"), page, per_page, serialize=serialize_task_list_item)
