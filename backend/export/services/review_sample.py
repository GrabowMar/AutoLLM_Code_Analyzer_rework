"""Export a sample of generated apps for manual review, and score the returned labels.

The automated verdicts (AI-reviewer findings, static findings, the
conformance outcome) are only as good as their agreement with a human. This
module draws a seeded, stratified sample of apps, writes their code and
specs plus blank label sheets into a ZIP, and later scores the filled
sheets against the automated verdicts:

* AI-reviewer and (deduplicated) static findings: precision with a 95%
  Wilson interval, per severity, category and tool,
* app-level functional verdict vs the conformance outcome: Cohen's kappa,
* per-app requirement compliance vs the conformance pass rate: Spearman rho.

With ``blind=True`` (default) sample folders are ``S001``… and model,
template and automated outcomes live only in ``key.csv``, which the
reviewer should not open until labelling is done.
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

from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.generation.models import GenerationJob
from backend.reports.services.loc import loc_from_job
from backend.statistics.services.dedup import cluster_findings
from backend.statistics.services.inference import cohens_kappa
from backend.statistics.services.inference import spearman
from backend.statistics.services.inference import wilson_interval

if TYPE_CHECKING:
    from django.db.models import QuerySet

SAMPLE_FORMAT_VERSION = 1
STRATIFY_KEYS = ("model", "template")

AI_FINDING_COLUMNS = [
    "sample_id",
    "finding_id",
    "file_path",
    "line",
    "severity",
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
    "severity",
    "category",
    "issue_class",
    "tools",
    "rule_ids",
    "title",
    "human_verdict",  # TP | FP | unsure
    "notes",
]
REQUIREMENT_COLUMNS = ["sample_id", "requirement_id", "kind", "requirement", "human_met", "notes"]  # yes|partial|no
APP_COLUMNS = ["sample_id", "files", "loc", "human_functional", "human_quality", "notes"]  # works|partial|broken; 1-5
KEY_COLUMNS = [
    "sample_id",
    "job_id",
    "model",
    "template",
    "repeat_index",
    "build_healing",
    "conformance_status",
    "conformance_pass_rate",
    "deployed",
    "analysis_run_id",
]

_README = """# Manual review sample

Sample {n} apps drawn {method} (seed {seed}) from {population} eligible apps.

Do NOT open `key.csv` until every sheet is filled in: it holds the model,
template and the automated verdicts you are being compared against.

Each `apps/<sample_id>/` folder holds the generated code and
`REQUIREMENTS.md`. Fill in the blank columns of the sheets in `labels/`:

| sheet | column | values |
|---|---|---|
| ai_findings.csv | human_verdict | TP (real issue), FP (not an issue), unsure |
| static_issues.csv | human_verdict | TP, FP, unsure |
| requirements.csv | human_met | yes, partial, no |
| apps.csv | human_functional | works, partial, broken |
| apps.csv | human_quality | 1 (poor) .. 5 (excellent) |

Leave a cell empty to skip it. Score the filled sheets with
`manage.py score_review_sample <this folder or zip>` or
`POST /api/export/review-sample/score/`.
"""


def _stratum(job: GenerationJob, keys: tuple[str, ...]) -> tuple[str, ...]:
    values = {
        "model": job.model.model_id if job.model_id else "",
        "template": job.app_requirement.slug if job.app_requirement_id else "",
    }
    return tuple(values[k] for k in keys)


def _has_code(job: GenerationJob) -> bool:
    data = job.result_data if isinstance(job.result_data, dict) else {}
    files = data.get("files")
    return bool((isinstance(files, dict) and files) or data.get("backend_code"))


def select_sample(
    jobs: QuerySet[GenerationJob] | list[GenerationJob],
    *,
    size: int | None = None,
    per_stratum: int | None = None,
    stratify: tuple[str, ...] = ("model",),
    seed: int = 0,
) -> tuple[list[GenerationJob], dict[str, Any]]:
    """A seeded stratified sample of completed jobs with code, and how it was drawn.

    ``per_stratum`` takes that many from each stratum; otherwise ``size`` is
    allocated proportionally (largest remainder, at least one per stratum).
    """
    unknown = set(stratify) - set(STRATIFY_KEYS)
    if unknown:
        msg = f"Unknown stratify key(s): {', '.join(sorted(unknown))}"
        raise ValueError(msg)
    eligible = [j for j in jobs if j.status == GenerationJob.Status.COMPLETED and _has_code(j)]
    strata: dict[tuple[str, ...], list[GenerationJob]] = defaultdict(list)
    for job in sorted(eligible, key=lambda j: str(j.id)):
        strata[_stratum(job, stratify)].append(job)

    rng = random.Random(seed)  # noqa: S311 - reproducible sampling, not crypto
    keys = sorted(strata)
    if per_stratum is not None:
        quota = {k: min(per_stratum, len(strata[k])) for k in keys}
    else:
        total = min(size or 0, len(eligible))
        quota = _allocate(total, {k: len(strata[k]) for k in keys})
    selected: list[GenerationJob] = []
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


def _code_files(job: GenerationJob) -> dict[str, str]:
    data = job.result_data if isinstance(job.result_data, dict) else {}
    files = data.get("files")
    if isinstance(files, dict) and files:
        return {k: v for k, v in files.items() if isinstance(v, str)}
    out = {}
    if data.get("backend_code"):
        out["app.py"] = data["backend_code"]
    if data.get("frontend_code"):
        out["frontend/src/App.jsx"] = data["frontend_code"]
    return out


def _requirements(job: GenerationJob) -> list[tuple[str, str, str]]:
    app = job.app_requirement
    if app is None:
        return []
    rows = []
    for prefix, kind, items in (
        ("B", "backend", app.backend_requirements),
        ("F", "frontend", app.frontend_requirements),
        ("A", "admin", app.admin_requirements),
    ):
        for i, text in enumerate(items or [], start=1):
            rows.append((f"{prefix}{i}", kind, text if isinstance(text, str) else json.dumps(text)))
    return rows


def _requirements_md(job: GenerationJob, reqs: list[tuple[str, str, str]]) -> str:
    app = job.app_requirement
    lines = [
        f"# {app.name if app else 'Free-form task'}",
        "",
        (app.description if app else job.task_description) or "",
    ]
    for kind in ("backend", "frontend", "admin"):
        own = [r for r in reqs if r[1] == kind]
        if own:
            lines += ["", f"## {kind.title()} requirements", ""]
            lines += [f"- **{rid}** {text}" for rid, _k, text in own]
    if app and app.api_endpoints:
        lines += ["", "## API endpoints", ""]
        lines += [
            f"- `{e.get('method', '')} {e.get('path', '')}` {e.get('description', '')}" for e in app.api_endpoints
        ]
    return "\n".join(lines) + "\n"


def _csv(columns: list[str], rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def build_review_sample(
    jobs: QuerySet[GenerationJob] | list[GenerationJob],
    *,
    size: int | None = 30,
    per_stratum: int | None = None,
    stratify: tuple[str, ...] = ("model",),
    seed: int = 0,
    blind: bool = True,
    max_static_per_app: int = 25,
) -> tuple[bytes, dict[str, Any]]:
    """The review ZIP for a sample of *jobs*, and its manifest."""
    selected, selection = select_sample(jobs, size=size, per_stratum=per_stratum, stratify=stratify, seed=seed)
    rng = random.Random(seed)  # noqa: S311 - reproducible sampling, not crypto
    # Shuffled sample ids: their order must not reveal the stratum.
    order = list(range(len(selected)))
    rng.shuffle(order)
    sample_ids = {selected[i].id: f"S{n + 1:03d}" for n, i in enumerate(order)}
    if not blind:
        sample_ids = {j.id: str(j.id) for j in selected}

    run_by_job = dict(
        AnalysisRun.objects.filter(id__in=AnalysisRun.latest_ids_per_job([j.id for j in selected])).values_list(
            "generation_job_id",
            "id",
        ),
    )
    ai_slugs = AnalyzerTool.ai_slugs()
    ai_rows: list[dict[str, Any]] = []
    static_rows: list[dict[str, Any]] = []
    req_rows: list[dict[str, Any]] = []
    app_rows: list[dict[str, Any]] = []
    key_rows: list[dict[str, Any]] = []

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for job in sorted(selected, key=lambda j: sample_ids[j.id]):
            sid = sample_ids[job.id]
            files = _code_files(job)
            for rel, content in files.items():
                zf.writestr(f"apps/{sid}/code/{rel}", content)
            reqs = _requirements(job)
            zf.writestr(f"apps/{sid}/REQUIREMENTS.md", _requirements_md(job, reqs))
            req_rows += [{"sample_id": sid, "requirement_id": r, "kind": k, "requirement": t} for r, k, t in reqs]
            app_rows.append({"sample_id": sid, "files": len(files), "loc": loc_from_job(job)["total_loc"]})

            run_id = run_by_job.get(job.id)
            findings = (
                list(
                    Finding.objects.filter(result__run_id=run_id)
                    .select_related("result")
                    .order_by("file_path", "line_number"),
                )
                if run_id
                else []
            )
            for f in findings:
                if f.result.tool_slug in ai_slugs:
                    ai_rows.append(
                        {
                            "sample_id": sid,
                            "finding_id": str(f.id),
                            "file_path": f.file_path,
                            "line": f.line_number or "",
                            "severity": f.severity,
                            "category": f.category,
                            "rule_id": f.rule_id,
                            "title": f.title,
                            "description": f.description[:2000],
                        },
                    )
            static = [
                {
                    "id": f.id,
                    "run_id": str(run_id),
                    "tool": f.result.tool_slug,
                    "rule_id": f.rule_id,
                    "severity": f.severity,
                    "category": f.category,
                    "file_path": f.file_path,
                    "line": f.line_number,
                    "end_line": (f.tool_specific_data or {}).get("end_line"),
                    "cwe": (f.tool_specific_data or {}).get("cwe") or "",
                    "title": f.title,
                }
                for f in findings
                if f.result.tool_slug not in ai_slugs
            ]
            clusters = cluster_findings(static)
            if len(clusters) > max_static_per_app:
                clusters = rng.sample(clusters, max_static_per_app)
            for n, c in enumerate(sorted(clusters, key=lambda c: (c.file_path, c.line or 0)), start=1):
                d = c.to_dict()
                static_rows.append(
                    {
                        "sample_id": sid,
                        "issue_id": f"{sid}-I{n:03d}",
                        "file_path": d["file_path"],
                        "line": d["line"] or "",
                        "severity": d["severity"],
                        "category": d["category"],
                        "issue_class": d["issue_class"],
                        "tools": ";".join(d["tools"]),
                        "rule_ids": ";".join(d["rule_ids"]),
                        "title": c.members[0]["title"][:300],
                    },
                )

            result = job.result_data if isinstance(job.result_data, dict) else {}
            evaluation = result.get("evaluation") or {}
            status = evaluation.get("status") or ""
            key_rows.append(
                {
                    "sample_id": sid,
                    "job_id": str(job.id),
                    "model": job.model.model_id if job.model_id else "",
                    "template": job.app_requirement.slug if job.app_requirement_id else "",
                    "repeat_index": job.repeat_index if job.repeat_index is not None else "",
                    "build_healing": job.build_healing,
                    "conformance_status": status,
                    "conformance_pass_rate": (evaluation.get("summary") or {}).get("pass_rate", ""),
                    "deployed": "" if not status else int(status in ("passed", "failed")),
                    "analysis_run_id": str(run_id) if run_id else "",
                },
            )

        manifest = {
            "format_version": SAMPLE_FORMAT_VERSION,
            "created_at": timezone.now().isoformat(),
            "blind": blind,
            "size": len(selected),
            **selection,
            "counts": {
                "ai_findings": len(ai_rows),
                "static_issues": len(static_rows),
                "requirements": len(req_rows),
                "max_static_per_app": max_static_per_app,
            },
        }
        zf.writestr(
            "README.md",
            _README.format(n=len(selected), method=selection["method"], seed=seed, population=selection["population"]),
        )
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr("key.csv", _csv(KEY_COLUMNS, key_rows))
        zf.writestr("labels/ai_findings.csv", _csv(AI_FINDING_COLUMNS, ai_rows))
        zf.writestr("labels/static_issues.csv", _csv(STATIC_ISSUE_COLUMNS, static_rows))
        zf.writestr("labels/requirements.csv", _csv(REQUIREMENT_COLUMNS, req_rows))
        zf.writestr("labels/apps.csv", _csv(APP_COLUMNS, app_rows))
    return buf.getvalue(), manifest


# ── scoring ──────────────────────────────────────────────────────────────


def _read_sheets(source: Path | bytes) -> dict[str, list[dict[str, str]]]:
    names = (
        "key.csv",
        "labels/ai_findings.csv",
        "labels/static_issues.csv",
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
            values = (r.get(key) or "").split(";") if key == "tools" else [r.get(key) or ""]
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
        auto = (key.get(r["sample_id"], {}).get("conformance_status") or "").strip()
        if human not in ("works", "partial", "broken") or auto in ("", "infra_error", "not_applicable"):
            continue
        h, a = human == "works", auto == "passed"
        functional_pairs.append((h, a))
        confusion[f"human_{'works' if h else 'not_works'}__auto_{'passed' if a else 'not_passed'}"] += 1

    compliance: dict[str, list[float]] = defaultdict(list)
    for r in sheets.get("labels/requirements.csv", []):
        v = _COMPLIANCE.get((r.get("human_met") or "").strip().lower())
        if v is not None:
            compliance[r["sample_id"]].append(v)
    human_c, auto_c = [], []
    for sid, values in compliance.items():
        rate = key.get(sid, {}).get("conformance_pass_rate")
        if rate not in (None, ""):
            human_c.append(sum(values) / len(values))
            auto_c.append(float(rate))

    quality = [int(r["human_quality"]) for r in apps if (r.get("human_quality") or "").strip().isdigit()]
    return {
        "ai_findings": _precision(sheets.get("labels/ai_findings.csv", []), ("severity", "category")),
        "static_issues": _precision(
            sheets.get("labels/static_issues.csv", []),
            ("severity", "category", "tools", "issue_class"),
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
            "spearman_vs_conformance_pass_rate": spearman(human_c, auto_c),
            "paired_apps": len(human_c),
        },
        "quality": {
            "labelled": len(quality),
            "mean": round(sum(quality) / len(quality), 2) if quality else None,
        },
    }
