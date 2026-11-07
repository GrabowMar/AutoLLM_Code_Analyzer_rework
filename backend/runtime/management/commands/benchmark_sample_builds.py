"""Opt-in image-build measurements; does not run apps or change job outcomes.

Run while the executor is idle: this command performs real builds directly
through the shared builder, whose global lock still serializes cache misses.
A forced refresh retains package-download caches, so it is not a cold build.
"""

from __future__ import annotations

import copy
import json
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

from backend.generation.models import GenerationJob
from backend.runtime.services import sample_builds
from backend.runtime.services.scaffolding import prepare_build_dir

_PROTECTED_FILES = frozenset({"Dockerfile", ".dockerignore", "docker-compose.yml"})
_FRONTEND_EXTENSIONS = (".jsx", ".tsx", ".vue", ".js", ".ts", ".css")


def _copy_job(job, files: dict[str, str] | None = None, *, deleted: list[str] | None = None):
    clone = copy.copy(job)
    clone.id = uuid.uuid4()
    if hasattr(job, "_state"):
        # Detach Django's save state and relation cache before editing a copy.
        clone._state = copy.copy(job._state)  # noqa: SLF001
        clone._state.adding = True  # noqa: SLF001
        clone._state.fields_cache = {}  # noqa: SLF001
    clone.resolved_bundle = copy.deepcopy(job.resolved_bundle)
    clone.result_data = copy.deepcopy(job.result_data or {})
    if files is not None:
        clone.result_data.update({"result_schema_version": 2, "materialize": "verbatim", "files": files})
        agent = clone.result_data.setdefault("agent", {})
        workspace = agent.setdefault("workspace", {})
        workspace["deleted_scaffold_files"] = deleted or []
    return clone


def _files(job) -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="llmlab-benchmark-input-") as tmp:
        root = Path(tmp)
        prepare_build_dir(job, root)
        files = {}
        for path in sorted(root.rglob("*")):
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                msg = f"Cannot benchmark a special input file: {path.relative_to(root)}"
                raise CommandError(msg)
            files[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
        return files


def _edit_source(files: dict[str, str], *, frontend: bool, marker: str) -> dict[str, str] | None:
    edited = dict(files)
    if frontend:
        candidates = [name for name in sorted(files) if name.startswith("frontend/src/")]
        target = next(
            (name for ext in _FRONTEND_EXTENSIONS for name in candidates if name.endswith(ext)),
            None,
        )
        if target is None:
            return None
        if target.endswith(".vue"):
            comment = f"<!-- {marker} -->"
        elif target.endswith(".css"):
            comment = f"/* {marker} */"
        else:
            comment = f"// {marker}"
    else:
        target = next(
            (name for name in sorted(files) if not name.startswith("frontend/") and name.endswith(".py")),
            None,
        )
        if target is None:
            return None
        comment = f"# {marker}"
    edited[target] = files[target].rstrip() + "\n" + comment + "\n"
    return edited


def benchmark_job(job, *, repeats: int) -> dict[str, Any]:
    """Measure copies of one completed sample without ever saving a job model."""
    effective_files = _files(job)
    # Verbatim copies preserve the exact already-healed inputs. Remove any
    # skeleton files absent from that effective tree when rematerializing it.
    skeleton_files = _files(_copy_job(job, {}))
    deleted = sorted(set(skeleton_files) - set(effective_files) - _PROTECTED_FILES)
    baseline = _copy_job(job, effective_files, deleted=deleted)
    token = uuid.uuid4().hex
    records = []
    scenarios = (
        ("forced_refresh", "Bypasses image/build-layer reuse; package downloads can remain cached."),
        ("unchanged_reuse", "Repeated identical prepared inputs; cache_hit records whether reuse actually occurred."),
        ("backend_source_change", "Unique comment-only Python source edit for each repetition."),
        ("frontend_source_change", "Unique comment-only frontend source edit for each repetition."),
        (
            "dependency_manifest_change",
            "A requirements.txt comment changes the install-layer checksum; the dependency graph stays identical.",
        ),
    )
    for scenario, note in scenarios:
        iterations = []
        for repeat in range(repeats):
            candidate = baseline
            marker = f"sample-build benchmark {token} {scenario} {repeat}"
            if scenario in {"backend_source_change", "frontend_source_change"}:
                edited = _edit_source(effective_files, frontend=scenario == "frontend_source_change", marker=marker)
                candidate = _copy_job(baseline, edited, deleted=deleted) if edited is not None else None
            elif scenario == "dependency_manifest_change":
                if "requirements.txt" in effective_files:
                    edited = dict(effective_files)
                    edited["requirements.txt"] = effective_files["requirements.txt"].rstrip() + f"\n# {marker}\n"
                    candidate = _copy_job(baseline, edited, deleted=deleted)
                else:
                    candidate = None
            if candidate is None:
                iterations.append({"repeat": repeat + 1, "status": "skipped", "reason": "Input file is absent."})
                break
            started = time.monotonic()
            try:
                result = sample_builds.build_sample(candidate, force_rebuild=scenario == "forced_refresh")
            except Exception as exc:  # noqa: BLE001 - retain per-scenario failures in the report
                # One broken sample/scenario must not discard measurements of
                # the other jobs. Retain real failures instead of timings alone.
                measurement = {
                    "repeat": repeat + 1,
                    "status": "failed",
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "error": f"{type(exc).__name__}: {exc}"[-6000:],
                }
                iterations.append(measurement)
            else:
                measurement = {
                    "repeat": repeat + 1,
                    "status": "ok",
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    **result.metrics(),
                }
                iterations.append(measurement)
        records.append({"name": scenario, "note": note, "iterations": iterations})
    snapshot = job.resolved_bundle if isinstance(job.resolved_bundle, dict) else {}
    return {"job_id": str(job.id), "stack": snapshot.get("stack") or job.stack_slug, "scenarios": records}


class Command(BaseCommand):
    help = (
        "Opt-in real image-build benchmark for completed job UUIDs; run while the executor is idle. "
        "Runs up to five build scenarios per repetition, retaining images under normal cache GC. "
        "Does not run app containers or update job outcomes."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("job_ids", nargs="+", type=uuid.UUID)
        parser.add_argument("--output", type=Path, help="Write JSON to this path instead of stdout.")
        parser.add_argument("--repeats", type=int, default=3)

    def handle(self, *args, **options) -> None:
        repeats = options["repeats"]
        if repeats < 1:
            msg = "--repeats must be at least 1."
            raise CommandError(msg)
        requested = list(dict.fromkeys(options["job_ids"]))
        jobs = {job.pk: job for job in GenerationJob.objects.filter(pk__in=requested)}
        missing = [str(job_id) for job_id in requested if job_id not in jobs]
        if missing:
            raise CommandError("Unknown job UUID(s): " + ", ".join(missing))
        incomplete = [str(job.pk) for job in jobs.values() if job.status != GenerationJob.Status.COMPLETED]
        if incomplete:
            raise CommandError("Only completed jobs can be benchmarked: " + ", ".join(incomplete))
        report = {
            "schema_version": 1,
            "started_at": timezone.now().isoformat(),
            "builder": getattr(settings, "SAMPLE_BUILD_BACKEND", "buildkit"),
            "repeats": repeats,
            "note": (
                "Forced refresh is not a cold-build measurement: package-download caches are retained. "
                "Each edit changes input bytes while preserving application/dependency semantics. "
                "Startup and conformance are not measured; no app containers are launched."
            ),
            "jobs": [benchmark_job(jobs[job_id], repeats=repeats) for job_id in requested],
        }
        report["completed_at"] = timezone.now().isoformat()
        output = json.dumps(report, indent=2)
        if options["output"]:
            Path(options["output"]).write_text(output + "\n", encoding="utf-8")
        else:
            self.stdout.write(output)
