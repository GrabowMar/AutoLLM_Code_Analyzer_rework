"""Memory rule, slot clamping, the /api/execution/ views and the executor management commands."""

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
from backend.execution.tests.conftest import make_job
from backend.execution.tests.conftest import make_model
from backend.execution.tests.conftest import running_experiment
from backend.generation.models import GenerationJob
from backend.users.tests.factories import UserFactory

S = GenerationJob.Status
API = "/api/execution"


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
    def staff(self, client):
        user = UserFactory(is_staff=True)
        client.force_login(user)
        return client, user

    @pytest.fixture
    def member(self, client):
        user = UserFactory()
        client.force_login(user)
        return client, user

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
        experiment = running_experiment(user)
        make_job(user, make_model(endpoint, "qwen"), experiment=experiment, sequence=0)
        make_job(
            user,
            make_model(),
            experiment=experiment,
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
        assert [(e["running"], e["pending"]) for e in data["experiments"]] == [(1, 1)]

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
            "draining_remaining": {"jobs": 0, "post_runs": 0},
        }

    def test_queue_lists_own_jobs_with_reasons(self, member, system_ep):
        client, user = member
        offline = make_endpoint(user, online=False, kind="openai_compatible", api_flavor="openai")
        stuck = make_job(user, make_model(offline, "m"))
        ready = make_job(user, make_model())
        backoff = make_job(user, make_model(), run_after=timezone.now() + timedelta(minutes=5))
        other = UserFactory()
        make_job(other, make_model(), queued_at=timezone.now() - timedelta(hours=1))
        data = client.get(f"{API}/queue/").json()
        reasons = {i["job_id"]: i["blocked_reason"] for i in data["items"]}
        assert reasons == {str(ready.id): None, str(stuck.id): "endpoint_offline", str(backoff.id): "retry_backoff"}
        assert data["items"][0]["job_id"] == str(ready.id)
        assert data["others_ahead"] == {"openrouter": 1, offline.slug: 0}
        assert GenerationJob.objects.filter(status=S.RUNNING).count() == 0
        assert client.get(f"{API}/queue/", {"experiment_id": "nope"}).status_code == 400


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
