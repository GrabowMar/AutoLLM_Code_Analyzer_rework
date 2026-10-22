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

The gateway's call log is the authoritative LLM record (tokens, cost, served
provider, every request); the harness's own report (event stream,
trajectory, chat log) is kept as its self-report. Both, plus the patch and
the final workspace, are written under ``GENERATION_RUNS_ROOT/<job_id>/``
with sha256s in the job row.

After the run, still inside the same resource slot, the platform evaluates
the sample (``services/evaluation``): build, run without egress, hidden
conformance suite. ``result_data.agent.validity`` lists everything an
analysis should know before pooling the sample (limits that are not
model-neutral, params a model could not take, evaluation infrastructure
errors, ...).

Runs start in FIFO order of dispatch (``RunQueue``), so the order an
experiment shuffles its jobs into is the order they actually run in.
"""

from __future__ import annotations

import ast
import gzip
import hashlib
import itertools
import json
import logging
import re
import threading
import time
from collections import Counter
from collections import deque
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from backend.credentials.services.resolver import MissingApiKeyError
from backend.credentials.services.resolver import get_openrouter_key
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


class RunQueue:
    """FIFO admission to the sandbox slots (``AGENT_MAX_CONCURRENT_RUNS``).

    A plain semaphore admits waiting threads in arbitrary order; experiments
    rely on dispatch order being run order (they interleave conditions so
    that time-of-day effects -- provider load, alias repointing -- cannot line
    up with one condition). ``reserve`` is called at dispatch time, in
    dispatch order; ``enter`` blocks until the job is at the head and a slot
    is free.
    """

    def __init__(self, slots: int) -> None:
        self.slots = max(1, slots)
        self.cv = threading.Condition()
        self.active = 0
        self.waiting: deque[str] = deque()
        self.tickets: dict[str, int] = {}
        self.counter = itertools.count(1)

    def reserve(self, job_id: str) -> int:
        with self.cv:
            if job_id not in self.tickets:
                self.tickets[job_id] = next(self.counter)
                self.waiting.append(job_id)
            return self.tickets[job_id]

    def enter(self, job_id: str, is_cancelled) -> int:
        ticket = self.reserve(job_id)
        while True:
            with self.cv:
                if self.waiting and self.waiting[0] == job_id and self.active < self.slots:
                    self.waiting.popleft()
                    self.active += 1
                    self.cv.notify_all()
                    return ticket
                self.cv.wait(timeout=5)
                head = self.waiting[0] if self.waiting else None
            if is_cancelled():
                self.drop(job_id)
                raise AgentRunCancelled
            if head and head != job_id and not _is_live(head):
                # A reserved job that died before reaching the queue must not
                # block everyone behind it.
                self.drop(head)

    def leave(self, job_id: str) -> None:
        with self.cv:
            self.active -= 1
            self.tickets.pop(job_id, None)
            self.cv.notify_all()

    def drop(self, job_id: str) -> None:
        with self.cv:
            if job_id in self.waiting:
                self.waiting.remove(job_id)
            self.tickets.pop(job_id, None)
            self.cv.notify_all()


_queue: RunQueue | None = None
_queue_lock = threading.Lock()


def run_queue() -> RunQueue:
    global _queue  # noqa: PLW0603
    with _queue_lock:
        if _queue is None:
            _queue = RunQueue(settings.AGENT_MAX_CONCURRENT_RUNS)
        return _queue


class AgentRunError(Exception):
    """An agent job could not produce a sample (infrastructure / configuration)."""

    def __init__(self, message: str, *, remediation: str | None = None) -> None:
        super().__init__(message)
        self.remediation = remediation

    def display(self) -> str:
        return f"{self} {self.remediation}" if self.remediation else str(self)


class AgentRunCancelled(Exception):  # noqa: N818 - control flow, not an error
    """The job was cancelled before or during its run."""


def _is_live(key: str) -> bool:
    if ":" in key:  # "<job id>:evaluate" entries are short and always processed
        return True
    try:
        status = GenerationJob.objects.filter(id=key).values_list("status", flat=True).first()
    except (ValueError, ValidationError):
        return True  # not a job id: never evict what we cannot check
    return status in (GenerationJob.Status.PENDING, GenerationJob.Status.RUNNING)


def _is_cancelled(job: GenerationJob) -> bool:
    return (
        GenerationJob.objects.filter(id=job.id).values_list("status", flat=True).first()
        == GenerationJob.Status.CANCELLED
    )


class _Heartbeat:
    """Cancellation check that also touches ``updated_at`` once a minute.

    Runs last up to hours without any other write to the row; the heartbeat
    keeps maintenance's "stuck job" sweep from failing a healthy run.
    """

    def __init__(self, job: GenerationJob, every_seconds: float = 60) -> None:
        self.job = job
        self.every = every_seconds
        self.last = time.monotonic()

    def __call__(self) -> bool:
        if time.monotonic() - self.last >= self.every:
            self.last = time.monotonic()
            GenerationJob.objects.filter(id=self.job.id).update(updated_at=timezone.now())
        return _is_cancelled(self.job)


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

    def execute(self) -> None:
        job = self.job
        snapshot = job.resolved_bundle if isinstance(job.resolved_bundle, dict) else {}
        queue = run_queue()
        try:
            if snapshot.get("mode") != "agent":
                apply_agent_snapshot_to_job(job)
                snapshot = job.resolved_bundle
            if int(snapshot.get("bundle_schema_version") or 0) < AGENT_SNAPSHOT_SCHEMA_VERSION:
                msg = "This job was frozen by an earlier agent-mode version and cannot be replayed exactly."
                raise AgentRunError(msg, remediation="Use Retry to re-create it under the current procedure.")
            try:
                api_key = get_openrouter_key(job.created_by)
            except MissingApiKeyError as exc:
                raise AgentRunError(str(exc), remediation=exc.remediation) from exc
        except Exception:
            queue.drop(str(job.id))
            raise

        self._progress("queued")
        queued_at = time.time()
        ticket = queue.enter(str(job.id), lambda: _is_cancelled(job))
        try:
            if _is_cancelled(job):
                raise AgentRunCancelled
            self._progress("running")
            started_at = time.time()
            seed = seed_files(job)
            sandbox = AgentSandbox(str(job.id), api_key=api_key, snapshot=snapshot)
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
                ) from exc
            except Exception as exc:
                logger.exception("Agent sandbox for job %s failed", job.id)
                msg = f"Agent sandbox failed: {exc}"
                raise AgentRunError(msg, remediation="Check that the apps daemon is reachable.") from exc

            if result.termination == "cancelled":
                raise AgentRunCancelled
            self._record(snapshot, seed, result)
            job.result_data["agent"]["schedule"] = {
                "run_order": ticket,
                "queued_at": queued_at,
                "started_at": started_at,
                "queue_seconds": round(started_at - queued_at, 2),
            }
            if settings.AGENT_AUTO_EVALUATE:
                self._progress("evaluating")
                self._evaluate()
            job.result_data["agent"]["validity"] = assess_validity(job.resolved_bundle, job.result_data)
        finally:
            queue.leave(str(job.id))

    def _evaluate(self) -> None:
        from backend.generation.services.evaluation.runner import evaluate_job

        # Persist the sample first: evaluation builds from result_data.files.
        self.job.save(update_fields=["result_data", "metrics", "updated_at"])
        evaluate_job(self.job)

    # -- evidence --------------------------------------------------------

    def _record(self, snapshot: dict[str, Any], seed: dict[str, bytes], result) -> None:
        job = self.job
        calls = _jsonl(result.gateway_logs.get("calls.jsonl"))
        gateway_events = _jsonl(result.gateway_logs.get("gateway_events.jsonl"))
        ok_calls = [c for c in calls if c.get("status") == 200 and not (c.get("response") or {}).get("error")]
        if not ok_calls:
            raise AgentRunError(self._first_llm_error(calls, result), remediation=self._remediation(calls))

        budget_events = [e for e in gateway_events if e.get("event") == "budget_exhausted"]
        termination = result.termination
        if budget_events and termination in ("completed", "harness_error"):
            termination = "budget_exhausted"

        adapter = get_harness(snapshot["harness"]["name"])
        hsum = adapter.parse_report(result.outputs)
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

        usage = self._usage(ok_calls)
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
            "budget_exhausted": budget_events[0].get("reason") if budget_events else None,
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
            "llm_seconds": round(sum(float(c.get("duration_s") or 0) for c in calls), 2),
            # Responses cut off at the per-call output cap (same cap for every
            # harness; single-turn harnesses are most exposed to it).
            "truncated_calls": sum(1 for c in ok_calls if (c.get("response") or {}).get("finish_reason") == "length"),
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
            return "Check the OpenRouter API key in Settings → API Access."
        if 404 in statuses:
            return "No provider serves this model under the run's routing policy; relax llm_params.provider."
        if 402 in statuses:
            return "The OpenRouter account has insufficient credits."
        return "See the job's LLM call log for details."

    def _write_run_record(self, result, ws) -> dict[str, Any]:
        """Persist the full evidence on disk; return {name: {path, sha256, bytes}}."""
        root = Path(settings.GENERATION_RUNS_ROOT) / str(self.job.id)
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


def assess_validity(snapshot: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
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
    if agent.get("llm_calls_failed"):
        note("llm_errors", "info", f"{agent['llm_calls_failed']} LLM calls failed (after gateway retries)")
    if (agent.get("workspace") or {}).get("protected_modified"):
        note("protected_files_modified", "info", "agent edited platform-owned build files (restored before build)")
    if (agent.get("egress") or {}).get("denied_connects"):
        note("egress_denied", "info", "blocked egress attempts: " + ", ".join(agent["egress"]["denied_connects"]))
    if evaluation is None:
        note("not_evaluated", "warn", "no platform evaluation yet")
    elif evaluation.get("status") == "infra_error":
        note("evaluation_infra_error", "exclude", "the platform could not evaluate this sample; re-evaluate it")
    return {
        "valid": not any(n["severity"] == "exclude" for n in notes),
        "notes": notes,
    }


def _safe_json(data: bytes | None) -> dict:
    try:
        value = json.loads(data or b"{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}
