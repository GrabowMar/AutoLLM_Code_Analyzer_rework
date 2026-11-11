"""Selection snapshots: create, exact replay from stored inputs, confirmation and supersession."""

from __future__ import annotations

import pytest

from backend.rankings.models import RankingSnapshot
from backend.rankings.models import SourceSnapshot
from backend.rankings.services.snapshot import confirm_selection
from backend.rankings.services.snapshot import create_selection_snapshot
from backend.rankings.services.snapshot import latest_selection
from backend.rankings.services.snapshot import replay_selection
from backend.rankings.services.study_selection import inputs_sha256
from backend.rankings.services.study_selection import run_selection
from backend.rankings.tests.test_selection import _inputs
from backend.rankings.tests.test_selection import _params
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _snapshot(user=None, *, sensitivity=False):
    inputs, params = _inputs(), _params(draws={"S1": 20, "S2": 10, "S3": 5})
    payload = run_selection(inputs, params, sensitivity=sensitivity)
    return create_selection_snapshot(payload, params=params, inputs=inputs, user=user), payload


def test_create_stores_params_inputs_and_version():
    user = UserFactory()
    snap, payload = _snapshot(user)
    snap.refresh_from_db()
    assert snap.kind == RankingSnapshot.Kind.SELECTION
    assert snap.status == RankingSnapshot.Status.DRAFT
    assert snap.version == "mss-2.0"
    assert snap.created_by == user
    assert snap.inputs_sha256 == inputs_sha256(_inputs(), _params(draws={"S1": 20, "S2": 10, "S3": 5}))
    assert snap.payload["inputs"]["frame"]["candidates"]
    assert [p["canonical_id"] for p in snap.payload["selection"]["picks"]] == [
        p["canonical_id"] for p in payload["selection"]["picks"]
    ]


@pytest.mark.parametrize("sensitivity", [False, True])
def test_replay_reproduces_the_stored_selection_exactly(sensitivity):
    snap, _ = _snapshot(sensitivity=sensitivity)
    snap.refresh_from_db()
    result = replay_selection(snap)
    assert result == {"identical": True, "differences": [], "inputs_sha256_matches": True}


def test_replay_detects_a_tampered_payload():
    snap, _ = _snapshot()
    snap.payload["selection"]["picks"][0]["mss"] = 0.0
    snap.save()
    result = replay_selection(snap)
    assert result["identical"] is False
    assert "selection" in result["differences"]


def test_inputs_hash_is_stable_and_sensitive():
    a = inputs_sha256(_inputs(), _params())
    assert a == inputs_sha256(_inputs(), _params())
    assert a != inputs_sha256(_inputs(openrouter=100.0), _params())
    assert a != inputs_sha256(_inputs(), _params(open_min=3))


def test_confirm_records_the_researcher_and_supersedes_the_previous_confirmation():
    user = UserFactory(is_staff=True)
    first, _ = _snapshot()
    nominal = [p["canonical_id"] for p in first.payload["selection"]["picks"]]
    confirm_selection(first, user, final_ids=nominal, notes="as selected")
    first.refresh_from_db()
    assert first.status == RankingSnapshot.Status.CONFIRMED
    assert first.confirmed_by == user
    assert first.confirmed_at is not None
    assert first.confirmation["final_ids"] == nominal

    second, _ = _snapshot()
    replaced = nominal[-1]
    alternative = next(
        c["canonical_id"]
        for c in second.payload["candidates"]
        if c.get("eligible") and c["canonical_id"] not in nominal
    )
    final = [*nominal[:-1], alternative]
    with pytest.raises(ValueError, match="override"):
        confirm_selection(second, user, final_ids=final)
    with pytest.raises(ValueError, match="reason"):
        confirm_selection(
            second,
            user,
            final_ids=final,
            overrides=[{"replace": replaced, "with": alternative, "reason": " "}],
        )
    confirm_selection(
        second,
        user,
        final_ids=final,
        overrides=[{"replace": replaced, "with": alternative, "reason": "borderline; researcher choice"}],
    )
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == RankingSnapshot.Status.SUPERSEDED
    assert second.status == RankingSnapshot.Status.CONFIRMED
    assert second.confirmation["overrides"][0]["reason"] == "borderline; researcher choice"
    assert latest_selection() == second
    with pytest.raises(ValueError, match="superseded"):
        confirm_selection(first, user, final_ids=nominal)


def test_confirm_rejects_unknown_and_duplicate_ids():
    snap, _ = _snapshot()
    user = UserFactory(is_staff=True)
    with pytest.raises(ValueError, match="unknown"):
        confirm_selection(snap, user, final_ids=["nope/x"])
    nominal = [p["canonical_id"] for p in snap.payload["selection"]["picks"]]
    with pytest.raises(ValueError, match="duplicates"):
        confirm_selection(snap, user, final_ids=[nominal[0], nominal[0]])


def test_latest_selection_prefers_the_confirmed_one():
    assert latest_selection() is None
    draft, _ = _snapshot()
    assert latest_selection() == draft


def test_source_snapshots_are_append_only():
    from django.utils import timezone

    snap = SourceSnapshot.objects.create(
        source=SourceSnapshot.Source.RANKINGS_DAILY,
        fetched_at=timezone.now(),
        payload={"data": []},
        sha256="0" * 64,
    )
    snap.citation = "changed"
    with pytest.raises(ValueError, match="append-only"):
        snap.save()


def test_code_version_pins_the_selection_code_even_without_git(monkeypatch):
    from backend.rankings.services import snapshot as snapshot_module

    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.setattr(snapshot_module, "_git_head", lambda: None)
    version = snapshot_module.code_version()
    assert version == f"src:{snapshot_module.source_digest()[:16]}"
    monkeypatch.setattr(snapshot_module, "_git_head", lambda: "abc123def456+dirty")
    assert snapshot_module.code_version() == f"git:abc123def456+dirty {version}"
    monkeypatch.setenv("GIT_SHA", "0123456789abcdef0123")
    assert snapshot_module.code_version().startswith("git:0123456789ab src:")
    assert len(snapshot_module.code_version()) <= 64
    snap, _ = _snapshot()
    assert snap.code_version != "unknown"
