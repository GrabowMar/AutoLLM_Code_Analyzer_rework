"""The retained generation routers: router assembly and the per-model behaviour aggregates."""

from __future__ import annotations

import pytest

from backend.generation import blobs
from backend.generation.api import RETAINED_ROUTERS
from backend.generation.api import router
from backend.generation.models.choices import Outcome
from backend.generation.models.choices import RunStatus
from backend.generation.tests.factories import GenerationRunFactory
from backend.generation.tests.factories import RunAttemptFactory
from backend.generation.tests.factories import RunManifestFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

API = "/api/generation"


def _paths() -> set[str]:
    paths: set[str] = set()
    for _prefix, sub, *_ in router._routers:
        paths.update(sub.path_operations)
    return paths


def test_retained_routers_are_attached_after_the_areas():
    assert [name for name, _ in RETAINED_ROUTERS] == ["telemetry", "trajectory", "variability", "job_insights"]
    attached = [sub for _prefix, sub, *_ in router._routers]
    assert attached[-len(RETAINED_ROUTERS) :] == [sub for _, sub in RETAINED_ROUTERS]


def test_no_v2_routes_survive():
    paths = _paths()
    assert not [p for p in paths if p.startswith(("/jobs/", "/experiments/", "/templates/", "/profiles/"))]
    assert {
        "/models/{model_id}/call-telemetry/",
        "/calls.csv",
        "/models/{model_id}/behaviour/",
        "/studies/{study_id}/variability/",
        "/runs/{run_id}/replicates/",
        "/models/{model_id}/variability/",
        "/runs/{run_id}/code-profile/",
        "/runs/{run_id}/timeline/",
        "/runs/{run_id}/quality-metrics/",
    } <= paths


def _trajectory(steps: int, **counters) -> str:
    return blobs.put_json(
        {"steps": [{"i": i} for i in range(steps)], "parser": "opencode", "parser_version": 1, **counters},
    )


@pytest.mark.django_db
class TestModelBehaviour:
    def _run(self, user, model, **kw):
        run = GenerationRunFactory(tested=True, created_by=user, model=model, **kw)
        RunManifestFactory(run=run)  # harness opencode 1.18.35
        return run

    def test_aggregates_the_last_attempt_of_usable_runs(self, client):
        user = UserFactory()
        model = LLMModelFactory()
        first = self._run(user, model, metrics={"eval": _eval(0.5)})
        RunAttemptFactory(run=first, trajectory_blob=_trajectory(2, tool_calls=99))  # superseded attempt
        RunAttemptFactory(
            run=first,
            trajectory_blob=_trajectory(4, tool_calls=3, edits=2, max_turns_hit=True),
            time_split={"llm_s": 30.0, "tool_s": 10.0},
        )
        second = self._run(user, model, metrics={"eval": {**_eval(None), "measured": False, "status": "error"}})
        RunAttemptFactory(run=second, trajectory_blob=_trajectory(6, tool_calls=5))
        no_record = self._run(user, model)
        RunAttemptFactory(run=no_record)
        unusable = self._run(user, model, outcome=Outcome.INFRA_ERROR)
        RunAttemptFactory(run=unusable, trajectory_blob=_trajectory(1))
        self._run(UserFactory(), model)  # someone else's run
        GenerationRunFactory(created_by=user, model=model, status=RunStatus.RUNNING)  # not finished

        client.force_login(user)
        body = client.get(f"{API}/models/{model.pk}/behaviour/").json()
        (group,) = body["harnesses"]
        assert (group["harness"], group["harness_version"]) == ("opencode", "1.18.35")
        assert group["n_runs"] == 4
        assert group["n_unusable_excluded"] == 1
        assert group["n_without_trajectory"] == 1
        assert group["n"] == 2
        assert group["metrics"]["tool_calls"] == {"n": 2, "mean": 4.0, "median": 4.0, "min": 3.0, "max": 5.0}
        assert group["metrics"]["steps"]["max"] == 6.0
        assert group["metrics"]["edits"]["n"] == 1  # a counter the trajectory does not carry is unknown
        assert group["time_split"]["llm_s"]["n"] == 1
        assert group["rates"]["max_turns_hit"] == {"n": 1, "k": 1, "share": 1.0}
        # Only the measured evaluation counts; the error is left out, never a 0.
        assert group["pass_rate"]["n"] == 1
        assert group["pass_rate"]["mean"] == 0.5
        assert group["parsers"] == ["opencode@1"]

    def test_invisible_model_is_404(self, client):
        client.force_login(UserFactory())
        assert client.get(f"{API}/models/999999/behaviour/").status_code == 404


def _eval(pass_rate):
    return {
        "eval_suite": "eval-v2@1",
        "eval_level": "functional",
        "status": "measured",
        "measured": True,
        "agent_pass_rate": pass_rate,
    }
