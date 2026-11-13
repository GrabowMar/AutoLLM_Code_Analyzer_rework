"""Memory rule, slot clamping, the /api/execution/ views and the executor management commands.

The views are called through ninja's ``TestClient`` on ``api_views.router`` (authenticated as the given
user), so these tests do not depend on the whole project URLconf.
"""

from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.utils import timezone

from backend.execution import capacity
from backend.execution.models import ExecutorInstance
from backend.execution.models import ExecutorSettings
from backend.execution.tests.conftest import make_endpoint
from backend.execution.tests.conftest import make_model
from backend.execution.tests.conftest import make_run
from backend.execution.tests.conftest import running_study
from backend.generation.models import GenerationRun
from backend.generation.models.choices import RunStatus
from backend.users.tests.factories import UserFactory

S = RunStatus
API = ""


class _RouterClient:
    """``client.get(path, params)`` / ``client.patch(path, body, content_type=...)`` on the execution router."""

    def __init__(self, user):
        from ninja.testing import TestClient

        from backend.execution.api_views import router

        self.user = user
        self._client = TestClient(router)

    def get(self, path, params=None):
        return self._client.get(path, query_params=params or {}, auth=self.user, user=self.user)

    def patch(self, path, body, content_type="application/json"):
        return self._client.patch(path, data=body, auth=self.user, user=self.user)


def _row(**kw):
    values = {"sandbox_slots": 1, "eval_slots": 1, "llm_job_threads": 16, "post_run_slots": 1, "draining": False}
    values.update(kw)
    return SimpleNamespace(**values)


class TestMemoryRule:
    def test_parse_and_format(self):
        assert capacity.parse_mem("4g") == 4 * 1024**3
        assert capacity.parse_mem("1536m") == 1536 * 1024**2
        assert capacity.parse_mem("2GiB") == 2 * 1024**3
        assert capacity.fmt_mem(capacity.parse_mem("1536m")) == "1.5g"
        with pytest.raises(ValueError, match="Unparseable"):
            capacity.parse_mem("lots")

    def test_rule_and_arithmetic(self, settings):
        settings.APPS_DIND_MEM_LIMIT = "10g"
        fits = capacity.check_memory(2, 1, 0)
        assert fits.ok
        assert "≤" in fits.arithmetic
        post_only = capacity.check_memory(2, 1, 1)
        assert (post_only.ok, post_only.post_only) == (False, True)
        assert not capacity.check_memory(3, 1, 0).ok_without_post
        assert capacity.max_sandbox_slots_allowed(1) == 2

    def test_effective_slots_clamp_bounds_and_memory(self, settings):
        settings.APPS_DIND_MEM_LIMIT = "10g"
        slots = capacity.effective_slots(_row(sandbox_slots=12, llm_job_threads=99))
        assert (slots.sandbox_slots, slots.llm_job_threads) == (2, 32)
        assert len(slots.warnings) >= 2
        assert slots.defer_post_runs  # 2 sandboxes + 1 eval fit only without the analysis workspace

    def test_small_apps_daemon_takes_turns_instead_of_zero_slots(self, settings):
        settings.APPS_DIND_MEM_LIMIT = "4g"
        slots = capacity.effective_slots(_row(sandbox_slots=2))
        assert (slots.sandbox_slots, slots.exclusive_eval) == (1, True)

    def test_code_version_changes_with_files(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1\n")
        before = capacity.compute_code_version(tmp_path)
        (tmp_path / "a.py").write_text("x = 22\n")
        assert capacity.compute_code_version(tmp_path) != before


@pytest.mark.django_db
class TestApi:
    @pytest.fixture
    def staff(self):
        user = UserFactory(is_staff=True)
        return _RouterClient(user), user

    @pytest.fixture
    def member(self):
        user = UserFactory()
        return _RouterClient(user), user

    def test_settings_are_staff_only(self, member):
        client, _ = member
        assert client.get(f"{API}/settings/").status_code == 403
        assert client.patch(f"{API}/settings/", "{}", content_type="application/json").status_code == 403

    def test_patch_refuses_what_does_not_fit(self, staff, settings):
        settings.APPS_DIND_MEM_LIMIT = "11g"
        client, user = staff
        res = client.patch(f"{API}/settings/", json.dumps({"sandbox_slots": 4}), content_type="application/json")
        assert res.status_code == 400
        assert "apps-dind" in res.json()["detail"]
        assert "sandbox" in res.json()["arithmetic"]
        res = client.patch(
            f"{API}/settings/",
            json.dumps({"sandbox_slots": 2, "draining": True}),
            content_type="application/json",
        )
        assert res.status_code == 200
        row = ExecutorSettings.get()
        assert (row.sandbox_slots, row.draining, row.updated_by_id) == (2, True, user.id)

    def test_capacity(self, member, system_ep):
        client, user = member
        endpoint = make_endpoint(user, served=[("qwen", "")])
        ExecutorInstance.objects.create(
            id="exec:1",
            hostname="h",
            pid=1,
            code_version="old",
            is_leader=True,
            last_seen_at=timezone.now(),
            config={"circuit_open": [endpoint.pk], "blocked_by_endpoint": {str(endpoint.pk): "residency_wait"}},
        )
        study = running_study(user)
        make_run(user, make_model(endpoint, "qwen"), study=study, sequence=0)
        make_run(
            user,
            make_model(),
            study=study,
            status=S.RUNNING,
            phase="running",
            lease_owner="exec:1",
        )
        foreign = make_endpoint(UserFactory())  # private, someone else's: invisible
        data = client.get(f"{API}/capacity/").json()
        assert data["executor"]["online"] is True
        assert data["executor"]["code_stale"] is True
        assert data["lanes"]["llm"] == {"used": 1, "total": 16}
        slugs = {e["slug"]: e for e in data["endpoints"]}
        assert foreign.slug not in slugs
        assert slugs[endpoint.slug]["circuit"] == "open"
        assert slugs[endpoint.slug]["status"] == "degraded"
        assert slugs[endpoint.slug]["blocked_reason"] == "residency_wait"
        assert slugs["openrouter"]["jobs"]["used"] == 1
        assert [(e["id"], e["running"], e["pending"]) for e in data["studies"]] == [(str(study.id), 1, 1)]

    def test_offline_executor(self, member, system_ep):
        client, _ = member
        ExecutorInstance.objects.create(
            id="exec:old",
            hostname="h",
            pid=1,
            code_version="x",
            is_leader=True,
            last_seen_at=timezone.now() - timedelta(minutes=10),
        )
        assert client.get(f"{API}/capacity/").json()["executor"] == {
            "online": False,
            "leader": None,
            "code_stale": False,
            "draining": False,
            "draining_remaining": {"runs": 0, "post_runs": 0},
        }

    def test_queue_lists_own_runs_with_reasons(self, member, system_ep):
        client, user = member
        offline = make_endpoint(user, online=False, kind="openai_compatible", api_flavor="openai")
        stuck = make_run(user, make_model(offline, "m"))
        ready = make_run(user, make_model())
        backoff = make_run(user, make_model(), run_after=timezone.now() + timedelta(minutes=5))
        other = UserFactory()
        make_run(other, make_model(), queued_at=timezone.now() - timedelta(hours=1))
        data = client.get(f"{API}/queue/").json()
        reasons = {i["run_id"]: i["blocked_reason"] for i in data["items"]}
        assert reasons == {str(ready.id): None, str(stuck.id): "endpoint_offline", str(backoff.id): "retry_backoff"}
        assert data["items"][0]["run_id"] == str(ready.id)
        assert data["items"][0]["kind"] == "slot_single_shot"
        assert data["others_ahead"] == {"openrouter": 1, offline.slug: 0}
        assert GenerationRun.objects.filter(status=S.RUNNING).count() == 0
        assert client.get(f"{API}/queue/", {"study_id": "nope"}).status_code == 400

    def test_queue_names_a_paused_condition(self, member, system_ep):
        """E2E-27: a stop rule's paused condition is skipped by the lane SQL; the queue says so."""
        from backend.generation.testing.factories import StudyConditionFactory

        client, user = member
        study = running_study(user)
        model = make_model()
        paused = StudyConditionFactory(plan__study=study, model=model, paused=True, pause_reason="rule:cost_overrun")
        active = StudyConditionFactory(plan=paused.plan, model=model)
        held = make_run(user, model, study=study, condition=paused, sequence=1)
        ready = make_run(user, model, study=study, condition=active, sequence=2)
        reasons = {i["run_id"]: i["blocked_reason"] for i in client.get(f"{API}/queue/").json()["items"]}
        assert reasons == {str(ready.id): None, str(held.id): "condition_paused"}

    def test_every_scheduler_reason_is_a_queue_reason(self):
        """E2E-27: a reason the queue schema did not list (study_model_cap) made GET /queue/ answer 500."""
        from typing import get_args

        from backend.execution import scheduler
        from backend.execution.api_schemas import BlockedReason

        names = [
            "SANDBOX_FULL",
            "LLM_THREADS_FULL",
            "ENDPOINT_OFFLINE",
            "ENDPOINT_FULL",
            "MODEL_NOT_SERVED",
            "RESIDENCY_WAIT",
            "WARMING_MODEL",
            "STUDY_CAP",
            "CIRCUIT_OPEN",
            "DRAINING",
            "RETRY_BACKOFF",
            "STUDY_ENDPOINT_CAP",
            "STUDY_MODEL_CAP",
            "STUDY_PROVIDER_CAP",
            "BUDGET",
        ]
        published = {getattr(scheduler, name) for name in names}
        assert published - set(get_args(BlockedReason)) == set()

    def test_post_run_slots_go_up_to_two(self, staff, settings):
        settings.APPS_DIND_MEM_LIMIT = "64g"
        client, _ = staff
        ok = client.patch(f"{API}/settings/", json.dumps({"post_run_slots": 2}), content_type="application/json")
        assert ok.status_code == 200
        assert ok.json()["post_run_slots"] == 2
        too_many = client.patch(f"{API}/settings/", json.dumps({"post_run_slots": 3}), content_type="application/json")
        assert too_many.status_code == 422
        assert capacity.SLOT_BOUNDS["post_run_slots"] == (0, 2)


@pytest.mark.django_db
def test_executor_command_drain_resume_status():
    out = StringIO()
    call_command("executor", "drain", stdout=out)
    assert json.loads(out.getvalue())["draining"] is True
    assert ExecutorSettings.get().draining is True
    out = StringIO()
    call_command("executor", "resume", stdout=out)
    payload = json.loads(out.getvalue())
    assert (payload["draining"], payload["online"], payload["safe_to_restart"]) == (False, False, True)


@pytest.mark.django_db
def test_wait_for_migrations_returns_on_a_migrated_database():
    out = StringIO()
    call_command("wait_for_migrations", "--timeout", "5", stdout=out)
    assert "All migrations applied" in out.getvalue()
