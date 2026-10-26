"""The review sandbox on the real apps daemon, without any LLM call (no key, no spend).

A scripted harness command stands in for the agent: it checks its
environment (read-only app copy, no route out, the gateway reachable),
writes a findings file and exits. The real llm-gateway image and the real
harness image run with the real entry script, so this covers what the fake
client cannot: put_archive ownership/modes, the findings copy, exit.json,
get_archive, the internal network and teardown. Skips when the daemon or the
(prebuilt) images are unavailable; it never builds images.
"""

from __future__ import annotations

import json

import pytest

from backend.analysis.engine.reviewer.agentic_harness import FINDINGS_PATH
from backend.analysis.engine.reviewer.agentic_harness import get_review_harness
from backend.analysis.engine.reviewer.agentic_sandbox import LABEL_REVIEW
from backend.analysis.engine.reviewer.agentic_sandbox import ReviewSandbox
from backend.analysis.engine.reviewer.agentic_sandbox import SandboxSpec
from backend.analysis.engine.reviewer.agentic_sandbox import gateway_config

pytestmark = pytest.mark.docker

DOC = {"findings": [], "files_reviewed": ["app.py"], "review_complete": True}
HARNESS_SCRIPT = f"""cd /workspace
echo tamper >> app.py 2>/dev/null && echo "app writable" || echo "app read-only"
python3 -c "import urllib.request as u; u.urlopen('http://llm-gateway:8080/healthz', timeout=5); print('gateway ok')"
python3 -c "import socket; socket.create_connection(('1.1.1.1', 443), timeout=3)" 2>/dev/null \\
  && echo "egress open" || echo "egress blocked"
cat > {FINDINGS_PATH} <<'EOF'
{json.dumps(DOC)}
EOF
echo done
"""


@pytest.fixture
def docker_client():
    try:
        from backend.runtime.services.docker_manager import new_client

        client = new_client()
        client.ping()
    except Exception as exc:  # noqa: BLE001 - any daemon problem means "skip"
        pytest.skip(f"apps daemon unreachable: {exc}")
    yield client
    client.close()


def test_real_sandbox_runs_collects_and_cleans_up(docker_client):
    import docker

    harness = get_review_harness("opencode")
    images = harness.images(harness.version())
    for role in ("gateway", "base", "harness"):
        try:
            docker_client.images.get(images[role])
        except docker.errors.ImageNotFound:
            pytest.skip(f"{images[role]} is not built on the apps daemon")

    run_id = "docker-test-agentic-review"
    spec = SandboxSpec(
        run_id=run_id,
        index=0,
        images=images,
        env={},
        workspace={"app.py": b"print('hi')\n"},
        out_files={"brief.md": b"# brief\n", "harness.sh": HARNESS_SCRIPT.encode()},
        gateway_config=gateway_config(
            model_slug="deepseek/deepseek-v4.1-flash",
            forced_params={"max_tokens": 1000},
            stripped_params=["seed"],
            budget={"max_llm_requests": 1, "max_cost_usd": 0.001, "wall_clock_seconds": 60},
            title="docker test",
        ),
        wall_clock_seconds=60,
        mem_limit="512m",
        nano_cpus=500_000_000,
        findings_path=FINDINGS_PATH,
    )
    result = ReviewSandbox(spec, api_key="sk-not-a-real-key", client=docker_client).run(
        is_cancelled=lambda: False,
        heartbeat=lambda: None,
    )

    assert result.termination == "completed", (result.error, result.outputs.get("harness.log"))
    assert result.exit_code == 0
    events = result.outputs["events.jsonl"].decode()
    assert "app read-only" in events
    assert "gateway ok" in events
    assert "egress blocked" in events
    assert json.loads(result.outputs["findings.json"]) == DOC
    assert json.loads(result.outputs["exit.json"])["exit_code"] == 0
    assert "gateway_events.jsonl" in result.gateway_logs
    assert docker_client.containers.list(all=True, filters={"label": f"{LABEL_REVIEW}={run_id}"}) == []
    assert docker_client.networks.list(filters={"label": f"{LABEL_REVIEW}={run_id}"}) == []
