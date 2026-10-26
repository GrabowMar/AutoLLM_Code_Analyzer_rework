"""Sandbox contract against the real apps daemon (``-m docker``; skipped without one).

Uses a tiny test image (alpine + an ``/out`` owned by the tool user + a fake
``/opt/analyzer/manifest.json``) built once per module and removed after.
Every container is memory-limited, at most two exist at once, and each test
verifies that nothing labelled with its run id survives.
"""

from __future__ import annotations

import contextlib
import io
import threading
import time
import uuid

import pytest

from backend.analysis.engine import sandbox
from backend.analysis.engine.types import Invocation

pytestmark = pytest.mark.docker

BASE_IMAGE = "alpine:3.22"
TEST_IMAGE = "llmlab/analysis-sandbox-test:v1"
DOCKERFILE = f"""\
FROM {BASE_IMAGE}
RUN mkdir -p /out /src /opt/analyzer && chown 10001:10001 /out \\
 && echo '{{"tools": {{"demo": {{"version": "1.2.3"}}}}}}' > /opt/analyzer/manifest.json
LABEL llmlab.analysis-dev=1
"""


def _client_or_skip():
    from backend.runtime.services import docker_manager

    try:
        client = docker_manager.new_client()
        client.ping()
    except Exception as exc:  # noqa: BLE001 - any failure: no daemon for this suite
        pytest.skip(f"apps daemon unreachable: {exc}")
    return client


@pytest.fixture(scope="module")
def client():
    return _client_or_skip()


@pytest.fixture(scope="module")
def test_image(client):
    import docker

    try:
        client.images.get(BASE_IMAGE)
    except docker.errors.ImageNotFound:
        try:
            client.images.pull(BASE_IMAGE)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"{BASE_IMAGE} unavailable: {exc}")
    client.images.build(fileobj=io.BytesIO(DOCKERFILE.encode()), tag=TEST_IMAGE, rm=True, forcerm=True)
    yield TEST_IMAGE
    with contextlib.suppress(Exception):
        client.images.remove(TEST_IMAGE, force=True)


@pytest.fixture
def run_id(client):
    rid = f"pytest-{uuid.uuid4().hex[:12]}"
    yield rid
    label = f"{sandbox.LABEL_RUN}={rid}"
    containers = client.containers.list(all=True, filters={"label": label})
    volumes = client.volumes.list(filters={"label": label})
    for container in containers:
        container.remove(force=True, v=True)
    for volume in volumes:
        volume.remove(force=True)
    assert not containers, f"leftover containers: {[c.name for c in containers]}"
    assert not volumes, f"leftover volumes: {[v.name for v in volumes]}"


def _inv(script: str, *, timeout_s: int = 30, mem_limit: str = "64m", **kwargs) -> Invocation:
    return Invocation(argv=["sh", "-c", script], timeout_s=timeout_s, mem_limit=mem_limit, **kwargs)


def _run(image, volume, run_id, invocation, **kwargs):
    return sandbox.run_tool(
        image=image,
        volume=volume,
        invocation=invocation,
        run_id=run_id,
        tool_slug="demo",
        **kwargs,
    )


def test_exit_code_streams_user_and_cwd(client, test_image, run_id):
    files = {"app.py": b"print('hi')\n", "web/src/App.jsx": b"export default 1\n"}
    with sandbox.run_volume(run_id, files, image=test_image) as volume:
        outcome = _run(test_image, volume, run_id, _inv("cat /src/app.py; id -u; pwd; ls -R /src >&2; exit 3"))
        assert client.volumes.get(volume).attrs["Labels"][sandbox.LABEL_RUN] == run_id
    assert outcome.infra_error is None
    assert outcome.exit_code == 3
    assert outcome.stdout.splitlines() == [b"print('hi')", b"10001", b"/tmp"]
    assert b"App.jsx" in outcome.stderr
    assert b"App.jsx" not in outcome.stdout
    assert not outcome.timed_out
    assert not outcome.oom_killed
    assert not outcome.cancelled
    assert outcome.image_id.startswith("sha256:")
    assert outcome.container_name.startswith("llmlab-an-")
    assert outcome.wall_seconds > 0
    # Container and volume are gone (the run_id fixture re-checks every label).
    assert not client.containers.list(all=True, filters={"name": outcome.container_name})


def test_isolation_and_out_collection(client, test_image, run_id):
    script = (
        "touch /src/x 2>/dev/null && echo src-writable || echo src-ro; "
        "touch /etc/x 2>/dev/null && echo root-writable || echo root-ro; "
        "touch /tmp/x && echo tmp-ok; "
        "ls /sys/class/net | tr '\\n' ' '; echo; "
        "cat /out/files.txt; "
        "echo '{\"ok\": true}' > /out/report.json; mkdir -p /out/sub && echo nested > /out/sub/n.txt"
    )
    invocation = _inv(script, inputs={"files.txt": b"app.py\n"}, report_files=("report.json",))
    with sandbox.run_volume(run_id, {"app.py": b"x = 1\n"}, image=test_image) as volume:
        outcome = _run(test_image, volume, run_id, invocation)
    assert outcome.infra_error is None, outcome.infra_error
    assert outcome.exit_code == 0, outcome.stderr
    lines = outcome.stdout.decode().split("\n")
    assert lines[0] == "src-ro"
    assert lines[1] == "root-ro"
    assert lines[2] == "tmp-ok"
    assert lines[3].split() == ["lo"]  # network_mode none: loopback only
    assert lines[4] == "app.py"  # inputs are in /out before start
    assert outcome.out_files["report.json"] == b'{"ok": true}\n'
    assert outcome.out_files["sub/n.txt"] == b"nested\n"
    assert outcome.out_files["files.txt"] == b"app.py\n"


def test_timeout_kills_the_container(client, test_image, run_id):
    started = time.monotonic()
    with sandbox.run_volume(run_id, {"a.py": b""}, image=test_image) as volume:
        outcome = _run(test_image, volume, run_id, _inv("echo started; sleep 120", timeout_s=2))
    assert outcome.timed_out
    assert outcome.exit_code == 137
    assert outcome.stdout == b"started\n"
    assert 1.5 <= outcome.wall_seconds < 15
    assert time.monotonic() - started < 40


def test_oom_is_detected(client, test_image, run_id):
    # BusyBox tail keeps the whole newline-free stream in memory.
    with sandbox.run_volume(run_id, {"a.py": b""}, image=test_image) as volume:
        outcome = _run(test_image, volume, run_id, _inv("tail /dev/zero", mem_limit="16m", timeout_s=60))
    assert outcome.oom_killed
    assert not outcome.timed_out
    assert outcome.exit_code not in (0, None)


def test_output_caps_and_flags(client, test_image, run_id, settings):
    settings.ANALYSIS_OUTPUT_CAP_BYTES = 1000
    script = "head -c 5000 /dev/zero | tr '\\0' 'a'; echo err >&2"
    with sandbox.run_volume(run_id, {"a.py": b""}, image=test_image) as volume:
        outcome = _run(test_image, volume, run_id, _inv(script))
    assert outcome.stdout == b"a" * 1000
    assert outcome.stdout_truncated
    assert outcome.stderr == b"err\n"
    assert not outcome.stderr_truncated


def test_cancel_kills_the_container(client, test_image, run_id):
    started = time.monotonic()
    beats = []
    with sandbox.run_volume(run_id, {"a.py": b""}, image=test_image) as volume:
        outcome = _run(
            test_image,
            volume,
            run_id,
            _inv("sleep 120", timeout_s=300),
            is_cancelled=lambda: time.monotonic() - started > 1.0,
            heartbeat=lambda: beats.append(1),
        )
    assert outcome.cancelled
    assert not outcome.timed_out
    assert beats  # the wait loop heartbeats while polling
    assert time.monotonic() - started < 30


def test_missing_image_is_an_infra_error(client, run_id, test_image):
    with sandbox.run_volume(run_id, {"a.py": b""}, image=test_image) as volume:
        outcome = _run("llmlab/does-not-exist:none", volume, run_id, _inv("true"))
    assert outcome.infra_error
    assert outcome.exit_code is None


def test_capacity_and_reap(client, test_image, run_id):
    labels = {sandbox.LABEL_RUN: run_id, sandbox.LABEL_STARTED: str(int(time.time()) - 3600)}
    container = client.containers.run(
        test_image,
        ["sleep", "60"],
        detach=True,
        mem_limit="32m",
        network_mode="none",
        labels=labels,
    )
    volume = client.volumes.create(name=f"llmlab-an-{run_id}", labels=labels)
    try:
        snapshot = sandbox.capacity()
        mine = [c for c in snapshot["containers"] if c["name"] == container.name]
        assert mine
        assert mine[0]["mem_bytes"] == 32 * 1024 * 1024
        assert mine[0]["analysis"]
        assert snapshot["used_bytes"] >= 32 * 1024 * 1024
        assert snapshot["budget_bytes"] > 0

        # run_ids scopes every sweep to this test's resources (other work shares the daemon).
        kept = sandbox.reap(live_run_ids={run_id}, run_ids={run_id})
        assert client.containers.get(container.id)
        assert kept["containers"] == 0

        removed = sandbox.reap(live_run_ids=set(), older_than_s=60, run_ids={run_id})
        assert removed["containers"] == 1
        assert removed["volumes"] == 1
        assert not client.containers.list(all=True, filters={"id": container.id})
        assert not client.volumes.list(filters={"name": volume.name})
    finally:
        for leftover in client.containers.list(all=True, filters={"id": container.id}):
            leftover.remove(force=True)


def test_reap_spares_young_resources(client, test_image, run_id):
    labels = {sandbox.LABEL_RUN: run_id, sandbox.LABEL_STARTED: str(int(time.time()))}
    container = client.containers.create(test_image, ["true"], network_mode="none", labels=labels)
    try:
        assert sandbox.reap(live_run_ids=set(), older_than_s=600, run_ids={run_id})["containers"] == 0
        assert client.containers.get(container.id)
    finally:
        container.remove(force=True)


def test_wait_for_capacity(client, settings):
    used = sandbox.capacity()["used_bytes"]
    settings.ANALYSIS_DIND_MEMORY_BUDGET = str(used + 64 * 1024 * 1024)
    assert sandbox.wait_for_capacity(32 * 1024 * 1024, is_cancelled=lambda: False, heartbeat=lambda: None, timeout_s=0)
    if used:
        assert not sandbox.wait_for_capacity(
            512 * 1024 * 1024,
            is_cancelled=lambda: False,
            heartbeat=lambda: None,
            timeout_s=0,
        )
        cancelled = threading.Event()
        cancelled.set()
        assert not sandbox.wait_for_capacity(
            512 * 1024 * 1024,
            is_cancelled=cancelled.is_set,
            heartbeat=lambda: None,
            timeout_s=60,
        )


@pytest.mark.django_db
def test_ensure_image_builds_and_reads_the_manifest(client, test_image, tmp_path, monkeypatch):
    (tmp_path / "Dockerfile").write_text(f"FROM {TEST_IMAGE}\nLABEL llmlab.analysis-dev=1\n")
    monkeypatch.setattr(sandbox, "IMAGE_DIR", tmp_path)
    tag = sandbox.image_tag()
    try:
        with pytest.raises(sandbox.ImageUnavailable):
            sandbox.ensure_image(build=False)
        built_tag, image_id = sandbox.ensure_image(expect_tag=tag)
        assert built_tag == tag
        assert client.images.get(tag).labels.get(sandbox.LABEL_IMAGE) == "1"
        assert sandbox.ensure_image(build=False) == (tag, image_id)
        manifest = sandbox.image_manifest(image_id)
        assert sandbox.tool_version(manifest, "demo") == "1.2.3"
        status = sandbox.image_status()
        assert status["built"]
        assert status["image_id"] == image_id
        assert status["manifest"] == manifest
        assert status["daemon_ok"]
        with pytest.raises(sandbox.ImageUnavailable, match="changed since"):
            sandbox.ensure_image(expect_tag="llmlab/analyzer:000000000000")
        # A plan frozen with an older definition keeps its image while it exists.
        (tmp_path / "Dockerfile").write_text(f"FROM {TEST_IMAGE}\nLABEL llmlab.analysis-dev=2\n")
        assert sandbox.image_tag() != tag
        assert sandbox.ensure_image(expect_tag=tag, build=False) == (tag, image_id)
    finally:
        with contextlib.suppress(Exception):
            client.images.remove(tag, force=True)
    # The manifest-reader helper container never outlives the call.
    assert not client.containers.list(
        all=True,
        filters={"label": f"{sandbox.LABEL_RUN}={sandbox.MANIFEST_READER_RUN}"},
    )


class _GrepAdapter:
    """A real tool in the test image: grep for os.system, report into /out, exit 1 on findings."""

    slug = "grep"

    def select_files(self, sample, config):
        return [f for f in sample.included() if f.language == "python"]

    def invocation(self, files, config):
        script = "grep -Hn 'os.system' $(cat /out/files.txt) > /out/report.txt; test ! -s /out/report.txt"
        return Invocation(
            argv=["sh", "-c", script],
            timeout_s=30,
            mem_limit="64m",
            inputs={"files.txt": "\n".join(f"/src/{f.path}" for f in files).encode()},
            report_files=("report.txt",),
        )

    def interpret(self, outcome, files, config):
        from backend.analysis.engine.types import RawFinding
        from backend.analysis.engine.types import ToolFailure
        from backend.analysis.engine.types import ToolParse

        if outcome.exit_code not in (0, 1):
            msg = f"grep exited {outcome.exit_code}"
            raise ToolFailure(reason="tool_error", message=msg)
        report = outcome.out_files.get("report.txt")
        if report is None:
            msg = "missing report"
            raise ToolFailure(reason="parse_error", message=msg)
        findings = []
        for line in report.decode().splitlines():
            path, number, _text = line.split(":", 2)
            findings.append(
                RawFinding(
                    rule_id="os-system",
                    message="os.system call",
                    file_path=path.removeprefix("/src/"),
                    line=int(number),
                    native_severity="HIGH",
                ),
            )
        return ToolParse(findings=findings, coverage={"files_scanned": len(files), "unparseable": []})


@pytest.mark.django_db
def test_executor_end_to_end_with_real_containers(client, test_image, monkeypatch, settings, user):
    import dataclasses

    from backend.analysis.engine import adapters
    from backend.analysis.engine import artifacts
    from backend.analysis.engine import catalog
    from backend.analysis.engine import dedup
    from backend.analysis.engine import executor
    from backend.analysis.engine import measurement
    from backend.analysis.engine import normalize
    from backend.analysis.engine import sample as sample_mod
    from backend.analysis.models import AnalysisRun
    from backend.analysis.models import Finding
    from backend.analysis.models import ToolResult
    from backend.analysis.tests import executor_fakes as fx

    settings.ANALYSIS_HEARTBEAT_THREAD = False
    settings.ANALYSIS_USE_CELERY = False
    image_id = client.images.get(test_image).id
    monkeypatch.setattr(sandbox, "ensure_image", lambda **kwargs: (test_image, image_id))
    monkeypatch.setattr(sandbox, "image_manifest", lambda ref: {"tools": {"grep": {"version": "1.0"}}})
    monkeypatch.setattr(adapters, "get_adapter", {"grep": _GrepAdapter()}.__getitem__)
    # "grep" is no catalog tool: give it a spec, or the executor refuses the plan (procedure_drift).
    real_get_spec = catalog.get_spec
    grep_spec = dataclasses.replace(real_get_spec("ruff"), slug="grep", name="grep", spec_hash="spec-grep")
    monkeypatch.setattr(catalog, "get_spec", lambda slug: grep_spec if slug == "grep" else real_get_spec(slug))
    monkeypatch.setattr(sample_mod, "resolve_inline", fx.Resolver().resolve_inline)
    monkeypatch.setattr(normalize, "normalize", fx.fake_normalize)
    monkeypatch.setattr(dedup, "assign_issue_keys", fx.fake_assign_issue_keys)
    monkeypatch.setattr(measurement, "compute", fx.fake_compute)
    monkeypatch.setattr(measurement, "summarize", fx.fake_summarize)
    monkeypatch.setattr(executor, "_publish", lambda run_id, event: None)

    files = {"app.py": "import os\nos.system(input())\n", "web/util.py": "x = 1\nos.system('ls')\n"}
    run = executor.submit_run(
        user=user,
        plan=fx.make_plan([fx.tool_entry("grep")]),
        source_code=files,
        sample=fx.make_sample(files),
        dispatch=False,
    )
    executor.process_run(str(run.pk))
    run.refresh_from_db()
    assert run.status == AnalysisRun.Status.COMPLETED, run.error_message
    result = ToolResult.objects.get(run=run)
    assert result.status == ToolResult.Status.SUCCEEDED
    assert result.exit_code == 1
    assert result.diagnostics["image_id"] == image_id
    assert sorted((f.file_path, f.line_number) for f in Finding.objects.filter(run=run)) == [
        ("app.py", 2),
        ("web/util.py", 2),
    ]
    report = artifacts.load(result.artifacts["out/report.txt"]).decode()
    assert "/src/app.py:2:os.system(input())" in report
    label = f"{sandbox.LABEL_RUN}={run.pk}"
    assert not client.containers.list(all=True, filters={"label": label})
    assert not client.volumes.list(filters={"label": label})
