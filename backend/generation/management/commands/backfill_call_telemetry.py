"""Backfill ``GenerationArtifact.telemetry`` (per-call timing, provider, tokens, OpenRouter stats).

``--from-logs``: agent calls from the run record's gateway log
(``GENERATION_RUNS_ROOT/<job>/llm_calls.jsonl.gz``, checked against the
sha256 recorded in ``result_data.agent.run_record``), matched to the
``agent_call_NNNN`` artifacts by seq and response id, plus the proxy's
``LLMRequest`` timings for local/proxied calls; template-mode artifacts (and
agent jobs whose log is gone) from their stored ``response_payload``, and
for finished template jobs the served-summary metrics keys they lack
(``llm_calls``, ``providers``, ``reasoning_tokens``, ``cached_tokens``).
``--openrouter``: OpenRouter's ``/api/v1/generation`` record for every
``gen-`` call without one, with each job owner's key (throttled).
``--rederive``: recompute the derived OpenRouter keys (``decode_tps``,
``fallbacks``, ``router_retries``) of stored stats from their measured fields
(no network; ``openrouter_stats.derived``).

Merges are additive: existing telemetry values are never overwritten (only
``--rederive`` replaces the platform's own derived keys, never a fetched one).

    manage.py backfill_call_telemetry --from-logs --openrouter --dry-run
    manage.py backfill_call_telemetry --from-logs --job <uuid> --job <uuid>
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter
from datetime import datetime
from datetime import time as dt_time
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.dateparse import parse_datetime

from backend.generation.models import GenerationArtifact
from backend.generation.models import GenerationJob
from backend.generation.services import call_telemetry
from backend.generation.services import openrouter_stats
from backend.generation.services.agent.runner import run_record_dir
from backend.generation.services.metrics import served_summary

LOG_NAME = "llm_calls.jsonl.gz"
TEMPLATE_MODES = frozenset({GenerationJob.Mode.CUSTOM, GenerationJob.Mode.SCAFFOLDING})
FINISHED = frozenset({GenerationJob.Status.COMPLETED, GenerationJob.Status.FAILED, GenerationJob.Status.CANCELLED})


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        day = parse_date(value)
        if day is None:
            msg = f"--since: not a date or datetime: {value!r}"
            raise CommandError(msg)
        parsed = datetime.combine(day, dt_time.min)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def load_gateway_calls(job: GenerationJob, run_record: dict[str, Any] | None) -> tuple[list[dict] | None, str]:
    """The job's gateway call lines, or ``(None, reason)`` when the log is missing or fails its sha256."""
    candidates = [run_record_dir(job) / LOG_NAME]
    recorded_dir = (run_record or {}).get("_dir")
    if recorded_dir:
        candidates.append(Path(recorded_dir) / LOG_NAME)
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        return None, "log_missing"
    data = path.read_bytes()
    expected = ((run_record or {}).get(LOG_NAME) or {}).get("sha256")
    if expected and hashlib.sha256(data).hexdigest() != expected:
        return None, "sha256_mismatch"
    lines = []
    for raw in gzip.decompress(data).splitlines():
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if isinstance(line, dict):
            lines.append(line)
    return lines, "ok"


class Command(BaseCommand):
    help = "Backfill per-call telemetry on generation artifacts (from run logs and/or OpenRouter generation stats)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--job", action="append", default=[], help="Job UUID (repeatable).")
        parser.add_argument("--since", help="Only jobs (artifacts for --openrouter) created on/after this date.")
        parser.add_argument("--from-logs", action="store_true", help="Fill telemetry from run logs / payloads.")
        parser.add_argument("--openrouter", action="store_true", help="Fetch OpenRouter stats for gen- calls.")
        parser.add_argument(
            "--rederive",
            action="store_true",
            help="Recompute derived OpenRouter keys (decode_tps, fallbacks, router_retries) of stored stats.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="At most N jobs (--from-logs) and N OpenRouter fetches (--openrouter).",
        )
        parser.add_argument("--dry-run", action="store_true", help="Report what would change; write nothing.")

    def handle(self, *args, **options) -> None:
        if not (options["from_logs"] or options["openrouter"] or options["rederive"]):
            msg = "choose --from-logs, --openrouter and/or --rederive"
            raise CommandError(msg)
        since = _parse_since(options.get("since"))
        job_ids = options["job"] or None
        if job_ids:
            for jid in job_ids:
                try:
                    GenerationJob.objects.filter(pk=jid).exists()
                except Exception as exc:  # malformed UUID
                    msg = f"--job: invalid id {jid!r}"
                    raise CommandError(msg) from exc
        dry = options["dry_run"]
        if options["from_logs"]:
            counts = self.from_logs(job_ids, since, options["limit"], dry)
            self.stdout.write(f"from-logs{' (dry run)' if dry else ''}: {dict(sorted(counts.items()))}")
        if options["openrouter"]:
            counts = self.openrouter(job_ids, since, options["limit"], dry)
            self.stdout.write(f"openrouter{' (dry run)' if dry else ''}: {dict(sorted(counts.items()))}")
        if options["rederive"]:
            counts = Counter(
                openrouter_stats.rederive_stored(artifact_id, dry_run=dry)
                for artifact_id in openrouter_stats.candidate_artifacts(job_ids, since).values_list("id", flat=True)
            )
            self.stdout.write(f"rederive{' (dry run)' if dry else ''}: {dict(sorted(counts.items()))}")

    # -- from logs / payloads ---------------------------------------------

    def from_logs(self, job_ids, since, limit, dry: bool) -> Counter:
        counts: Counter = Counter()
        jobs = GenerationJob.objects.filter(artifacts__isnull=False).distinct()
        if job_ids:
            jobs = jobs.filter(pk__in=job_ids)
        if since:
            jobs = jobs.filter(created_at__gte=since)
        jobs = jobs.order_by("created_at").only("id", "attempt", "mode", "status")
        if limit is not None:
            jobs = jobs[:limit]
        for job in jobs:
            counts["jobs"] += 1
            for key, value in self._job_from_logs(job, dry).items():
                counts[key] += value
            if job.mode in TEMPLATE_MODES:
                counts[self._template_metrics(job, dry)] += 1
        return counts

    @staticmethod
    def _template_metrics(job: GenerationJob, dry: bool) -> str:
        """Add the served summary keys (llm_calls, providers, reasoning/cached tokens) a template job lacks.

        Only keys the job's metrics do not have yet; jobs still running are skipped.
        """
        if job.status not in FINISHED:
            return "template_metrics_skipped_running"
        metrics = GenerationJob.objects.filter(pk=job.pk).values_list("metrics", flat=True).first()
        metrics = metrics if isinstance(metrics, dict) else {}
        if not metrics:
            return "template_metrics_skipped_empty"
        calls = [
            (stage, payload if isinstance(payload, dict) and payload.get("choices") else None)
            for stage, payload in GenerationArtifact.objects.filter(job=job)
            .order_by("id")
            .values_list("stage", "response_payload")
        ]
        summary = {k: v for k, v in served_summary(calls).items() if v is not None and k not in metrics}
        if not summary:
            return "template_metrics_unchanged"
        if not dry:
            GenerationJob.objects.filter(pk=job.pk).update(metrics={**metrics, **summary})
        return "template_metrics_updated"

    def _job_from_logs(self, job: GenerationJob, dry: bool) -> Counter:
        counts: Counter = Counter()
        artifacts = list(
            GenerationArtifact.objects.filter(job=job)
            .order_by("id")
            .values("id", "stage", "telemetry", "response_payload"),
        )
        by_seq: dict[int, dict] = {}
        links: dict[int, Any] = {}
        if any(a["stage"].startswith("agent_call_") for a in artifacts):
            run_record = (
                GenerationJob.objects.filter(pk=job.pk)
                .values_list("result_data__agent__run_record", flat=True)
                .first()
            )
            lines, status = load_gateway_calls(job, run_record if isinstance(run_record, dict) else None)
            counts[f"agent_log_{status}"] += 1
            if lines:
                by_seq = {int(line.get("seq") or 0): line for line in lines}
                proxied = call_telemetry.proxy_requests(job)
                links = call_telemetry.link_llm_requests(lines, proxied) if proxied else {}
        for artifact in artifacts:
            stage = artifact["stage"]
            payload = artifact["response_payload"] if isinstance(artifact["response_payload"], dict) else {}
            seq = call_telemetry.call_seq(stage)
            line = by_seq.get(seq) if seq is not None else None
            if line is not None:
                line_id = (line.get("response") or {}).get("id")
                if line_id and payload.get("id") and line_id != payload.get("id"):
                    counts["id_mismatch"] += 1
                    line = None
            if line is not None:
                new = call_telemetry.from_gateway_call(line)
                row = links.get(seq)
                if row is not None:
                    new["local"] = call_telemetry.local_from_llm_request(row)
                    counts["linked_llm_request"] += 1
                counts["from_log"] += 1
            else:
                new = call_telemetry.from_artifact_payload(stage, payload)
                counts["from_payload"] += 1
            merged = call_telemetry.merge_additive(artifact["telemetry"], new)
            if merged == (artifact["telemetry"] or {}):
                counts["unchanged"] += 1
                continue
            counts["updated"] += 1
            if not dry:
                GenerationArtifact.objects.filter(pk=artifact["id"]).update(telemetry=merged)
        return counts

    # -- OpenRouter generation stats --------------------------------------

    def openrouter(self, job_ids, since, limit, dry: bool) -> Counter:
        from backend.credentials.services.resolver import MissingApiKeyError
        from backend.credentials.services.resolver import get_openrouter_key

        counts: Counter = Counter()
        rows = list(
            openrouter_stats.candidate_artifacts(job_ids, since).values_list(
                "id",
                "response_payload__id",
                "telemetry",
                "job__created_by_id",
            ),
        )
        by_owner: dict[Any, list[tuple]] = {}
        for artifact_id, gen_id, telemetry, owner_id in rows:
            by_owner.setdefault(owner_id, []).append((artifact_id, gen_id, telemetry))
        from backend.users.models import User

        throttle = openrouter_stats.Throttle()
        remaining = limit
        for owner_id, owner_rows in by_owner.items():
            if remaining is not None and remaining <= 0:
                counts["over_limit"] += sum(1 for _, _, t in owner_rows if openrouter_stats.needs_stats(t))
                continue
            user = User.objects.filter(pk=owner_id).first()
            try:
                api_key = "" if dry else get_openrouter_key(user)
            except MissingApiKeyError:
                counts["no_key"] += len(owner_rows)
                continue
            try:
                result = openrouter_stats.fetch_for_artifacts(
                    owner_rows,
                    api_key,
                    dry_run=dry,
                    throttle=throttle,
                    limit=remaining,
                )
            except openrouter_stats.StatsAuthError as exc:
                self.stderr.write(f"owner {owner_id}: {exc}")
                counts["key_rejected"] += len(owner_rows)
                continue
            counts.update(result)
            if remaining is not None:
                remaining -= result.get("fetched", 0)
        return counts
