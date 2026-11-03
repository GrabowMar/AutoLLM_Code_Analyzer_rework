"""The opt-in benchmark changes only unsaved inputs and reports actual cache data."""

from __future__ import annotations

import copy
import io
import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from backend.runtime.management.commands import benchmark_sample_builds as command


@pytest.fixture
def benchmark_inputs(monkeypatch):
    identifier = uuid.uuid4()
    job = SimpleNamespace(
        id=identifier,
        pk=identifier,
        status="completed",
        stack_slug="generic-python",
        resolved_bundle={"stack": {"slug": "generic-python", "version": 1}},
        result_data={"backend_code": "original source", "metrics": {"functional": {"passed": True}}},
        save=MagicMock(),
    )
    files = {
        "app.py": "print('sample')\n",
        "requirements.txt": "Flask==3.1.0\n",
        "Dockerfile": "FROM python:3.13-slim\n",
        "frontend/src/App.jsx": "export default () => <p>Sample</p>;\n",
    }
    inputs = []

    def prepare(job, root):
        tree = job.result_data.get("files", files)
        for name, text in tree.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def build(candidate, *, force_rebuild):
        inputs.append((candidate, force_rebuild))
        return SimpleNamespace(
            metrics=lambda: {
                "cache_hit": not force_rebuild and candidate.result_data["files"] == files,
                "seconds": 0 if not force_rebuild else 2,
                "cpu_seconds": None,
                "peak_memory_bytes": None,
            },
        )

    monkeypatch.setattr(command, "prepare_build_dir", prepare)
    monkeypatch.setattr(command.sample_builds, "build_sample", build)
    monkeypatch.setattr(command.GenerationJob.objects, "filter", lambda **kwargs: [job])
    return SimpleNamespace(job=job, files=files, builds=inputs)


def test_benchmark_reports_real_scenarios_and_preserves_live_job(benchmark_inputs):
    env = benchmark_inputs
    original = copy.deepcopy(env.job.result_data)
    stdout = io.StringIO()
    call_command("benchmark_sample_builds", str(env.job.id), repeats=1, stdout=stdout)
    report = json.loads(stdout.getvalue())
    scenarios = {scenario["name"]: scenario for scenario in report["jobs"][0]["scenarios"]}

    assert set(scenarios) == {
        "forced_refresh",
        "unchanged_reuse",
        "backend_source_change",
        "frontend_source_change",
        "dependency_manifest_change",
    }
    assert scenarios["forced_refresh"]["iterations"][0]["cache_hit"] is False
    assert scenarios["unchanged_reuse"]["iterations"][0]["cache_hit"] is True
    assert [refresh for _, refresh in env.builds] == [True, False, False, False, False]
    assert all(candidate.id != env.job.id for candidate, _ in env.builds)
    assert env.job.result_data == original
    env.job.save.assert_not_called()
    dependency_files = env.builds[-1][0].result_data["files"]
    assert dependency_files["requirements.txt"].startswith(env.files["requirements.txt"])
    assert "# sample-build benchmark" in dependency_files["requirements.txt"]


def test_benchmark_writes_output_and_repeats_edits(benchmark_inputs, tmp_path):
    output = tmp_path / "benchmark.json"
    call_command("benchmark_sample_builds", str(benchmark_inputs.job.id), repeats=2, output=str(output))
    report = json.loads(output.read_text())
    assert report["repeats"] == 2
    assert len(benchmark_inputs.builds) == 10
    edited_sources = [candidate.result_data["files"]["app.py"] for candidate, _ in benchmark_inputs.builds[4:6]]
    assert edited_sources[0] != edited_sources[1]


def test_benchmark_records_failure_and_skips_absent_frontend(benchmark_inputs, monkeypatch):
    env = benchmark_inputs
    env.files.pop("frontend/src/App.jsx")

    def fail(*args, **kwargs):
        msg = "registry unavailable"
        raise RuntimeError(msg)

    monkeypatch.setattr(command.sample_builds, "build_sample", fail)
    result = command.benchmark_job(env.job, repeats=1)
    scenarios = {scenario["name"]: scenario for scenario in result["scenarios"]}
    assert scenarios["forced_refresh"]["iterations"][0]["status"] == "failed"
    assert "registry unavailable" in scenarios["forced_refresh"]["iterations"][0]["error"]
    assert scenarios["frontend_source_change"]["iterations"][0]["status"] == "skipped"


def test_benchmark_rejects_nonpositive_repeats_before_querying():
    with pytest.raises(CommandError, match="at least 1"):
        call_command("benchmark_sample_builds", str(uuid.uuid4()), repeats=0)


def test_benchmark_rejects_live_jobs(benchmark_inputs):
    benchmark_inputs.job.status = "running"
    with pytest.raises(CommandError, match="Only completed jobs"):
        call_command("benchmark_sample_builds", str(benchmark_inputs.job.id))
