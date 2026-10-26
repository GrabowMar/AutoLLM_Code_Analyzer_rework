"""Generation orchestrator: executes generation jobs per mode.

The executor (``backend.execution``) claims a job and calls
:meth:`GenerationService.execute` with ``claimed=True`` in a worker thread;
the job's stage then ends through ``execution.transitions.finish_stage``
(or ``infra_retry``). Every LLM call goes through ``inference.chat`` with the
access ``inference.access.resolve_access`` resolves for the job owner.
"""

import logging
import time

from django.utils import timezone

from backend.execution import transitions
from backend.execution.transitions import LeaseLost
from backend.generation.models import GenerationArtifact
from backend.generation.models import GenerationJob
from backend.generation.services import result_writer
from backend.generation.services.agent.runner import AgentRunCancelled
from backend.generation.services.agent.runner import AgentRunError
from backend.generation.services.agent.runner import AgentRunner
from backend.generation.services.backend_scanner import BackendScanner
from backend.generation.services.code_parser import parse_to_files
from backend.generation.services.copilot_validation import validate_python_code
from backend.generation.services.generation_validation import ValidationResult
from backend.generation.services.generation_validation import build_repair_prompt
from backend.generation.services.generation_validation import extract_repaired_file
from backend.generation.services.generation_validation import validate_frontend_files
from backend.generation.services.generation_validation import validate_python_files
from backend.generation.services.llm_params import sampling_params
from backend.generation.services.metrics import JobMetrics
from backend.generation.services.openrouter_client import OpenRouterClient
from backend.generation.services.openrouter_client import OpenRouterError
from backend.generation.services.output_guard import call_with_continuations
from backend.generation.services.output_guard import clamped_max_tokens
from backend.generation.services.profile_resolver import DEFAULT_CONTINUATION_LIMIT
from backend.generation.services.profile_resolver import DEFAULT_ENABLE_REPAIR
from backend.generation.services.profile_resolver import generation_options
from backend.generation.services.prompt_renderer import PromptRenderer
from backend.runtime.services.scaffolding import get_stack_config
from backend.runtime.services.scaffolding import resolve_stack_slug

logger = logging.getLogger(__name__)

# Continuation/repair (template modes): an experiment's continuation_limit
# and enable_repair are frozen into each job's snapshot (``generation``,
# only when non-default) and read back in ``_run_scaffolding``; every other
# job runs with the service defaults (DEFAULT_CONTINUATION_LIMIT /
# DEFAULT_ENABLE_REPAIR, overridable on the instance).


def _is_cancelled(job: GenerationJob) -> bool:
    return (
        GenerationJob.objects.filter(id=job.id).values_list("status", flat=True).first()
        == GenerationJob.Status.CANCELLED
    )


class _InferenceClient:
    """``chat_completion`` over ``inference.chat`` for one job (the call shape ``_call_llm`` uses).

    OpenRouter models delegate to ``OpenRouterClient`` inside ``chat`` (same
    behaviour and retries); local models go through the ``LLMRequest`` queue.
    The job's cancel status is polled while a local call waits.
    """

    def __init__(self, job: GenerationJob, access) -> None:
        self.job = job
        self.access = access
        self.last_served: dict = {}

    def chat_completion(self, *, model, messages, temperature=None, max_tokens=None, seed=None, params=None):
        from backend.inference.chat import chat

        params = dict(params or {})
        if temperature is not None:
            params.setdefault("temperature", temperature)
        if max_tokens is not None:
            params.setdefault("max_tokens", max_tokens)
        result = chat(
            self.access,
            messages=messages,
            params=params,
            seed=seed,
            job=self.job,
            cancel_check=lambda: _is_cancelled(self.job),
            priority=self.job.priority,
        )
        self.last_served = result.served or {}
        return result.response


class GenerationService:
    """Orchestrates generation for every mode (agent first; template modes secondary)."""

    def __init__(self) -> None:
        self.renderer = PromptRenderer()
        self.scanner = BackendScanner()
        self.continuation_limit = DEFAULT_CONTINUATION_LIMIT
        self.enable_repair = DEFAULT_ENABLE_REPAIR
        # Successful LLM calls of the current attempt: an infra error after
        # one of them is no longer "nothing happened" (spec §C.7).
        self._ok_calls = 0
        self._access = None

    def _build_client_for(self, job: GenerationJob) -> _InferenceClient:
        """The chat client for ``job``: ``resolve_access(job.created_by, job.model)`` (raises ``LLMAccessError``)."""
        from backend.inference.access import resolve_access

        if job.model is None:
            msg = "job has no model"
            raise ValueError(msg)
        self._access = resolve_access(job.created_by, job.model)
        return _InferenceClient(job, self._access)

    # ── execution ─────────────────────────────────────────────────────

    def execute(self, job: GenerationJob, claimed: bool = False) -> None:
        """Run ``job``'s generation stage and end it (``finish_stage`` / ``infra_retry``).

        ``claimed=True``: the executor already moved the job to RUNNING under
        its lease. ``claimed=False`` (tests, shell): claim it here
        conditionally (a cancel that already landed wins), and run an
        evaluation the stage asks for inline.
        """
        if not claimed and not self._claim_inline(job):
            return
        if job.status == GenerationJob.Status.RUNNING and not claimed:
            result_writer.publish_status(job)
        self._ok_calls = 0
        outcome = transitions.OUTCOME_DONE
        retry: tuple[str, str] | None = None
        try:
            if job.mode == GenerationJob.Mode.AGENT:
                outcome = AgentRunner(job).run_stage()
            elif job.mode == GenerationJob.Mode.CUSTOM:
                self._run_custom(job)
            elif job.mode == GenerationJob.Mode.SCAFFOLDING:
                self._run_scaffolding(job)
                if self._evaluate_scaffolding(job):
                    outcome = transitions.OUTCOME_EVALUATE
            elif job.mode == GenerationJob.Mode.COPILOT:
                self._run_copilot(job)
            else:
                msg = f"Unknown mode: {job.mode}"
                raise ValueError(msg)
            job.status = GenerationJob.Status.COMPLETED
        except LeaseLost:
            logger.info("Job %s attempt %s: lease lost; stopping quietly", job.id, job.attempt)
            # A cancel won: give its slot back now (no-op when the job was requeued or reclaimed).
            transitions._release(job)  # noqa: SLF001
            return
        except AgentRunCancelled:
            logger.info("Job %s cancelled", job.id)
            job.status = GenerationJob.Status.CANCELLED
            outcome = transitions.OUTCOME_CANCELLED
        except OpenRouterError as exc:
            retry = self._llm_infra_reason(job, exc)
            if retry is None:
                logger.exception("Job %s failed (LLM)", job.id)
            self._fail(job, exc.display(), detail=str(exc), status_code=exc.status_code, remediation=exc.remediation)
        except AgentRunError as exc:
            logger.warning("Job %s failed (agent run): %s", job.id, exc)
            if exc.infra:
                retry = (transitions.REASON_INFRA, str(exc)[:1000])
            self._fail(job, exc.display(), detail=str(exc), remediation=exc.remediation)
        except Exception as exc:
            logger.exception("Job %s failed", job.id)
            job.status = GenerationJob.Status.FAILED
            job.error_message = str(exc)[:2000]
        if retry is not None:
            transitions.infra_retry(job, *retry)
            return
        if outcome == transitions.OUTCOME_EVALUATE and not claimed:
            # Inline (tests, shell): the evaluation follows in the same call.
            self.evaluate(job, finish=False)
            outcome = transitions.OUTCOME_DONE
        if outcome == transitions.OUTCOME_EVALUATE:
            # The sample is saved with the eval_queued transition; the eval pool picks it up.
            job.status = GenerationJob.Status.RUNNING
        transitions.finish_stage(job, outcome)

    def evaluate(self, job: GenerationJob, *, finish: bool = True) -> None:
        """The evaluation stage (``phase=evaluating``): build, run, grade; then validity; then finish.

        Used by the executor's eval pool (``finish=True``) and inline by
        ``execute(claimed=False)``. An evaluation infra error is recorded on the
        sample (the reconciler bounds requeues of *lost* evaluations), never a
        job failure.
        """
        from backend.generation.services.evaluation.runner import evaluate_job

        try:
            evaluate_job(job, save=transitions.guarded_save)
            if job.mode == GenerationJob.Mode.AGENT and isinstance((job.result_data or {}).get("agent"), dict):
                from backend.generation.services.agent.runner import assess_validity
                from backend.generation.services.agent.runner import execution_record

                # Keep the executor's notes (infra_retried, relay_interruptions, contended) next to the grade.
                job.result_data["agent"]["validity"] = assess_validity(
                    job.resolved_bundle or {},
                    job.result_data,
                    execution=execution_record(job),
                    attempt=job.attempt,
                )
        except LeaseLost:
            logger.info("Job %s: lease lost during evaluation; stopping quietly", job.id)
            raise
        except Exception:
            logger.exception("Evaluation of job %s failed", job.id)
        if finish:
            if job.status == GenerationJob.Status.RUNNING:
                job.status = GenerationJob.Status.COMPLETED
            transitions.finish_stage(job, transitions.OUTCOME_DONE)

    def _claim_inline(self, job: GenerationJob) -> bool:
        started_at = timezone.now()
        # Conditional claim: a cancel (job or batch) that landed between
        # creation and this call must not be overwritten.
        claimed = (
            GenerationJob.objects.filter(id=job.id)
            .exclude(status=GenerationJob.Status.CANCELLED)
            .update(
                status=GenerationJob.Status.RUNNING,
                phase=GenerationJob.Phase.RUNNING,
                started_at=started_at,
                stage_entered_at=started_at,
                updated_at=started_at,
            )
        )
        if not claimed:
            self._skip_cancelled(job)
            return False
        job.status = GenerationJob.Status.RUNNING
        job.phase = GenerationJob.Phase.RUNNING
        job.started_at = started_at
        job.updated_at = started_at
        return True

    @staticmethod
    def _skip_cancelled(job: GenerationJob) -> None:
        """Close out a job that was cancelled before it started (it never ran)."""
        now = timezone.now()
        GenerationJob.objects.filter(id=job.id, completed_at__isnull=True).update(completed_at=now, updated_at=now)
        job.refresh_from_db(fields=["status", "phase", "completed_at", "updated_at"])
        result_writer.publish_status(job)
        result_writer.update_batch(job)

    @staticmethod
    def _fail(job, message: str, *, detail: str, status_code=None, remediation=None) -> None:
        job.status = GenerationJob.Status.FAILED
        job.error_message = (message or detail)[:2000]
        existing = job.result_data if isinstance(job.result_data, dict) else {}
        existing["error_detail"] = detail[:2000]
        if status_code is not None:
            existing["error_status_code"] = status_code
        existing["error_remediation"] = remediation
        job.result_data = existing

    def _llm_infra_reason(self, job: GenerationJob, exc: OpenRouterError) -> tuple[str, str] | None:
        """Whether an LLM error is an infrastructure failure to retry (spec §C.7), and as what.

        Retryable access errors (endpoint offline/unavailable, timeouts, lease
        expiry, model OOM) with no successful call in the attempt → ``infra``;
        on a local endpoint after a successful call → ``infra_interrupted``
        (scaffolding restarts from scratch with the same seed). Anything else
        (model 4xx, OpenRouter errors after retries) is a failure of the job.
        """
        from backend.inference.access import LLMAccessError

        if not isinstance(exc, LLMAccessError) or not exc.retryable:
            return None
        if self._ok_calls == 0:
            return (transitions.REASON_INFRA, f"{exc.code}: {exc}"[:1000])
        access = self._access
        if access is not None and not access.is_openrouter:
            return (transitions.REASON_INFRA_INTERRUPTED, f"{exc.code}: {exc}"[:1000])
        return None

    @staticmethod
    def _evaluate_scaffolding(job: GenerationJob) -> bool:
        """Scaffolding experiment jobs evaluate per job when ``post_run.evaluate`` (default on)."""
        if job.experiment_id is None or job.app_requirement_id is None:
            return False
        from backend.generation.models import Experiment

        post_run = Experiment.objects.filter(pk=job.experiment_id).values_list("post_run", flat=True).first()
        if not isinstance(post_run, dict):
            return True
        return bool(post_run.get("evaluate", True))

    # ── Custom Mode ───────────────────────────────────────────────────

    def _run_custom(self, job: GenerationJob) -> None:
        """Custom mode: direct system + user prompts → LLM."""
        # No silent model fallback: a job without a model fails at its first call (_build_client_for).
        model_id = job.model.model_id if job.model else ""
        messages = []
        if job.custom_system_prompt:
            messages.append({"role": "system", "content": job.custom_system_prompt})
        messages.append({"role": "user", "content": job.custom_user_prompt})

        start = time.time()
        response = self._call_llm(job, model_id, messages, stage="custom")
        elapsed = time.time() - start

        content = OpenRouterClient.extract_content(response)
        usage = OpenRouterClient.extract_usage(response)
        truncated = OpenRouterClient.is_truncated(response)

        job.result_data = {
            "content": content,
            "truncated": truncated,
            "finish_reason": response.get("choices", [{}])[0].get("finish_reason"),
        }
        job.metrics = JobMetrics(
            **usage,
            duration_seconds=round(elapsed, 2),
            model=model_id,
            lines_of_code=content.count("\n") + 1 if isinstance(content, str) else 0,
        ).dump()

    # ── Scaffolding Mode ──────────────────────────────────────────────

    def _run_scaffolding(self, job: GenerationJob) -> None:
        """Scaffolding mode: two-stage generation (backend → scan → frontend)."""
        # No silent model fallback: a job without a model fails at its first call (_build_client_for).
        model_id = job.model.model_id if job.model else ""
        app_req = job.app_requirement
        if not app_req:
            msg = "Scaffolding mode requires an app requirement template"
            raise ValueError(msg)

        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0.0}
        stack_slug = resolve_stack_slug(job)
        stack_config = get_stack_config(stack_slug)

        def _call(messages: list[dict], stage_name: str) -> dict:
            return self._call_llm(job, model_id, messages, stage=stage_name)

        bundle = job.resolved_bundle if isinstance(job.resolved_bundle, dict) else {}
        continuation_limit, enable_repair = generation_options(
            bundle,
            continuation_limit=self.continuation_limit,
            enable_repair=self.enable_repair,
        )
        backend_messages = self.renderer.render_backend_messages(
            app_requirement=app_req,
            resolved_bundle=bundle if bundle else None,
        )
        start = time.time()
        backend_content, backend_resp, backend_continuations, backend_usage = call_with_continuations(
            _call,
            backend_messages,
            stage="backend",
            limit=continuation_limit,
        )
        backend_elapsed = time.time() - start
        for k in total_usage:
            total_usage[k] += backend_usage.get(k, 0)

        scan_result = self.scanner.scan_raw_response(backend_content)
        api_context = scan_result.to_frontend_context()
        logger.info(
            "Backend scan: %d endpoints, %d models",
            len(scan_result.endpoints),
            len(scan_result.models),
        )

        frontend_messages = self.renderer.render_frontend_messages(
            app_requirement=app_req,
            backend_code=backend_content,
            api_context_override=api_context if scan_result.endpoints else None,
            resolved_bundle=bundle if bundle else None,
        )
        start2 = time.time()
        frontend_content, frontend_resp, frontend_continuations, frontend_usage = call_with_continuations(
            _call,
            frontend_messages,
            stage="frontend",
            limit=continuation_limit,
        )
        frontend_elapsed = time.time() - start2
        for k in total_usage:
            total_usage[k] += frontend_usage.get(k, 0)

        parsed = parse_to_files(backend_content, frontend_content, stack_config)
        files = parsed["files"]

        py_validation = validate_python_files(files)
        fe_validation = validate_frontend_files(files)
        repair_used = False
        if enable_repair and not (py_validation.passed and fe_validation.passed):
            files, repair_used = self._run_repair_round(job, model_id, files, py_validation, fe_validation)
            py_validation = validate_python_files(files)
            fe_validation = validate_frontend_files(files)
        validation_passed = py_validation.passed and fe_validation.passed

        entry = parsed.get("backend_entry") or stack_config.get("backend_filename", "app.py")
        component_path = f"frontend/src/{stack_config.get('frontend_component', 'App.jsx')}"

        job.result_data = {
            "result_schema_version": 2,
            "files": files,
            "backend_entry": entry,
            # Compatibility shims for readers that still expect a single
            # backend/frontend string (reports.loc, older exports).
            "backend_code": files.get(entry, ""),
            "frontend_code": files.get(component_path, frontend_content),
            "backend_truncated": OpenRouterClient.is_truncated(backend_resp),
            "frontend_truncated": OpenRouterClient.is_truncated(frontend_resp),
            "backend_scan": scan_result.to_dict(),
            "backend_dependencies": parsed.get("backend_dependencies", []),
            "backend_files": parsed.get("backend_files", 0),
            "frontend_files": parsed.get("frontend_files", 0),
            "validation": {
                "passed": validation_passed,
                "python": py_validation.to_dict(),
                "frontend": fe_validation.to_dict(),
            },
        }
        loc = sum(code.count("\n") + 1 for code in files.values() if isinstance(code, str))
        job.metrics = JobMetrics(
            **total_usage,
            backend_duration=round(backend_elapsed, 2),
            frontend_duration=round(frontend_elapsed, 2),
            total_duration=round(backend_elapsed + frontend_elapsed, 2),
            model=model_id,
            lines_of_code=loc,
            endpoints_found=len(scan_result.endpoints),
            models_found=len(scan_result.models),
            continuations_used=backend_continuations + frontend_continuations,
            repair_used=repair_used,
            validation_passed=validation_passed,
        ).dump()

    def _run_repair_round(
        self,
        job: GenerationJob,
        model_id: str,
        files: dict[str, str],
        py_validation: ValidationResult,
        fe_validation: ValidationResult,
    ) -> tuple[dict[str, str], bool]:
        """One repair attempt per failing file; keeps the original unless the fix validates."""
        files = dict(files)
        repair_used = False
        failing = {**py_validation.errors, **fe_validation.errors}

        for name, errors in failing.items():
            code = files.get(name)
            if not code:
                continue
            try:
                prompt = build_repair_prompt(name, code, errors)
                repair_messages = [
                    {
                        "role": "system",
                        "content": (
                            "You are a meticulous code-fixing assistant. "
                            "Output only the corrected file in a single code block."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ]
                stage = f"{'backend' if name.endswith('.py') else 'frontend'}_repair"
                response = self._call_llm(job, model_id, repair_messages, stage=stage)
            except OpenRouterError:
                logger.warning("Repair call failed for %s", name)
                continue

            repaired = extract_repaired_file(OpenRouterClient.extract_content(response), name)
            if not repaired:
                continue

            if name.endswith(".py"):
                still_failing = validate_python_code(repaired)
            else:
                still_failing = validate_frontend_files({name: repaired}).errors.get(name, [])

            if not still_failing:
                files[name] = repaired
                repair_used = True

        return files, repair_used

    # ── Copilot Mode (retired) ────────────────────────────────────────

    def _run_copilot(self, job: GenerationJob) -> None:
        """The in-process aider loop was replaced by agent mode's sandboxed harness."""
        msg = "Copilot mode was retired; create an agent-mode job instead."
        raise ValueError(msg)

    @staticmethod
    def _validate_python_code(code: str) -> list[str]:
        """Validate Python code (delegates to copilot_validation)."""
        return validate_python_code(code)

    # ── LLM call helper ───────────────────────────────────────────────

    def _call_llm(
        self,
        job: GenerationJob,
        model_id: str,
        messages: list[dict],
        *,
        stage: str,
    ) -> dict:
        """Call LLM and save the artifact."""
        llm_config = {}
        if isinstance(job.resolved_bundle, dict):
            llm_config = job.resolved_bundle.get("llm") or {}
        params = sampling_params(llm_config)
        # Legacy fallback for pre-snapshot jobs whose llm section is empty.
        params.setdefault("temperature", job.temperature)
        params.setdefault("max_tokens", job.max_tokens)
        seed = job.experiment_seed
        params["max_tokens"] = clamped_max_tokens(params["max_tokens"], messages, llm_config)

        request_payload = {
            "model": model_id,
            "messages": messages,
            **params,
        }
        if seed is not None:
            request_payload["seed"] = seed

        try:
            client = self._build_client_for(job)
            access = self._access
            if access is not None and not access.is_openrouter:
                request_payload["_upstream"] = {
                    "endpoint": access.endpoint.slug,
                    "path": "/api/chat" if access.api_flavor == "native" else "/v1/chat/completions",
                }
            response = client.chat_completion(
                model=model_id,
                messages=messages,
                temperature=params["temperature"],
                max_tokens=params["max_tokens"],
                seed=seed,
                params=params,
            )
        except OpenRouterError:
            GenerationArtifact.objects.create(
                job=job,
                stage=stage,
                request_payload=request_payload,
                response_payload={"error": "API call failed"},
            )
            raise

        self._ok_calls += 1
        usage = OpenRouterClient.extract_usage(response)
        GenerationArtifact.objects.create(
            job=job,
            stage=stage,
            request_payload=request_payload,
            response_payload=response,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_cost=usage.get("cost", 0.0),
        )
        return response
