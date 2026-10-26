"""The review sandbox on a fake apps daemon: topology, limits, collection, termination, cleanup."""

from __future__ import annotations

import json

import pytest

from backend.analysis.engine.reviewer import agentic_sandbox
from backend.analysis.engine.reviewer.agentic_harness import FINDINGS_PATH
from backend.analysis.engine.reviewer.agentic_sandbox import LABEL_REVIEW
from backend.analysis.engine.reviewer.agentic_sandbox import LABEL_STARTED
from backend.analysis.engine.reviewer.agentic_sandbox import ReviewSandbox
from backend.analysis.engine.reviewer.agentic_sandbox import SandboxSpec
from backend.analysis.engine.reviewer.agentic_sandbox import classify
from backend.analysis.engine.reviewer.agentic_sandbox import gateway_config
from backend.analysis.engine.reviewer.agentic_sandbox import reap_review_sandboxes
from backend.analysis.engine.reviewer.agentic_sandbox import review_entry_script
from backend.analysis.engine.sandbox import LABEL_RUN
from backend.analysis.tests.agentic_fakes import Behaviour
from backend.analysis.tests.agentic_fakes import FakeClient
from backend.analysis.tests.agentic_fakes import untar
from backend.generation.services.agent.images import harness_image_plan
from backend.generation.services.agent.images import image_tag

RUN_ID = "0b6f5c1e-1111-4222-8333-944445555666"
FINDINGS = json.dumps({"findings": [], "files_reviewed": ["app.py"], "review_complete": True}).encode()


def _images() -> dict:
    args = {"OPENCODE_VERSION": "1.18.35"}
    plan = harness_image_plan("agent-opencode", args)
    return {
        "base": plan[0][2],
        "harness": plan[1][2],
        "gateway": image_tag("llm-gateway"),
        "harness_dir": "agent-opencode",
        "harness_buildargs": args,
    }


def _spec(**overrides) -> SandboxSpec:
    values = {
        "run_id": RUN_ID,
        "index": 1,
        "images": _images(),
        "env": {"OPENCODE_CONFIG_CONTENT": "{}"},
        "workspace": {"app.py": b"print('hi')\n", "frontend/src/App.jsx": b"export default 1\n"},
        "out_files": {"brief.md": b"# brief", "harness.sh": b"cd /workspace\nexec true\n"},
        "gateway_config": gateway_config(
            model_slug="deepseek/deepseek-v4.1-flash",
            forced_params={"max_tokens": 16000},
            stripped_params=["seed"],
            budget={"max_llm_requests": 20, "max_cost_usd": 0.1, "wall_clock_seconds": 600},
            title="review",
        ),
        "wall_clock_seconds": 600,
        "mem_limit": "1g",
        "nano_cpus": 1_000_000_000,
        "findings_path": FINDINGS_PATH,
    }
    values.update(overrides)
    return SandboxSpec(**values)


def _outputs(exit_code=0, **extra) -> dict[str, bytes]:
    return {"exit.json": json.dumps({"exit_code": exit_code, "agent_seconds": 3}).encode(), **extra}


def _run(client: FakeClient, spec: SandboxSpec | None = None, *, cancel_after: int | None = None):
    calls = {"cancel": 0, "beat": 0}

    def is_cancelled() -> bool:
        calls["cancel"] += 1
        return cancel_after is not None and calls["cancel"] > cancel_after

    def heartbeat() -> None:
        calls["beat"] += 1

    sandbox = ReviewSandbox(spec or _spec(), api_key="sk-or-real", client=client)
    return sandbox.run(is_cancelled=is_cancelled, heartbeat=heartbeat), calls


def _assert_clean(client: FakeClient) -> None:
    assert all(c.removed for c in client.created)
    assert all(n.removed for n in client.networks_created)
    assert client.container_list == []
    assert client.network_list == []


def test_happy_path_topology_limits_and_collection():
    logs = {"calls.jsonl": b'{"status": 200}\n', "gateway_events.jsonl": b'{"event": "started"}\n'}
    client = FakeClient(Behaviour(out_files=_outputs(**{"findings.json": FINDINGS}), gateway_logs=logs))
    result, calls = _run(client)

    assert result.termination == "completed"
    assert result.started
    assert result.exit_code == 0
    assert result.outputs["findings.json"] == FINDINGS
    assert result.gateway_logs == logs
    assert result.images["harness"] == _images()["harness"]
    assert result.images["gateway_id"].startswith("sha256:")

    network, egress = client.networks_created
    assert network.kwargs["internal"] is True
    assert network.kwargs["labels"][LABEL_REVIEW] == RUN_ID
    assert LABEL_STARTED in network.kwargs["labels"]
    gateway, harness = client.gateway, client.harness
    # The gateway reaches the internet through its own network (never the daemon's shared bridge,
    # where other containers could use its key-holding proxy); the harness sees the internal one alone.
    assert gateway.kwargs["network"] == egress.name != "bridge"
    assert egress.kwargs["internal"] is False
    assert egress.kwargs["options"] == {"com.docker.network.bridge.enable_icc": "false"}
    assert egress.kwargs["labels"][LABEL_REVIEW] == RUN_ID  # reaped with the rest
    assert egress.connected == []
    assert network.connected == [(gateway.name, ["llm-gateway"])]
    assert harness.kwargs["network"] == network.name
    assert harness.kwargs["mem_limit"] == harness.kwargs["memswap_limit"] == "1g"
    assert gateway.kwargs["mem_limit"] == "256m"
    for container in (gateway, harness):
        assert container.kwargs["labels"][LABEL_REVIEW] == RUN_ID
        assert container.kwargs["labels"][LABEL_RUN] == RUN_ID  # the executor's reaper finds them
        assert container.kwargs["cap_drop"] == ["ALL"]
        assert container.kwargs["security_opt"] == ["no-new-privileges"]
        assert container.name.startswith("llmlab-review-")
    # The key lives only in the gateway; the gateway config holds the budget and no egress.
    assert gateway.kwargs["environment"]["OPENROUTER_API_KEY"] == "sk-or-real"
    assert "sk-or-real" not in json.dumps(harness.kwargs)
    config = json.loads(gateway.kwargs["environment"]["GATEWAY_CONFIG"])
    assert config["budget"] == {"max_requests": 20, "max_cost_usd": 0.1}
    assert config["connect_allowlist"] == []
    # The app copy goes in read-only; the brief and command go to /out.
    (ws_path, ws_tar), (out_path, out_tar) = harness.archives
    assert (ws_path, out_path) == ("/workspace", "/out")
    workspace = untar(ws_tar)
    assert workspace["app.py"] == b"print('hi')\n"
    assert workspace["app.py:mode"] == 0o444
    assert untar(out_tar)["harness.sh"] == b"cd /workspace\nexec true\n"
    assert calls["beat"] >= 1  # gateway health loop heartbeats
    _assert_clean(client)


def test_shared_bridge_egress_only_when_configured(settings):
    settings.ANALYSIS_REVIEW_GATEWAY_EGRESS = "bridge"
    client = FakeClient(Behaviour(out_files=_outputs(), gateway_logs={}))
    _run(client)
    assert [n.kwargs["internal"] for n in client.networks_created] == [True]
    assert client.gateway.kwargs["network"] == "bridge"
    _assert_clean(client)


def test_entry_script_runs_the_harness_under_timeout_without_git_and_copies_the_findings():
    script = review_entry_script(wall_clock_seconds=900, findings_path=FINDINGS_PATH)
    assert "git" not in script
    assert "workspace.tar" not in script
    assert "timeout -s TERM -k 15 900 sh /out/harness.sh" in script
    assert f"mkdir -p {FINDINGS_PATH.rsplit('/', 1)[0]}" in script
    assert f"rm -f {FINDINGS_PATH} /out/findings.json" in script
    assert f"head -c 4194304 {FINDINGS_PATH} > /out/findings.json" in script
    assert "truncate -s 20971520" in script
    answer_only = review_entry_script(wall_clock_seconds=60, findings_path=None)
    assert ".llmlab-review" not in answer_only
    assert "rm -f /out/findings.json" in answer_only


@pytest.mark.parametrize(
    ("termination", "exit_code", "oom", "expected"),
    [
        ("completed", 0, False, "completed"),
        ("completed", 124, False, "timeout"),
        ("completed", 1, True, "resource_limit"),
        ("completed", 137, False, "harness_killed"),
        ("completed", 143, False, "harness_killed"),
        ("completed", 2, False, "harness_error"),
        ("completed", None, False, "harness_error"),
        ("cancelled", None, False, "cancelled"),
        ("timeout", None, False, "timeout"),
    ],
)
def test_classify(termination, exit_code, oom, expected):
    assert classify(termination, harness_exit=exit_code, oom_killed=oom) == expected


def test_harness_exit_codes_reach_the_result():
    client = FakeClient(Behaviour(out_files=_outputs(124, **{"harness.log": b"slow"}), exit_code=0))
    result, _ = _run(client)
    assert result.termination == "timeout"
    assert result.exit_code == 124
    _assert_clean(client)


def test_oom_kill_is_a_resource_limit():
    client = FakeClient(Behaviour(out_files=_outputs(137), exit_code=137, oom_killed=True))
    result, _ = _run(client)
    assert result.termination == "resource_limit"
    assert result.oom_killed


def test_cancel_during_the_run_kills_the_harness_and_still_collects_evidence():
    logs = {"calls.jsonl": b'{"status": 200}\n'}
    client = FakeClient(Behaviour(never_exits=True, gateway_logs=logs, out_files={"events.jsonl": b"{}"}))
    result, _ = _run(client, cancel_after=3)
    assert result.termination == "cancelled"
    assert client.harness.killed
    assert result.gateway_logs == logs
    assert result.outputs["events.jsonl"] == b"{}"
    _assert_clean(client)


def test_cancel_before_start_never_starts_the_harness():
    client = FakeClient(Behaviour())
    result, _ = _run(client, cancel_after=0)
    assert result.termination == "cancelled"
    assert not result.started
    assert all(not c.started for c in client.created if not c.is_gateway)
    _assert_clean(client)


def test_backstop_timeout_kills_a_harness_that_ignores_its_timeout(monkeypatch):
    monkeypatch.setattr(agentic_sandbox, "GRACE_SECONDS", 0)
    client = FakeClient(Behaviour(never_exits=True))
    result, _ = _run(client, _spec(wall_clock_seconds=0))
    assert result.termination == "timeout"
    assert client.harness.killed
    _assert_clean(client)


@pytest.mark.parametrize("failure", ["put_archive", "network", "start"])
def test_infrastructure_failures_are_reported_and_cleaned_up(failure):
    client = FakeClient(Behaviour(fail_on=failure))
    result, _ = _run(client)
    assert result.termination == "infra_error"
    assert result.error
    assert not result.started
    _assert_clean(client)


def test_gateway_that_exits_during_startup_is_an_infra_error():
    client = FakeClient(Behaviour(fail_on="gateway_start"))
    result, _ = _run(client)
    assert result.termination == "infra_error"
    assert "gateway exited during startup" in result.error
    assert not any(not c.is_gateway for c in client.created)  # the harness was never created
    _assert_clean(client)


def test_unhealthy_gateway_times_out(monkeypatch):
    monkeypatch.setattr(agentic_sandbox, "GATEWAY_HEALTH_SECONDS", 0)
    client = FakeClient(Behaviour(fail_on="gateway_unhealthy"))
    result, _ = _run(client)
    assert result.termination == "infra_error"
    assert "did not become healthy" in result.error
    _assert_clean(client)


def test_changed_image_definition_refuses_to_run():
    client = FakeClient(Behaviour())
    images = {**_images(), "harness": "llmlab/agent-opencode:000000000000"}
    result, _ = _run(client, _spec(images=images))
    assert result.termination == "infra_error"
    assert "changed since this job was created" in result.error
    assert client.created == []


def test_reap_removes_only_sandboxes_of_runs_that_are_not_live():
    client = FakeClient(Behaviour())
    live = client.containers.create("img", name="live", labels={LABEL_REVIEW: "live-run", LABEL_STARTED: "0"})
    dead = client.containers.create("img", name="dead", labels={LABEL_REVIEW: "dead-run", LABEL_STARTED: "0"})
    young = client.containers.create("img", name="young", labels={LABEL_REVIEW: "dead-run", LABEL_STARTED: "9e12"})
    other = client.containers.create("img", name="sample-app", labels={"llmlab.app": "x"})
    client.networks.create("dead-net", labels={LABEL_REVIEW: "dead-run"})
    client.networks.create("live-net", labels={LABEL_REVIEW: "live-run"})

    removed = reap_review_sandboxes(live_run_ids={"live-run"}, older_than_s=60, client=client)

    assert removed == {"containers": 1, "networks": 1}
    assert dead.removed
    assert not live.removed
    assert not young.removed
    assert not other.removed
    assert [n.name for n in client.network_list] == ["live-net"]


def test_gateway_config_maps_the_review_budget_and_never_allows_egress():
    config = gateway_config(
        model_slug="m/x",
        forced_params={"temperature": 0.2},
        stripped_params=["top_p", "seed"],
        budget={"max_llm_requests": 7, "max_cost_usd": 0.25, "max_total_tokens": 100000, "wall_clock_seconds": 60},
        title="t",
    )
    assert config["model"] == "m/x"
    assert config["budget"] == {"max_requests": 7, "max_cost_usd": 0.25, "max_total_tokens": 100000}
    assert config["stripped_params"] == ["seed", "top_p"]
    assert config["connect_allowlist"] == []
    assert config["upstream"] == "https://openrouter.ai/api/v1"
