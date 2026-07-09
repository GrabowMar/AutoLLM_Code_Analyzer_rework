"""Generation orchestrator — executes generation jobs per mode."""

import logging
import time

from django.utils import timezone

from backend.credentials.services.resolver import MissingApiKeyError
from backend.credentials.services.resolver import get_openrouter_key
from backend.generation.models import GenerationArtifact
from backend.generation.models import GenerationJob
from backend.generation.services import result_writer
from backend.generation.services.aider_runner import AiderExecutionError
from backend.generation.services.backend_scanner import BackendScanner
from backend.generation.services.code_parser import parse_to_files
from backend.generation.services.copilot_validation import validate_python_code
from backend.generation.services.generation_validation import ValidationResult
from backend.generation.services.generation_validation import build_repair_prompt
from backend.generation.services.generation_validation import extract_repaired_file
from backend.generation.services.generation_validation import validate_frontend_files
from backend.generation.services.generation_validation import validate_python_files
from backend.generation.services.metrics import JobMetrics
from backend.generation.services.openrouter_client import OpenRouterClient
from backend.generation.services.openrouter_client import OpenRouterError
from backend.generation.services.output_guard import call_with_continuations
from backend.generation.services.output_guard import clamped_max_tokens
from backend.generation.services.prompt_renderer import PromptRenderer
from backend.runtime.services.scaffolding import get_stack_config
from backend.runtime.services.scaffolding import resolve_stack_slug

logger = logging.getLogger(__name__)

# Continuation/repair are per-experiment config in the planned Experiment
# entity (not yet implemented); these are the defaults every job gets until
# that config exists.
DEFAULT_CONTINUATION_LIMIT = 1
DEFAULT_ENABLE_REPAIR = True


class GenerationService:
    """Orchestrates generation for all three modes."""

    def __init__(self) -> None:
        self.renderer = PromptRenderer()
        self.scanner = BackendScanner()
        # Per-job client is constructed in ``_call_llm`` so it uses the
        # job owner's resolved OpenRouter API key.
        self.client: OpenRouterClient | None = None
        self.continuation_limit = DEFAULT_CONTINUATION_LIMIT
        self.enable_repair = DEFAULT_ENABLE_REPAIR

    def _build_client_for(self, job: GenerationJob) -> OpenRouterClient:
        """Build an OpenRouterClient using ``job.created_by``'s API key."""
        try:
            api_key = get_openrouter_key(job.created_by)
        except MissingApiKeyError as exc:
            raise OpenRouterError(
                str(exc),
                status_code=401,
                user_facing_message=str(exc),
                remediation=exc.remediation,
            ) from exc
        return OpenRouterClient(api_key=api_key)

    def execute(self, job: GenerationJob) -> None:
        """Execute a generation job based on its mode."""
        job.status = GenerationJob.Status.RUNNING
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at", "updated_at"])
        result_writer.publish_status(job)

        try:
            if job.mode == GenerationJob.Mode.CUSTOM:
                self._run_custom(job)
            elif job.mode == GenerationJob.Mode.SCAFFOLDING:
                self._run_scaffolding(job)
            elif job.mode == GenerationJob.Mode.COPILOT:
                self._run_copilot(job)
            else:
                msg = f"Unknown mode: {job.mode}"
                raise ValueError(msg)

            job.status = GenerationJob.Status.COMPLETED
        except OpenRouterError as exc:
            logger.exception("Job %s failed (OpenRouter)", job.id)
            job.status = GenerationJob.Status.FAILED
            job.error_message = exc.display()[:2000]
            existing = job.result_data if isinstance(job.result_data, dict) else {}
            existing["error_detail"] = str(exc)[:2000]
            existing["error_status_code"] = exc.status_code
            existing["error_remediation"] = exc.remediation
            job.result_data = existing
        except AiderExecutionError as exc:
            logger.exception("Job %s failed (Aider)", job.id)
            job.status = GenerationJob.Status.FAILED
            job.error_message = exc.display()[:2000]
            existing = job.result_data if isinstance(job.result_data, dict) else {}
            existing["error_detail"] = str(exc)[:2000]
            existing["error_remediation"] = exc.remediation
            job.result_data = existing
        except Exception as exc:
            logger.exception("Job %s failed", job.id)
            job.status = GenerationJob.Status.FAILED
            job.error_message = str(exc)[:2000]
        finally:
            job.completed_at = timezone.now()
            if job.started_at:
                job.duration_seconds = (job.completed_at - job.started_at).total_seconds()
            job.save(
                update_fields=[
                    "status",
                    "completed_at",
                    "duration_seconds",
                    "error_message",
                    "result_data",
                    "metrics",
                    "copilot_current_iteration",
                    "updated_at",
                ],
            )
            result_writer.publish_status(job)
            result_writer.update_batch(job)

    # ── Custom Mode ───────────────────────────────────────────────────

    def _run_custom(self, job: GenerationJob) -> None:
        """Custom mode: direct system + user prompts → LLM."""
        model_id = job.model.model_id if job.model else "openai/gpt-4o-mini"
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
        model_id = job.model.model_id if job.model else "openai/gpt-4o-mini"
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
        backend_messages = self.renderer.render_backend_messages(
            app_requirement=app_req,
            prompt_template_system=job.backend_prompt_template,
            prompt_template_user=None,
            resolved_bundle=bundle if bundle else None,
        )
        start = time.time()
        backend_content, backend_resp, backend_continuations, backend_usage = call_with_continuations(
            _call,
            backend_messages,
            stage="backend",
            limit=self.continuation_limit,
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
            prompt_template_system=job.frontend_prompt_template,
            prompt_template_user=None,
            api_context_override=api_context if scan_result.endpoints else None,
            resolved_bundle=bundle if bundle else None,
        )
        start2 = time.time()
        frontend_content, frontend_resp, frontend_continuations, frontend_usage = call_with_continuations(
            _call,
            frontend_messages,
            stage="frontend",
            limit=self.continuation_limit,
        )
        frontend_elapsed = time.time() - start2
        for k in total_usage:
            total_usage[k] += frontend_usage.get(k, 0)

        parsed = parse_to_files(backend_content, frontend_content, stack_config)
        files = parsed["files"]

        py_validation = validate_python_files(files)
        fe_validation = validate_frontend_files(files)
        repair_used = False
        if self.enable_repair and not (py_validation.passed and fe_validation.passed):
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

    # ── Copilot Mode ──────────────────────────────────────────────────

    def _run_copilot(self, job: GenerationJob) -> None:
        """Copilot mode: Aider agent loop in an ephemeral git workspace."""
        from backend.generation.services.aider_runner import AiderRunner
        from backend.generation.services.aider_runner import pick_copilot_model_id
        from backend.generation.services.copilot_results import CopilotResults
        from backend.generation.services.copilot_workspace import CopilotWorkspace

        max_iters = min(job.copilot_max_iterations or 5, 10)
        total_start = time.time()
        workspace = CopilotWorkspace.create(job)
        try:
            iterations = AiderRunner(job, workspace).run_loop(max_iters)
            CopilotResults.apply(job, workspace, iterations)

            loc = 0
            if isinstance(job.result_data, dict):
                files = job.result_data.get("files") or {}
                for code in files.values():
                    if isinstance(code, str):
                        loc += code.count("\n") + 1

            job.metrics = JobMetrics(
                duration_seconds=round(time.time() - total_start, 2),
                model=pick_copilot_model_id(job),
                iterations_used=len(iterations),
                final_error_count=len(iterations[-1].errors) if iterations else 0,
                engine="aider",
                lines_of_code=loc,
            ).dump()
        finally:
            workspace.cleanup()

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
        top_p = llm_config.get("top_p")
        seed = job.experiment_seed
        max_tokens = clamped_max_tokens(job.max_tokens, messages, llm_config)

        request_payload = {
            "model": model_id,
            "messages": messages,
            "temperature": job.temperature,
            "max_tokens": max_tokens,
        }
        if top_p is not None:
            request_payload["top_p"] = top_p
        if seed is not None:
            request_payload["seed"] = seed

        try:
            client = self._build_client_for(job)
            response = client.chat_completion(
                model=model_id,
                messages=messages,
                temperature=job.temperature,
                max_tokens=max_tokens,
                top_p=top_p,
                seed=seed,
            )
        except OpenRouterError:
            GenerationArtifact.objects.create(
                job=job,
                stage=stage,
                request_payload=request_payload,
                response_payload={"error": "API call failed"},
            )
            raise

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
