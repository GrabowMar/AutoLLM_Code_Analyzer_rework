"""Assignment context (owner B2): what a participant sees, frozen when the assignment is first opened.

An assignment's ``context`` (TS ``AssignmentContext``) records per design block what
the participant was shown, so analysis and exports never depend on live job,
spec or finding rows that may change or disappear later:

- **requirements** blocks: per app slot the checklist items derived from the
  app's spec frozen at first task open (:func:`frozen_spec`: ``EvalTaskApp.snapshot
  ["spec"]``, else the job's :func:`spec_dict`) plus the block's custom items, with ids
  ``f"{prefix}_{sha1(f'{source}|{key}').hexdigest()[:10]}"`` (prefix b/f/a/ep/aep;
  key = the text for prose requirements, ``"METHOD /path"`` for endpoints, whose
  text is ``"METHOD /path — description"``; custom items ``c_<id>``). The same spec
  yields the same ids for every app, so verdicts are comparable across apps.
- the **security** block with ``finding_review.enabled``: per slot the automated
  findings to review (TS ``ReviewFinding`` — the tool's own ``tool_severity`` label
  only, never the normalized ``Finding.severity``), drawn once per task app
  (:func:`finding_sample`) so every rater of an app reviews the same findings. The
  ReviewFinding dicts are frozen with the draw in ``EvalTaskApp.snapshot
  ["review_findings"]``, so findings deleted later (a deleted analysis run) never
  shrink a later rater's list.
- standard **questionnaire** blocks: the instrument's fixed questions.

Plus ``instructions_sha256`` (sha256 of the task instructions the participant saw),
``hosting`` (TS ``HostingProvenance`` per slot, recorded by
:func:`record_hosting_provenance` from ``hosting.hosting_provenance`` the first time
the app is seen running) and, for
skips, ``skip_hosting`` (written by ``responses.skip``).

Finding samples (spec R1) are proportional with a minimum: ``max_findings`` seats
are allocated across the tools that reported findings in proportion to their
population (largest remainder, at least one seat per tool; if there are more
tools than seats, the tools with the largest populations get one each), then
drawn uniformly within each tool with ``random.Random(f"{seed}:{task_app.id}")``
over a deterministic order. ``EvalTaskApp.finding_frame`` stores
``{tool: {population, sampled}}`` for inverse-probability weighting. A calls
:func:`draw_finding_samples` when the task first opens.
"""

from __future__ import annotations

import hashlib
import logging
import random
from typing import TYPE_CHECKING
from typing import Any
from urllib.parse import urlsplit

from django.db import transaction
from django.utils import timezone

from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.services import access
from backend.human_eval.services import design as design_service
from backend.human_eval.services import source as source_service
from backend.human_eval.services.errors import not_found
from backend.human_eval.services.instruments import instrument_questions

if TYPE_CHECKING:
    from backend.human_eval.models import EvalTask

logger = logging.getLogger(__name__)

REQUIREMENT_PREFIXES: dict[str, str] = {"backend": "b", "frontend": "f", "admin": "a", "api": "ep", "admin_api": "aep"}
_PROSE_FIELDS = {"backend": "backend_requirements", "frontend": "frontend_requirements", "admin": "admin_requirements"}
_ENDPOINT_FIELDS = {"api": "api_endpoints", "admin_api": "admin_api_endpoints"}
_MAX_REQUIREMENT_TEXT = 2000

# ReviewFinding text caps (the participant reads them; the frozen context stays small).
_MAX_TITLE = 500
_MAX_DESCRIPTION = 5000
_MAX_SNIPPET = 5000
_MAX_EVIDENCE = 2000
_MAX_SHORT = 200

# ---------------------------------------------------------------------------
# Spec
# ---------------------------------------------------------------------------


def spec_dict(job: Any) -> dict[str, Any] | None:
    """The app spec dict the job was generated from (frozen snapshot first).

    ``job.resolved_bundle["app_requirement"]`` when it is a dict, else
    ``profile_resolver.app_requirement_to_dict(job.app_requirement)``, else None.
    """
    if job is None:
        return None
    bundle = job.resolved_bundle if isinstance(job.resolved_bundle, dict) else {}
    snapshot = bundle.get("app_requirement")
    if isinstance(snapshot, dict):
        return snapshot
    if job.app_requirement_id is None:
        return None
    from backend.generation.services.profile_resolver import app_requirement_to_dict

    return app_requirement_to_dict(job.app_requirement)


def _text(value: Any) -> str:
    """A requirement entry as plain text (strings; dicts with text/description; else '')."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("text", "description", "requirement"):
            if isinstance(value.get(key), str):
                return value[key].strip()
    return ""


def _string_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [text for text in (_text(value) for value in values) if text]


def _endpoints(values: Any) -> list[dict[str, str]]:
    endpoints: list[dict[str, str]] = []
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, dict):
            continue
        method = str(value.get("method") or "").strip().upper()
        path = str(value.get("path") or "").strip()
        if not method or not path:
            continue
        endpoints.append(
            {
                "method": method,
                "path": path,
                "description": str(value.get("description") or "").strip(),
                "auth": str(value.get("auth") or "").strip(),
            },
        )
    return endpoints


def frozen_spec(task_app: EvalTaskApp, job: Any = None) -> dict[str, Any] | None:
    """The app spec frozen at first task open (``snapshot["spec"]``, None when the app had none then).

    Apps without a frozen copy (added after the first open) fall back to :func:`spec_dict` of ``job``
    (default: ``task_app.job``).
    """
    snapshot = task_app.snapshot if isinstance(task_app.snapshot, dict) else {}
    if "spec" in snapshot:
        spec = snapshot["spec"]
        return spec if isinstance(spec, dict) else None
    return spec_dict(job if job is not None else task_app.job)


def spec_view(job: Any) -> dict[str, Any] | None:
    """TS ``AppSpecView`` of the job's spec (only these fields), or None when the job has no spec."""
    return _spec_view(spec_dict(job))


def app_spec_view(task_app: EvalTaskApp, job: Any = None) -> dict[str, Any] | None:
    """TS ``AppSpecView`` of the app's :func:`frozen_spec` (what the participant's requirement items use)."""
    return _spec_view(frozen_spec(task_app, job))


def _spec_view(spec: dict[str, Any] | None) -> dict[str, Any] | None:
    if spec is None:
        return None
    return {
        "name": str(spec.get("name") or ""),
        "description": str(spec.get("description") or ""),
        "backend_requirements": _string_list(spec.get("backend_requirements")),
        "frontend_requirements": _string_list(spec.get("frontend_requirements")),
        "admin_requirements": _string_list(spec.get("admin_requirements")),
        "api_endpoints": _endpoints(spec.get("api_endpoints")),
        "admin_api_endpoints": _endpoints(spec.get("admin_api_endpoints")),
    }


def requirement_id(source: str, key: str) -> str:
    """The stable id of one spec requirement (``f"{prefix}_{sha1(f'{source}|{key}')[:10]}"``)."""
    digest = hashlib.sha1(f"{source}|{key}".encode(), usedforsecurity=False).hexdigest()[:10]
    return f"{REQUIREMENT_PREFIXES[source]}_{digest}"


def requirement_items(
    spec: dict[str, Any] | None,
    sources: list[str],
    custom: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """TS ``RequirementItem`` list for a requirements block (stable ids, see module docstring).

    Sources in block order, then the custom items. A requirement repeated verbatim within one
    source is listed once (it has one id).
    """
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(item_id: str, source: str, text: str) -> None:
        if item_id in seen:
            return
        seen.add(item_id)
        items.append({"id": item_id, "source": source, "text": text[:_MAX_REQUIREMENT_TEXT]})

    spec = spec if isinstance(spec, dict) else {}
    for source in sources or []:
        if source in _PROSE_FIELDS:
            for text in _string_list(spec.get(_PROSE_FIELDS[source])):
                add(requirement_id(source, text), source, text)
        elif source in _ENDPOINT_FIELDS:
            for endpoint in _endpoints(spec.get(_ENDPOINT_FIELDS[source])):
                key = f"{endpoint['method']} {endpoint['path']}"
                text = f"{key} — {endpoint['description']}" if endpoint["description"] else key
                add(requirement_id(source, key), source, text)
    for entry in custom or []:
        text = str(entry.get("text") or "").strip()
        if entry.get("id") and text:
            add(f"c_{entry['id']}", "custom", text)
    return items


# ---------------------------------------------------------------------------
# Finding samples
# ---------------------------------------------------------------------------


def _population(job: Any, config: dict[str, Any]) -> dict[str, list[str]]:
    """Eligible finding ids per tool slug, each list in a deterministic order (file, line, id)."""
    from backend.analysis.models import AnalyzerTool
    from backend.analysis.models import Finding
    from backend.analysis.models import ToolResult

    if job is None:
        return {}
    result_ids = ToolResult.latest_ids_per_job([job.id])
    results = ToolResult.objects.filter(id__in=result_ids, status=ToolResult.Status.COMPLETED)
    slugs = set(results.values_list("tool_slug", flat=True))
    excluded: set[str] = set()
    for tool in AnalyzerTool.objects.filter(slug__in=slugs).only("slug", "kind", "analysis_mode"):
        if tool.kind == "ai" and not config.get("include_ai"):
            excluded.add(tool.slug)
        if tool.analysis_mode == "dynamic" and not config.get("include_dynamic"):
            excluded.add(tool.slug)
    findings = Finding.objects.filter(result__in=results).exclude(result__tool_slug__in=excluded)
    categories = [category for category in config.get("categories") or [] if category]
    if categories:
        findings = findings.filter(category__in=categories)
    if not config.get("include_dependency"):
        findings = findings.exclude(category="dependency")
    rows = findings.values_list("id", "result__tool_slug", "file_path", "line_number")
    by_tool: dict[str, list[tuple[str, int, str]]] = {}
    for finding_id, slug, file_path, line in rows:
        by_tool.setdefault(slug, []).append((file_path or "", line if line is not None else -1, str(finding_id)))
    return {slug: [entry[2] for entry in sorted(entries)] for slug, entries in sorted(by_tool.items())}


def allocate_seats(populations: dict[str, int], seats: int) -> dict[str, int]:
    """Proportional-with-minimum allocation of ``seats`` across tools (spec R1).

    Every tool with findings gets at least one seat, then seats go to the largest
    ``quota - allocated`` (quota = seats x population / total), capped at the population:
    this is the largest-remainder method on top of the minimum. With fewer seats than
    tools, the tools with the largest populations (ties: slug order) get one seat each.
    """
    tools = sorted(slug for slug, count in populations.items() if count > 0)
    total = sum(populations[slug] for slug in tools)
    if seats <= 0 or not tools:
        return dict.fromkeys(tools, 0)
    if total <= seats:
        return {slug: populations[slug] for slug in tools}
    if seats < len(tools):
        ranked = sorted(tools, key=lambda slug: (-populations[slug], slug))[:seats]
        return {slug: int(slug in ranked) for slug in tools}
    allocation = dict.fromkeys(tools, 1)
    quota = {slug: seats * populations[slug] / total for slug in tools}
    for _ in range(seats - len(tools)):
        open_tools = [slug for slug in tools if allocation[slug] < populations[slug]]
        best = max(open_tools, key=lambda slug: (quota[slug] - allocation[slug], populations[slug], slug))
        allocation[best] += 1
    return allocation


def _draw(task_app: EvalTaskApp, config: dict[str, Any], seed: int) -> tuple[list[str], dict[str, dict[str, int]]]:
    population = _population(task_app.job, config)
    seats = int(config.get("max_findings") or 0)
    allocation = allocate_seats({slug: len(ids) for slug, ids in population.items()}, seats)
    rng = random.Random(f"{seed}:{task_app.id}")  # noqa: S311 - seeded, reproducible study sample
    chosen: dict[str, list[str]] = {}
    for slug, ids in population.items():
        shuffled = list(ids)
        rng.shuffle(shuffled)
        chosen[slug] = shuffled[: allocation.get(slug, 0)]
    # Round-robin across tools so one tool's findings do not come in one run.
    sample: list[str] = []
    for index in range(max((len(ids) for ids in chosen.values()), default=0)):
        sample.extend(ids[index] for ids in chosen.values() if index < len(ids))
    frame = {slug: {"population": len(ids), "sampled": len(chosen[slug])} for slug, ids in population.items()}
    return sample, frame


def finding_sample(task_app: EvalTaskApp, config: dict[str, Any], seed: int) -> list[str]:
    """The task app's frozen finding sample (finding ids as str), drawing it if not drawn yet.

    Drawn once under a row lock (``finding_sample`` + ``finding_sample_at`` + ``finding_frame``, and
    the sampled findings' ReviewFinding dicts in ``snapshot["review_findings"]``); later calls return
    the stored ids even if the job's findings changed or were deleted.
    """
    if task_app.finding_sample_at is None:
        with transaction.atomic():
            locked = EvalTaskApp.objects.select_for_update(of=("self",)).select_related("job").get(pk=task_app.pk)
            if locked.finding_sample_at is None:
                sample, frame = _draw(locked, config, seed)
                locked.finding_sample = sample
                locked.finding_frame = frame
                locked.finding_sample_at = timezone.now()
                locked.snapshot = {
                    **(locked.snapshot if isinstance(locked.snapshot, dict) else {}),
                    "review_findings": _live_review_findings(locked, sample),
                }
                locked.save(update_fields=["finding_sample", "finding_frame", "finding_sample_at", "snapshot"])
        task_app.finding_sample = locked.finding_sample
        task_app.finding_frame = locked.finding_frame
        task_app.finding_sample_at = locked.finding_sample_at
        task_app.snapshot = locked.snapshot
    return [str(finding_id) for finding_id in task_app.finding_sample or []]


def draw_finding_samples(task: EvalTask) -> int:
    """Draw the finding samples of every app of the task (A calls it in the first-open transition).

    Uses the design's finding-review security block (none → nothing to draw). Returns the number
    of apps sampled by this call (apps already sampled are left as they are).
    """
    block = design_service.finding_review_block(task.design or {})
    if block is None:
        return 0
    config = block.get("finding_review") or {}
    drawn = 0
    for task_app in EvalTaskApp.objects.filter(task=task).select_related("job").order_by("position", "label"):
        if task_app.finding_sample_at is None:
            finding_sample(task_app, config, task.seed)
            drawn += 1
    return drawn


def _clip(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    return text[:limit]


def _http_url(value: Any) -> str:
    """``value`` when it is an absolute http(s) URL, else '' (untrusted strings never become other links)."""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc or len(value) > 2000:
        return ""
    return value


def _endpoint_of(url: Any) -> str:
    """A DAST URL reduced to path + query: the host is the analyzer's internal address, not the tester's app."""
    if not isinstance(url, str) or not url.strip():
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return ""
    if not parts.scheme and not parts.netloc:
        return _clip(url.strip(), 2000)
    path = parts.path or "/"
    return _clip(f"{path}?{parts.query}" if parts.query else path, 2000)


def _cwe(value: Any) -> str:
    if isinstance(value, list | tuple):
        return _clip(", ".join(str(item) for item in value if item not in (None, "")), _MAX_SHORT)
    if value is None or isinstance(value, bool):
        return ""
    return _clip(str(value), _MAX_SHORT)


def review_finding(finding: Any, *, job: Any = None, tool: Any = None) -> dict[str, Any]:
    """TS ``ReviewFinding`` of an analysis Finding (``tool_severity`` only, never ``severity``).

    ``job`` (the finding's generation job) maps scaffolding blob paths to the source viewer's names;
    ``tool`` is the ``AnalyzerTool`` (looked up by slug when omitted). DAST URLs are reduced to
    path + query; ``docs_url`` is kept only when it is an http(s) URL.
    """
    from backend.analysis.models import AnalyzerTool

    slug = finding.result.tool_slug
    if tool is None:
        tool = AnalyzerTool.objects.filter(slug=slug).only("slug", "name", "analysis_mode").first()
    if job is None:
        job = getattr(finding.result.run, "generation_job", None)
    data = finding.tool_specific_data if isinstance(finding.tool_specific_data, dict) else {}
    dynamic = tool is not None and tool.analysis_mode == "dynamic"
    return {
        "id": str(finding.id),
        "tool": slug,
        "tool_name": tool.name if tool is not None else slug,
        "tool_severity": finding.tool_severity or "",
        "category": finding.category or "",
        "rule_id": finding.rule_id or "",
        "title": _clip(finding.title, _MAX_TITLE),
        "description": _clip(finding.description, _MAX_DESCRIPTION),
        "file_path": source_service.display_path(finding.file_path or "", job),
        "line": finding.line_number,
        "code_snippet": _clip(finding.code_snippet, _MAX_SNIPPET),
        "docs_url": _http_url(data.get("docs_url")),
        "cwe": _cwe(data.get("cwe")),
        "url": _endpoint_of(data.get("url")) if dynamic else "",
        "method": _clip(data.get("method"), 20).upper() if dynamic else "",
        "param": _clip(data.get("param"), _MAX_SHORT) if dynamic else "",
        "evidence": _clip(data.get("evidence"), _MAX_EVIDENCE),
    }


def _live_review_findings(task_app: EvalTaskApp, ids: list[str]) -> list[dict[str, Any]]:
    """ReviewFinding dicts of the finding rows ``ids`` as they are now, in ``ids`` order (missing rows skipped)."""
    from backend.analysis.models import AnalyzerTool
    from backend.analysis.models import Finding

    if not ids:
        return []
    findings = {str(f.id): f for f in Finding.objects.filter(id__in=ids).select_related("result")}
    slugs = {finding.result.tool_slug for finding in findings.values()}
    tools = {tool.slug: tool for tool in AnalyzerTool.objects.filter(slug__in=slugs)}
    job = task_app.job
    return [
        review_finding(findings[finding_id], job=job, tool=tools.get(findings[finding_id].result.tool_slug))
        for finding_id in ids
        if finding_id in findings
    ]


def _frozen_review_findings(task_app: EvalTaskApp) -> list[dict[str, Any]] | None:
    snapshot = task_app.snapshot if isinstance(task_app.snapshot, dict) else {}
    frozen = snapshot.get("review_findings")
    return frozen if isinstance(frozen, list) else None


def _freeze_review_findings(task_app: EvalTaskApp, ids: list[str]) -> list[dict[str, Any]]:
    """Freeze the ReviewFinding dicts of a sample drawn before they were frozen with it (under a row lock)."""
    with transaction.atomic():
        locked = EvalTaskApp.objects.select_for_update(of=("self",)).select_related("job").get(pk=task_app.pk)
        frozen = _frozen_review_findings(locked)
        if frozen is None:
            frozen = _live_review_findings(locked, ids)
            locked.snapshot = {
                **(locked.snapshot if isinstance(locked.snapshot, dict) else {}),
                "review_findings": frozen,
            }
            locked.save(update_fields=["snapshot"])
    task_app.snapshot = locked.snapshot
    return frozen


def review_findings(task_app: EvalTaskApp, config: dict[str, Any], seed: int) -> list[dict[str, Any]]:
    """The app's sampled findings as TS ``ReviewFinding`` dicts, in sample order.

    Read from the copy frozen with the draw (``snapshot["review_findings"]``), never from live finding
    rows, so every rater of the app gets the same list even after the analysis run was deleted. A sample
    drawn before the copies were frozen gets them frozen now (findings already deleted are skipped).
    """
    ids = finding_sample(task_app, config, seed)
    if not ids:
        return []
    frozen = _frozen_review_findings(task_app)
    if frozen is None:
        frozen = _freeze_review_findings(task_app, ids)
    by_id = {str(entry.get("id")): entry for entry in frozen if isinstance(entry, dict)}
    return [by_id[finding_id] for finding_id in ids if finding_id in by_id]


# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------


def slot_apps(assignment: Assignment) -> list[tuple[str, EvalTaskApp]]:
    """The assignment's apps by display slot: ``[("single", app)]``, or ``[("A", ...), ("B", ...)]``.

    Pairwise: displayed A is ``item.app_a`` unless ``assignment.swapped`` (then ``item.app_b``).
    """
    item = assignment.item
    if item.app_b_id is None:
        return [("single", item.app_a)]
    if assignment.swapped:
        return [("A", item.app_b), ("B", item.app_a)]
    return [("A", item.app_a), ("B", item.app_b)]


def slot_for_app(assignment: Assignment, task_app: EvalTaskApp) -> str:
    """The display slot of ``task_app`` in this assignment (404 when it is not one of its apps)."""
    for slot, app in slot_apps(assignment):
        if app is not None and app.pk == task_app.pk:
            return slot
    msg = "App not in this assignment"
    raise not_found(msg)


def app_for_slot(assignment: Assignment, slot: str) -> EvalTaskApp:
    """The task app shown in ``slot`` (404 for a slot this assignment does not have)."""
    for name, app in slot_apps(assignment):
        if name == slot:
            return app
    msg = "No such app slot"
    raise not_found(msg)


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


def instructions_sha256(task: EvalTask) -> str:
    """sha256 of the task instructions (``context.instructions_sha256``)."""
    return hashlib.sha256((task.instructions or "").encode("utf-8")).hexdigest()


def compute_context(assignment: Assignment) -> dict[str, Any]:
    """A fresh TS ``AssignmentContext`` for the assignment (not stored; see :func:`build_context`)."""
    task = assignment.task
    slots = slot_apps(assignment)
    blocks: dict[str, dict[str, Any]] = {}
    for block in (task.design or {}).get("blocks") or []:
        block_type = block.get("type")
        entry: dict[str, Any] = {}
        if block_type == "requirements":
            entry["requirements"] = {
                slot: requirement_items(frozen_spec(app), block.get("sources") or [], block.get("custom") or [])
                for slot, app in slots
            }
        elif block_type == "security" and (block.get("finding_review") or {}).get("enabled"):
            config = block["finding_review"]
            entry["findings"] = {slot: review_findings(app, config, task.seed) for slot, app in slots}
        elif block_type == "questionnaire" and block.get("instrument") in design_service.STANDARD_INSTRUMENTS:
            entry["questions"] = instrument_questions(block["instrument"])
        blocks[block["id"]] = entry
    return {"blocks": blocks, "hosting": {}, "instructions_sha256": instructions_sha256(task)}


def has_context(assignment: Assignment) -> bool:
    """Whether the assignment's context has been built (it then never changes)."""
    return isinstance(assignment.context, dict) and isinstance(assignment.context.get("blocks"), dict)


def build_context(assignment: Assignment) -> dict[str, Any]:
    """TS ``AssignmentContext`` for the assignment (requirements, findings, instrument questions per block).

    Built and STORED once (under a row lock), then returned unchanged on every later call, so it is
    frozen at first open. Provenance or skip data recorded before the build is kept.
    """
    if has_context(assignment):
        return assignment.context
    with transaction.atomic():
        locked = Assignment.objects.select_for_update(of=("self",)).only("id", "context").get(pk=assignment.pk)
        current = locked.context if isinstance(locked.context, dict) else {}
        if not isinstance(current.get("blocks"), dict):
            built = compute_context(assignment)
            built["hosting"] = {**built["hosting"], **(current.get("hosting") or {})}
            if current.get("skip_hosting"):
                built["skip_hosting"] = current["skip_hosting"]
            current = built
            locked.context = current
            locked.save(update_fields=["context"])
    assignment.context = current
    return current


def open_assignment(assignment: Assignment) -> Assignment:
    """First open: ``assigned`` → ``in_progress`` (``started_at``, ``last_activity_at``) and the frozen context.

    Does nothing to a read-only assignment (closed, task not accepting, participant withdrawn).
    """
    if access.assignment_read_only(assignment):
        return assignment
    if assignment.status == Assignment.Status.ASSIGNED:
        with transaction.atomic():
            locked = Assignment.objects.select_for_update(of=("self",)).get(pk=assignment.pk)
            if locked.status == Assignment.Status.ASSIGNED:
                now = timezone.now()
                locked.status = Assignment.Status.IN_PROGRESS
                locked.started_at = locked.started_at or now
                locked.last_activity_at = now
                locked.save(update_fields=["status", "started_at", "last_activity_at"])
            assignment.status = locked.status
            assignment.started_at = locked.started_at
            assignment.last_activity_at = locked.last_activity_at
    build_context(assignment)
    return assignment


def mark_activity(assignment: Assignment) -> None:
    """Record participant activity on a writable assignment (opens it first if needed)."""
    open_assignment(assignment)
    now = timezone.now()
    Assignment.objects.filter(pk=assignment.pk).update(last_activity_at=now)
    assignment.last_activity_at = now


def portal_context(assignment: Assignment) -> dict[str, Any]:
    """The context as participants receive it: hosting provenance (container ids, names, images) is withheld.

    Participants never receive container ids/names other than the app URL host (spec §1), so
    ``hosting`` is always ``{}`` in portal payloads; researchers see the full context.
    """
    context = assignment.context if isinstance(assignment.context, dict) else {}
    payload: dict[str, Any] = {
        "blocks": context.get("blocks") if isinstance(context.get("blocks"), dict) else {},
        "hosting": {},
        "instructions_sha256": str(context.get("instructions_sha256") or ""),
    }
    skip_hosting = context.get("skip_hosting")
    if isinstance(skip_hosting, dict):
        payload["skip_hosting"] = {
            slot: {"state": str(entry.get("state") or ""), "error": str(entry.get("error") or "")}
            for slot, entry in skip_hosting.items()
            if isinstance(entry, dict)
        }
    return payload


def finding_entry(assignment: Assignment, finding_id: str) -> tuple[str, dict[str, Any]]:
    """``(slot, ReviewFinding)`` of a finding in the assignment's frozen context (404 when it is not there)."""
    context = assignment.context if isinstance(assignment.context, dict) else {}
    for block in (context.get("blocks") or {}).values():
        for slot, findings in ((block or {}).get("findings") or {}).items():
            for entry in findings or []:
                if isinstance(entry, dict) and entry.get("id") == finding_id:
                    return slot, entry
    msg = "Finding not found in this assignment"
    raise not_found(msg)


def context_finding_ids(assignment: Assignment, block_id: str) -> list[str]:
    """Every finding id of one block's context (all slots), in context order."""
    context = assignment.context if isinstance(assignment.context, dict) else {}
    block = (context.get("blocks") or {}).get(block_id) or {}
    ids: list[str] = []
    for findings in (block.get("findings") or {}).values():
        ids.extend(str(entry["id"]) for entry in findings or [] if isinstance(entry, dict) and entry.get("id"))
    return ids


# ---------------------------------------------------------------------------
# Hosting provenance
# ---------------------------------------------------------------------------


def record_hosting_provenance(assignment: Assignment, slot: str, task_app: EvalTaskApp) -> None:
    """Store ``context.hosting[slot]`` (TS ``HostingProvenance``) the first time the app is seen running.

    The values come from ``hosting.hosting_provenance`` (owner C: container id/name/image, the last
    reset and ``prior_users`` = other assignments handed this container state since its last reset
    or build). No-op when the slot is already recorded, the assignment is closed, ``task_app`` is not
    the slot's app, or the app is not running (``first_running_at`` is None).
    """
    from backend.human_eval.services import hosting

    if not assignment.is_open:
        return
    if not any(name == slot and app.pk == task_app.pk for name, app in slot_apps(assignment)):
        return
    existing = (assignment.context or {}).get("hosting") if isinstance(assignment.context, dict) else None
    if isinstance(existing, dict) and slot in existing:
        return
    provenance = hosting.hosting_provenance(task_app, assignment=assignment)
    if not provenance.get("first_running_at"):
        return
    entry = {
        "container_id": provenance.get("container_id"),
        "container_name": provenance.get("container_name"),
        "image": provenance.get("image") or "",
        "reset_at": provenance.get("reset_at"),
        "prior_users": int(provenance.get("prior_users") or 0),
        "first_running_at": provenance["first_running_at"],
    }
    with transaction.atomic():
        locked = Assignment.objects.select_for_update(of=("self",)).only("id", "context").get(pk=assignment.pk)
        context = dict(locked.context) if isinstance(locked.context, dict) else {}
        recorded = dict(context.get("hosting") or {})
        if slot not in recorded:
            recorded[slot] = entry
            context["hosting"] = recorded
            locked.context = context
            locked.save(update_fields=["context"])
    assignment.context = context
