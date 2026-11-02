"""Recompute agent trajectory analytics from the run records on disk.

For every completed agent-mode job with a run record, rebuild
``result_data.agent.trajectory`` and ``metrics.behaviour`` (plus
``metrics.list_cost`` / ``metrics.billing_source`` for subscription-billed
runs) from ``llm_calls.jsonl.gz`` and the harness evidence. No LLM calls.

Additive only: harness self-reports, ``metrics.cost`` and every measured
value stay untouched; only the derived keys above are written. Jobs whose
stored record already has the current parser version are skipped unless
``--force``.

    python manage.py backfill_agent_trajectories --dry-run
    python manage.py backfill_agent_trajectories --job 0d40e996-... --job d6ce02d4-...
"""

from __future__ import annotations

import uuid

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import transaction

from backend.generation.models import GenerationJob
from backend.generation.services.agent import trajectory as traj


class Command(BaseCommand):
    help = "Rebuild agent trajectory records (result_data.agent.trajectory, metrics.behaviour) from run records."

    def add_arguments(self, parser):
        parser.add_argument(
            "--job",
            action="append",
            default=[],
            help="Job id (full UUID or a unique prefix); repeatable. Default: every completed agent job.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Compute and report, write nothing.")
        parser.add_argument("--force", action="store_true", help="Recompute records of the current parser version.")

    def handle(self, *args, **options):
        jobs = self._jobs(options["job"])
        dry, force = options["dry_run"], options["force"]
        counts = {"updated": 0, "would_update": 0, "current": 0, "unavailable": 0, "failed": 0}
        for job_id in jobs:
            outcome = self._one(job_id, dry=dry, force=force)
            counts[outcome] += 1
        summary = ", ".join(f"{k} {v}" for k, v in counts.items() if v)
        self.stdout.write(self.style.SUCCESS(f"{len(jobs)} agent jobs: {summary or 'nothing to do'}"))

    def _jobs(self, selectors: list[str]) -> list[uuid.UUID]:
        base = GenerationJob.objects.filter(mode=GenerationJob.Mode.AGENT, status=GenerationJob.Status.COMPLETED)
        if not selectors:
            return list(base.order_by("created_at").values_list("id", flat=True))
        ids: list[uuid.UUID] = []
        for selector in selectors:
            try:
                ids.append(base.get(id=uuid.UUID(selector)).id)
                continue
            except (ValueError, GenerationJob.DoesNotExist):
                pass
            matches = [i for i in base.values_list("id", flat=True) if str(i).startswith(selector.lower())]
            if len(matches) != 1:
                msg = f"--job {selector}: {'no' if not matches else 'more than one'} completed agent job matches"
                raise CommandError(msg)
            ids.append(matches[0])
        return ids

    def _one(self, job_id: uuid.UUID, *, dry: bool, force: bool) -> str:
        label = str(job_id)[:8]
        job = GenerationJob.objects.get(id=job_id)
        agent = (job.result_data or {}).get("agent")
        if not isinstance(agent, dict):
            self.stdout.write(f"{label}  no agent section: skipped")
            return "unavailable"
        stored = agent.get("trajectory") or {}
        if not force and stored.get("parser_version") == traj.PARSER_VERSION:
            self.stdout.write(f"{label}  record is current (parser {traj.PARSER_VERSION})")
            return "current"
        record, outcome = self._compute(job, label)
        if record is None:
            return outcome
        if dry:
            return "would_update"
        with transaction.atomic():
            # Re-read under a row lock: only the derived keys are merged into the current row.
            locked = GenerationJob.objects.select_for_update().get(id=job_id)
            if not isinstance((locked.result_data or {}).get("agent"), dict):
                return "unavailable"
            traj.apply_record(locked, record)
            locked.save(update_fields=["result_data", "metrics"])
        return "updated"

    def _compute(self, job: GenerationJob, label: str) -> tuple[dict | None, str]:
        try:
            record = traj.record_from_disk(job)
        except traj.RunRecordUnavailable as exc:
            self.stdout.write(self.style.WARNING(f"{label}  run record unavailable: {exc}"))
            return None, "unavailable"
        except Exception as exc:  # noqa: BLE001 - one bad record must not stop the backfill
            self.stdout.write(self.style.ERROR(f"{label}  failed: {type(exc).__name__}: {exc}"))
            return None, "failed"
        behaviour = traj.behaviour_summary(record)
        agent = (job.result_data or {}).get("agent") or {}
        self.stdout.write(
            f"{label}  {agent.get('harness')}: actions {behaviour['actions_total']} "
            f"(gateway tool calls {behaviour['tool_calls_gateway']}), failed {behaviour['failed_actions']}, "
            f"builds {behaviour['builds']}, context {behaviour['context_tokens_first']}→"
            f"{behaviour['context_tokens_max']}"
            + (f", list cost ${record['list_cost']['list_cost']:.4f}" if record.get("list_cost") else ""),
        )
        return record, "computed"
