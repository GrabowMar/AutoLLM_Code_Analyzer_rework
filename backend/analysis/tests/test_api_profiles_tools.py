"""API tests for profiles (versioning, ownership, 422s) and tools / image / self-test / reviewer options."""

from __future__ import annotations

import json

import pytest
from django.test import Client

from backend.analysis.engine import canary
from backend.analysis.models import AnalysisProfile
from backend.analysis.models import AnalysisRun
from backend.analysis.tests.support.api import STATIC_CONFIG
from backend.analysis.tests.support.api import install_fakes
from backend.analysis.tests.support.api import llm_model
from backend.analysis.tests.support.api import make_profile
from backend.analysis.tests.support.api import review_model
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
BASE = "/api/analysis"


@pytest.fixture
def engine(monkeypatch, settings):
    return install_fakes(monkeypatch, settings)


@pytest.fixture
def client(user):
    c = Client()
    c.force_login(user)
    return c


def _send(client, method, path, payload=None):
    return getattr(client, method)(f"{BASE}{path}", data=json.dumps(payload or {}), content_type="application/json")


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------


def test_create_and_validate_profiles(engine, client, user):
    res = _send(client, "post", "/profiles/", {"name": "My Security Set", "config": STATIC_CONFIG})
    assert res.status_code == 201, res.content
    body = res.json()
    assert (body["slug"], body["version"], body["latest_version"]) == ("my-security-set", 1, 1)
    assert body["is_owner"] is True
    assert body["is_system"] is False
    assert body["summary"]["tools"] == ["bandit", "eslint", "gitleaks"]
    assert len(body["content_hash"]) == 64

    duplicate = _send(client, "post", "/profiles/", {"name": "x", "slug": "my-security-set", "config": STATIC_CONFIG})
    assert duplicate.status_code == 409

    invalid = _send(
        client,
        "post",
        "/profiles/",
        {"name": "bad", "config": {"tools": [{"slug": "bandit", "config": {"strict": "yes"}}], "colour": 1}},
    )
    assert invalid.status_code == 422
    errors = invalid.json()["field_errors"]
    assert errors["config.tools.0.config.strict"] == "must be a boolean"
    assert errors["config.colour"] == "unknown field"
    assert ["body", "payload", "config", "tools", "0", "config", "strict"] in [
        d["loc"] for d in invalid.json()["detail"]
    ]

    ok = _send(client, "post", "/profiles/validate/", {"config": STATIC_CONFIG}).json()
    assert ok["ok"] is True
    assert ok["config"]["tools"][0] == {"slug": "bandit", "required": True, "config": {"strict": False}}
    bad = _send(client, "post", "/profiles/validate/", {"config": {"tools": []}}).json()
    assert bad == {"ok": False, "errors": {"tools": "select at least one tool or enable the AI reviewer"},
                   "config": None, "content_hash": None}  # fmt: skip


def test_update_creates_versions_only_for_content_changes(engine, client, user):
    profile = make_profile(user, slug="mine")

    renamed = _send(client, "put", f"/profiles/{profile.id}/", {"name": "Renamed"}).json()
    assert (renamed["id"], renamed["version"], renamed["name"]) == (str(profile.id), 1, "Renamed")
    same = _send(client, "put", f"/profiles/{profile.id}/", {"config": STATIC_CONFIG}).json()
    assert same["version"] == 1

    changed_config = {**STATIC_CONFIG, "tools": [{"slug": "bandit", "config": {"strict": True}}]}
    v2 = _send(client, "put", f"/profiles/{profile.id}/", {"config": changed_config}).json()
    assert (v2["slug"], v2["version"], v2["latest_version"]) == ("mine", 2, 2)
    assert v2["id"] != str(profile.id)
    assert [v["version"] for v in v2["versions"]] == [2, 1]
    assert v2["name"] == "Renamed"

    # Editing an old version builds on the latest one.
    v3 = _send(client, "put", f"/profiles/{profile.id}/", {"config": STATIC_CONFIG}).json()
    assert v3["version"] == 3
    # v1 is untouched (pinned by runs/studies as provenance).
    profile.refresh_from_db()
    assert profile.config["tools"][0]["config"] == {"strict": False}
    assert profile.version == 1

    listed = client.get(f"{BASE}/profiles/").json()
    assert [(p["slug"], p["version"]) for p in listed if not p["is_system"]] == [("mine", 3)]

    bad = _send(client, "put", f"/profiles/{profile.id}/", {"config": {"tools": [{"slug": "nope"}]}})
    assert bad.status_code == 422
    assert "config.tools.0.slug" in bad.json()["field_errors"]


def test_profile_visibility_system_profiles_and_archive(engine, client, user):
    system = make_profile(None, slug="research-baseline", system=True)
    others = make_profile(UserFactory(), slug="theirs")
    mine = make_profile(user, slug="mine")

    # (Seeded system profiles may be listed too; only ours and other users' matter here.)
    listed = {p["slug"]: p for p in client.get(f"{BASE}/profiles/").json()}
    assert {"research-baseline", "mine"} <= set(listed)
    assert "theirs" not in listed
    assert listed["research-baseline"]["is_system"] is True
    assert client.get(f"{BASE}/profiles/{others.id}/").status_code == 404
    assert client.get(f"{BASE}/profiles/not-a-uuid/").status_code == 404

    assert _send(client, "put", f"/profiles/{system.id}/", {"name": "hacked"}).status_code == 403
    assert client.delete(f"{BASE}/profiles/{system.id}/").status_code == 403

    archived = client.delete(f"{BASE}/profiles/{mine.id}/")
    assert archived.json() == {"archived": True, "slug": "mine", "versions": 1}
    assert "mine" not in {p["slug"] for p in client.get(f"{BASE}/profiles/").json()}
    assert "mine" in {p["slug"] for p in client.get(f"{BASE}/profiles/?include_archived=true").json()}
    assert client.get(f"{BASE}/profiles/{mine.id}/").json()["is_archived"] is True

    staff = Client()
    staff.force_login(UserFactory(is_staff=True))
    assert {p["slug"] for p in staff.get(f"{BASE}/profiles/").json()} >= {"research-baseline", "theirs"}


def test_clone_and_detail_usage(engine, client, user):
    system = make_profile(None, slug="research-baseline", system=True)
    AnalysisRun.objects.create(created_by=user, profile=system, name="x")

    detail = client.get(f"{BASE}/profiles/{system.id}/").json()
    assert detail["usage"] == {"runs": 1, "studies": 0}

    clone = _send(client, "post", f"/profiles/{system.id}/clone/", {})
    assert clone.status_code == 201
    body = clone.json()
    assert body["name"] == f"{system.name} (copy)"
    assert body["version"] == 1
    assert body["is_owner"] is True
    assert body["config"] == system.config
    named = _send(client, "post", f"/profiles/{system.id}/clone/", {"name": "Tuned", "slug": "tuned"}).json()
    assert named["slug"] == "tuned"
    assert AnalysisProfile.objects.get(slug="tuned").created_by == user


def test_profile_defaults_for_the_editor(engine, client):
    body = client.get(f"{BASE}/profiles/defaults/").json()
    assert [t["slug"] for t in body["config"]["tools"]] == ["bandit", "eslint", "gitleaks"]
    assert body["config"]["reviewer"]["model"] == "deepseek/deepseek-v4.1-flash"
    assert [t["slug"] for t in body["tools"]] == ["bandit", "eslint", "gitleaks"]
    assert body["exclusion_rules"]["exclude_invalid"] is True
    assert "measured_origins" in body["scope"]
    assert body["design"]["group_by"] == ["model"]
    assert "bh" in body["design_choices"]["corrections"]


# --------------------------------------------------------------------------
# Tools / image / self-test / reviewer options
# --------------------------------------------------------------------------


def test_tool_catalog_query_count_does_not_grow_with_selftest_history(engine, client, user):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from django.utils import timezone

    from backend.analysis.tests.factories import AnalysisRunFactory
    from backend.analysis.tests.factories import FindingFactory
    from backend.analysis.tests.factories import ToolResultFactory
    from backend.analysis.tests.support.api import FAKE_TAG

    def selftest():
        run = AnalysisRunFactory(
            created_by=user,
            purpose=AnalysisRun.Purpose.CANARY,
            status=AnalysisRun.Status.COMPLETED,
            completed_at=timezone.now(),
            plan={"analyzer_image": {"tag": FAKE_TAG}, "tools": [{"slug": "bandit", "config_hash": "h"}]},
        )
        result = ToolResultFactory(run=run, tool_slug="bandit", status="succeeded")
        for line in range(10):
            FindingFactory(result=result, run=run, rule_id="B602", file_path="vulnerable/app.py", line_number=line)

    def queries() -> int:
        with CaptureQueriesContext(connection) as ctx:
            assert client.get(f"{BASE}/tools/").status_code == 200
        return len(ctx.captured_queries)

    selftest()
    client.get(f"{BASE}/tools/")  # warm caches (image status, session)
    one = queries()
    for _ in range(canary.PER_TOOL_LOOKBACK + 5):
        selftest()

    assert queries() == one


def test_tool_catalog_with_versions_and_canary(engine, client, monkeypatch):
    monkeypatch.setattr(
        canary,
        "latest_per_tool",
        lambda image_tag=None: {"bandit": {"passed": True, "verdict": "passed", "run_id": "r1"}},
    )
    monkeypatch.setattr(canary, "expectations", lambda: {"tools": {"bandit": {"must_find": [{"rule": "B602"}]}}})

    tools = client.get(f"{BASE}/tools/").json()

    assert [t["slug"] for t in tools] == ["bandit", "eslint", "gitleaks"]
    bandit = tools[0]
    assert bandit["version"] == "1.8.6"
    assert bandit["version_source"] == "image_manifest"
    assert bandit["canary"]["passed"] is True
    assert bandit["expectations"] == {"must_find": [{"rule": "B602"}]}
    assert bandit["languages"] == ["python"]
    assert tools[1]["canary"] is None
    assert client.get(f"{BASE}/tools/eslint/").json()["name"] == "ESLint"
    assert client.get(f"{BASE}/tools/nope/").status_code == 404

    engine.built = False
    client.get(f"{BASE}/image/?refresh=true")
    assert client.get(f"{BASE}/tools/bandit/").json()["version_source"] == "catalog"


def test_image_status_and_build(engine, client):
    status = client.get(f"{BASE}/image/?refresh=true").json()
    assert status["built"] is True
    assert status["tag"] == engine.tag
    assert _send(client, "post", "/image/build/").status_code == 200
    assert engine.build_requests == 0

    engine.built = False
    started = _send(client, "post", "/image/build/")
    assert started.status_code == 202
    assert started.json()["building"] is True
    assert engine.build_requests == 1
    assert client.get(f"{BASE}/image/").json()["building"] is True

    engine.daemon_ok = False
    assert _send(client, "post", "/image/build/").status_code == 503


def test_selftest_endpoints(engine, client, tmp_path, monkeypatch):
    root = tmp_path / "canary"
    (root / "vulnerable").mkdir(parents=True)
    (root / "vulnerable" / "app.py").write_text("eval(input())\n")
    (root / "clean").mkdir()
    (root / "clean" / "app.py").write_text("print('ok')\n")
    (root / "expectations.yaml").write_text("version: 1\ntools:\n  bandit:\n    must_find: [B307]\n")
    monkeypatch.setattr(canary, "CANARY_DIR", root)
    monkeypatch.setattr(canary, "EXPECTATIONS_FILE", root / "expectations.yaml")
    canary._load_expectations.cache_clear()

    started = _send(client, "post", "/selftest/", {"tool_slugs": ["bandit"]})
    assert started.status_code == 201, started.content
    assert started.json()["purpose"] == "canary"
    assert started.json()["tool_slugs"] == ["bandit"]
    rejected = _send(client, "post", "/selftest/", {"tool_slugs": ["ai_review"]})
    assert rejected.status_code == 422
    assert "tool_slugs" in rejected.json()["field_errors"]

    latest = client.get(f"{BASE}/selftest/latest/").json()
    assert latest["image_tag"] == engine.tag
    assert latest["latest"] is None  # the queued self-test has not finished
    assert latest["per_tool"] == {}
    canary._load_expectations.cache_clear()


def test_reviewer_options(engine, client):
    body = client.get(f"{BASE}/reviewer/options/").json()
    assert body["key_present"] is True
    assert {e["name"] for e in body["engines"]} == {"single_shot", "agent"}
    assert body["default_model"] == "deepseek/deepseek-v4.1-flash"
    engine.key = None
    assert client.get(f"{BASE}/reviewer/options/").json()["key_present"] is False


def test_unarchive_and_mine_filter(engine, client, user):
    make_profile(None, slug="sys-profile", system=True)
    make_profile(UserFactory(), slug="theirs")
    mine = make_profile(user, slug="mine")
    assert {p["slug"] for p in client.get(f"{BASE}/profiles/?mine=true").json()} == {"mine"}

    client.delete(f"{BASE}/profiles/{mine.id}/")
    assert client.get(f"{BASE}/profiles/?mine=true").json() == []
    restored = _send(client, "post", f"/profiles/{mine.id}/unarchive/")
    assert restored.status_code == 200, restored.content
    assert (restored.json()["slug"], restored.json()["is_archived"]) == ("mine", False)
    assert all(not v["is_archived"] for v in restored.json()["versions"])
    assert {p["slug"] for p in client.get(f"{BASE}/profiles/?mine=true").json()} == {"mine"}

    system = AnalysisProfile.objects.get(slug="sys-profile")
    assert _send(client, "post", f"/profiles/{system.id}/unarchive/").status_code == 403
    theirs = AnalysisProfile.objects.get(slug="theirs")
    assert _send(client, "post", f"/profiles/{theirs.id}/unarchive/").status_code == 404


def test_reviewer_model_capabilities_and_search(engine, client):
    review_model()
    llm_model("acme/coder-1", provider="acme")

    body = client.get(f"{BASE}/reviewer/model-capabilities/deepseek/deepseek-v4.1-flash").json()
    assert body["in_catalog"] is True
    assert body["model_id"] == "deepseek/deepseek-v4.1-flash"
    assert body["structured_outputs"] is True
    assert body["context_window"] == 128_000
    assert {"supported_efforts", "effort_choices", "default_effort"} <= set(body["reasoning"])
    assert "tools" in body

    unknown = client.get(f"{BASE}/reviewer/model-capabilities/nobody/nothing-1")
    assert unknown.status_code == 200
    assert (unknown.json()["in_catalog"], unknown.json()["structured_outputs"]) == (False, None)

    found = client.get(f"{BASE}/reviewer/models/?q=deepseek").json()
    assert found["total"] == 1
    assert found["items"][0]["model_id"] == "deepseek/deepseek-v4.1-flash"
    everything = client.get(f"{BASE}/reviewer/models/?limit=1").json()
    assert (everything["total"] >= 2, len(everything["items"])) == (True, 1)


def test_reviewer_model_capabilities_of_a_local_model(engine, client, user):
    from backend.inference.tests.conftest import make_endpoint
    from backend.inference.tests.conftest import make_local_model

    model = make_local_model(make_endpoint(user))

    body = client.get(f"{BASE}/reviewer/model-capabilities/{model.model_id}").json()
    assert body["in_catalog"] is False  # not an OpenRouter catalog model...
    assert body["local"]["served_model_name"] == "qwen2.5-coder:7b"  # ...but a known local one
    assert body["structured_outputs"] is False
    assert body["context_window"] is not None
    assert model.model_id in {i["model_id"] for i in client.get(f"{BASE}/reviewer/models/?q=qwen").json()["items"]}

    # Another user cannot see the endpoint: the model is unknown to them.
    other = Client()
    other.force_login(UserFactory())
    hidden = other.get(f"{BASE}/reviewer/model-capabilities/{model.model_id}").json()
    assert (hidden["local"], hidden["resolvable"]) == (None, False)
    assert other.get(f"{BASE}/reviewer/models/?q=qwen").json()["total"] == 0


def test_endpoints_require_authentication(db):
    anonymous = Client()
    for path in ("/tools/", "/runs/", "/studies/", "/profiles/", "/targets/options/"):
        assert anonymous.get(f"{BASE}{path}").status_code == 401


def test_admin_pages_render_read_only(engine, user, settings):
    from backend.analysis.tests.factories import AnalysisRunFactory
    from backend.analysis.tests.factories import AnalysisStudyFactory
    from backend.analysis.tests.factories import FindingFactory
    from backend.analysis.tests.factories import ReviewSampleFactory

    admin = Client()
    admin.force_login(UserFactory(is_staff=True, is_superuser=True))
    run = AnalysisRunFactory(created_by=user)
    finding = FindingFactory(result__run=run)
    sample = ReviewSampleFactory(result__run=run)
    study = AnalysisStudyFactory(created_by=user)
    profile = make_profile(user)

    base = f"/{settings.ADMIN_URL}analysis"
    for path in (
        "/analysisrun/",
        f"/analysisrun/{run.pk}/change/",
        "/toolresult/",
        "/finding/",
        f"/finding/{finding.pk}/change/",
        f"/reviewsample/{sample.pk}/change/",
        "/analysisstudy/",
        f"/analysisstudy/{study.pk}/change/",
        f"/analysisprofile/{profile.pk}/change/",
        "/analyzertool/",
    ):
        assert admin.get(f"{base}{path}").status_code == 200, path
    # Engine output cannot be added by hand.
    assert admin.get(f"{base}/analysisrun/add/").status_code == 403


def test_action_bodies_are_optional_but_validated(engine, client, user):
    profile = make_profile(user)
    assert client.post(f"{BASE}/profiles/{profile.id}/clone/").status_code == 201
    bad_slug = _send(client, "post", f"/profiles/{profile.id}/clone/", {"slug": "no spaces allowed"})
    assert bad_slug.status_code == 422
    assert list(bad_slug.json()["field_errors"]) == ["slug"]
    garbage = client.post(f"{BASE}/profiles/{profile.id}/clone/", data="{", content_type="application/json")
    assert garbage.status_code == 400
    empty_json = client.post(f"{BASE}/profiles/{profile.id}/clone/", data="", content_type="application/json")
    assert empty_json.status_code == 201


def test_unstorable_text_in_a_profile_is_a_422(engine, client, user):
    created = _send(client, "post", "/profiles/", {"name": "nul\x00name", "config": STATIC_CONFIG})
    assert created.status_code == 422, created.content
    assert "NUL character" in created.json()["detail"][0]["msg"]
    profile = make_profile(user, slug="clean-name")
    cloned = _send(client, "post", f"/profiles/{profile.id}/clone/", {"name": "x\x00"})
    assert cloned.status_code == 422, cloned.content
    assert not AnalysisProfile.objects.filter(name__startswith="nul").exists()
