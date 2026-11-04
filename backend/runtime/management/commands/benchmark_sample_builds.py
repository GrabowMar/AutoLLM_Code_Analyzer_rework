"""Opt-in image-build measurements; does not run apps or change run outcomes.

Run while the executor is idle: this command performs real builds directly
through the shared builder, whose global lock still serializes cache misses.
A forced refresh retains package-download caches, so it is not a cold build.

Each scenario builds a copy of the run's build context (``prepare_build_dir``: the collected tree
written verbatim plus the resolved lock files). Edits touch only that temporary copy; the run and its
stored files are never changed or saved.
"""

from __future__ import annotations

import json
import shutil
import stat
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.utils import timezone

from backend.generation.models import GenerationRun
from backend.generation.models.choices import RunStatus
from backend.runtime.services import sample_builds
from backend.runtime.services.build_context import build_identity
from backend.runtime.services.build_context import prepare_build_dir

_FRONTEND_EXTENSIONS = (".jsx", ".tsx", ".vue", ".js", ".ts", ".css")
_DEPENDENCY_MANIFESTS = ("requirements.txt", "backend/requirements.txt")
_SCENARIOS = (
    ("forced_refresh", "Bypasses image/build-layer reuse; package downloads can remain cached."),
    ("unchanged_reuse", "Repeated identical prepared inputs; cache_hit records whether reuse actually occurred."),
    ("backend_source_change", "Unique comment-only Python source edit for each repetition."),
    ("frontend_source_change", "Unique comment-only frontend source edit for each repetition."),
    (
        "dependency_manifest_change",
        "A requirements.txt comment changes the install-layer checksum; the dependency graph stays identical.",
    ),
)


def _names(root: Path) -> list[str]:
    """Regular files of a prepared context, sorted; a special file aborts the benchmark."""
    names = []
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            msg = f"Cannot benchmark a special input file: {path.relative_to(root)}"
            raise CommandError(msg)
        names.append(path.relative_to(root).as_posix())
    return names


def _comment(name: str) -> str:
    if name.endswith(".vue"):
        return "<!-- {} -->"
    if name.endswith(".css"):
        return "/* {} */"
    return "// {}" if name.startswith("frontend/") else "# {}"


def _edit_target(names: list[str], scenario: str) -> tuple[str, str] | None:
    """``(file, comment template)`` to append for an edit scenario, or None when the input is absent."""
    target = None
    if scenario == "frontend_source_change":
        candidates = [name for name in names if name.startswith("frontend/src/")]
        target = next((name for ext in _FRONTEND_EXTENSIONS for name in candidates if name.endswith(ext)), None)
    elif scenario == "backend_source_change":
        target = next((name for name in names if not name.startswith("frontend/") and name.endswith(".py")), None)
    elif scenario == "dependency_manifest_change":
        target = next((name for name in _DEPENDENCY_MANIFESTS if name in names), None)
    return (target, _comment(target)) if target is not None else None


def _edited_copy(base: Path, workdir: Path, target: str, comment: str) -> Path:
    """A copy of ``base`` with ``comment`` appended to ``target`` (file mode kept)."""
    copy = workdir / uuid.uuid4().hex
    shutil.copytree(base, copy, symlinks=True)
    path = copy / target
    text = path.read_text(encoding="utf-8")
    path.write_text(text.rstrip() + "\n" + comment + "\n", encoding="utf-8")
    return copy


def benchmark_run(run: GenerationRun, *, repeats: int) -> dict[str, Any]:
    """Measure builds of copies of one completed run's build context, never saving the run."""
    token = uuid.uuid4().hex
    records = []
    with tempfile.TemporaryDirectory(prefix="llmlab-benchmark-input-") as tmp:
        workdir = Path(tmp)
        base = workdir / "base"
        try:
            prepare_build_dir(run, base)
        except ValueError as exc:
            msg = f"Run {run.pk} has no buildable tree: {exc}"
            raise CommandError(msg) from exc
        names = _names(base)
        for scenario, note in _SCENARIOS:
            iterations = []
            for repeat in range(repeats):
                context = base
                if scenario not in {"forced_refresh", "unchanged_reuse"}:
                    target = _edit_target(names, scenario)
                    if target is None:
                        iterations.append(
                            {"repeat": repeat + 1, "status": "skipped", "reason": "Input file is absent."},
                        )
                        break
                    marker = f"sample-build benchmark {token} {scenario} {repeat}"
                    context = _edited_copy(base, workdir, target[0], target[1].format(marker))
                started = time.monotonic()
                try:
                    result = sample_builds.build_sample(
                        run,
                        force_rebuild=scenario == "forced_refresh",
                        context_dir=context,
                    )
                except Exception as exc:  # noqa: BLE001 - retain per-scenario failures in the report
                    # One broken run/scenario must not discard measurements of
                    # the other runs. Retain real failures instead of timings alone.
                    iterations.append(
                        {
                            "repeat": repeat + 1,
                            "status": "failed",
                            "elapsed_seconds": round(time.monotonic() - started, 3),
                            "error": f"{type(exc).__name__}: {exc}"[-6000:],
                        },
                    )
                else:
                    iterations.append(
                        {
                            "repeat": repeat + 1,
                            "status": "ok",
                            "elapsed_seconds": round(time.monotonic() - started, 3),
                            **result.metrics(),
                        },
                    )
                finally:
                    if context != base:
                        shutil.rmtree(context, ignore_errors=True)
            records.append({"name": scenario, "note": note, "iterations": iterations})
    return {
        "run_id": str(run.pk),
        "kind": run.kind,
        "stack": build_identity(run).get("stack"),
        "scenarios": records,
    }


class Command(BaseCommand):
    help = (
        "Opt-in real image-build benchmark for completed generation run UUIDs; run while the executor is idle. "
        "Runs up to five build scenarios per repetition, retaining images under normal cache GC. "
        "Does not run app containers or update run outcomes."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("run_ids", nargs="+", type=uuid.UUID)
        parser.add_argument("--output", type=Path, help="Write JSON to this path instead of stdout.")
        parser.add_argument("--repeats", type=int, default=3)

    def handle(self, *args, **options) -> None:
        repeats = options["repeats"]
        if repeats < 1:
            msg = "--repeats must be at least 1."
            raise CommandError(msg)
        requested = list(dict.fromkeys(options["run_ids"]))
        runs = {run.pk: run for run in GenerationRun.objects.filter(pk__in=requested)}
        missing = [str(run_id) for run_id in requested if run_id not in runs]
        if missing:
            raise CommandError("Unknown run UUID(s): " + ", ".join(missing))
        incomplete = [str(run.pk) for run in runs.values() if run.status != RunStatus.COMPLETED]
        if incomplete:
            raise CommandError("Only completed runs can be benchmarked: " + ", ".join(incomplete))
        report = {
            "schema_version": 2,
            "started_at": timezone.now().isoformat(),
            "builder": getattr(settings, "SAMPLE_BUILD_BACKEND", "buildkit"),
            "repeats": repeats,
            "note": (
                "Forced refresh is not a cold-build measurement: package-download caches are retained. "
                "Each edit changes input bytes while preserving application/dependency semantics. "
                "Startup and evaluation are not measured; no app containers are launched."
            ),
            "runs": [benchmark_run(runs[run_id], repeats=repeats) for run_id in requested],
        }
        report["completed_at"] = timezone.now().isoformat()
        output = json.dumps(report, indent=2)
        if options["output"]:
            Path(options["output"]).write_text(output + "\n", encoding="utf-8")
        else:
            self.stdout.write(output)
