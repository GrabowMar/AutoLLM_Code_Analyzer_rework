"""The 9.7 Patterns API: routing, ownership, payload shapes and the export download."""

from __future__ import annotations

import io
import zipfile

import pytest

from backend.generation.models import PatternRun
from backend.generation.patterns import normalize as norm
from backend.generation.patterns import run as run_patterns
from backend.generation.tests.wp13_patterns.test_pipeline import INSECURE
from backend.generation.tests.wp13_patterns.test_pipeline import make_study
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
API = "/api/generation"


@pytest.fixture
def setup(client, detectors, fake_semgrep_runner):
    synthetic = make_study()
    client.force_login(synthetic.user)
    return client, synthetic, detectors


def test_create_list_get_and_results(setup):
    client, synthetic, detectors = setup
    body = {"study": str(synthetic.study.pk), "detectors": [detectors["py-sql-fstring"].pinned_ref]}
    created = client.post(f"{API}/patterns/runs/", body, content_type="application/json")
    assert created.status_code == 200, created.content
    out = created.json()
    assert (out["label"], out["status"], out["is_live"]) == ("exploratory", "pending", False)
    assert out["post_run"] is not None
    run_id = out["id"]

    pending = client.get(f"{API}/patterns/runs/{run_id}/prevalence/")
    assert pending.status_code == 409

    run_patterns(PatternRun.objects.get(pk=run_id))
    listed = client.get(f"{API}/patterns/runs/", {"study": str(synthetic.study.pk)}).json()
    assert [r["id"] for r in listed] == [run_id]
    detail = client.get(f"{API}/patterns/runs/{run_id}/").json()
    assert detail["status"] == "completed"
    assert "prevalence" not in detail["summary"]

    target = str(synthetic.targets[0].pk)
    rows = client.get(
        f"{API}/patterns/runs/{run_id}/prevalence/",
        {"target": target, "feature": "py-sql-fstring", "scope": "span"},
    ).json()
    assert {r["target"] for r in rows} == {target}
    plain = [r for r in rows if not r.get("conditional_on")]
    assert len(plain) == 2
    assert all(r["audit"] is None and r["audit_status"] == "not_audited" for r in rows)
    assert {"k", "n_measured", "n_itt", "p", "ci_low", "ci_high", "method", "errors", "na", "adaptive_n"} <= set(
        plain[0],
    )

    comps = client.get(f"{API}/patterns/runs/{run_id}/comparisons/", {"feature": "py-sql-fstring"}).json()
    assert {c["method"] for c in comps} == {"paired"}
    pooled = client.get(f"{API}/patterns/runs/{run_id}/pooled/", {"feature": "py-sql-fstring@span"}).json()
    assert pooled["method"] == "mh_stratified"
    assert "few_clusters" in pooled["notes"]

    clusters = client.get(f"{API}/patterns/runs/{run_id}/clusters/", {"target": target}).json()
    assert clusters["clusters"]
    assert {
        "cluster_id",
        "key",
        "size",
        "share",
        "representative",
        "includes_original",
        "labels",
        "members_sample",
    } <= set(
        clusters["clusters"][0],
    )
    one = client.get(
        f"{API}/patterns/runs/{run_id}/clusters/",
        {"target": target, "condition": synthetic.conditions[0].pk},
    ).json()
    assert one["stats"]["low_effective_n"] in (True, False)

    zipped = client.get(f"{API}/patterns/runs/{run_id}/export.zip")
    assert zipped.status_code == 200
    assert zipped["Content-Type"] == "application/zip"
    assert "variants.csv" in zipfile.ZipFile(io.BytesIO(zipped.content)).namelist()

    audit = client.post(
        f"{API}/patterns/runs/{run_id}/audits/",
        {"feature_key": "py-sql-fstring", "n_hits": 2, "n_non_hits": 2},
        content_type="application/json",
    )
    assert audit.status_code == 200, audit.content
    assert audit.json()["status"] == "pending"
    assert len(client.get(f"{API}/patterns/runs/{run_id}/audits/").json()) == 1


def test_bad_requests(setup):
    client, synthetic, _detectors = setup
    bad = client.post(
        f"{API}/patterns/runs/",
        {"study": str(synthetic.study.pk), "detectors": ["nope@1"]},
        content_type="application/json",
    )
    assert bad.status_code == 400
    assert bad.json()["code"] == "invalid_pattern_run"


def test_foreign_studies_and_runs_are_404(setup):
    client, synthetic, _detectors = setup
    other = UserFactory.create()
    client.force_login(other)
    body = {"study": str(synthetic.study.pk), "detectors": "default"}
    assert client.post(f"{API}/patterns/runs/", body, content_type="application/json").status_code == 404
    assert client.get(f"{API}/patterns/runs/", {"study": str(synthetic.study.pk)}).status_code == 404
    assert client.get(f"{API}/patterns/runs/not-a-uuid/").status_code == 404


def test_from_selection_and_cluster_label(setup):
    client, synthetic, detectors = setup
    run = synthetic.runs[(0, 0, 0)]
    for engine in ("regex", "ts_query"):
        response = client.post(
            f"{API}/patterns/detectors/from-selection/",
            {
                "run_id": str(run.pk),
                "path": "app/routes.py",
                "span": {"start_line": 5, "end_line": 5},
                "engine": engine,
                "feature_key": "my-feature",
                "polarity": "insecure",
            },
            content_type="application/json",
        )
        assert response.status_code == 200, response.content
        draft = response.json()["draft"]
        assert (draft["engine"], draft["languages"], draft["feature_key"]) == (engine, ["python"], "my-feature")
    outside = client.post(
        f"{API}/patterns/detectors/from-selection/",
        {
            "run_id": str(run.pk),
            "path": "app/routes.py",
            "span": {"start_line": 500, "end_line": 501},
            "engine": "regex",
            "feature_key": "x",
        },
        content_type="application/json",
    )
    assert outside.status_code == 400

    label = client.post(
        f"{API}/patterns/clusters/label/",
        {
            "slot_target": str(synthetic.targets[0].pk),
            "normalized_sha": norm.normalized_sha(INSECURE, "python").sha,
            "detector": detectors["manual-owner-check"].pinned_ref,
            "value": "absent",
        },
        content_type="application/json",
    )
    assert label.status_code == 200, label.content
    assert label.json()["labeler"] == synthetic.user.get_username()
    wrong_engine = client.post(
        f"{API}/patterns/clusters/label/",
        {
            "slot_target": str(synthetic.targets[0].pk),
            "normalized_sha": "a" * 64,
            "detector": detectors["py-sql-fstring"].pinned_ref,
            "value": "present",
        },
        content_type="application/json",
    )
    assert wrong_engine.status_code == 400
