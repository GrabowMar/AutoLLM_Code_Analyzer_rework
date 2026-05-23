"""Generation orchestrator — executes generation jobs per mode."""

import logging
import time

from django.utils import timezone

from llm_lab.credentials.services.resolver import MissingApiKeyError
from llm_lab.credentials.services.resolver import get_openrouter_key
from llm_lab.generation.models import GenerationArtifact
from llm_lab.generation.models import GenerationJob
from llm_lab.generation.services import result_writer
from llm_lab.generation.services.aider_runner import AiderExecutionError
from llm_lab.generation.services.backend_scanner import BackendScanner
from llm_lab.generation.services.code_parser import parse_result_to_structured
from llm_lab.generation.services.copilot_validation import validate_python_code
from llm_lab.generation.services.openrouter_client import OpenRouterClient
from llm_lab.generation.services.openrouter_client import OpenRouterError
from llm_lab.generation.services.prompt_renderer import PromptRenderer

logger = logging.getLogger(__name__)


class GenerationService:
    """Orchestrates generation for all three modes."""

    def __init__(self) -> None:
        self.renderer = PromptRenderer()
        self.scanner = BackendScanner()
        # Per-job client is constructed in ``_call_llm`` so it uses the
        # job owner's resolved OpenRouter API key.
        self.client: OpenRouterClient | None = None

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
        job.metrics = {
            **usage,
            "duration_seconds": round(elapsed, 2),
            "model": model_id,
            "lines_of_code": content.count("\n") + 1 if isinstance(content, str) else 0,
        }

    # ── Scaffolding Mode ──────────────────────────────────────────────

    def _run_scaffolding(self, job: GenerationJob) -> None:
        """Scaffolding mode: two-stage generation (backend → scan → frontend)."""
        model_id = job.model.model_id if job.model else "openai/gpt-4o-mini"
        app_req = job.app_requirement
        if not app_req:
            msg = "Scaffolding mode requires an app requirement template"
            raise ValueError(msg)

        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        bundle = job.resolved_bundle if isinstance(job.resolved_bundle, dict) else {}
        backend_messages = self.renderer.render_backend_messages(
            app_requirement=app_req,
            prompt_template_system=job.backend_prompt_template,
            prompt_template_user=None,
            resolved_bundle=bundle if bundle else None,
        )
        start = time.time()
        backend_resp = self._call_llm(
            job,
            model_id,
            backend_messages,
            stage="backend",
        )
        backend_elapsed = time.time() - start
        backend_content = OpenRouterClient.extract_content(backend_resp)
        backend_usage = OpenRouterClient.extract_usage(backend_resp)
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
        frontend_resp = self._call_llm(
            job,
            model_id,
            frontend_messages,
            stage="frontend",
        )
        frontend_elapsed = time.time() - start2
        frontend_content = OpenRouterClient.extract_content(frontend_resp)
        frontend_usage = OpenRouterClient.extract_usage(frontend_resp)
        for k in total_usage:
            total_usage[k] += frontend_usage.get(k, 0)

        structured = parse_result_to_structured(backend_content, frontend_content)

        job.result_data = {
            "backend_code": structured.get("backend_code") or backend_content,
            "frontend_code": structured.get("frontend_code") or frontend_content,
            "backend_truncated": OpenRouterClient.is_truncated(backend_resp),
            "frontend_truncated": OpenRouterClient.is_truncated(frontend_resp),
            "backend_scan": scan_result.to_dict(),
            "backend_dependencies": structured.get("backend_dependencies", []),
            "backend_files": structured.get("backend_files", 0),
            "frontend_files": structured.get("frontend_files", 0),
        }
        loc = 0
        if isinstance(backend_content, str):
            loc += backend_content.count("\n") + 1
        if isinstance(frontend_content, str):
            loc += frontend_content.count("\n") + 1
        job.metrics = {
            **total_usage,
            "backend_duration": round(backend_elapsed, 2),
            "frontend_duration": round(frontend_elapsed, 2),
            "total_duration": round(backend_elapsed + frontend_elapsed, 2),
            "model": model_id,
            "lines_of_code": loc,
            "endpoints_found": len(scan_result.endpoints),
            "models_found": len(scan_result.models),
        }

    # ── Copilot Mode ──────────────────────────────────────────────────

    def _run_copilot(self, job: GenerationJob) -> None:
        """Copilot mode: Aider agent loop in an ephemeral git workspace."""
        from llm_lab.generation.services.aider_runner import AiderRunner
        from llm_lab.generation.services.aider_runner import pick_copilot_model_id
        from llm_lab.generation.services.copilot_results import CopilotResults
        from llm_lab.generation.services.copilot_workspace import CopilotWorkspace

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

            job.metrics = {
                **(job.metrics or {}),
                "duration_seconds": round(time.time() - total_start, 2),
                "model": pick_copilot_model_id(job),
                "iterations_used": len(iterations),
                "final_error_count": len(iterations[-1].errors) if iterations else 0,
                "engine": "aider",
                "lines_of_code": loc,
            }
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
        request_payload = {
            "model": model_id,
            "messages": messages,
            "temperature": job.temperature,
            "max_tokens": job.max_tokens,
        }

        try:
            client = self._build_client_for(job)
            response = client.chat_completion(
                model=model_id,
                messages=messages,
                temperature=job.temperature,
                max_tokens=job.max_tokens,
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
        )
        return response
