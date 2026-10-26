"""Manual review sample: stratified seeded selection, blind ZIP, label scoring."""

from __future__ import annotations

import csv
import io
import zipfile

import pytest

from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import FindingFactory
from backend.export.services import review_sample as svc
from backend.generation.models import GenerationJob
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.rankings.tests.measured import PLAN_B
from backend.rankings.tests.measured import make_run
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

_TOOLS = {"bandit": ToolResult.Status.SUCCEEDED, "ai_review": ToolResult.Status.SUCCEEDED}


def _measured(job, **kw):
    """A complete v2 run of *job* with bandit + the AI reviewer."""
    kw.setdefault("ai", {"high": 1})
    return make_run(job, tools=_TOOLS, measured_sloc=2, **kw)


def _result(run, slug):
    return run.results.get(tool_slug=slug)


def _ai_cluster(run, key, *, n=3, title="AI says SQLi", severity="high", counted=True):
    """One AI consensus cluster: *n* replicate findings sharing *key*."""
    for _ in range(n):
        FindingFactory(
            result=_result(run, "ai_review"),
            is_ai=True,
            is_consensus=counted,
            counted=counted,
            issue_key=key,
            title=title,
            severity=severity,
            file_path="app.py",
            line_number=1,
            grounding="exact",
        )


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
    run = _measured(jobs[0])
    # Three replicates of one consensus opinion are one row; a non-consensus opinion is none.
    _ai_cluster(run, "ai-1")
    _ai_cluster(run, "ai-2", n=1, title="Only one replicate", counted=False)
    # Bandit + ruff on one line under one engine issue key are one static issue; an
    # uncounted (style) finding is not a verdict the measurement counts.
    FindingFactory(result=_result(run, "bandit"), rule_id="B105", file_path="app.py", line_number=1, issue_key="s1")
    FindingFactory(
        result=_result(run, "bandit"),
        tool_slug="ruff",
        rule_id="S105",
        file_path="app.py",
        line_number=1,
        issue_key="s1",
    )
    FindingFactory(result=_result(run, "bandit"), rule_id="E501", category="style", counted=False, line_number=2)

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
    assert [(r["title"], r["replicates"], r["grounding"]) for r in ai] == [("AI says SQLi", "3", "exact")]
    static = list(csv.DictReader(io.StringIO(zf.read("labels/static_issues.csv").decode())))
    assert [(r["tools"], r["rule_ids"]) for r in static] == [("bandit;ruff", "bandit:B105;ruff:S105")]
    assert manifest["counts"]["static_issues"] == 1
    assert manifest["counts"]["requirements"] == 9
    assert manifest["format_version"] == 2
    analysed = {k["job_id"]: k for k in key}[str(jobs[0].id)]
    assert analysed["analysis_run_id"] == str(run.id)
    assert analysed["analysis_status"] == "measured"
    assert manifest["analysis_coverage"]["measured"] == 1
    assert manifest["analysis_coverage"]["not_analysed"] == 2


def test_verdicts_come_from_one_plan_and_missing_rates_stay_empty(population):
    _user, jobs = population
    run = _measured(jobs[0])
    _ai_cluster(run, "ai-1")
    other = _measured(jobs[1], plan_hash=PLAN_B)
    _ai_cluster(other, "ai-x", title="Other plan")
    jobs[2].result_data = {**jobs[2].result_data, "evaluation": {"status": "infra_error", "summary": {}}}
    jobs[2].save(update_fields=["result_data"])

    data, manifest = svc.build_review_sample(jobs[:3], size=3, stratify=(), seed=0, plan_hash=run.plan_hash)
    zf = zipfile.ZipFile(io.BytesIO(data))
    key = {k["job_id"]: k for k in csv.DictReader(io.StringIO(zf.read("key.csv").decode()))}
    ai = list(csv.DictReader(io.StringIO(zf.read("labels/ai_findings.csv").decode())))
    assert [r["title"] for r in ai] == ["AI says SQLi"]
    assert key[str(jobs[1].id)]["analysis_status"] == "other_plan"
    assert key[str(jobs[1].id)]["analysis_run_id"] == ""
    # No recorded pass rate is an empty cell, never 0.
    assert key[str(jobs[2].id)]["conformance_pass_rate"] == ""
    assert manifest["plan_hash"] == run.plan_hash


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
    for n, job in enumerate(jobs[:4]):
        _ai_cluster(_measured(job), f"ai-{n}")
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
    assert scores["ai_findings"]["by_severity"]["high"]["tp"] == 2
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
