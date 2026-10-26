"""Re-evaluate generated apps without build healing (paired ablation).

Usage:
    python manage.py healing_ablation --experiment <uuid> [--modes none,no_deps] [--force] [--limit N]
    python manage.py healing_ablation --jobs <uuid> <uuid> ...

Each selected scaffolding/custom job is built and graded again from its
unchanged output under every requested healing mode; results land in
``result_data.healing_ablation`` and feed ``/api/statistics/study/healing/``
and ``export_study_tables``. The job's own evaluation is not touched.

Runs builds on apps-dind, one at a time — do not run it while agent runs are
live. Agent (verbatim) jobs are skipped: their builds are never healed.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from backend.generation.models import GenerationJob
from backend.generation.services.evaluation.runner import evaluate_healing_ablation
from backend.runtime.services.scaffolding import HealingMode
from backend.statistics.services.study import study_jobs


class Command(BaseCommand):
    help = "Evaluate jobs' unchanged output under other build-healing modes."

    def add_arguments(self, parser):
        parser.add_argument("--experiment", default=None)
        parser.add_argument("--batch", default=None)
        parser.add_argument("--jobs", nargs="*", default=None)
        parser.add_argument("--modes", default="none", help="Comma-separated: none, no_deps, full")
        parser.add_argument("--force", action="store_true", help="Re-run modes a job already has")
        parser.add_argument("--limit", type=int, default=None)

    def handle(self, *args, **opts):
        if not (opts["experiment"] or opts["batch"] or opts["jobs"]):
            msg = "Pick a scope: --experiment, --batch or --jobs"
            raise CommandError(msg)
        try:
            modes = tuple(HealingMode(m.strip()) for m in opts["modes"].split(",") if m.strip())
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        jobs = (
            study_jobs(experiment_id=opts["experiment"], batch_id=opts["batch"], job_ids=opts["jobs"])
            .filter(
                status=GenerationJob.Status.COMPLETED,
            )
            .exclude(mode=GenerationJob.Mode.AGENT)
        )
        if opts["limit"]:
            jobs = jobs[: opts["limit"]]

        done = skipped = 0
        for job in jobs:
            have = set(((job.result_data or {}).get("healing_ablation") or {}).keys())
            todo = modes if opts["force"] else tuple(m for m in modes if m not in have)
            if not todo:
                skipped += 1
                continue
            results = evaluate_healing_ablation(job, todo)
            done += 1
            summary = ", ".join(f"{m}={r['status']}" for m, r in results.items())
            self.stdout.write(f"{job.id} {job.model.model_id if job.model_id else '-'}: {summary}")
        self.stdout.write(self.style.SUCCESS(f"Evaluated {done} job(s); {skipped} already had every mode."))
