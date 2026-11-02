"""Manual review sample: stratified seeded selection, blind ZIP, label scoring."""

from __future__ import annotations

import csv
import io
import zipfile

import pytest

from backend.analysis.models import AnalysisRun
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import AnalyzerToolFactory
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.export.services import review_sample as svc
from backend.generation.models import GenerationJob
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def population():
    user = UserFactory()
    app = AppRequirementTemplateFactory(
        slug="todo",
        backend_requirements=["Todo model", "CRUD API"],
        frontend_requirements=["List view"],
    )
    models = [LLMModelFactory(model_id=f"vendor/m{i}") for i in range(3)]
    jobs = []
    for i, model in enumerate(models):
        for r in range(4 + i):
            jobs.append(
                GenerationJobFactory(
                    created_by=user,
                    mode="scaffolding",
                    status=GenerationJob.Status.COMPLETED,
                    model=model,
                    app_requirement=app,
                    repeat_index=r,
                    result_data={
                        "files": {"app.py": f"print({r})\n", "frontend/src/App.jsx": "export default 1\n"},
                        "evaluation": {
                            "status": "passed" if r % 2 else "build_failed",
                            "summary": {"pass_rate": r / 10},
                        },
                    },
                ),
            )
    # Not eligible: failed, no code.
    GenerationJobFactory(created_by=user, mode="scaffolding", status=GenerationJob.Status.FAILED, model=models[0])
    return user, jobs


def test_selection_is_stratified_and_reproducible(population):
    _user, jobs = population
    a, info = svc.select_sample(GenerationJob.objects.all(), size=6, stratify=("model",), seed=7)
    b, _ = svc.select_sample(GenerationJob.objects.all(), size=6, stratify=("model",), seed=7)
    c, _ = svc.select_sample(GenerationJob.objects.all(), size=6, stratify=("model",), seed=8)
    assert [j.id for j in a] == [j.id for j in b]
    assert {j.id for j in a} != {j.id for j in c}
    assert info["population"] == len(jobs)
    assert sorted(s["sampled"] for s in info["strata"]) == [2, 2, 2]

    per, info = svc.select_sample(GenerationJob.objects.all(), per_stratum=3, stratify=("model",), seed=1)
    assert len(per) == 9
    assert all(s["sampled"] == 3 for s in info["strata"])


def test_allocation_never_exceeds_a_stratum():
    quota = svc._allocate(10, {("a",): 1, ("b",): 20})
    assert quota == {("a",): 1, ("b",): 9}
    assert svc._allocate(1, {("a",): 5, ("b",): 9}) == {("a",): 0, ("b",): 1}


def test_zip_is_blind_and_complete(population):
    _user, jobs = population
    AnalyzerToolFactory(slug="llm_review", kind="ai", category="ai")
    run = AnalysisRunFactory(
        created_by=jobs[0].created_by,
        generation_job=jobs[0],
        status=AnalysisRun.Status.COMPLETED,
    )
    FindingFactory(result=ToolResultFactory(run=run, tool_slug="llm_review"), title="AI says SQLi", file_path="app.py")
    FindingFactory(
        result=ToolResultFactory(run=run, tool_slug="bandit"),
        rule_id="B105",
        file_path="app.py",
        line_number=1,
    )

    data, manifest = svc.build_review_sample([jobs[0], *jobs[1:3]], size=3, stratify=(), seed=0)
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = set(zf.namelist())
    assert {"README.md", "manifest.json", "key.csv", "labels/ai_findings.csv", "labels/apps.csv"} <= names
    assert any(n.startswith("apps/S001/code/") for n in names)
    assert "apps/S001/REQUIREMENTS.md" in names
    labels = zf.read("labels/apps.csv").decode() + zf.read("labels/requirements.csv").decode()
    assert "vendor/" not in labels
    assert "passed" not in labels
    key = list(csv.DictReader(io.StringIO(zf.read("key.csv").decode())))
    assert {k["model"] for k in key} <= {"vendor/m0"}
    ai = list(csv.DictReader(io.StringIO(zf.read("labels/ai_findings.csv").decode())))
    assert [r["title"] for r in ai] == ["AI says SQLi"]
    assert manifest["counts"]["static_issues"] == 1
    assert manifest["counts"]["requirements"] == 9
    assert manifest["format_version"] == svc.SAMPLE_FORMAT_VERSION
    static = list(csv.DictReader(io.StringIO(zf.read("labels/static_issues.csv").decode())))
    assert static[0]["tool_severity"] == "bandit:unrated"
    assert static[0]["normalized_severity"] == "high"
    assert "severity" not in static[0]
    assert ai[0]["tool_severity"] == "unrated"
    runs_by_job = {k["job_id"]: k["analysis_run_ids"] for k in key}
    assert runs_by_job[str(jobs[0].id)] == str(run.id)
    assert runs_by_job[str(jobs[1].id)] == ""  # not analyzed


def _rewrite(data: bytes, edits: dict[str, callable]) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for name in src.namelist():
            text = src.read(name)
            if name in edits:
                rows = list(csv.DictReader(io.StringIO(text.decode())))
                for row in rows:
                    edits[name](row)
                buf = io.StringIO()
                writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
                text = buf.getvalue().encode()
            dst.writestr(name, text)
    return out.getvalue()


def test_scoring_filled_labels(population):
    _user, jobs = population
    AnalyzerToolFactory(slug="llm_review", kind="ai", category="ai")
    for job in jobs[:4]:
        run = AnalysisRunFactory(created_by=job.created_by, generation_job=job, status=AnalysisRun.Status.COMPLETED)
        FindingFactory(result=ToolResultFactory(run=run, tool_slug="llm_review"), severity="high", file_path="app.py")
    data, _ = svc.build_review_sample(jobs[:4], size=4, stratify=(), seed=0)
    key = {
        r["sample_id"]: r
        for r in csv.DictReader(io.StringIO(zipfile.ZipFile(io.BytesIO(data)).read("key.csv").decode()))
    }

    verdicts = iter(["TP", "TP", "FP", "unsure"])

    def ai(row):
        row["human_verdict"] = next(verdicts)

    def app(row):
        # The human agrees with the conformance tester on every app.
        row["human_functional"] = "works" if key[row["sample_id"]]["conformance_status"] == "passed" else "broken"
        row["human_quality"] = "4"

    def req(row):
        row["human_met"] = "yes" if row["requirement_id"] != "F1" else "no"

    filled = _rewrite(data, {"labels/ai_findings.csv": ai, "labels/apps.csv": app, "labels/requirements.csv": req})
    scores = svc.score_review_labels(filled)

    assert scores["ai_findings"]["labelled"] == 3
    assert scores["ai_findings"]["precision"] == pytest.approx(0.667, abs=1e-3)
    assert scores["ai_findings"]["unsure"] == 1
    # Grouped by the normalized bucket, and named as such (not as the tool's severity).
    assert scores["ai_findings"]["by_normalized_severity"]["high"]["tp"] == 2
    assert "by_severity" not in scores["ai_findings"]
    assert scores["functional"]["cohens_kappa"] == 1.0
    assert scores["functional"]["labelled"] == 4
    assert scores["compliance"]["mean_human_compliance"] == pytest.approx(2 / 3, abs=1e-3)
    assert scores["quality"]["mean"] == 4.0


def test_stat_helpers():
    from backend.statistics.services.inference import cohens_kappa
    from backend.statistics.services.inference import spearman
    from backend.statistics.services.inference import wilson_interval

    assert cohens_kappa([(1, 1), (0, 0), (1, 0), (0, 1)]) == 0.0
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    lo, hi = wilson_interval(8, 10)
    assert lo < 0.8 < hi


def test_api_round_trip(client, population):
    user, _jobs = population
    client.force_login(user)
    res = client.get("/api/export/review-sample.zip?size=3&seed=2&stratify=model")
    assert res.status_code == 200, res.content
    assert res["Content-Type"] == "application/zip"
    upload = io.BytesIO(res.content)
    upload.name = "sample.zip"
    scored = client.post("/api/export/review-sample/score/", {"file": upload})
    assert scored.status_code == 200, scored.content
    assert scored.json()["ai_findings"]["labelled"] == 0


def _sheet(data: bytes, name: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(zipfile.ZipFile(io.BytesIO(data)).read(name).decode())))


def test_findings_come_from_the_latest_result_per_tool(population):
    """A partial re-run must not shrink the sheet to the re-run tools (runner-3)."""
    _user, jobs = population
    job = jobs[0]
    full = AnalysisRunFactory(created_by=job.created_by, generation_job=job, status=AnalysisRun.Status.COMPLETED)
    FindingFactory(
        result=ToolResultFactory(run=full, tool_slug="bandit"),
        rule_id="B105",
        file_path="app.py",
        line_number=1,
        title="old bandit",
    )
    FindingFactory(
        result=ToolResultFactory(run=full, tool_slug="semgrep"),
        rule_id="python.lang.eval",
        file_path="app.py",
        line_number=40,
        title="semgrep eval",
    )
    rerun = AnalysisRunFactory(created_by=job.created_by, generation_job=job, status=AnalysisRun.Status.COMPLETED)
    FindingFactory(
        result=ToolResultFactory(run=rerun, tool_slug="bandit"),
        rule_id="B201",
        file_path="app.py",
        line_number=20,
        title="new bandit",
    )
    # A failed result's findings are not measurements.
    FindingFactory(
        result=ToolResultFactory(run=rerun, tool_slug="ruff", status="failed"),
        rule_id="E1",
        file_path="app.py",
        line_number=60,
        title="failed ruff",
    )

    data, _ = svc.build_review_sample([job], size=1, stratify=(), seed=0, blind=False)
    titles = {r["title"] for r in _sheet(data, "labels/static_issues.csv")}
    assert titles == {"semgrep eval", "new bandit"}
    (key,) = _sheet(data, "key.csv")
    assert key["analysis_run_ids"] == ";".join(sorted([str(full.id), str(rerun.id)]))


def test_dependency_advisories_get_their_own_sheet(population):
    """Advisories leave static_issues, merge across scanners by alias, and say who chose the package."""
    from backend.runtime.models import Stack

    Stack.objects.create(slug="dep-stack", version=1, files={"requirements.txt": "Flask==3.0.0\n"})
    _user, jobs = population
    job = jobs[0]
    job.result_data = {
        **job.result_data,
        "files": {**job.result_data["files"], "requirements.txt": "flask==3.0.0\nleftpad==0.1\n"},
    }
    job.stack_slug = "dep-stack"
    job.resolved_bundle = {"stack": {"slug": "dep-stack", "version": 1}}
    job.save(update_fields=["result_data", "stack_slug", "resolved_bundle"])
    run = AnalysisRunFactory(created_by=job.created_by, generation_job=job, status=AnalysisRun.Status.COMPLETED)

    results: dict = {}

    def advisory(tool, rule_id, package, path, *, aliases=(), label=""):
        if tool not in results:
            results[tool] = ToolResultFactory(run=run, tool_slug=tool)
        FindingFactory(
            result=results[tool],
            category="dependency",
            severity="high",
            tool_severity=label,
            rule_id=rule_id,
            file_path=path,
            title=f"@{package} advisory {rule_id}",
            tool_specific_data={
                "package": package,
                "installed_version": "0.1",
                "vuln_id": rule_id,
                "aliases": list(aliases),
            },
        )

    # One advisory named by two scanners under different ids, linked by an alias.
    advisory("trivy", "CVE-2026-0001", "leftpad", "requirements.txt", label="HIGH")
    advisory("osv-scanner", "GHSA-xxxx", "leftpad", "requirements.txt", aliases=["CVE-2026-0001"], label="7.5")
    advisory("pip-audit", "PYSEC-1", "transitive-lib", "requirements.txt")
    advisory("pip-audit", "PYSEC-2", "flask", "requirements.txt")
    # Manifest hygiene is the app's own issue, not an advisory.
    FindingFactory(
        result=ToolResultFactory(run=run, tool_slug="deptry"),
        category="dependency",
        rule_id="DEP002",
        file_path="requirements.txt",
        title="leftpad unused",
    )

    data, manifest = svc.build_review_sample([job], size=1, stratify=(), seed=0, blind=False)
    static = _sheet(data, "labels/static_issues.csv")
    deps = _sheet(data, "labels/dependency_issues.csv")
    assert [r["tools"] for r in static] == ["deptry"]
    assert len(deps) == 3
    leftpad = next(r for r in deps if r["package"] == "leftpad")
    assert leftpad["tools"] == "osv-scanner;trivy"
    assert leftpad["origin"] == "model"
    assert leftpad["file_path"] == "requirements.txt"
    assert leftpad["installed_version"] == "0.1"
    assert leftpad["tool_severity"] == "osv-scanner:7.5;trivy:HIGH"
    assert leftpad["title"].startswith("'@")  # neutralized for spreadsheets
    origins = {r["package"]: r["origin"] for r in deps}
    assert origins == {"leftpad": "model", "flask": "scaffold", "transitive-lib": "transitive"}
    assert manifest["counts"]["dependency_issues"] == 3


def test_dependency_cap_takes_model_added_advisories_first(population):
    _user, jobs = population
    job = jobs[0]
    job.result_data = {**job.result_data, "files": {**job.result_data["files"], "requirements.txt": "mine==1\n"}}
    job.save(update_fields=["result_data"])
    run = AnalysisRunFactory(created_by=job.created_by, generation_job=job, status=AnalysisRun.Status.COMPLETED)
    result = ToolResultFactory(run=run, tool_slug="pip-audit")
    for i, package in enumerate(["mine", "a", "b", "c", "d"]):
        FindingFactory(
            result=result,
            category="dependency",
            rule_id=f"PYSEC-{i}",
            file_path="requirements.txt",
            tool_specific_data={"package": package},
        )
    data, _ = svc.build_review_sample([job], size=1, stratify=(), seed=3, blind=False, max_dependency_per_app=2)
    deps = _sheet(data, "labels/dependency_issues.csv")
    assert len(deps) == 2
    assert "mine" in {r["package"] for r in deps}


def test_scoring_reads_format_1_sheets():
    """Sheets exported before format 2 named the normalized column ``severity``."""
    rows = [
        {"human_verdict": "TP", "severity": "high", "category": "security"},
        {"human_verdict": "FP", "severity": "low", "category": "security"},
    ]
    scores = svc._precision(rows, ("normalized_severity", "category"))
    assert scores["by_normalized_severity"]["high"]["tp"] == 1
    assert scores["by_normalized_severity"]["low"]["fp"] == 1


def test_runtime_and_test_code_issues_are_split_as_in_the_study(population):
    """static_issues holds the app's code issues only (P2-P4), runtime issues get one row per class."""
    AnalyzerToolFactory(slug="zap", analysis_mode="dynamic")
    _user, jobs = population
    job = jobs[0]
    run = AnalysisRunFactory(created_by=job.created_by, generation_job=job, status=AnalysisRun.Status.COMPLETED)
    zap = ToolResultFactory(run=run, tool_slug="zap")
    for path in ("/assets/index.js", "/", "/api/items"):
        FindingFactory(
            result=zap,
            rule_id="10020",
            file_path=path,
            tool_severity="Medium",
            title="Missing Anti-clickjacking Header",
            tool_specific_data={"issue_key": "missing-header:x-frame-options"},
        )
    FindingFactory(
        result=zap,
        rule_id="10021",
        file_path="/",
        tool_severity="Low",
        title="X-Content-Type-Options Header Missing",
        tool_specific_data={"issue_key": "missing-header:x-content-type-options"},
    )
    bandit = ToolResultFactory(run=run, tool_slug="bandit")
    FindingFactory(result=bandit, rule_id="B105", file_path="app.py", line_number=3, title="real password")
    FindingFactory(result=bandit, rule_id="B105", file_path="tests/test_app.py", line_number=3, title="test password")
    # Manifest hygiene shares category "dependency" but is the app's own issue (P2).
    FindingFactory(
        result=ToolResultFactory(run=run, tool_slug="deptry"),
        category="dependency",
        rule_id="DEP001",
        file_path="app.py",
        title="requests imported but not declared",
    )

    data, manifest = svc.build_review_sample([job], size=1, stratify=(), seed=0, blind=False)
    static = _sheet(data, "labels/static_issues.csv")
    assert {r["title"] for r in static} == {"real password", "requests imported but not declared"}
    assert _sheet(data, "labels/dependency_issues.csv") == []
    runtime = _sheet(data, "labels/runtime_issues.csv")
    assert [r["issue_class"] for r in runtime] == [
        "missing-header:x-content-type-options",
        "missing-header:x-frame-options",
    ]
    xfo = runtime[1]
    assert xfo["url_paths"] == "/;/api/items;/assets/index.js"  # one issue, on three URLs
    assert xfo["tool_severity"] == "zap:Medium"
    assert xfo["tools"] == "zap"
    assert manifest["counts"]["runtime_issues"] == 2
    assert manifest["counts"]["test_code_issues_not_sampled"] == 1
    assert manifest["format_version"] == svc.SAMPLE_FORMAT_VERSION == 3

    verdicts = iter(["TP", "FP"])

    def label(row):
        row["human_verdict"] = next(verdicts)

    scores = svc.score_review_labels(_rewrite(data, {"labels/runtime_issues.csv": label}))
    assert scores["runtime_issues"]["labelled"] == 2
    assert scores["runtime_issues"]["by_issue_class"]["missing-header:x-frame-options"]["fp"] == 1


def test_runtime_cap_draws_issue_classes(population):
    AnalyzerToolFactory(slug="nuclei", analysis_mode="dynamic")
    _user, jobs = population
    job = jobs[0]
    run = AnalysisRunFactory(created_by=job.created_by, generation_job=job, status=AnalysisRun.Status.COMPLETED)
    result = ToolResultFactory(run=run, tool_slug="nuclei")
    for i in range(5):
        FindingFactory(result=result, rule_id=f"t{i}", file_path="/", tool_specific_data={"issue_key": f"class-{i}"})
    data, _ = svc.build_review_sample([job], size=1, stratify=(), seed=0, blind=False, max_runtime_per_app=2)
    assert len(_sheet(data, "labels/runtime_issues.csv")) == 2
