"""Run snapshots (owner A): the de-blinding key frozen on each task app.

``EvalTaskApp.snapshot`` records what the app *was* when it entered the study, so
analysis and exports never read live run data (spec §5.2, R1). Everything is read
through ``generation.runs.view`` (``RunView``), never from run columns directly:

- **taken when the app is added** (:func:`run_snapshot` with ``full=False``): model,
  condition, study, task (``app_slug`` / ``app_name`` / ``task_ref`` / ``prose_variant``),
  stack, kind, harness, the functional summary of the latest evaluation
  (:func:`functional_summary`), the evaluation suite and tester versions, plus
  ``spec_content_hash`` (the run's ``task_hash``: task version and prose variant);
- **re-taken at first open** (``full=True``, :func:`full_snapshots` for a whole pool):
  additionally ``functional.by_category``, the endpoint ``checks``
  (``name, category, passed``), ``static_findings`` and the blinding scan.

``functional`` comes from ``RunView.latest_evaluation()``. Missing data is never a pass
rate of 0: an unevaluated run, a ``not_measured`` / ``error`` evaluation or a tester result
with ``measured=false`` has ``pass_rate=None`` (``status`` says why).

``static_findings = {count, loc, loc_model, tools, by_tool}`` counts non-dependency findings
of static, non-AI tools in the latest ``ToolResult`` per (run, tool) that measured the run
(completed; a not-applicable tool is not measured, never a zero); ``tools`` lists the tools
that measured the run (so per-kLOC comparisons can keep only tools present
for every app) and ``by_tool`` their counts. Analysis runs on the whole app tree, so ``loc``
is the app-scope source LOC (``RunView.loc("app")``); ``loc_model`` is the model's own
contribution. Either is None when the run has no collected tree (never 0). Findings are
never deduplicated here.

``blinding_scan = {source: [...], findings: [...]}`` lists the model / provider tokens
(:func:`model_tokens`) found in the generated source exactly as the portal's source
viewer serves it (:func:`source_texts`: file paths and texts from ``source.run_files``;
case-insensitive) and, once the finding sample is drawn, in the sampled findings' text. It feeds the
``model_name_in_source`` open warning; generated code never changes, so the scan is
done once per app.

Group key: ``f"{app_slug}:{spec_content_hash[:12]}"``; an app without a task gets its
own app id (never paired) — see :func:`group_key`.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from typing import Any

from backend.human_eval.services import source as source_service

if TYPE_CHECKING:
    from collections.abc import Iterable

    from backend.generation.models import GenerationRun
    from backend.generation.models import RunEvaluation
    from backend.generation.runs.view import RunView
    from backend.human_eval.models import EvalTaskApp

#: Keys of the light snapshot (taken when an app is added); the full snapshot adds :data:`FULL_KEYS`.
BASE_KEYS = (
    "run_id",
    "model_id",
    "model_name",
    "provider",
    "condition_id",
    "condition_label",
    "study_id",
    "study_name",
    "app_slug",
    "app_name",
    "task_ref",
    "prose_variant",
    "stack",
    "auth_mode",
    "unit_key",
    "repeat_index",
    "kind",
    "harness",
    "functional",
    "evaluator_version",
    "tester_version",
    "created_at",
    "spec_content_hash",
    "run_fingerprint",
)
FULL_KEYS = ("checks", "static_findings", "blinding_scan")
FUNCTIONAL_KEYS = ("status", "pass_rate", "deployed", "source")
#: ``functional.source`` of a summary read from a ``RunEvaluation`` (the automated evaluation suite).
EVAL_SOURCE = "eval_suite"
#: Run outcomes that mean the app never got deployed (no evaluation can have a pass rate).
UNDEPLOYED_OUTCOMES = frozenset({"build_fail", "start_fail"})
#: Run outcomes reported as the functional status when no evaluation measured the run.
OUTCOME_STATUSES = frozenset({"build_fail", "start_fail", "infra_error", "output_invalid", "excluded"})
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
# Task (the app spec)
# ---------------------------------------------------------------------------


def spec_dict(run: GenerationRun | None) -> dict[str, Any] | None:
    """The task the run was generated from (``RunView.task_dict()``: structured task + the chosen prose)."""
    if run is None:
        return None
    from backend.generation.runs.view import run_view

    return run_view(run).task_dict()


def group_key(snapshot: dict[str, Any], app_id: Any) -> str:
    """``f"{app_slug}:{spec_content_hash[:12]}"``, or the app's own id when it has no task (never paired)."""
    slug = snapshot.get("app_slug") or ""
    digest = snapshot.get("spec_content_hash") or ""
    if slug and digest:
        return f"{slug}:{digest[:12]}"
    return str(app_id)


# ---------------------------------------------------------------------------
# Functional summary (latest evaluation)
# ---------------------------------------------------------------------------


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def evaluation_result(evaluation: RunEvaluation | None) -> dict[str, Any]:
    """The evaluation's ``EvalResult`` when it measured the app, else ``{}`` (nothing to report)."""
    from backend.generation.models import choices

    if evaluation is None or evaluation.status != choices.EvaluationStatus.MEASURED:
        return {}
    result = _dict(evaluation.result)
    return result if result.get("measured") is True else {}


def functional_summary(view: RunView, evaluation: RunEvaluation | None) -> dict[str, Any]:
    """TS ``FunctionalSummary`` of the run's latest evaluation (``RunView.latest_evaluation()``).

    ``pass_rate`` is the tester's ``agent_pass_rate`` and only set when the evaluation measured the app;
    ``deployed`` is the start stage's verdict (False for build/start failures, None when unknown).
    ``status``: ``tested`` (measured), the evaluation status (``pending``, ``running``, ``not_measured``,
    ``error``) or the run outcome when the app was never deployed or no evaluation exists.
    """
    outcome = view.outcome or ""
    deployed: bool | None = False if outcome in UNDEPLOYED_OUTCOMES else None
    if evaluation is None:
        status = outcome if outcome in OUTCOME_STATUSES else None
        return {"status": status, "pass_rate": None, "deployed": deployed, "source": None}
    result = evaluation_result(evaluation)
    start = _dict(_dict(evaluation.stages).get("start")).get("status")
    if start == "ok":
        deployed = True
    elif start == "fail":
        deployed = False
    if result:
        status = "tested"
    elif outcome in UNDEPLOYED_OUTCOMES:
        status = outcome
    else:
        status = str(evaluation.status)
    pass_rate = _number(_dict(result.get("metrics")).get("agent_pass_rate")) if result else None
    return {"status": status, "pass_rate": pass_rate, "deployed": deployed, "source": EVAL_SOURCE}


def _endpoints_by_id(task: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    structured = _dict((task or {}).get("structured"))
    return {
        str(endpoint["id"]): endpoint
        for endpoint in structured.get("endpoints") or []
        if isinstance(endpoint, dict) and endpoint.get("id")
    }


def endpoint_checks(result: dict[str, Any], task: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Per task endpoint, whether the tester's checks of it passed: ``{name, category, passed, endpoint_id}``.

    ``name`` is ``"METHOD /path"`` (the key of the participants' endpoint requirement items),
    ``category`` ``admin`` for admin endpoints, else ``api``. Only checks that ran (``pass`` / ``fail``)
    and are not flaky count; an endpoint passes when every one of its counted checks passed. Endpoints
    without a counted check are left out (not measured, never a fail).
    """
    endpoints = _endpoints_by_id(task)
    flaky = set(result.get("flaky_checks") or [])
    verdicts: dict[str, bool] = {}
    for check in result.get("checks") or []:
        if not isinstance(check, dict) or check.get("id") in flaky:
            continue
        endpoint_id = check.get("endpoint")
        if endpoint_id not in endpoints or check.get("status") not in ("pass", "fail"):
            continue
        verdicts[endpoint_id] = verdicts.get(endpoint_id, True) and check["status"] == "pass"
    checks = []
    for endpoint_id, passed in verdicts.items():
        endpoint = endpoints[endpoint_id]
        checks.append(
            {
                "name": f"{str(endpoint.get('method') or '').upper()} {endpoint.get('path') or ''}",
                "category": "admin" if endpoint.get("auth") == "admin" else "api",
                "passed": passed,
                "endpoint_id": endpoint_id,
            },
        )
    return sorted(checks, key=lambda check: (check["category"], check["name"]))


def checks_by_category(result: dict[str, Any]) -> dict[str, dict[str, int]]:
    """``{category: {passed, total}}`` of the tester's checks (its ``metrics.by_category`` summary)."""
    by_category = _dict(_dict(result.get("metrics")).get("by_category"))
    return {
        str(category): {"passed": int(counts.get("passed") or 0), "total": int(counts.get("n") or 0)}
        for category, counts in sorted(by_category.items())
        if isinstance(counts, dict)
    }


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

#: Run relations the snapshot reads (select them to avoid queries).
RUN_RELATIONS = ("model", "study", "condition__model")


def condition_label(condition: Any) -> str:
    """A study condition's label, else its model id."""
    if condition is None:
        return ""
    return str(condition.label or (condition.model.model_id if condition.model_id else ""))


def _base_snapshot(run: GenerationRun, view: RunView) -> dict[str, Any]:
    model = run.model if run.model_id else None
    study = run.study if run.study_id else None
    task = view.task
    evaluation = view.latest_evaluation()
    result = evaluation_result(evaluation)
    return {
        "run_id": view.id,
        "model_id": model.model_id if model is not None else "",
        "model_name": model.model_name if model is not None else "",
        "provider": model.provider if model is not None else "",
        "condition_id": view.condition_id,
        "condition_label": condition_label(run.condition if run.condition_id else None),
        "study_id": view.study_id,
        "study_name": study.name if study is not None else "",
        "app_slug": task.slug if task is not None else "",
        "app_name": task.title if task is not None else "",
        "task_ref": task.ref if task is not None else None,
        "prose_variant": (task.variant or None) if task is not None else None,
        "stack": view.stack.ref if view.stack is not None else None,
        "auth_mode": view.auth_mode,
        "unit_key": view.unit_key,
        "repeat_index": view.repeat_index,
        "kind": view.kind,
        "harness": view.harness[0] if view.harness else "",
        "functional": functional_summary(view, evaluation),
        "evaluator_version": evaluation.eval_suite.ref if evaluation is not None else None,
        "tester_version": result.get("tester_version") if result else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "spec_content_hash": (task.task_hash or None) if task is not None else None,
        "run_fingerprint": view.fingerprints.get("run_fingerprint") or None,
    }


def run_snapshot(run: GenerationRun, *, full: bool = True) -> dict[str, Any]:
    """The snapshot dict of a GenerationRun (stored in ``EvalTaskApp.snapshot``).

    ``full=False`` (when the app is added) skips the parts that need the code tree and the
    analysis tables; first open re-takes the full snapshot. Select :data:`RUN_RELATIONS` to avoid queries.
    """
    if full:
        return full_snapshots([run])[str(run.pk)]
    from backend.generation.runs.view import run_view

    return _base_snapshot(run, run_view(run))


def full_snapshots(runs: Iterable[GenerationRun]) -> dict[str, dict[str, Any]]:
    """Full snapshots of many runs keyed by ``str(run.pk)`` (static findings in a few batched queries)."""
    from backend.generation.runs.view import run_view

    runs = list(runs)
    views = {str(run.pk): run_view(run) for run in runs}
    findings = static_findings_by_run(runs, views)
    snapshots: dict[str, dict[str, Any]] = {}
    for run in runs:
        view = views[str(run.pk)]
        snapshot = _base_snapshot(run, view)
        result = evaluation_result(view.latest_evaluation())
        snapshot["functional"]["by_category"] = checks_by_category(result)
        snapshot["checks"] = endpoint_checks(result, view.task_dict())
        snapshot["static_findings"] = findings[str(run.pk)]
        snapshot["blinding_scan"] = {
            "source": scan_text(source_texts(run), model_tokens(snapshot)),
            "findings": [],
        }
        snapshots[str(run.pk)] = snapshot
    return snapshots


# ---------------------------------------------------------------------------
# Static findings
# ---------------------------------------------------------------------------


def _measured(status: str, summary: Any) -> bool:
    """Whether a tool result measured the app: completed, and not flagged ``tool_applicable: false``.

    A tool with no applicable file (``skipped`` / ``not_applicable``) is **not measured** in v3 (spec 8.8):
    it is left out of ``tools``, never counted as a true zero.
    """
    from backend.analysis.models import ToolResult

    if status != ToolResult.Status.COMPLETED:
        return False
    return not (isinstance(summary, dict) and summary.get("tool_applicable") is False)


def static_findings_by_run(
    runs: Iterable[GenerationRun],
    views: dict[str, RunView] | None = None,
) -> dict[str, dict[str, Any]]:
    """``{str(run_id): {count, loc, loc_model, tools, by_tool}}`` for static, non-AI, non-dependency analysis.

    The latest ``ToolResult`` per (run, tool) (``ToolResult.latest_ids_per_job``), kept when it measured the
    run (:func:`_measured`). AI tools (kind ``ai``), dynamic tools (``analysis_mode == "dynamic"``) and
    dependency scanners (tool category ``dependencies``) are left out, as are findings with category ``dependency``.
    """
    from django.db.models import Count

    from backend.analysis.models import AnalyzerTool
    from backend.analysis.models import Finding
    from backend.analysis.models import ToolResult
    from backend.generation.runs.view import run_view

    runs = list(runs)
    views = views or {}
    result: dict[str, dict[str, Any]] = {}
    if not runs:
        return result
    rows = list(
        ToolResult.objects.filter(id__in=ToolResult.latest_ids_per_job([run.pk for run in runs])).values(
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
    per_run: dict[str, dict[str, int]] = {}
    for row in kept:
        tools = per_run.setdefault(str(row["run__generation_job_id"]), {})
        tools[row["tool_slug"]] = counts.get(str(row["id"]), 0)
    for run in runs:
        tools = per_run.get(str(run.pk), {})
        view = views.get(str(run.pk)) or run_view(run)
        result[str(run.pk)] = {
            "count": sum(tools.values()),
            "loc": view.loc("app"),
            "loc_model": view.loc("model"),
            "tools": sorted(tools),
            "by_tool": dict(sorted(tools.items())),
        }
    return result


# ---------------------------------------------------------------------------
# Blinding scan
# ---------------------------------------------------------------------------


def model_tokens(snapshot: dict[str, Any]) -> list[str]:
    """Lower-case tokens that name the run's model or provider (for the ``model_name_in_source`` warning).

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


def source_texts(run: GenerationRun | None) -> list[str]:
    """What the portal's source viewer shows of the run: every file path (the tree), then every file's text.

    Read through ``source.run_files`` itself, so the scan covers exactly what participants can open.
    Paths come first: they are short and must never fall behind :data:`MAX_SCAN_CHARS`.
    """
    files = source_service.run_files(run)
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
