"""Export a sample of generated apps for manual review, and score the returned labels.

The automated verdicts (AI-reviewer findings, static findings, the
evaluation outcome) are only as good as their agreement with a human. This
module draws a seeded, stratified sample of apps, writes their code and
specs plus blank label sheets into a ZIP, and later scores the filled
sheets against the automated verdicts:

* AI-reviewer and (deduplicated) static findings: precision with a 95%
  Wilson interval, per normalized severity, category and tool; dependency
  advisories on their own sheet, also per origin (model-added, starter,
  transitive); runtime issues (reported by dynamic scanners only) on their
  own sheet, one row per issue class per app,
* app-level functional verdict vs the evaluation outcome: Cohen's kappa,
* per-app requirement compliance vs the evaluated pass rate: Spearman rho.

The population is a study's current runs (or every sample run the caller owns) with a usable outcome
and a collected tree. Each app folder holds the app tree (``code/``, read through ``RunView``), its
``files.csv`` (path, origin, sha, loc) and ``resolved_lock.json``; the frozen ``manifest.json`` names the
model, so it is kept under ``key/`` with the other automated facts.

With ``blind=True`` (default) sample folders are ``S001``… and model,
task and automated outcomes live only in ``key.csv`` and ``key/``, which the
reviewer should not open until labelling is done.

Findings come from each app's latest result per tool
(``ToolResult.latest_ids_per_job``), the results the study tables count, so
a partial re-run does not shrink the sample to the re-run tools. Issues are
split the way the study tables split them: ``static_issues`` holds the
app's code issues (``code_unique``); issues in test code are not sampled.
"""

from __future__ import annotations

import csv
import io
import json
import math
import random
import zipfile
from collections import Counter
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from django.utils import timezone

from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.analysis.services.base import ADVISORY_TOOLS
from backend.analysis.services.base import is_dependency_advisory
from backend.export.services.csv_safe import safe_cell
from backend.export.services.findings import dynamic_tool_slugs
from backend.export.services.runs import files_csv
from backend.export.services.runs import resolved_lock
from backend.generation.models import GenerationRun
from backend.generation.models import choices
from backend.reports.services.loc import is_test_path
from backend.reports.services.loc import loc_from_view
from backend.statistics.services.dedup import RECORD_FIELDS
from backend.statistics.services.dedup import SEVERITY_RANK
from backend.statistics.services.dedup import IssueCluster
from backend.statistics.services.dedup import cluster_findings
from backend.statistics.services.dedup import finding_record
from backend.statistics.services.dependencies import DependencyOwnership
from backend.statistics.services.dependencies import cluster_package
from backend.statistics.services.dependencies import dependency_ownership
from backend.statistics.services.dependencies import dependency_scope
from backend.statistics.services.inference import cohens_kappa
from backend.statistics.services.inference import spearman
from backend.statistics.services.inference import wilson_interval
from backend.statistics.services.runs import condition_label

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from backend.generation.runs.view import RunView

# 2: tool_severity columns, severity -> normalized_severity, dependency_issues.csv,
#    key.csv analysis_run_ids (findings may come from several runs).
# 3: runtime_issues.csv (dynamic-scanner issues); static_issues holds code
#    issues only (no test code, no runtime issues).
# 4: v3 runs: task instead of template, files.csv / resolved_lock.json per app, key/<sid>/manifest.json,
#    evaluation status and pass rate from RunEvaluation, dependency origin "starter".
SAMPLE_FORMAT_VERSION = 4
STRATIFY_KEYS = ("model", "task")
UNRATED = "unrated"
# What a deduplicated issue describes, as the study tables classify it.
CODE, TEST, RUNTIME, DEPENDENCY = "code", "test", "runtime", "dependency"
# A runtime row lists at most this many of the URL paths its issue class was seen on.
_MAX_RUNTIME_PATHS = 10

AI_FINDING_COLUMNS = [
    "sample_id",
    "finding_id",
    "file_path",
    "line",
    "tool_severity",
    "normalized_severity",
    "category",
    "rule_id",
    "title",
    "description",
    "human_verdict",  # TP | FP | unsure
    "human_severity",
    "notes",
]
STATIC_ISSUE_COLUMNS = [
    "sample_id",
    "issue_id",
    "file_path",
    "line",
    "tool_severity",
    "normalized_severity",
    "category",
    "issue_class",
    "tools",
    "rule_ids",
    "title",
    "human_verdict",  # TP | FP | unsure
    "notes",
]
DEPENDENCY_ISSUE_COLUMNS = [
    "sample_id",
    "issue_id",
    "file_path",
    "package",
    "installed_version",
    "advisories",
    "origin",  # model | starter | transitive
    "tool_severity",
    "normalized_severity",
    "tools",
    "title",
    "human_verdict",  # TP | FP | unsure
    "notes",
]
RUNTIME_ISSUE_COLUMNS = [
    "sample_id",
    "issue_id",
    "issue_class",
    "url_paths",
    "tool_severity",
    "normalized_severity",
    "tools",
    "rule_ids",
    "title",
    "human_verdict",  # TP | FP | unsure
    "notes",
]
REQUIREMENT_COLUMNS = [
    "sample_id",
    "requirement_id",
    "kind",
    "requirement",
    "human_met",
    "notes",
]  # yes|partial|no; kind is "endpoint" or "ui"
APP_COLUMNS = ["sample_id", "files", "loc", "human_functional", "human_quality", "notes"]  # works|partial|broken; 1-5
KEY_COLUMNS = [
    "sample_id",
    "run_id",
    "model",
    "task",
    "condition_label",
    "kind",
    "repeat_index",
    "outcome",
    "eval_status",
    "eval_pass_rate",
    "deployed",
    "analysis_run_ids",
]

_README = """# Manual review sample

Sample {n} apps drawn {method} (seed {seed}) from {population} eligible apps.

Do NOT open `key.csv` or `key/` until every sheet is filled in: they hold the
model, task and the automated verdicts you are being compared against.

Each `apps/<sample_id>/` folder holds the app's code (`code/`), `REQUIREMENTS.md`,
`files.csv` (every path with its origin: `starter` files came with the starter,
`authored`/`modified`/`slot` files are the model's, `parent` files come from the
parent app of a slot variant) and `resolved_lock.json` (the dependency versions
the build used). Fill in the blank columns of the sheets in `labels/`:

| sheet | column | values |
|---|---|---|
| ai_findings.csv | human_verdict | TP (real issue), FP (not an issue), unsure |
| static_issues.csv | human_verdict | TP, FP, unsure |
| dependency_issues.csv | human_verdict | TP (the installed version is affected), FP, unsure |
| runtime_issues.csv | human_verdict | TP (the running app has the issue), FP, unsure |
| requirements.csv | human_met | yes, partial, no |
| apps.csv | human_functional | works, partial, broken |
| apps.csv | human_quality | 1 (poor) .. 5 (excellent) |

`tool_severity` is the level exactly as each tool printed it (`tool:LABEL`
per tool for a merged issue; `unrated` when the tool rates nothing).
`normalized_severity` is a heuristic 5-level bucket, used only to group
the scores. `static_issues.csv` holds issues in the app's code (issues
in test code are left out); `dependency_issues.csv` holds
known-vulnerability advisories about its packages, with `origin` saying
who chose the vulnerable version: `model` (the app added the package, or
changed the starter's version to an affected one), `starter` (the starter's
own version spec) or `transitive` (no manifest declares it).
`runtime_issues.csv` holds what only the dynamic scanners (ZAP, Nuclei,
Wapiti) reported against the running app, one row per kind of issue with
the URL paths it was seen on; judge it from the code (does the app set
that header, escape that parameter?).

Leave a cell empty to skip it. Score the filled sheets with
`manage.py score_review_sample <this folder or zip>` or
`POST /api/export/review-sample/score/`.
"""


def _stratum(run: GenerationRun, view: RunView, keys: tuple[str, ...]) -> tuple[str, ...]:
    values = {
        "model": view.model_slug or "",
        "task": view.task.ref if view.task is not None else "",
    }
    return tuple(values[k] for k in keys)


def is_eligible(run: GenerationRun) -> bool:
    """A completed run with a usable outcome and a collected tree (infra errors and exclusions are not apps)."""
    return run.status == choices.RunStatus.COMPLETED and run.usable is True and bool(run.tree_hash)


def review_population(user, *, study_id: str | None = None) -> QuerySet[GenerationRun]:
    """The runs a review sample is drawn from: a study's current runs (owner-checked) or every sample run.

    ``user=None`` is the shell's staff view. Raises ``ValueError`` ("Study not found") for an unknown study
    or one *user* may not report on.
    """
    from django.core.exceptions import ValidationError

    from backend.generation.models import Study
    from backend.reports.services.generators import owned_study
    from backend.statistics.services.runs import study_runs

    if study_id and user is not None:
        owned_study(study_id, user)
    elif study_id:
        try:
            found = Study.objects.filter(id=study_id).exists()
        except ValidationError:
            found = False
        if not found:
            msg = f"Study not found: {study_id}"
            raise ValueError(msg)
    return study_runs(user, study_id=study_id).exclude(kind=choices.RunKind.REFERENCE)


def select_sample(
    runs: QuerySet[GenerationRun] | list[GenerationRun],
    *,
    size: int | None = None,
    per_stratum: int | None = None,
    stratify: tuple[str, ...] = ("model",),
    seed: int = 0,
) -> tuple[list[GenerationRun], dict[str, Any]]:
    """A seeded stratified sample of usable runs with a collected tree (:func:`is_eligible`), and how it was drawn.

    ``per_stratum`` takes that many from each stratum; otherwise ``size`` is
    allocated proportionally (largest remainder, at least one per stratum).
    """
    unknown = set(stratify) - set(STRATIFY_KEYS)
    if unknown:
        msg = f"Unknown stratify key(s): {', '.join(sorted(unknown))}"
        raise ValueError(msg)
    from backend.generation.runs.view import run_views

    eligible = sorted((r for r in runs if is_eligible(r)), key=lambda r: str(r.id))
    strata: dict[tuple[str, ...], list[GenerationRun]] = defaultdict(list)
    for run, view in zip(eligible, run_views(eligible), strict=True):
        strata[_stratum(run, view, stratify)].append(run)

    rng = random.Random(seed)  # noqa: S311 - reproducible sampling, not crypto
    keys = sorted(strata)
    if per_stratum is not None:
        quota = {k: min(per_stratum, len(strata[k])) for k in keys}
    else:
        total = min(size or 0, len(eligible))
        quota = _allocate(total, {k: len(strata[k]) for k in keys})
    selected: list[GenerationRun] = []
    for k in keys:
        selected.extend(rng.sample(strata[k], quota[k]))
    method = (
        f"stratified by {' x '.join(stratify)}, {per_stratum} per stratum"
        if per_stratum is not None
        else f"stratified by {' x '.join(stratify)}, proportional allocation"
        if stratify
        else "by simple random sampling"
    )
    return selected, {
        "seed": seed,
        "method": method,
        "stratify": list(stratify),
        "population": len(eligible),
        "strata": [{"stratum": list(k), "population": len(strata[k]), "sampled": quota[k]} for k in keys],
    }


def _allocate(total: int, sizes: dict[tuple, int]) -> dict[tuple, int]:
    if not sizes or total <= 0:
        return dict.fromkeys(sizes, 0)
    population = sum(sizes.values())
    if total < len(sizes):
        # Too few draws for one per stratum: the largest strata get one each.
        largest = sorted(sizes, key=lambda k: (-sizes[k], k))[:total]
        return {k: int(k in largest) for k in sizes}
    quota = {k: max(1, math.floor(total * n / population)) for k, n in sizes.items()}
    quota = {k: min(q, sizes[k]) for k, q in quota.items()}
    remainders = sorted(sizes, key=lambda k: (-(total * sizes[k] / population - quota[k]), k))
    i = 0
    while sum(quota.values()) < total and i < 10 * len(remainders):
        k = remainders[i % len(remainders)]
        if quota[k] < sizes[k]:
            quota[k] += 1
        i += 1
    return quota


def _requirements(view: RunView) -> list[tuple[str, str, str]]:
    """The task's checkable requirements: each endpoint (``E<n>``) and each UI requirement (``U<n>``)."""
    task = view.task_dict() or {}
    structured = task.get("structured") or {}
    rows = []
    for i, endpoint in enumerate(structured.get("endpoints") or [], start=1):
        text = f"{endpoint.get('method', '')} {endpoint.get('path', '')} (auth: {endpoint.get('auth', '')})"
        rows.append((f"E{i}", "endpoint", f"[{endpoint.get('id', '')}] {text}".strip()))
    for i, item in enumerate(structured.get("ui") or [], start=1):
        rows.append((f"U{i}", "ui", f"[{item.get('id', '')}] {item.get('text', '')}".strip()))
    return rows


def _requirements_md(view: RunView, reqs: list[tuple[str, str, str]]) -> str:
    task = view.task_dict() or {}
    lines = [f"# {task.get('title') or 'Task'}", "", task.get("summary") or ""]
    if task.get("prose"):
        lines += ["", "## Task as the model received it", "", task["prose"]]
    for kind, heading in (("endpoint", "API endpoints"), ("ui", "UI requirements")):
        own = [r for r in reqs if r[1] == kind]
        if own:
            lines += ["", f"## {heading}", ""]
            lines += [f"- **{rid}** {text}" for rid, _k, text in own]
    return "\n".join(lines) + "\n"


def _csv(columns: list[str], rows: list[dict[str, Any]]) -> str:
    """A label sheet; the sheets are opened in a spreadsheet, so formula-like cells are neutralized."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: safe_cell(v) for k, v in row.items()})
    return buf.getvalue()


def _scope_ownerships(runs: list[GenerationRun], memo: dict) -> dict[str, DependencyOwnership]:
    """Every eligible run's ownership, linked on the population's advisory evidence.

    The study tables judge a changed starter spec on what every app of the
    scope installed (:func:`dependency_scope`); the review sample links the
    population it was drawn from the same way, so both attribute an advisory
    to the same party.
    """
    ownerships = {str(r.id): dependency_ownership(r, memo) for r in runs}
    if not ownerships:
        return ownerships
    result_ids = ToolResult.latest_ids_per_job([r.id for r in runs])
    rows = (
        Finding.objects.filter(
            result_id__in=result_ids,
            result__status=ToolResult.Status.COMPLETED,
            result__tool_slug__in=ADVISORY_TOOLS,
            category="dependency",
        )
        .order_by("id")
        .values("result__run__generation_job_id", "tool_severity", "description", *RECORD_FIELDS)
    )
    by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        run_id = str(row["result__run__generation_job_id"])
        by_run[run_id].append(_static_record(row, run_id))
    clusters = [c for records in by_run.values() for c in cluster_findings(records)]
    dependency_scope(ownerships, clusters)
    return ownerships


def _dependency_origins(advisories: list[IssueCluster], ownership: DependencyOwnership) -> dict[int, str]:
    """Per advisory (by ``id()``), who chose the vulnerable version: the model, the starter, or neither.

    ``model`` is the study's attribution (package and version,
    :meth:`DependencyOwnership.is_models`, judged on the scope's evidence);
    otherwise ``starter`` when a manifest declares the package (with the
    starter's spec) and ``transitive`` when nothing declares it.
    """
    if not advisories:
        return {}
    origins = {}
    for c in advisories:
        package = cluster_package(c)
        if ownership.is_models(c):
            origins[id(c)] = "model"
        elif package and (package in ownership.app or package in ownership.starter):
            origins[id(c)] = "starter"
        else:
            origins[id(c)] = "transitive"
    return origins


def issue_kind(cluster: IssueCluster, dynamic: set[str]) -> str:
    """``dependency``, ``runtime``, ``test`` or ``code``: the study tables' split of deduplicated issues.

    A dependency advisory (a known-vulnerability scanner's report about a
    package, by the analyzer's one rule :func:`is_dependency_advisory`) is
    about a package, a runtime issue (every member from a dynamic scanner)
    about the running app, and a test-code issue (:func:`is_test_path`)
    about code that is not the delivered app.
    """
    members = cluster.members
    if any(is_dependency_advisory(m["tool"], m.get("category") or "", m.get("rule_id") or "") for m in members):
        return DEPENDENCY
    if members and all(m["tool"] in dynamic for m in members):
        return RUNTIME
    if is_test_path(cluster.file_path):
        return TEST
    return CODE


def _tool_labels(clusters: list[IssueCluster]) -> str:
    """The level each member tool printed, as ``tool:LABEL`` (a merged issue has no single label)."""
    return ";".join(sorted({f"{m['tool']}:{m.get('tool_severity') or UNRATED}" for c in clusters for m in c.members}))


def _runtime_rows(clusters: list[IssueCluster]) -> list[list[IssueCluster]]:
    """Runtime clusters grouped per issue class: one missing header is one issue, on however many URLs."""
    by_class: dict[str, list[IssueCluster]] = defaultdict(list)
    for c in clusters:
        by_class[c.issue_class].append(c)
    return [by_class[k] for k in sorted(by_class)]


def _url_paths(group: list[IssueCluster]) -> str:
    paths = sorted({m.get("file_path") or "/" for c in group for m in c.members})
    more = len(paths) - _MAX_RUNTIME_PATHS
    return ";".join(paths[:_MAX_RUNTIME_PATHS]) + (f";(+{more} more)" if more > 0 else "")


def _member_paths(cluster: IssueCluster) -> str:
    """The files the issue's findings name (one advisory can name several manifests)."""
    return ";".join(sorted({m.get("file_path") or "" for m in cluster.members} - {""}))


def _sample_clusters(
    clusters: list[Any],
    cap: int,
    rng: random.Random,
    *,
    first: list[Any] | None = None,
) -> list[Any]:
    """Up to *cap* clusters (or groups of clusters) drawn with *rng*; the *first* ones are taken before the rest."""
    if len(clusters) <= cap:
        return clusters
    first = first or []
    if len(first) >= cap:
        return rng.sample(first, cap)
    taken = {id(c) for c in first}
    rest = [c for c in clusters if id(c) not in taken]
    return first + rng.sample(rest, cap - len(first))


def _run_findings(run_ids: list) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[str]]]:
    """Per generation run, its findings from the latest completed result per tool, and the analyses they came from."""
    result_ids = ToolResult.latest_ids_per_job(run_ids)
    results = ToolResult.objects.filter(id__in=result_ids, status=ToolResult.Status.COMPLETED)
    analyses_by_run: dict[str, set[str]] = defaultdict(set)
    for analysis_id, run_id in results.values_list("run_id", "run__generation_job_id"):
        analyses_by_run[str(run_id)].add(str(analysis_id))
    fields = dict.fromkeys(("result__run__generation_job_id", "tool_severity", "description", *RECORD_FIELDS))
    rows = Finding.objects.filter(result__in=results).order_by("file_path", "line_number", "id").values(*fields)
    by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_run[str(row["result__run__generation_job_id"])].append(row)
    return by_run, {run: sorted(ids) for run, ids in analyses_by_run.items()}


def _static_record(row: dict[str, Any], run_id: str) -> dict[str, Any]:
    """A dedup record (keyed by the app, so its results from several analyses cluster together) plus sheet fields."""
    extra = row["tool_specific_data"] if isinstance(row["tool_specific_data"], dict) else {}
    installed = extra.get("installed_version") or extra.get("version") or ""
    return finding_record(row, run_id) | {"tool_severity": row["tool_severity"], "installed_version": str(installed)}


def _evaluation_key(run: GenerationRun, view: RunView) -> dict[str, Any]:
    """The automated functional verdict of a run: its latest evaluation's status and pass rate."""
    evaluation = view.latest_evaluation()
    status = evaluation.status if evaluation is not None else ""
    result = (evaluation.result if evaluation is not None else None) or {}
    measured = status == choices.EvaluationStatus.MEASURED and bool(result.get("measured", True))
    rate = (result.get("metrics") or {}).get("agent_pass_rate") if measured else None
    deployed = run.outcome == choices.Outcome.TESTED
    if measured and not deployed:
        rate = 0.0  # built or started nothing: every functional check fails
    return {
        "eval_status": status,
        "eval_pass_rate": "" if rate is None else rate,
        "deployed": int(deployed) if measured else "",
    }


def build_review_sample(
    runs: QuerySet[GenerationRun] | list[GenerationRun],
    *,
    size: int | None = 30,
    per_stratum: int | None = None,
    stratify: tuple[str, ...] = ("model",),
    seed: int = 0,
    blind: bool = True,
    max_static_per_app: int = 25,
    max_dependency_per_app: int = 10,
    max_runtime_per_app: int = 10,
) -> tuple[bytes, dict[str, Any]]:
    """The review ZIP for a sample of *runs*, and its manifest.

    Issues in the app's code, dependency advisories and runtime issues go on
    separate sheets with their own per-app caps; advisories about packages
    the model added itself are drawn before starter-pinned and transitive
    ones. Issues in test code are counted in the manifest, not sampled.
    """
    from backend.generation.runs.view import run_views

    runs = list(runs)
    selected, selection = select_sample(runs, size=size, per_stratum=per_stratum, stratify=stratify, seed=seed)
    rng = random.Random(seed)  # noqa: S311 - reproducible sampling, not crypto
    # Shuffled sample ids: their order must not reveal the stratum.
    order = list(range(len(selected)))
    rng.shuffle(order)
    sample_ids = {selected[i].id: f"S{n + 1:03d}" for n, i in enumerate(order)}
    if not blind:
        sample_ids = {r.id: str(r.id) for r in selected}

    findings_by_run, analyses_by_run = _run_findings([r.id for r in selected])
    ai_slugs = AnalyzerTool.ai_slugs()
    dynamic = dynamic_tool_slugs()
    stack_memo: dict = {}
    population = [r for r in runs if is_eligible(r)]
    ownerships = _scope_ownerships(population, stack_memo)
    by_id = GenerationRun.objects.select_related("model", "condition__model").in_bulk([r.pk for r in selected])
    ordered = sorted((by_id[r.pk] for r in selected if r.pk in by_id), key=lambda r: sample_ids[r.id])
    test_issues = 0
    ai_rows: list[dict[str, Any]] = []
    static_rows: list[dict[str, Any]] = []
    dependency_rows: list[dict[str, Any]] = []
    runtime_rows: list[dict[str, Any]] = []
    req_rows: list[dict[str, Any]] = []
    app_rows: list[dict[str, Any]] = []
    key_rows: list[dict[str, Any]] = []

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for run, view in zip(ordered, run_views(ordered), strict=True):
            sid = sample_ids[run.id]
            files = view.code_files("app")
            for rel, content in files.items():
                zf.writestr(f"apps/{sid}/code/{rel}", content)
            zf.writestr(f"apps/{sid}/files.csv", files_csv(view))
            zf.writestr(f"apps/{sid}/resolved_lock.json", json.dumps(resolved_lock(run), indent=2, default=str))
            # The manifest names the model and the condition: automated facts, kept with the key.
            zf.writestr(f"key/{sid}/manifest.json", json.dumps(view.manifest() or None, indent=2, default=str))
            reqs = _requirements(view)
            zf.writestr(f"apps/{sid}/REQUIREMENTS.md", _requirements_md(view, reqs))
            req_rows += [{"sample_id": sid, "requirement_id": r, "kind": k, "requirement": t} for r, k, t in reqs]
            # LOC: the model's own non-test source lines (unknown, never 0, when not collected).
            app_rows.append({"sample_id": sid, "files": len(files), "loc": loc_from_view(view)["total_loc"]})

            findings = findings_by_run.get(str(run.id), [])
            for f in findings:
                if f["result__tool_slug"] in ai_slugs:
                    ai_rows.append(
                        {
                            "sample_id": sid,
                            "finding_id": str(f["id"]),
                            "file_path": f["file_path"],
                            "line": f["line_number"] or "",
                            "tool_severity": f["tool_severity"] or UNRATED,
                            "normalized_severity": f["severity"],
                            "category": f["category"],
                            "rule_id": f["rule_id"],
                            "title": f["title"],
                            "description": f["description"][:2000],
                        },
                    )
            # The study's own dedup records (advisory ids and aliases link one
            # advisory across scanners); every result of the app clusters together.
            static = [_static_record(f, str(run.id)) for f in findings if f["result__tool_slug"] not in ai_slugs]
            by_kind: dict[str, list[IssueCluster]] = defaultdict(list)
            for c in cluster_findings(static):
                by_kind[issue_kind(c, dynamic)].append(c)
            code, advisories = by_kind[CODE], by_kind[DEPENDENCY]
            test_issues += len(by_kind[TEST])
            code = _sample_clusters(code, max_static_per_app, rng)
            ownership = ownerships.get(str(run.id)) or dependency_ownership(run, stack_memo)
            origin = _dependency_origins(advisories, ownership)
            advisories = _sample_clusters(
                advisories,
                max_dependency_per_app,
                rng,
                first=[c for c in advisories if origin[id(c)] == "model"],
            )
            runtime = _sample_clusters(_runtime_rows(by_kind[RUNTIME]), max_runtime_per_app, rng)
            for n, c in enumerate(sorted(code, key=lambda c: (c.file_path, c.line or 0)), start=1):
                d = c.to_dict()
                static_rows.append(
                    {
                        "sample_id": sid,
                        "issue_id": f"{sid}-I{n:03d}",
                        "file_path": _member_paths(c),
                        "line": d["line"] or "",
                        "tool_severity": _tool_labels([c]),
                        "normalized_severity": d["severity"],
                        "category": d["category"],
                        "issue_class": d["issue_class"],
                        "tools": ";".join(d["tools"]),
                        "rule_ids": ";".join(d["rule_ids"]),
                        "title": c.members[0]["title"][:300],
                    },
                )
            for n, c in enumerate(sorted(advisories, key=lambda c: (cluster_package(c), c.issue_class)), start=1):
                d = c.to_dict()
                versions = {m["installed_version"] for m in c.members if m.get("installed_version")}
                dependency_rows.append(
                    {
                        "sample_id": sid,
                        "issue_id": f"{sid}-D{n:03d}",
                        "file_path": _member_paths(c),
                        "package": cluster_package(c),
                        "installed_version": ";".join(sorted(versions)),
                        "advisories": ";".join(sorted({m.get("rule_id") or "" for m in c.members} - {""})),
                        "origin": origin[id(c)],
                        "tool_severity": _tool_labels([c]),
                        "normalized_severity": d["severity"],
                        "tools": ";".join(d["tools"]),
                        "title": c.members[0]["title"][:300],
                    },
                )
            for n, group in enumerate(runtime, start=1):
                members = [m for c in group for m in c.members]
                runtime_rows.append(
                    {
                        "sample_id": sid,
                        "issue_id": f"{sid}-R{n:03d}",
                        "issue_class": group[0].issue_class,
                        "url_paths": _url_paths(group),
                        "tool_severity": _tool_labels(group),
                        "normalized_severity": max((c.severity for c in group), key=lambda s: SEVERITY_RANK.get(s, 0)),
                        "tools": ";".join(sorted({m["tool"] for m in members})),
                        "rule_ids": ";".join(sorted({f"{m['tool']}:{m.get('rule_id') or ''}" for m in members})),
                        "title": members[0]["title"][:300],
                    },
                )

            key_rows.append(
                {
                    "sample_id": sid,
                    "run_id": str(run.id),
                    "model": view.model_slug or "",
                    "task": view.task.ref if view.task is not None else "",
                    "condition_label": condition_label(run.condition) if run.condition is not None else "",
                    "kind": run.kind,
                    "repeat_index": run.repeat_index if run.repeat_index is not None else "",
                    "outcome": run.outcome,
                    **_evaluation_key(run, view),
                    "analysis_run_ids": ";".join(analyses_by_run.get(str(run.id), [])),
                },
            )

        manifest = {
            "format_version": SAMPLE_FORMAT_VERSION,
            "created_at": timezone.now().isoformat(),
            "blind": blind,
            "size": len(selected),
            **selection,
            "findings_source": "latest result per (app, tool) (ToolResult.latest_ids_per_job), completed only",
            "counts": {
                "ai_findings": len(ai_rows),
                "static_issues": len(static_rows),
                "dependency_issues": len(dependency_rows),
                "runtime_issues": len(runtime_rows),
                "test_code_issues_not_sampled": test_issues,
                "requirements": len(req_rows),
                "max_static_per_app": max_static_per_app,
                "max_dependency_per_app": max_dependency_per_app,
                "max_runtime_per_app": max_runtime_per_app,
            },
            "issue_split": (
                "as the study tables: static_issues = code issues (code_unique); dependency advisories, "
                "runtime issues (dynamic scanners only, one row per issue class) and test-code issues apart"
            ),
            "dependency_sampling": "advisories about model-added packages first, then a seeded random draw",
        }
        zf.writestr(
            "README.md",
            _README.format(n=len(selected), method=selection["method"], seed=seed, population=selection["population"]),
        )
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr("key.csv", _csv(KEY_COLUMNS, key_rows))
        zf.writestr("labels/ai_findings.csv", _csv(AI_FINDING_COLUMNS, ai_rows))
        zf.writestr("labels/static_issues.csv", _csv(STATIC_ISSUE_COLUMNS, static_rows))
        zf.writestr("labels/dependency_issues.csv", _csv(DEPENDENCY_ISSUE_COLUMNS, dependency_rows))
        zf.writestr("labels/runtime_issues.csv", _csv(RUNTIME_ISSUE_COLUMNS, runtime_rows))
        zf.writestr("labels/requirements.csv", _csv(REQUIREMENT_COLUMNS, req_rows))
        zf.writestr("labels/apps.csv", _csv(APP_COLUMNS, app_rows))
    return buf.getvalue(), manifest


# ── scoring ──────────────────────────────────────────────────────────────


def _read_sheets(source: Path | bytes) -> dict[str, list[dict[str, str]]]:
    names = (
        "key.csv",
        "labels/ai_findings.csv",
        "labels/static_issues.csv",
        "labels/dependency_issues.csv",
        "labels/runtime_issues.csv",
        "labels/requirements.csv",
        "labels/apps.csv",
    )
    sheets: dict[str, list[dict[str, str]]] = {}
    if isinstance(source, bytes) or (isinstance(source, Path) and source.is_file()):
        data = source if isinstance(source, bytes) else source.read_bytes()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            members = set(zf.namelist())
            # Tolerate a ZIP re-packed with a top-level folder.
            prefix = next((m[: -len("key.csv")] for m in members if m.endswith("key.csv")), "")
            for name in names:
                if prefix + name in members:
                    text = zf.read(prefix + name).decode("utf-8-sig")
                    sheets[name] = list(csv.DictReader(io.StringIO(text)))
    else:
        for name in names:
            path = Path(source) / name
            if path.is_file():
                sheets[name] = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
    return sheets


def _sheet_value(row: dict[str, str], key: str) -> str:
    if key == "normalized_severity":
        # Format-1 sheets named the (normalized) column "severity".
        return row.get("normalized_severity") or row.get("severity") or ""
    return row.get(key) or ""


def _precision(rows: list[dict[str, str]], group_keys: tuple[str, ...]) -> dict[str, Any]:
    labelled = [r for r in rows if (r.get("human_verdict") or "").strip().upper() in ("TP", "FP")]

    def block(items: list[dict[str, str]]) -> dict[str, Any]:
        tp = sum(1 for r in items if r["human_verdict"].strip().upper() == "TP")
        return {
            "labelled": len(items),
            "tp": tp,
            "fp": len(items) - tp,
            "precision": round(tp / len(items), 3) if items else None,
            "ci95": wilson_interval(tp, len(items)),
        }

    out: dict[str, Any] = {
        "rows": len(rows),
        "unsure": sum(1 for r in rows if (r.get("human_verdict") or "").strip().lower() == "unsure"),
        **block(labelled),
    }
    for key in group_keys:
        groups: dict[str, list] = defaultdict(list)
        for r in labelled:
            values = _sheet_value(r, key).split(";") if key == "tools" else [_sheet_value(r, key)]
            for v in values:
                groups[v].append(r)
        out[f"by_{key}"] = {k: block(v) for k, v in sorted(groups.items())}
    return out


_COMPLIANCE = {"yes": 1.0, "partial": 0.5, "no": 0.0}


def score_review_labels(source: Path | bytes) -> dict[str, Any]:
    """Agreement between the filled-in sheets of a review sample and the automated verdicts."""
    sheets = _read_sheets(source)
    key = {r["sample_id"]: r for r in sheets.get("key.csv", [])}

    apps = sheets.get("labels/apps.csv", [])
    functional_pairs = []
    confusion: Counter = Counter()
    for r in apps:
        human = (r.get("human_functional") or "").strip().lower()
        auto_key = key.get(r["sample_id"], {})
        rate = (auto_key.get("eval_pass_rate") or "").strip()
        # Not measured (tester or infra error, never evaluated) has no automated verdict to agree with.
        if human not in ("works", "partial", "broken") or auto_key.get("eval_status") != "measured" or not rate:
            continue
        h, a = human == "works", float(rate) == 1.0
        functional_pairs.append((h, a))
        confusion[f"human_{'works' if h else 'not_works'}__auto_{'passed' if a else 'not_passed'}"] += 1

    compliance: dict[str, list[float]] = defaultdict(list)
    for r in sheets.get("labels/requirements.csv", []):
        v = _COMPLIANCE.get((r.get("human_met") or "").strip().lower())
        if v is not None:
            compliance[r["sample_id"]].append(v)
    human_c, auto_c = [], []
    for sid, values in compliance.items():
        rate = key.get(sid, {}).get("eval_pass_rate")
        if rate not in (None, ""):
            human_c.append(sum(values) / len(values))
            auto_c.append(float(rate))

    quality = [int(r["human_quality"]) for r in apps if (r.get("human_quality") or "").strip().isdigit()]
    return {
        "ai_findings": _precision(sheets.get("labels/ai_findings.csv", []), ("normalized_severity", "category")),
        "static_issues": _precision(
            sheets.get("labels/static_issues.csv", []),
            ("normalized_severity", "category", "tools", "issue_class"),
        ),
        "dependency_issues": _precision(
            sheets.get("labels/dependency_issues.csv", []),
            ("origin", "normalized_severity", "tools"),
        ),
        "runtime_issues": _precision(
            sheets.get("labels/runtime_issues.csv", []),
            ("normalized_severity", "tools", "issue_class"),
        ),
        "functional": {
            "labelled": len(functional_pairs),
            "agreement": round(sum(1 for h, a in functional_pairs if h == a) / len(functional_pairs), 3)
            if functional_pairs
            else None,
            "cohens_kappa": cohens_kappa(functional_pairs),
            "confusion": dict(confusion),
        },
        "compliance": {
            "apps_labelled": len(compliance),
            "mean_human_compliance": round(sum(sum(v) / len(v) for v in compliance.values()) / len(compliance), 3)
            if compliance
            else None,
            "spearman_vs_eval_pass_rate": spearman(human_c, auto_c),
            "paired_apps": len(human_c),
        },
        "quality": {
            "labelled": len(quality),
            "mean": round(sum(quality) / len(quality), 2) if quality else None,
        },
    }
