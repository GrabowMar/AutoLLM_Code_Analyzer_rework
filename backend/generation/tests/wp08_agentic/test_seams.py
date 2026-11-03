"""Seams of the agentic stage: gateway credentials, orphan reaping, static checks, live events."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.generation.agentic import events
from backend.generation.agentic import runner
from backend.generation.agentic import sandbox
from backend.generation.agentic import static_checks
from backend.generation.agentic.collector import classify_tree
from backend.generation.agentic.workspace import SeedFile
from backend.generation.contracts.harness import Step
from backend.inference.access import LLMAccessError


@pytest.mark.parametrize(("retryable", "error"), [(True, runner.InfraError), (False, runner.ConfigError)])
def test_gateway_credential_failures(monkeypatch, retryable, error) -> None:
    def refuse(job, snapshot):
        raise LLMAccessError(code="missing_api_key", message="no key", retryable=retryable)

    monkeypatch.setattr("backend.inference.gateway_wiring.for_job", refuse)
    monkeypatch.setattr("backend.inference.proxy_server.frozen_endpoint", lambda manifest, model: None)
    with pytest.raises(error):
        runner.default_gateway_wiring(SimpleNamespace(model=None), {})


def test_gateway_wiring_keeps_the_key_out_of_the_agent(monkeypatch) -> None:
    from backend.inference.gateway_wiring import GatewayWiring

    wiring = GatewayWiring(image_local=False, upstream="https://x", api_key="sk-real", network="bridge")
    monkeypatch.setattr("backend.inference.gateway_wiring.for_job", lambda job, snapshot: wiring)
    monkeypatch.setattr("backend.inference.proxy_server.frozen_endpoint", lambda manifest, model: None)
    result = runner.default_gateway_wiring(SimpleNamespace(model=None), {})
    assert result == {"env": {"OPENROUTER_API_KEY": "sk-real"}, "network": "bridge", "local": False}


class Removable:
    def __init__(self, started: float) -> None:
        self.labels = {sandbox.LABEL: "run", "llmlab.started": str(started)}
        self.removed = False
        self.name = "c"

    def remove(self, force: bool = False) -> None:
        self.removed = True


def test_reap_orphans_removes_old_sandboxes_only() -> None:
    import time

    old, fresh = Removable(time.time() - 7200), Removable(time.time())
    network = Removable(0)
    client = SimpleNamespace(
        containers=SimpleNamespace(list=lambda all, filters: [old, fresh]),  # noqa: A006
        networks=SimpleNamespace(list=lambda filters: [network]),
    )
    assert sandbox.reap_orphans(older_than_seconds=3600, client=client) == 1
    assert old.removed
    assert not fresh.removed
    assert network.removed


def test_frontend_static_check_parsing_and_scope(settings) -> None:
    rows = classify_tree(
        {
            "frontend/src/App.jsx": SeedFile(b"export default () => <div/>\n"),
            "frontend/package.json": SeedFile(b"{}"),
            "backend/app.py": SeedFile(b"x = 1\n"),
        },
        {"frontend/package.json": SeedFile(b"{}")},
    )
    assert set(static_checks.frontend_files(rows)) == {"frontend/src/App.jsx"}
    report = static_checks.parse_report(
        b'{"validator": "esbuild@0.25.5", "files": 1, "errors": [{"path": "a.jsx", "line": 1, "message": "x"}]}\n',
    )
    assert (report["status"], report["files"]) == ("errors", 1)
    assert static_checks.parse_report(b"garbage")["status"] == "not_measured"
    settings.GENERATION_FRONTEND_VALIDATOR = False
    assert static_checks.frontend_static_check(rows)["status"] == "not_measured"
    assert static_checks.frontend_static_check([])["status"] == "not_applicable"


def test_trajectory_stream_is_throttled(monkeypatch) -> None:
    published: list[dict] = []
    monkeypatch.setattr(events, "publish", lambda run_id, payload: published.append(payload))
    now = [0.0]
    stream = events.TrajectoryStream("r", 1, lambda line: Step(index=0, kind="llm"), clock=lambda: now[0])
    for _ in range(10):
        stream("{}")
        now[0] += 0.1
    assert stream.n_steps == 10
    assert len(published) == 2  # 2 per second
    assert published[-1]["type"] == "trajectory_step"
    plain = events.TrajectoryStream("r", 1, None, clock=lambda: 100.0)
    plain("line")
    plain("")
    assert plain.n_steps == 1


def test_live_usage_publishes_and_feeds_the_ledger_hook(monkeypatch) -> None:
    published: list[dict] = []
    recorded: list[int] = []
    monkeypatch.setattr(events, "publish", lambda run_id, payload: published.append(payload))
    from backend.generation.budget import live

    monkeypatch.setattr(live, "record_live", lambda run, *, attempt, state, seq: recorded.append(seq), raising=False)
    usage = events.LiveUsage(SimpleNamespace(pk="r"), 1)
    usage({"requests_by_class": {"main": 2}, "cost": 0.01, "inflight": 1})
    assert published[0]["type"] == "usage"
    assert published[0]["inflight"] == 1
    assert recorded == [1]


def test_step_recorder_times_and_publishes(monkeypatch) -> None:
    published: list[dict] = []
    monkeypatch.setattr(events, "publish", lambda run_id, payload: published.append(payload))
    steps = events.StepRecorder("r", clock=iter([0.0, 1.5, 2.0, 2.0]).__next__)
    steps.start("images")
    steps.end("images")
    steps.skip("collect", "nothing to collect")
    assert steps.steps[0] == {"step": "images", "status": "ok", "seconds": 1.5}
    assert steps.steps[1]["status"] == "skipped"
    assert [p["status"] for p in published] == ["started", "ok", "skipped"]
