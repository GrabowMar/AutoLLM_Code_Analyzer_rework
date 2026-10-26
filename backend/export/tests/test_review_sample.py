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
