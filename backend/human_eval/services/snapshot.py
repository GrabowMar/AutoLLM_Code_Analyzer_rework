"""Job snapshots (owner A): the de-blinding key frozen on each task app.

``EvalTaskApp.snapshot`` records what the app *was* when it entered the study, so
analysis and exports never read live job data (spec §5.2, R1):

- **taken when the app is added** (:func:`job_snapshot` with ``full=False``): model,
  condition, experiment, app spec, functional summary, evaluator version, plus the
  ``spec_content_hash`` that names the spec version (cheap: no ``result_data``);
- **re-taken at first open** (``full=True``, :func:`full_snapshots` for a whole pool):
  additionally ``functional.by_category``, the api/admin conformance ``checks``
  (``name, category, passed``), ``static_findings`` and the blinding scan.

``static_findings = {count, loc, tools, by_tool}`` counts non-dependency findings of
static, non-AI tools in the latest measured ``ToolResult`` per (job, tool);
``tools`` lists the tools that measured the job (so per-kLOC comparisons can keep
only tools present for every app) and ``by_tool`` their counts; ``loc`` is
``loc_from_job(job)["total_loc"]``. Findings are never deduplicated here.

``blinding_scan = {source: [...], findings: [...]}`` lists the model / provider tokens
(:func:`model_tokens`) found in the generated source exactly as the portal's source
viewer serves it (:func:`source_texts`: file paths and texts from ``source.job_files``;
case-insensitive) and, once the finding sample is drawn, in the sampled findings' text. It feeds the
``model_name_in_source`` open warning; generated code never changes, so the scan is
done once per app.

Group key: ``f"{app_slug}:{spec_content_hash[:12]}"``; an app without a spec gets its
own app id (never paired) — see :func:`group_key`.
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING
from typing import Any

from backend.human_eval.services import source as source_service
from backend.human_eval.services.design import canonical_json

if TYPE_CHECKING:
    from collections.abc import Iterable

    from backend.generation.models import GenerationJob
    from backend.human_eval.models import EvalTaskApp

NON_SAMPLE_STATUSES = frozenset({"infra_error", "not_applicable"})
DEPLOYED_STATUSES = frozenset({"passed", "failed"})
UNDEPLOYED_STATUSES = frozenset({"build_failed", "start_failed"})
ENDPOINT_CHECK_CATEGORIES = ("api", "admin")
#: Upper bound of generated text scanned per app for model names (characters).
MAX_SCAN_CHARS = 8 * 1024 * 1024

# Tokens too generic to signal a model name in code or prose (HTML tags, common words, size tiers).
_GENERIC_TOKENS = frozenset(
    {
        "alpha",
        "amazon",
        "and",
        "api",
        "app",
        "auto",
        "base",
        "beta",
        "chat",
        "code",
        "coder",
        "command",
        "default",
        "exp",
        "experimental",
        "fast",
        "flash",
        "free",
        "google",
        "instruct",
        "large",
        "latest",
        "lite",
        "local",
        "max",
        "medium",
        "meta",
        "microsoft",
        "mini",
        "model",
        "nano",
        "online",
        "openrouter",
        "plus",
        "preview",
        "pro",
        "reasoning",
        "small",
        "test",
        "the",
        "thinking",
        "turbo",
        "vision",
    },
)
_MIN_TOKEN_LENGTH = 3
_LEADING_WORD = re.compile(r"[a-z]+")
_PARENTHESIS = re.compile(r"\(.*?\)")


# ---------------------------------------------------------------------------
# Spec
# ---------------------------------------------------------------------------


def spec_dict(job: GenerationJob) -> dict[str, Any] | None:
    """The app spec the job was generated from: the frozen ``resolved_bundle`` copy first, else the live row."""
    bundle = job.resolved_bundle if isinstance(job.resolved_bundle, dict) else {}
    spec = bundle.get("app_requirement")
    if isinstance(spec, dict):
        return spec
    if job.app_requirement_id is None:
        return None
    from backend.generation.services.profile_resolver import app_requirement_to_dict

    return app_requirement_to_dict(job.app_requirement)


def spec_content_hash(spec: dict[str, Any] | None) -> str | None:
    """sha256 of the canonical JSON of the spec dict (None without a spec)."""
    if not spec:
        return None
    return hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()


def group_key(snapshot: dict[str, Any], app_id: Any) -> str:
    """``f"{app_slug}:{spec_content_hash[:12]}"``, or the app's own id when it has no spec (never paired)."""
    slug = snapshot.get("app_slug") or ""
    digest = snapshot.get("spec_content_hash") or ""
    if slug and digest:
        return f"{slug}:{digest[:12]}"
    return str(app_id)


# ---------------------------------------------------------------------------
# Functional summary
# ---------------------------------------------------------------------------


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def functional_summary(metrics: Any) -> dict[str, Any]:
    """TS ``FunctionalSummary`` of ``job.metrics["functional"]`` (conformance verdict or smoke probe).

    ``pass_rate`` is None for ungraded runs (infra_error / not_applicable); ``deployed`` is the
    conformance verdict's own flag (derived from the status for older verdicts), None for smoke.
    """
    functional = metrics.get("functional") if isinstance(metrics, dict) else None
    if not isinstance(functional, dict):
        return {"status": None, "pass_rate": None, "deployed": None, "source": None}
    conformance = functional.get("source") == "conformance"
    status = functional.get("status") if isinstance(functional.get("status"), str) else None
    pass_rate = None if status in NON_SAMPLE_STATUSES else _number(functional.get("pass_rate"))
    deployed = None
    if conformance:
        stored = functional.get("deployed")
        if isinstance(stored, bool):
            deployed = stored
        elif status in DEPLOYED_STATUSES:
            deployed = True
        elif status in UNDEPLOYED_STATUSES:
            deployed = False
    return {
        "status": status if conformance else None,
        "pass_rate": pass_rate,
        "deployed": deployed,
        "source": "conformance" if conformance else "smoke",
    }


def _evaluation(job: GenerationJob) -> dict[str, Any]:
    data = job.result_data if isinstance(job.result_data, dict) else {}
    evaluation = data.get("evaluation")
    return evaluation if isinstance(evaluation, dict) else {}


def endpoint_checks(evaluation: dict[str, Any]) -> list[dict[str, Any]]:
    """The api/admin conformance checks as ``{name, category, passed}`` (names are ``"METHOD /path"``)."""
    checks = []
    for check in evaluation.get("checks") or []:
        if not isinstance(check, dict) or check.get("category") not in ENDPOINT_CHECK_CATEGORIES:
            continue
        checks.append(
            {"name": str(check.get("name") or ""), "category": check["category"], "passed": bool(check.get("passed"))},
        )
    return checks


def checks_by_category(evaluation: dict[str, Any]) -> dict[str, dict[str, int]]:
    """``{category: {passed, total}}`` of every conformance check (the stored summary, else counted)."""
    summary = evaluation.get("summary")
    stored = summary.get("by_category") if isinstance(summary, dict) else None
    if isinstance(stored, dict) and stored:
        result: dict[str, dict[str, int]] = {}
        for category, counts in stored.items():
            if isinstance(counts, dict):
                result[str(category)] = {
                    "passed": int(counts.get("passed") or 0),
                    "total": int(counts.get("total") or 0),
                }
        return result
    result = {}
    for check in evaluation.get("checks") or []:
        if not isinstance(check, dict):
            continue
        entry = result.setdefault(str(check.get("category") or ""), {"passed": 0, "total": 0})
        entry["total"] += 1
        entry["passed"] += int(bool(check.get("passed")))
    return result


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def _harness(job: GenerationJob) -> str:
    bundle = job.resolved_bundle if isinstance(job.resolved_bundle, dict) else {}
    harness = bundle.get("harness")
    if isinstance(harness, dict) and harness.get("name"):
        return str(harness["name"])
    condition = job.condition if job.condition_id else None
    if condition is not None and condition.harness:
        return str(condition.harness)
    return ""


def _condition_label(job: GenerationJob) -> str:
    if job.condition_id is None:
        return ""
    from backend.generation.services.experiments import condition_label

    return condition_label(job.condition)


def _base_snapshot(job: GenerationJob) -> dict[str, Any]:
    model = job.model if job.model_id else None
    experiment = job.experiment if job.experiment_id else None
    spec = spec_dict(job)
    functional_raw = job.metrics.get("functional") if isinstance(job.metrics, dict) else None
    functional_raw = functional_raw if isinstance(functional_raw, dict) else {}
    return {
        "model_id": model.model_id if model is not None else "",
        "model_name": model.model_name if model is not None else "",
        "provider": model.provider if model is not None else "",
        "condition_id": job.condition_id,
        "condition_label": _condition_label(job),
        "experiment_id": str(experiment.id) if experiment is not None else None,
        "experiment_name": experiment.name if experiment is not None else "",
        "app_slug": str((spec or {}).get("slug") or ""),
        "app_name": str((spec or {}).get("name") or ""),
        "repeat_index": job.repeat_index,
        "mode": job.mode,
        "harness": _harness(job),
        "functional": functional_summary(job.metrics),
        "evaluator_version": functional_raw.get("evaluator_version"),
        "tester_sha256": functional_raw.get("tester_sha256"),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "spec_content_hash": spec_content_hash(spec),
    }


def job_snapshot(job: GenerationJob, *, full: bool = True) -> dict[str, Any]:
    """The snapshot dict of a GenerationJob (stored in ``EvalTaskApp.snapshot``).

    ``full=False`` (when the app is added) skips the parts that need ``result_data`` and the
    analysis tables; first open re-takes the full snapshot. Select ``model``, ``experiment``,
    ``app_requirement`` and ``condition__model`` (+ profile / harness preset) to avoid queries.
    """
    if full:
        return full_snapshots([job])[str(job.id)]
    return _base_snapshot(job)


def full_snapshots(jobs: Iterable[GenerationJob]) -> dict[str, dict[str, Any]]:
    """Full snapshots of many jobs keyed by ``str(job.id)`` (static findings in a few batched queries)."""
    jobs = list(jobs)
    findings = static_findings_by_job(jobs)
    snapshots: dict[str, dict[str, Any]] = {}
    for job in jobs:
        snapshot = _base_snapshot(job)
        evaluation = _evaluation(job)
        if snapshot["evaluator_version"] is None:
            snapshot["evaluator_version"] = evaluation.get("evaluator_version")
        if snapshot["tester_sha256"] is None:
            snapshot["tester_sha256"] = evaluation.get("tester_sha256")
        snapshot["functional"]["by_category"] = checks_by_category(evaluation)
        snapshot["checks"] = endpoint_checks(evaluation)
        snapshot["static_findings"] = findings[str(job.id)]
        snapshot["blinding_scan"] = {
            "source": scan_text(source_texts(job), model_tokens(snapshot)),
            "findings": [],
        }
        snapshots[str(job.id)] = snapshot
    return snapshots


# ---------------------------------------------------------------------------
# Static findings
# ---------------------------------------------------------------------------


def _measured(status: str, summary: Any) -> bool:
    from backend.analysis.models import ToolResult

    if status == ToolResult.Status.COMPLETED:
        return True
    return (
        status == ToolResult.Status.SKIPPED
        and isinstance(summary, dict)
        and summary.get("skip_reason") == ("not_applicable")
    )


def static_findings_by_job(jobs: Iterable[GenerationJob]) -> dict[str, dict[str, Any]]:
    """``{str(job_id): {count, loc, tools, by_tool}}`` for static, non-AI, non-dependency analysis.

    Latest measured ``ToolResult`` per (job, tool) (``ToolResult.latest_ids_per_job``); AI tools
    (kind ``ai``), dynamic tools (``analysis_mode == "dynamic"``) and dependency scanners (tool
    category ``dependencies``) are left out, as are findings with category ``dependency``.
    """
    from django.db.models import Count

    from backend.analysis.models import AnalyzerTool
    from backend.analysis.models import Finding
    from backend.analysis.models import ToolResult
    from backend.reports.services.loc import loc_from_job

    jobs = list(jobs)
    result: dict[str, dict[str, Any]] = {}
    if not jobs:
        return result
    rows = list(
        ToolResult.objects.filter(id__in=ToolResult.latest_ids_per_job([job.id for job in jobs])).values(
            "id",
            "tool_slug",
            "category",
            "status",
            "summary",
            "run__generation_job_id",
        ),
    )
    catalog = {
        tool["slug"]: tool
        for tool in AnalyzerTool.objects.filter(slug__in={row["tool_slug"] for row in rows}).values(
            "slug",
            "kind",
            "analysis_mode",
            "category",
        )
    }

    def excluded(row: dict[str, Any]) -> bool:
        tool = catalog.get(row["tool_slug"], {})
        if row["category"] == "ai" or tool.get("kind") == "ai":
            return True
        if tool.get("analysis_mode") == "dynamic":
            return True
        return (tool.get("category") or row["category"]) == "dependencies"

    kept = [row for row in rows if not excluded(row) and _measured(row["status"], row["summary"])]
    counts = {
        str(entry["result_id"]): entry["n"]
        for entry in Finding.objects.filter(result_id__in=[row["id"] for row in kept])
        .exclude(category="dependency")
        .order_by()
        .values("result_id")
        .annotate(n=Count("id"))
    }
    per_job: dict[str, dict[str, int]] = {}
    for row in kept:
        tools = per_job.setdefault(str(row["run__generation_job_id"]), {})
        tools[row["tool_slug"]] = counts.get(str(row["id"]), 0)
    for job in jobs:
        tools = per_job.get(str(job.id), {})
        result[str(job.id)] = {
            "count": sum(tools.values()),
            "loc": loc_from_job(job).get("total_loc"),
            "tools": sorted(tools),
            "by_tool": dict(sorted(tools.items())),
        }
    return result


# ---------------------------------------------------------------------------
# Blinding scan
# ---------------------------------------------------------------------------


def model_tokens(snapshot: dict[str, Any]) -> list[str]:
    """Lower-case tokens that name the job's model or provider (for the ``model_name_in_source`` warning).

    From the provider, the model id (``provider/slug:variant``) and the display name
    (``"Provider: Name (date)"``): the whole slug and name plus their leading word (the model
    family, e.g. ``gpt``, ``claude``, ``qwen``). Generic words, local-endpoint prefixes and
    tokens shorter than 3 characters are dropped.
    """
    provider = str(snapshot.get("provider") or "")
    model_id = str(snapshot.get("model_id") or "")
    model_name = str(snapshot.get("model_name") or "")
    prefix, _, slug = model_id.rpartition("/")
    slug = slug.split(":", 1)[0]
    name = model_name.split(":", 1)[1] if ":" in model_name else model_name
    name = _PARENTHESIS.sub("", name)
    candidates = [provider, prefix, slug, name]
    for value in (slug, name):
        match = _LEADING_WORD.match(value.strip().lower())
        if match:
            candidates.append(match.group(0))
    tokens: set[str] = set()
    for candidate in candidates:
        token = " ".join(candidate.strip().lower().split())
        if len(token) < _MIN_TOKEN_LENGTH or token in _GENERIC_TOKENS or token.startswith("local"):
            continue
        tokens.add(token)
    return sorted(tokens)


def scan_text(texts: Iterable[str], tokens: Iterable[str]) -> list[str]:
    """The tokens found in ``texts`` (case-insensitive, not inside a longer word or number)."""
    tokens = [token for token in tokens if token]
    if not tokens:
        return []
    remaining = MAX_SCAN_CHARS
    chunks = []
    for text in texts:
        if remaining <= 0:
            break
        if isinstance(text, str) and text:
            chunks.append(text[:remaining].lower())
            remaining -= len(chunks[-1])
    haystack = "\n".join(chunks)
    found = []
    for token in tokens:
        if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", haystack):
            found.append(token)
    return sorted(found)


def source_texts(job: GenerationJob) -> list[str]:
    """What the portal's source viewer shows of the job: every file path (the tree), then every file's text.

    Read through ``source.job_files`` itself, so the scan follows the viewer's layout rules exactly
    (an all-blank ``files`` map falls back to the scaffolding blobs, then ``content``). Paths come
    first: they are short and must never fall behind :data:`MAX_SCAN_CHARS`.
    """
    files = source_service.job_files(job)
    return [*files.keys(), *files.values()]


def scan_findings(task_app: EvalTaskApp) -> list[str]:
    """Model tokens found in the task app's sampled findings (title, description, code snippet)."""
    from backend.analysis.models import Finding
    from backend.human_eval.services.access import parse_uuid

    ids = [pk for pk in (parse_uuid(value) for value in task_app.finding_sample or []) if pk is not None]
    if not ids:
        return []
    texts: list[str] = []
    for row in Finding.objects.filter(id__in=ids).values("title", "description", "code_snippet"):
        texts.extend(str(row[key] or "") for key in ("title", "description", "code_snippet"))
    return scan_text(texts, model_tokens(task_app.snapshot or {}))
