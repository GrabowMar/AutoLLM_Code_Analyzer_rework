"""Analysis-run context endpoints (source LOC by layer, test split, setup line, rule prevalence).

``GET /analysis/runs/{run_id}/context/?scope=study|mine`` puts one run in
context, descriptively (counts and n, no tests). Everything about the analyzed
generation run is read through ``RunView`` (``backend.generation.runs.view``):

* ``job`` -- the generation setup of the analyzed app: study, condition,
  repeat k of the planned N, model, harness, task, stack, fingerprints.
* ``source_loc`` -- the app's size by the study's definition: the **model**
  scope's non-test source lines by layer (``reports.services.loc`` rules), test
  lines apart, and ``app_total`` (the whole app tree the tools read, starter
  included); plus the stored files in ``NON_SOURCE_DIRS`` -- left out by this
  run when it recorded them, read by its tools when it predates that rule (``left_out``).
* ``test_split`` -- per tool, the run's code findings in test files and in the
  app's own source (``is_test_path``; the study counts test code apart), and
  apart from both those in ``NON_SOURCE_DIRS`` files (``findings_in_excluded``:
  read by a run finalized before the exclusion rule; neither test code nor source).
* ``rule_prevalence`` -- for each rule of this run, in how many analyzed apps
  of the corpus (the study's current runs -- the first usable attempt of each
  cell -- or all the caller's apps) that tool's latest result reports it, over
  the apps that tool measured (completed; a not-applicable tool measured
  nothing) -- never over all apps -- and the same for this model's apps. Each
  tool's latest result per app counts (``ToolResult.latest_ids_per_job``), as in
  the study tables; a note names this run's tools whose result is not the one
  counted (superseded, or not measured), and ``interim`` flags a study corpus
  while the study runs.
"""

from __future__ import annotations

from collections import Counter
from collections import defaultdict
from typing import Any

from django.db.models import Count
from django.db.models import Q
from ninja import Query
from ninja.errors import HttpError

from backend.analysis.api.schema import RunContextSchema
from backend.analysis.api.views._router import router
from backend.analysis.api.views.runs import _get_run
from backend.analysis.api.views.runs import code_finding_parts
from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.analysis.services import runner
from backend.analysis.services.base import NON_SOURCE_DIRS
from backend.analysis.services.base import is_dependency_advisory
from backend.analysis.services.base import is_non_source_path
from backend.generation.models import GenerationRun
from backend.generation.models import choices
from backend.generation.runs.view import RunView
from backend.generation.runs.view import run_view
from backend.reports.services.loc import is_source_file
from backend.reports.services.loc import loc_from_files
from backend.reports.services.loc import loc_of_test_files
from backend.statistics.services.dedup import RECORD_FIELDS
from backend.statistics.services.dedup import finding_record
from backend.statistics.services.dedup import issue_class
from backend.statistics.services.runs import study_current_runs

CONTEXT_SCOPES = ("study", "mine")
_EXCLUDED_SAMPLE = 20
# A study in one of these states has all its runs: its counts are final.
_STUDY_FINAL = frozenset({choices.StudyStatus.COMPLETED, choices.StudyStatus.CANCELLED, choices.StudyStatus.STOPPED})


def _is_measured(status: str, skip_reason: Any) -> bool:
    """A completed result: the only one that measured the code (not applicable is not measured, never 0)."""
    return runner._is_measurement(status, skip_reason)  # noqa: SLF001


# --- Setup ---------------------------------------------------------------------


def _setup(gen: GenerationRun, view: RunView) -> dict[str, Any]:
    """The generation setup of the analyzed run (``RunView`` plus the study/condition rows)."""
    model = gen.model if gen.model_id else None
    study = gen.study if gen.study_id else None
    condition = gen.condition if gen.condition_id else None
    task = view.task
    return {
        "id": view.id,
        "kind": view.kind,
        "status": view.status,
        "outcome": view.outcome,
        "usable": view.usable,
        "model_pk": model.pk if model else None,
        "model_id": model.model_id if model else (view.model_slug or ""),
        "model_name": model.model_name if model else "",
        "model_slug": (model.canonical_slug or "") if model else "",
        "task_ref": task.ref if task else "",
        "task_slug": task.slug if task else "",
        "task_title": task.title if task else "",
        "prose_variant": task.variant if task else "",
        "unit_key": view.unit_key or "",
        "stack_ref": view.stack.ref if view.stack else "",
        "study_id": view.study_id,
        "study_name": study.name if study else "",
        "condition_id": view.condition_id,
        "condition_label": str(condition) if condition else "",
        "stage": view.stage or "",
        "repeat_index": view.repeat_index,
        "planned_repeats": study.repeats if study else None,
        "harness": view.harness[0] if view.harness else None,
        "harness_version": view.harness[1] if view.harness else "",
        "config_fingerprint": view.fingerprints.get("config_fingerprint") or None,
        "run_fingerprint": view.fingerprints.get("run_fingerprint") or None,
    }


# --- Source size -----------------------------------------------------------------


def _loc(files: dict[str, str]) -> dict[str, Any]:
    return {**loc_from_files(files), **loc_of_test_files(files)}


def _source_loc(run: AnalysisRun) -> dict[str, Any] | None:
    """Source LOC by layer (the study's definition: model scope) and the stored files the analysis left out."""
    code = run.get_code_for_analysis()
    if not code:
        return None
    excluded = sorted(path for path in code if is_non_source_path(path))
    dirs = Counter(
        next((part for part in path.strip("/").split("/")[:-1] if part in NON_SOURCE_DIRS), "") for path in excluded
    )
    gen = run.generation_job if run.generation_job_id and not run.source_code else None
    if gen is not None:
        scope = "model"
        loc = _loc(run_view(gen).code_files("model"))
        app_total = loc_from_files(code)["total_loc"]
    else:
        # Pasted code is a {path: content} map; bare keys (``backend``) are single blobs.
        scope = "code"
        loc = _loc(runner._materialize(code))  # noqa: SLF001
        app_total = loc["total_loc"]
    other = sum(1 for path in code if not is_non_source_path(path) and not is_source_file(path))
    left_out, in_excluded = _excluded_by_run(run, excluded)
    return {
        "scope": scope,
        "total": loc["total_loc"],
        "files": loc["source_files"],
        "by_layer": {"backend": loc["backend_loc"], "frontend": loc["frontend_loc"], "tests": loc["test_loc"]},
        "test_files": loc["test_files"],
        "app_total": app_total,
        "other_files": other,
        "excluded_paths": {
            "count": len(excluded),
            "sample": excluded[:_EXCLUDED_SAMPLE],
            "dirs": dict(dirs.most_common()),
            "left_out": left_out,
            "findings_in_excluded": in_excluded,
        },
    }


def _excluded_by_run(run: AnalysisRun, excluded: list[str]) -> tuple[bool | None, int]:
    """Whether *run*'s tools left the *excluded* paths out, and how many of its findings lie in them.

    The paths are computed with today's rule; a run records the ones it left
    out (``summary.excluded_paths``, written since the rule exists). A run
    finalized with tools but without that record predates the rule: its tools
    read those files, and some of its findings may be about them (False).
    None when no tool ran or the run never finished (nothing to say).
    """
    if not excluded:
        return None, 0
    summary = run.summary if isinstance(run.summary, dict) else {}
    if "excluded_paths" in summary:
        left_out: bool | None = True
    elif "total_findings" in summary and summary.get("tools_run"):
        left_out = False
    else:
        left_out = None
    paths = Finding.objects.filter(result__run=run).exclude(file_path="").values_list("file_path", flat=True)
    return left_out, sum(1 for path in paths if is_non_source_path(path))


# --- Test split ------------------------------------------------------------------


def _test_split(run: AnalysisRun) -> list[dict[str, Any]]:
    """Per tool: the run's code findings in test files, in the app's own source, and in excluded directories.

    A file in ``NON_SOURCE_DIRS`` is neither test code nor source: its findings
    are counted apart (``findings_in_excluded``), as ``findings?scope=tests|source``
    leave them out (``code_finding_parts``).
    """
    findings = Finding.objects.filter(result__run=run)
    in_tests, excluded = code_finding_parts(findings)
    split: dict[str, Counter] = defaultdict(Counter)
    for finding_id, tool in findings.filter(runner.finding_scope_q("code")).values_list("id", "result__tool_slug"):
        split[tool]["excluded" if finding_id in excluded else "tests" if finding_id in in_tests else "source"] += 1
    return [
        {
            "tool": tool,
            "findings_in_tests": c["tests"],
            "findings_in_source": c["source"],
            "findings_in_excluded": c["excluded"],
        }
        for tool, c in sorted(split.items())
    ]


# --- Rule prevalence ---------------------------------------------------------------


def _run_rules(run: AnalysisRun) -> dict[tuple[str, str], dict[str, Any]]:
    """The run's (tool, rule) pairs with their count, a representative name/title and their issue class."""
    dynamic = AnalyzerTool.dynamic_slugs()
    rules: dict[tuple[str, str], dict[str, Any]] = {}
    for row in Finding.objects.filter(result__run=run).exclude(rule_id="").values(*RECORD_FIELDS):
        tool, rule_id = row["result__tool_slug"], row["rule_id"]
        record = finding_record(row, "")
        extra = row["tool_specific_data"] if isinstance(row["tool_specific_data"], dict) else {}
        entry = rules.setdefault(
            (tool, rule_id),
            {
                "tool": tool,
                "rule_id": rule_id,
                "rule_name": "",
                "title": "",
                "classes": Counter(),
                # As runner.finding_scope_q counts it: an advisory tool's non-dependency finding
                # (a trivy secret or misconfiguration) is code, not a dependency advisory.
                "scope": "dependencies"
                if is_dependency_advisory(tool, row["category"])
                else ("runtime" if tool in dynamic else "code"),
                "count_in_run": 0,
            },
        )
        entry["count_in_run"] += 1
        # The lowest name / title, not the first the (unordered) query returned: stable output.
        for key, value in (("rule_name", str(extra.get("rule_name") or "")), ("title", row["title"] or "")):
            if value and (not entry[key] or value < entry[key]):
                entry[key] = value
        entry["classes"][
            issue_class(
                tool,
                rule_id,
                cwe=record["cwe"],
                category=record["category"],
                package=record["package"],
                issue_key=record["issue_key"],
            )
        ] += 1
    return rules


def _corpus_scope(run: AnalysisRun, user, requested: str) -> tuple[str | None, list[str], list[str]]:
    """The corpus scope to count prevalence over, the scopes available, and notes on a fallback."""
    if requested and requested not in CONTEXT_SCOPES:
        raise HttpError(400, f"scope must be one of: {', '.join(CONTEXT_SCOPES)}.")
    gen = run.generation_job if run.generation_job_id else None
    notes: list[str] = []
    study = gen.study if gen is not None and gen.study_id else None
    own_study = study is not None and (study.created_by_id == user.id or user.is_staff)
    available = ["study", "mine"] if own_study else ["mine"]
    if requested == "study" and not own_study:
        notes.append(
            "This app is not part of a study of yours: prevalence is counted over all your analyzed apps.",
        )
        return "mine", available, notes
    return (requested or ("study" if own_study else "mine")), available, notes


def _mine(user):
    """The caller's current runs: runs outside any study cell, plus each study's current run per cell.

    A cell's current run is its first usable attempt (``current_runs``), never a later one;
    ``beyond_plan`` runs are left out.
    """
    own = GenerationRun.objects.filter(created_by=user, beyond_plan=False)
    ids = set(own.filter(cell__isnull=True).values_list("id", flat=True))
    for study_id in own.filter(cell__isnull=False).values_list("study_id", flat=True).distinct():
        for stage in (choices.Stage.PILOT, choices.Stage.MAIN):
            ids |= set(study_current_runs(study_id, stage=stage).filter(created_by=user).values_list("id", flat=True))
    return GenerationRun.objects.filter(id__in=ids)


def _corpus_runs(run: AnalysisRun, user, scope: str):
    """The corpus's generation runs: the study's current runs (main stage), or every current run of the caller."""
    if scope == "study":
        return study_current_runs(run.generation_job.study_id, stage=choices.Stage.MAIN)
    return _mine(user)


def _prevalence(
    run: AnalysisRun,
    user,
    scope: str,
) -> tuple[list[dict[str, Any]], int, int | None, bool, str, list[str]]:
    """Rule prevalence rows, corpus size n, this model's n, whether the app is in the corpus, the scope label,
    and the tools of the run's rules whose result here is not the one counted for the app.

    The corpus counts an app by each tool's latest measuring result: a result of this run
    that a later analysis superseded, or that measured nothing (failed, not applicable), is
    not among the counts, so its findings may not be either.
    """
    gen = run.generation_job if run.generation_job_id else None
    model_id = gen.model_id if gen is not None else None
    corpus = _corpus_runs(run, user, scope)
    label = f"study {run.generation_job.study.name}" if scope == "study" else "all your analyzed apps"
    model_of = dict(corpus.values_list("id", "model_id"))
    results = list(
        ToolResult.objects.filter(id__in=ToolResult.latest_ids_per_job(list(model_of))).values_list(
            "id",
            "run__generation_job_id",
            "tool_slug",
            "status",
            "summary__skip_reason",
        ),
    )
    analyzed = {gen_id for _id, gen_id, _slug, _status, _reason in results}
    n = len(analyzed)
    model_n = sum(1 for g in analyzed if model_of.get(g) == model_id) if model_id is not None else None
    self_in = gen is not None and gen.id in analyzed

    rules = _run_rules(run)
    if not rules:
        return [], n, model_n, self_in, label, []
    tools = {tool for tool, _rule in rules}
    counted_here = {
        result_id
        for result_id, gen_id, _slug, status, reason in results
        if gen is not None and gen_id == gen.id and _is_measured(status, reason)
    }
    here = list(run.results.filter(tool_slug__in=tools).values_list("id", "tool_slug"))
    uncounted = sorted({slug for _id, slug in here} - {slug for result_id, slug in here if result_id in counted_here})
    measured_ids: list = []
    apps_measured: Counter = Counter()
    model_measured: Counter = Counter()
    for result_id, gen_id, slug, status, reason in results:
        if slug not in tools or not _is_measured(status, reason):
            continue
        measured_ids.append(result_id)
        apps_measured[slug] += 1
        if model_id is not None and model_of.get(gen_id) == model_id:
            model_measured[slug] += 1

    # One grouped query: per (tool, rule), the measured apps whose latest result reports the rule.
    app_field = "result__run__generation_job_id"
    counted = (
        Finding.objects.filter(result_id__in=measured_ids, rule_id__in=sorted({r for _t, r in rules}))
        .values("result__tool_slug", "rule_id")
        .annotate(
            apps=Count(app_field, distinct=True),
            model_apps=Count(app_field, distinct=True, filter=Q(result__run__generation_job__model_id=model_id)),
        )
    )
    with_rule = {(r["result__tool_slug"], r["rule_id"]): (r["apps"], r["model_apps"]) for r in counted}
    rows = []
    for key, entry in rules.items():
        apps, model_apps = with_rule.get(key, (0, 0))
        tool = key[0]
        classes = entry["classes"]
        rows.append(
            {
                **{k: v for k, v in entry.items() if k != "classes"},
                # One class for the rule's findings, else none: a finding is never labelled with
                # another finding's class (nuclei's missing-headers rule: one class per header).
                "issue_class": next(iter(classes)) if len(classes) == 1 else "",
                "apps_with_rule": apps,
                "apps_measured": apps_measured[tool],
                "model_apps_with_rule": model_apps if model_id is not None else None,
                "model_apps_measured": model_measured[tool] if model_id is not None else None,
            },
        )
    rows.sort(key=lambda r: (-r["count_in_run"], r["tool"], r["rule_id"]))
    return rows, n, model_n, self_in, label, uncounted


@router.get("/runs/{run_id}/context/", response=RunContextSchema)
def run_context(
    request,
    run_id: str,
    scope: str = Query(
        "",
        description="Corpus of the rule prevalence: study (the app's study: its current runs) | mine (all your "
        "analyzed apps); empty: the study when the app belongs to one of yours, else mine.",
    ),
):
    """A run in context: the setup behind the app, its source size, test-code findings apart, rule prevalence."""
    run = _get_run(request, run_id)
    user = request.auth
    gen = run.generation_job if run.generation_job_id else None
    used, available, notes = _corpus_scope(run, user, scope)
    rows, n, model_n, self_in, label, uncounted = _prevalence(run, user, used)
    test_split = _test_split(run)
    if in_excluded := sum(r["findings_in_excluded"] for r in test_split):
        notes.append(
            f"{in_excluded} code finding{'s' if in_excluded != 1 else ''} of this run lie in excluded directories "
            "(dependency, cache and scratch directories such as .tmp or node_modules) that its tools read before "
            "the exclusion rule: the test split counts them apart, neither as test code nor as the app's source.",
        )
    if gen is not None and not self_in:
        notes.append(
            "This app is not among the corpus's analyzed apps (not its cell's current run, or no finished "
            "analysis): its rules are compared with the others, but it is not counted in them.",
        )
    elif gen is not None and uncounted:
        notes.append(
            (
                f"This run's {', '.join(uncounted)} results are not the ones counted for this app (later "
                "analyses superseded them, or they measured nothing)"
                if len(uncounted) > 1
                else f"This run's {uncounted[0]} result is not the one counted for this app (a later analysis "
                "superseded it, or it measured nothing)"
            )
            + ": the counts use the app's latest measuring result of each tool, so they may not include this "
            "run's findings of " + ("those tools." if len(uncounted) > 1 else "that tool."),
        )
    # Counts over a running study change as its runs finish: interim, not final.
    study = gen.study if gen is not None and gen.study_id else None
    interim = used == "study" and study is not None and study.status not in _STUDY_FINAL
    return {
        "run_id": str(run.id),
        "scope": used,
        "scopes_available": available,
        "scope_label": label,
        "n": n,
        "model_n": model_n,
        "self_in_corpus": self_in,
        "interim": interim,
        "job": _setup(gen, run_view(gen)) if gen is not None else None,
        "source_loc": _source_loc(run),
        "test_split": test_split,
        "rule_prevalence": rows,
        "notes": notes,
    }
