"""Execute an agent-mode job: sandbox run → evidence → result_data/metrics.

Outcome semantics (what an analysis can rely on):

* A run that made at least one successful LLM call is a **sample**, whatever
  way it ended. ``result_data.agent.termination`` says how: ``completed``
  (the agent stopped by itself), ``timeout``, ``budget_exhausted``,
  ``resource_limit`` (killed at the sandbox memory cap), ``harness_killed``
  (the agent's own commands signalled the harness) or ``harness_error``.
  Dropping timeouts/budget stops would bias comparisons toward models that
  finish quickly, so these jobs are COMPLETED, not FAILED.
* A run with no successful LLM call (bad key, no provider matching the
  routing policy, sandbox failure) is an infrastructure failure: FAILED, with
  the upstream error surfaced.
* A run whose last LLM call(s) failed transiently (408/429/5xx after the
  gateway's own retries) and that then stopped is *infra-interrupted*: retried
  as a new attempt; when retries run out it stays COMPLETED but invalid.
* ``budget_exhausted`` also covers a harness that stopped at its own step
  limit (mini-swe-agent's ``LimitsExceeded``, step_limit = max_llm_requests).
* ``metrics.cost``/tokens sum every call the gateway accounted, failed ones
  included (``metrics.failed_call_cost`` is the failed share).

The gateway's call log is the authoritative LLM record (tokens, cost, served
provider, every request); the harness's own report (event stream,
trajectory, chat log) is kept as its self-report. Both, plus the patch and
the final workspace, are written under ``GENERATION_RUNS_ROOT/<job_id>/``
with sha256s in the job row.

After the run the platform evaluates
the sample (``services/evaluation``): build, run without egress, hidden
conformance suite. ``result_data.agent.validity`` lists everything an
analysis should know before pooling the sample (limits that are not
model-neutral, params a model could not take, evaluation infrastructure
errors, ...).

The executor (``backend.execution``) admits runs in each lane's frozen
``sequence`` order, so the order an experiment shuffles its jobs into is the
order they actually run in. :meth:`AgentRunner.run_stage` is the sandbox
stage; the evaluation stage runs later in the executor's eval pool, after the
sandbox slot is released (``GenerationService.evaluate``).
"""

from __future__ import annotations

import ast
import gzip
import hashlib
import json
import logging
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone

from backend.generation.models import GenerationArtifact
from backend.generation.models import GenerationJob
from backend.generation.services import result_writer
from backend.generation.services.agent.harnesses import get_harness
from backend.generation.services.agent.sandbox import AgentSandbox
from backend.generation.services.agent.sandbox import SandboxError
from backend.generation.services.agent.sandbox import tar_bytes
from backend.generation.services.agent.snapshot import AGENT_SNAPSHOT_SCHEMA_VERSION
from backend.generation.services.agent.snapshot import apply_agent_snapshot_to_job
from backend.generation.services.agent.workspace import collect
from backend.generation.services.agent.workspace import seed_files
from backend.generation.services.generation_validation import ValidationResult
from backend.generation.services.generation_validation import validate_frontend_files
from backend.generation.services.metrics import JobMetrics
from backend.runtime.services.scaffolding import get_stack_config

logger = logging.getLogger(__name__)

RESULT_SCHEMA_VERSION = 2


class AgentRunError(Exception):
    """An agent job could not produce a sample (infrastructure / configuration).

    ``infra`` marks failures of the platform rather than of the configuration
    (apps daemon, gateway, transport errors with no successful LLM call): the
    executor retries those as a new attempt with the same snapshot and seed.
    """

    def __init__(self, message: str, *, remediation: str | None = None, infra: bool = False) -> None:
        super().__init__(message)
        self.remediation = remediation
        self.infra = infra

    def display(self) -> str:
        return f"{self} {self.remediation}" if self.remediation else str(self)


class AgentRunCancelled(Exception):  # noqa: N818 - control flow, not an error
    """The job was cancelled before or during its run."""


def _is_cancelled(job: GenerationJob) -> bool:
    return (
        GenerationJob.objects.filter(id=job.id).values_list("status", flat=True).first()
        == GenerationJob.Status.CANCELLED
    )


class _Heartbeat:
    """Cancellation check that also touches ``updated_at`` once a minute.

    Lease renewal is the executor heartbeat's job; touching ``updated_at``
    keeps the row visibly alive for list views and maintenance.
    """

    def __init__(self, job: GenerationJob, every_seconds: float = 60) -> None:
        self.job = job
        self.every = every_seconds
        self.last = time.monotonic()

    def __call__(self) -> bool:
        if time.monotonic() - self.last >= self.every:
            self.last = time.monotonic()
            GenerationJob.objects.filter(id=self.job.id).update(updated_at=timezone.now())
        # Cancelled, requeued (another attempt) or reclaimed: this sandbox no longer owns the job.
        return not GenerationJob.objects.filter(
            id=self.job.id,
            status=GenerationJob.Status.RUNNING,
            attempt=self.job.attempt,
            lease_owner=self.job.lease_owner or "",
        ).exists()


def _jsonl(data: bytes | None) -> list[dict]:
    rows = []
    for line in (data or b"").decode("utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _truncate(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"... [{len(value) - limit} chars truncated]"
    return value


class AgentRunner:
    def __init__(self, job: GenerationJob) -> None:
        self.job = job

    def run_stage(self) -> str:
        """The sandbox stage: run the harness, record the evidence; returns the stage outcome.

        ``"evaluate"`` (``AGENT_AUTO_EVALUATE``: the sandbox slot is released and
        the eval pool grades the sample), ``"done"`` or ``"infra_interrupted"``: a
        local-endpoint run after the proxy returned an infra error, or any run
        that ended on LLM calls failing transiently after the gateway's retries
        (``result_data.agent.ended_on_llm_error``).
        Raises ``AgentRunError`` (``infra=True`` for platform failures),
        ``AgentRunCancelled`` or ``LeaseLost``.
        """
        from backend.execution.transitions import OUTCOME_DONE
        from backend.execution.transitions import OUTCOME_EVALUATE
        from backend.execution.transitions import OUTCOME_INFRA_INTERRUPTED
        from backend.execution.transitions import guarded_save
        from backend.inference.access import LLMAccessError
        from backend.inference.gateway_wiring import for_job

        job = self.job
        snapshot = job.resolved_bundle if isinstance(job.resolved_bundle, dict) else {}
        if snapshot.get("mode") != "agent":
            apply_agent_snapshot_to_job(job)
            snapshot = job.resolved_bundle
        if int(snapshot.get("bundle_schema_version") or 0) < AGENT_SNAPSHOT_SCHEMA_VERSION:
            msg = "This job was frozen by an earlier agent-mode version and cannot be replayed exactly."
            raise AgentRunError(msg, remediation="Use Retry to re-create it under the current procedure.")
        try:
            wiring = for_job(job, snapshot)
        except LLMAccessError as exc:
            raise AgentRunError(str(exc), remediation=exc.remediation, infra=exc.retryable) from exc
        if _is_cancelled(job):
            raise AgentRunCancelled

        self._progress("running")
        started_at = time.time()
        seed = seed_files(job)
        sandbox = AgentSandbox(str(job.id), job.attempt, wiring=wiring, snapshot=snapshot)
        try:
            result = sandbox.run(
                seed=seed,
                brief=snapshot["brief"]["text"],
                is_cancelled=_Heartbeat(job),
            )
        except SandboxError as exc:
            raise AgentRunError(
                str(exc),
                remediation="Check the apps daemon (apps-dind) and server logs.",
                infra=True,
            ) from exc
        except Exception as exc:
            logger.exception("Agent sandbox for job %s failed", job.id)
            msg = f"Agent sandbox failed: {exc}"
            raise AgentRunError(msg, remediation="Check that the apps daemon is reachable.", infra=True) from exc

        if result.termination == "cancelled":
            raise AgentRunCancelled
        self._record(snapshot, seed, result)
        # _record replaces result_data wholesale: the schedule goes in after it.
        execution = self._execution()
        job.result_data["agent"]["schedule"] = {
            "run_order": execution.get("started_seq"),
            "sequence": job.sequence,
            "attempt": job.attempt,
            "queued_at": execution.get("queued_at"),
            "claimed_at": execution.get("claimed_at"),
            "started_at": started_at,
            "queue_seconds": execution.get("queue_seconds"),
            "prefetch_load_s": execution.get("prefetch_load_s"),
        }
        job.result_data["agent"]["validity"] = assess_validity(
            job.resolved_bundle,
            job.result_data,
            execution=execution,
            attempt=job.attempt,
        )
        guarded_save(job, ["result_data", "metrics", "resolved_bundle", "prompt_hash", "bundle_key"])
        if snapshot.get("endpoint") and execution.get("infra_errors"):
            job.error_message = "; ".join(
                f"{e.get('code')} ({e.get('status')})" for e in execution["infra_errors"][-3:]
            )
            return OUTCOME_INFRA_INTERRUPTED
        # Any endpoint (OpenRouter included): the run ended on LLM calls that
        # failed after the gateway's own retries (outage, rate limit), so how
        # far the agent got says nothing about the model.
        ended = job.result_data["agent"].get("ended_on_llm_error")
        if ended:
            job.error_message = (
                f"the run ended after {ended['trailing_failures']} failed LLM call(s) "
                f"(HTTP {ended.get('status')}{', ' + ended['code'] if ended.get('code') else ''})"
            )
            return OUTCOME_INFRA_INTERRUPTED
        return OUTCOME_EVALUATE if settings.AGENT_AUTO_EVALUATE else OUTCOME_DONE

    def execute(self) -> None:
        """Sandbox stage then (``AGENT_AUTO_EVALUATE``) the evaluation, inline: the shell / test path."""
        from backend.execution.transitions import OUTCOME_EVALUATE

        if self.run_stage() == OUTCOME_EVALUATE:
            self._progress("evaluating")
            self._evaluate()
            self.job.result_data["agent"]["validity"] = assess_validity(
                self.job.resolved_bundle,
                self.job.result_data,
                execution=self._execution(),
                attempt=self.job.attempt,
            )

    def _execution(self) -> dict[str, Any]:
        """The job's live ``execution`` record (the proxy appends infra errors to it while the run is going)."""
        execution = execution_record(self.job)
        self.job.execution = {k: v for k, v in execution.items() if k != "relay_interruptions"}
        return execution

    def _evaluate(self) -> None:
        from backend.execution.transitions import guarded_save
        from backend.generation.services.evaluation.runner import evaluate_job

        # Persist the sample first: evaluation builds from result_data.files.
        guarded_save(self.job, ["result_data", "metrics"])
        evaluate_job(self.job, save=guarded_save)

    # -- evidence --------------------------------------------------------

    def _record(self, snapshot: dict[str, Any], seed: dict[str, bytes], result) -> None:
        job = self.job
        calls = _jsonl(result.gateway_logs.get("calls.jsonl"))
        gateway_events = _jsonl(result.gateway_logs.get("gateway_events.jsonl"))
        ok_calls = [c for c in calls if c.get("status") == 200 and not (c.get("response") or {}).get("error")]
        if not ok_calls:
            raise AgentRunError(
                self._first_llm_error(calls, result),
                remediation=self._remediation(calls),
                infra=_transport_failure(calls),
            )

        adapter = get_harness(snapshot["harness"]["name"])
        hsum = adapter.parse_report(result.outputs)

        budget_events = [e for e in gateway_events if e.get("event") == "budget_exhausted"]
        budget_reason = budget_events[0].get("reason") if budget_events else None
        termination = result.termination
        if budget_events and termination in ("completed", "harness_error"):
            termination = "budget_exhausted"
        elif hsum.exit_status in HARNESS_LIMIT_STATUSES and termination in ("completed", "harness_error"):
            # The harness stopped itself at its step limit (mini's step_limit =
            # max_llm_requests) before the gateway ever had to refuse a request.
            termination = "budget_exhausted"
            budget_reason = "max_requests"
        ended_on_llm_error = _ended_on_llm_error(calls, termination)
        exit_info = _safe_json(result.outputs.get("exit.json"))

        stack_slug = snapshot["scaffolding_slug"]
        stack_config = get_stack_config(stack_slug)
        protected = tuple(snapshot["brief"]["protected_files"])
        ws = collect(
            result.workspace,
            seed,
            protected=protected,
            has_frontend=bool(stack_config.get("has_frontend")),
            artifacts=adapter.workspace_artifacts,
        )
        files = ws.files

        py_validation = validate_python_sources(files)
        fe_validation = validate_frontend_files(files) if stack_config.get("has_frontend") else None
        validation_passed = py_validation.passed and (fe_validation is None or fe_validation.passed)

        # Spend over every call the gateway accounted (a failed call can still be billed).
        failed_calls = [c for c in calls if _call_failed(c)]
        usage = self._usage(calls)
        failed_call_cost = self._usage(failed_calls)["cost"]
        providers = Counter((c.get("response") or {}).get("provider") or "unknown" for c in ok_calls)
        served_models = Counter((c.get("response") or {}).get("model") or "unknown" for c in ok_calls)
        policy_overrides = Counter(key for c in calls for key in (c.get("policy_diff") or {}) if key == "model")
        run_record = self._write_run_record(result, ws)

        entry = stack_config.get("backend_filename", "app.py")
        component = f"frontend/src/{stack_config.get('frontend_component', 'App.jsx')}"
        loc = sum(text.count("\n") + 1 for text in files.values())
        agent_section = {
            "harness": snapshot["harness"]["name"],
            "harness_version": snapshot["harness"]["version"],
            "termination": termination,
            "exit_code": result.exit_code,
            "oom_killed": result.oom_killed,
            "wall_seconds": result.wall_seconds,
            "agent_seconds": exit_info.get("agent_seconds"),
            "budget": {
                k: snapshot["agent"].get(k)
                for k in (
                    "wall_clock_seconds",
                    "max_llm_requests",
                    "max_total_tokens",
                    "max_cost_usd",
                    "sandbox_mem_limit",
                )
            },
            "budget_exhausted": budget_reason,
            "network_policy": snapshot["agent"]["network_policy"],
            "egress": {
                "allowed_connects": sum(1 for e in gateway_events if e.get("event") == "connect"),
                "denied_connects": sorted(
                    {e.get("target", "") for e in gateway_events if e.get("event") == "rejected_connect"},
                ),
                "denied_paths": sorted(
                    {e.get("path", "") for e in gateway_events if e.get("event") == "rejected_path"},
                ),
            },
            "llm_calls": len(calls),
            "llm_calls_failed": len(calls) - len(ok_calls),
            "llm_calls_failed_transient": sum(1 for c in failed_calls if _is_transient_failure(c)),
            "failed_call_cost": failed_call_cost,
            "ended_on_llm_error": ended_on_llm_error,
            "llm_seconds": round(sum(float(c.get("duration_s") or 0) for c in calls), 2),
            # Responses cut off at the per-call output cap (same cap for every
            # harness; single-turn harnesses are most exposed to it).
            "truncated_calls": sum(1 for c in ok_calls if (c.get("response") or {}).get("finish_reason") == "length"),
            # The run ended on a cut-off response (its last successful call hit
            # the cap and the agent stopped by itself right after): the final
            # state may be truncated, unlike a mid-run cut-off the agent recovers from.
            "final_response_truncated": _final_response_truncated(ok_calls, termination),
            "upstream_retries": sum(int(c.get("upstream_retries") or 0) for c in calls),
            "sampling_policy": snapshot["gateway"].get("sampling_policy"),
            "harness_sampling_stripped": dict(
                Counter(k for c in calls for k in ((c.get("policy_diff") or {}).get("stripped") or {})),
            ),
            "providers": dict(providers),
            "served_models": dict(served_models),
            "harness_policy_overrides": dict(policy_overrides),
            "harness_report": hsum.to_dict(),
            "workspace": {
                "origins": ws.counts(),
                "manifest": ws.manifest,
                "skipped": ws.skipped[:200],
                "deleted_scaffold_files": ws.deleted_scaffold_files,
                "protected_modified": ws.protected_modified,
            },
            "diffstat": result.outputs.get("diffstat.txt", b"").decode("utf-8", errors="replace")[-20000:],
            "images": result.images,
            "run_record": run_record,
        }
        job.result_data = {
            "result_schema_version": RESULT_SCHEMA_VERSION,
            # Runtime writes these verbatim over the pinned skeleton (no entry
            # renaming or code patching): what is built is what the agent wrote.
            "materialize": "verbatim",
            "files": files,
            "backend_entry": entry,
            "backend_code": files.get(entry, ""),
            "frontend_code": files.get(component, ""),
            "backend_truncated": False,
            "frontend_truncated": False,
            "validation": {
                "passed": validation_passed,
                "python": py_validation.to_dict(),
                "frontend": fe_validation.to_dict() if fe_validation else None,
            },
            "agent": agent_section,
        }
        job.metrics = JobMetrics(
            **usage,
            duration_seconds=result.wall_seconds,
            total_duration=result.wall_seconds,
            model=snapshot["llm"].get("model_slug", ""),
            lines_of_code=loc,
            engine=f"{snapshot['harness']['name']}@{snapshot['harness']['version']}",
            validation_passed=validation_passed,
            llm_calls=len(calls),
            harness_steps=hsum.steps,
            tool_calls=sum(hsum.tool_calls.values()),
            termination=termination,
            providers=dict(providers),
        ).dump()
        # Part of ``cost`` already; kept apart so billed-but-failed spend is visible.
        job.metrics["failed_call_cost"] = failed_call_cost
        self._write_call_artifacts(calls)

    @staticmethod
    def _usage(calls: list[dict]) -> dict[str, Any]:
        totals = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost": 0.0,
            "reasoning_tokens": 0,
            "cached_tokens": 0,
        }
        for call in calls:
            u = (call.get("response") or {}).get("usage") or {}
            totals["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
            totals["completion_tokens"] += int(u.get("completion_tokens") or 0)
            totals["cost"] += float(u.get("cost") or 0.0)
            totals["reasoning_tokens"] += int((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
            totals["cached_tokens"] += int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        totals["total_tokens"] = totals["prompt_tokens"] + totals["completion_tokens"]
        totals["cost"] = round(totals["cost"], 8)
        return totals

    @staticmethod
    def _first_llm_error(calls: list[dict], result) -> str:
        for call in calls:
            resp = call.get("response") or {}
            err = resp.get("error") or call.get("error")
            if err:
                message = err.get("message") if isinstance(err, dict) else str(err)
                return f"No successful LLM call (HTTP {call.get('status')}): {message}"[:2000]
        if not calls:
            tail = result.outputs.get("harness.log", b"").decode("utf-8", errors="replace")[-1500:]
            return f"The agent made no LLM calls (exit code {result.exit_code}). Harness log: {tail}"
        return "No successful LLM call"

    @staticmethod
    def _remediation(calls: list[dict]) -> str:
        statuses = {c.get("status") for c in calls}
        if 401 in statuses or 403 in statuses:
            return "Check the OpenRouter API key in Settings → API keys."
        if 404 in statuses:
            return "No provider serves this model under the run's routing policy; relax llm_params.provider."
        if 402 in statuses:
            return "The OpenRouter account has insufficient credits."
        return "See the job's LLM call log for details."

    def _write_run_record(self, result, ws) -> dict[str, Any]:
        """Persist the full evidence on disk; return {name: {path, sha256, bytes}}.

        Attempt 0 writes under ``<job_id>/``; a retried attempt under
        ``<job_id>/a<attempt>/``, so the evidence of each attempt is kept.
        """
        root = run_record_dir(self.job)
        root.mkdir(parents=True, exist_ok=True)
        blobs = {
            "llm_calls.jsonl.gz": gzip.compress(result.gateway_logs.get("calls.jsonl", b"")),
            "gateway_events.jsonl": result.gateway_logs.get("gateway_events.jsonl", b""),
            "harness_events.jsonl.gz": gzip.compress(result.outputs.get("events.jsonl", b"")),
            "harness.log": result.outputs.get("harness.log", b"")[-2_000_000:],
            "harness_report.tar.gz": tar_bytes(
                {k: v for k, v in result.outputs.items() if k not in ("events.jsonl", "changes.patch", "brief.md")},
            ),
            "changes.patch": result.outputs.get("changes.patch", b""),
            "name_status.txt": result.outputs.get("name_status.txt", b""),
            "workspace.tar.gz": tar_bytes(result.workspace),
            "container.log": result.container_logs_tail.encode("utf-8"),
        }
        record: dict[str, Any] = {}
        for name, data in blobs.items():
            (root / name).write_bytes(data)
            record[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        record["_dir"] = str(root)
        record["_skipped_files"] = len(ws.skipped)
        return record

    def _write_call_artifacts(self, calls: list[dict]) -> None:
        """One compact artifact per LLM call (full requests stay in the run record)."""
        rows = []
        for call in calls:
            request = call.get("request") or {}
            response = call.get("response") or {}
            usage = response.get("usage") or {}
            rows.append(
                GenerationArtifact(
                    job=self.job,
                    stage=f"agent_call_{int(call.get('seq') or 0):04d}",
                    request_payload={
                        "model": request.get("model"),
                        "params": {k: v for k, v in request.items() if k not in ("messages", "tools")},
                        "message_count": len(request.get("messages") or []),
                        "tool_count": len(request.get("tools") or []),
                        "last_message": _truncate(
                            json.dumps((request.get("messages") or [None])[-1], default=str),
                            4000,
                        ),
                        "policy_diff": call.get("policy_diff"),
                    },
                    response_payload={
                        "status": call.get("status"),
                        "duration_s": call.get("duration_s"),
                        "id": response.get("id"),
                        "provider": response.get("provider"),
                        "model": response.get("model"),
                        "finish_reason": response.get("finish_reason"),
                        "content": _truncate(response.get("content"), 8000),
                        "reasoning": _truncate(response.get("reasoning"), 4000),
                        "tool_calls": [
                            {**tc, "arguments": _truncate(tc.get("arguments"), 4000)}
                            for tc in response.get("tool_calls") or []
                        ],
                        "usage": usage,
                        "error": response.get("error") or call.get("error"),
                    },
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    total_cost=float(usage.get("cost") or 0.0),
                ),
            )
        GenerationArtifact.objects.bulk_create(rows, batch_size=200)

    def _progress(self, phase: str) -> None:
        result_writer.publish_phase(self.job, phase)


def execution_record(job: GenerationJob) -> dict[str, Any]:
    """``job``'s live ``execution`` record plus ``relay_interruptions`` (what :func:`assess_validity` reads).

    Read from the row, not the instance: the proxy appends infra errors and
    the chat client adds ``llm_queue_wait_s`` while the run is going.
    ``relay_interruptions`` counts this attempt's LLM requests that were
    re-sent after a relay lease was lost.
    """
    from backend.inference.models import LLMRequest

    row = GenerationJob.objects.filter(id=job.id).values_list("execution", flat=True).first()
    execution = dict(row) if isinstance(row, dict) else {}
    interruptions = LLMRequest.objects.filter(job_id=job.id, job_attempt=job.attempt, attempt__gt=0).count()
    if interruptions:
        execution["relay_interruptions"] = interruptions
    return execution


def run_record_dir(job: GenerationJob) -> Path:
    """Where the run evidence of ``job``'s current attempt lives."""
    root = Path(settings.GENERATION_RUNS_ROOT) / str(job.id)
    return root / f"a{job.attempt}" if job.attempt else root


# Transport failures (gateway upstream error, proxy unavailable / wait, relay lease expiry):
# with no successful call they say nothing about the model, so the attempt is retried.
# Mirrors the gateway's RETRY_STATUSES: rate limits (429), request timeouts (408)
# and 5xx are what it already retried before giving the failure to the harness.
_TRANSPORT_STATUSES = {408, 429, 500, 502, 503, 504}
_TRANSPORT_CODES = {"upstream_error", "endpoint_unavailable", "proxy_wait", "lease_expired", "unavailable", "timeout"}
# Harness self-reported exit statuses that mean "stopped at its own step limit".
HARNESS_LIMIT_STATUSES = frozenset({"LimitsExceeded"})
# Terminations that a trailing LLM failure can explain (the harness gave up on
# the error). Timeouts, budget and memory stops end the run for their own reason.
_LLM_ERROR_TERMINATIONS = frozenset({"completed", "harness_error"})
# A run where at least this share of calls failed transiently is flagged even
# when it did not end on a failure.
FAILED_CALL_WARN_SHARE = 0.25


def _call_failed(call: dict) -> bool:
    return call.get("status") != 200 or bool((call.get("response") or {}).get("error"))


def _is_transient_failure(call: dict) -> bool:
    if not _call_failed(call):
        return False
    err = (call.get("response") or {}).get("error") or call.get("error")
    code = err.get("code") if isinstance(err, dict) else None
    return call.get("status") in _TRANSPORT_STATUSES or code in _TRANSPORT_CODES


def _transport_failure(calls: list[dict]) -> bool:
    for call in calls:
        if not _call_failed(call):
            continue
        return _is_transient_failure(call)
    return False


def _ended_on_llm_error(calls: list[dict], termination: str) -> dict[str, Any] | None:
    """The trailing transient LLM failures a run ended on, or None.

    The run's last call(s) failed after the gateway's retries and the harness
    then stopped by itself (``completed`` / ``harness_error``): an outage or
    rate limit, not the model, ended it.
    """
    if termination not in _LLM_ERROR_TERMINATIONS:
        return None
    ordered = sorted(calls, key=lambda c: int(c.get("seq") or 0))
    trailing = []
    for call in reversed(ordered):
        if not _call_failed(call):
            break
        trailing.append(call)
    if not trailing or not _is_transient_failure(trailing[0]):
        return None
    last = trailing[0]
    err = (last.get("response") or {}).get("error") or last.get("error")
    return {
        "trailing_failures": len(trailing),
        "status": last.get("status"),
        "code": err.get("code") if isinstance(err, dict) else None,
    }


def _final_response_truncated(ok_calls: list[dict], termination: str) -> bool:
    if not ok_calls or termination not in _LLM_ERROR_TERMINATIONS:
        return False
    last = max(ok_calls, key=lambda c: int(c.get("seq") or 0))
    return (last.get("response") or {}).get("finish_reason") == "length"


_STUB_RE = re.compile(r"def\s+\w+\s*\([^)]*\)\s*(?:->[^:]+)?:\s*\n\s+(?:pass|\.\.\.)\s*$", re.MULTILINE)


def validate_python_sources(files: dict[str, str]) -> ValidationResult:
    """Parse every ``.py`` file; flag syntax errors and stub-only bodies.

    Deliberately no length heuristic (the template modes' "100+ lines" rule):
    agents split code into modules, and a size threshold would reward
    verbosity rather than correctness.
    """
    result = ValidationResult()
    for name, code in files.items():
        if not name.endswith(".py"):
            continue
        errors: list[str] = []
        try:
            ast.parse(code, filename=name)
        except SyntaxError as exc:
            errors.append(f"SyntaxError at line {exc.lineno}: {exc.msg}")
        else:
            stubs = len(_STUB_RE.findall(code))
            if stubs > 2:
                errors.append(f"{stubs} stub functions with only pass/...")
        result.add(name, errors)
    return result


# Validity notes. severity: "exclude" = not a valid sample of the condition
# (drop it, fix the cause, re-run); "warn" = valid but not model-neutral or not
# exactly what was configured (stratify or report); "info" = context.
_NON_NEUTRAL_LIMITS = {
    "max_cost_usd": "USD cap reached: expensive models get fewer steps under a cost cap",
    "max_total_tokens": "token cap reached: verbose models get fewer steps under a token cap",
    "max_attempts": "attempt cap reached: the harness kept re-sending failed requests",
}


def assess_validity(
    snapshot: dict[str, Any],
    result: dict[str, Any],
    *,
    execution: dict[str, Any] | None = None,
    attempt: int | None = None,
) -> dict[str, Any]:
    agent = result.get("agent") or {}
    evaluation = result.get("evaluation")
    notes: list[dict[str, str]] = []

    def note(code: str, severity: str, detail: str) -> None:
        notes.append({"code": code, "severity": severity, "detail": detail})

    termination = agent.get("termination")
    reason = agent.get("budget_exhausted")
    if reason in _NON_NEUTRAL_LIMITS:
        note(f"budget:{reason}", "warn", _NON_NEUTRAL_LIMITS[reason])
    elif reason in {"max_llm_requests", "max_requests"}:
        note("budget:max_requests", "info", "request budget reached (the model-neutral limit)")
    if termination == "timeout":
        note("wall_clock", "warn", "wall-clock limit reached: provider latency, not only the model, bounded this run")
    if termination == "resource_limit":
        note("resource_limit", "warn", "killed at the sandbox memory cap")
    if termination == "harness_killed":
        note("harness_killed", "info", "the agent's own commands signalled the harness")
    if termination == "harness_error":
        note("harness_error", "warn", "the harness exited with an error")
    dropped = (snapshot.get("gateway") or {}).get("dropped_params") or {}
    if set(dropped) - {"seed"}:
        note("params_not_applied", "warn", "configured params this model cannot take: " + ", ".join(sorted(dropped)))
    elif dropped:
        # Every run has a seed, so a model without seed support drops it on
        # every run: worth recording, not a reason to doubt the sample.
        note("params_not_applied", "info", "this model takes no seed: the run is not seed-reproducible")
    requested = (snapshot.get("llm") or {}).get("model_slug")
    served = [m for m in agent.get("served_models") or {} if m != requested]
    if served:
        note("served_model_differs", "warn", "served model id differs from the requested one: " + ", ".join(served))
    if agent.get("truncated_calls"):
        note(
            "output_truncated",
            "warn",
            f"{agent['truncated_calls']} model responses hit the per-call output cap and were cut off",
        )
    failed = int(agent.get("llm_calls_failed") or 0)
    transient = int(agent.get("llm_calls_failed_transient") or 0)
    total_calls = int(agent.get("llm_calls") or 0)
    ended = agent.get("ended_on_llm_error")
    if ended:
        note(
            "ended_on_llm_error",
            "exclude",
            f"the run ended after {ended.get('trailing_failures')} LLM call(s) failed after gateway retries "
            f"(HTTP {ended.get('status')}): an outage or rate limit, not the model, stopped it",
        )
    elif total_calls and transient >= 2 and transient / total_calls >= FAILED_CALL_WARN_SHARE:
        note(
            "llm_errors",
            "warn",
            f"{transient} of {total_calls} LLM calls failed transiently (after gateway retries): "
            "provider trouble shaped this run",
        )
    elif failed:
        note("llm_errors", "info", f"{failed} LLM calls failed (after gateway retries)")
    if (agent.get("workspace") or {}).get("protected_modified"):
        note("protected_files_modified", "info", "agent edited platform-owned build files (restored before build)")
    if (agent.get("egress") or {}).get("denied_connects"):
        note("egress_denied", "info", "blocked egress attempts: " + ", ".join(agent["egress"]["denied_connects"]))
    if evaluation is None:
        note("not_evaluated", "warn", "no platform evaluation yet")
    elif evaluation.get("status") == "infra_error":
        note("evaluation_infra_error", "exclude", "the platform could not evaluate this sample; re-evaluate it")
    _execution_notes(note, execution or {}, attempt, agent)
    if result.get("infra_interrupted"):
        note("infra_interrupted", "exclude", "the run kept being interrupted by infrastructure errors")
    return {
        "valid": not any(n["severity"] == "exclude" for n in notes),
        "notes": notes,
    }


CONTENDED_SHARE = 0.1


def _execution_notes(note, execution: dict[str, Any], attempt: int | None, agent: dict[str, Any]) -> None:
    """Validity notes from the executor's runtime record (spec §C.6, §C.9)."""
    if attempt:
        note("infra_retried", "warn", f"this is attempt {attempt + 1}: earlier attempts hit infrastructure failures")
    interruptions = execution.get("relay_interruptions")
    if interruptions:
        note("relay_interruptions", "info", f"{interruptions} LLM requests were re-sent after a relay interruption")
    wait = float(execution.get("llm_queue_wait_s") or 0.0)
    wall = float(agent.get("wall_seconds") or 0.0)
    if wall > 0 and wait > CONTENDED_SHARE * wall:
        note(
            "contended",
            "warn",
            f"the run waited {wait:.0f} s of its {wall:.0f} s wall clock for endpoint request slots",
        )


def _safe_json(data: bytes | None) -> dict:
    try:
        value = json.loads(data or b"{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}
