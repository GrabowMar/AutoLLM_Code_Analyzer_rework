"""The opt-in benchmark builds only temporary copies of a run's context and reports actual cache data."""

from __future__ import annotations

import io
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from backend.runtime.management.commands import benchmark_sample_builds as command


def _tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def benchmark_inputs(monkeypatch):
    identifier = uuid.uuid4()
    run = SimpleNamespace(id=identifier, pk=identifier, status="completed", kind="app_agentic", save=MagicMock())
    files = {
        "app.py": "print('sample')\n",
        "requirements.txt": "Flask==3.1.0\n",
        "Dockerfile": "FROM python:3.13-slim\n",
        "frontend/src/App.jsx": "export default () => <p>Sample</p>;\n",
    }
    builds = []

    def prepare(run, root):
        for name, text in files.items():
            path = Path(root) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return Path(root)

    def build(candidate, *, force_rebuild, context_dir):
        tree = _tree(context_dir)
        builds.append(SimpleNamespace(run=candidate, force_rebuild=force_rebuild, files=tree))
        return SimpleNamespace(
            metrics=lambda: {
                "cache_hit": not force_rebuild and tree == files,
                "seconds": 0 if not force_rebuild else 2,
                "cpu_seconds": None,
                "peak_memory_bytes": None,
            },
        )

    monkeypatch.setattr(command, "prepare_build_dir", prepare)
    monkeypatch.setattr(command, "build_identity", lambda run: {"stack": "stack:generic-python@1"})
    monkeypatch.setattr(command.sample_builds, "build_sample", build)
    monkeypatch.setattr(command.GenerationRun.objects, "filter", lambda **kwargs: [run])
    return SimpleNamespace(run=run, files=files, builds=builds)


def test_benchmark_reports_real_scenarios_and_never_saves_the_run(benchmark_inputs):
    env = benchmark_inputs
    stdout = io.StringIO()
    call_command("benchmark_sample_builds", str(env.run.id), repeats=1, stdout=stdout)
    report = json.loads(stdout.getvalue())
    result = report["runs"][0]
    scenarios = {scenario["name"]: scenario for scenario in result["scenarios"]}

    assert result["run_id"] == str(env.run.id)
    assert result["stack"] == "stack:generic-python@1"
    assert set(scenarios) == {
        "forced_refresh",
        "unchanged_reuse",
        "backend_source_change",
        "frontend_source_change",
        "dependency_manifest_change",
    }
    assert scenarios["forced_refresh"]["iterations"][0]["cache_hit"] is False
    assert scenarios["unchanged_reuse"]["iterations"][0]["cache_hit"] is True
    assert [b.force_rebuild for b in env.builds] == [True, False, False, False, False]
    assert all(b.run is env.run for b in env.builds)
    env.run.save.assert_not_called()
    assert "# sample-build benchmark" in env.builds[2].files["app.py"]
    assert "// sample-build benchmark" in env.builds[3].files["frontend/src/App.jsx"]
    dependency = env.builds[4].files["requirements.txt"]
    assert dependency.startswith(env.files["requirements.txt"])
    assert "# sample-build benchmark" in dependency
    # Each edit scenario changes exactly one file of the context.
    for build in env.builds[2:]:
        assert sum(build.files[name] != text for name, text in env.files.items()) == 1


def test_benchmark_writes_output_and_repeats_edits(benchmark_inputs, tmp_path):
    output = tmp_path / "benchmark.json"
    call_command("benchmark_sample_builds", str(benchmark_inputs.run.id), repeats=2, output=str(output))
    report = json.loads(output.read_text())
    assert report["repeats"] == 2
    assert len(benchmark_inputs.builds) == 10
    edited_sources = [b.files["app.py"] for b in benchmark_inputs.builds[4:6]]
    assert edited_sources[0] != edited_sources[1]


def test_benchmark_records_failure_and_skips_absent_frontend(benchmark_inputs, monkeypatch):
    env = benchmark_inputs
    env.files.pop("frontend/src/App.jsx")

    def fail(*args, **kwargs):
        msg = "registry unavailable"
        raise RuntimeError(msg)

    monkeypatch.setattr(command.sample_builds, "build_sample", fail)
    result = command.benchmark_run(env.run, repeats=1)
    scenarios = {scenario["name"]: scenario for scenario in result["scenarios"]}
    assert scenarios["forced_refresh"]["iterations"][0]["status"] == "failed"
    assert "registry unavailable" in scenarios["forced_refresh"]["iterations"][0]["error"]
    assert scenarios["frontend_source_change"]["iterations"][0]["status"] == "skipped"


def test_benchmark_refuses_a_run_without_a_tree(benchmark_inputs, monkeypatch):
    def empty(run, root):
        msg = "no collected tree"
        raise ValueError(msg)

    monkeypatch.setattr(command, "prepare_build_dir", empty)
    with pytest.raises(CommandError, match="no buildable tree"):
        command.benchmark_run(benchmark_inputs.run, repeats=1)
    assert benchmark_inputs.builds == []


def test_benchmark_rejects_nonpositive_repeats_before_querying():
    with pytest.raises(CommandError, match="at least 1"):
        call_command("benchmark_sample_builds", str(uuid.uuid4()), repeats=0)


def test_benchmark_rejects_live_runs(benchmark_inputs):
    benchmark_inputs.run.status = "running"
    with pytest.raises(CommandError, match="Only completed runs"):
        call_command("benchmark_sample_builds", str(benchmark_inputs.run.id))
