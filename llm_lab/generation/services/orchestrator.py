"""Generation orchestrator — executes generation jobs per mode."""

import ast as ast_module
import logging
import re
import time

from django.utils import timezone

from llm_lab.credentials.services.resolver import MissingApiKeyError
from llm_lab.credentials.services.resolver import get_openrouter_key
from llm_lab.generation.models import CopilotIteration
from llm_lab.generation.models import GenerationArtifact
from llm_lab.generation.models import GenerationJob
from llm_lab.generation.services import result_writer
from llm_lab.generation.services.backend_scanner import BackendScanner
from llm_lab.generation.services.code_parser import extract_python_code
from llm_lab.generation.services.code_parser import infer_python_dependencies
from llm_lab.generation.services.code_parser import parse_result_to_structured
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

        backend_messages = self.renderer.render_backend_messages(
            app_requirement=app_req,
            prompt_template_system=job.backend_prompt_template,
            prompt_template_user=None,
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
            "backend_code": backend_content,
            "frontend_code": frontend_content,
            "backend_truncated": OpenRouterClient.is_truncated(backend_resp),
            "frontend_truncated": OpenRouterClient.is_truncated(frontend_resp),
            "backend_scan": scan_result.to_dict(),
            "backend_dependencies": structured.get("backend_dependencies", []),
            "backend_files": structured.get("backend_files", 0),
            "frontend_files": structured.get("frontend_files", 0),
        }
        job.metrics = {
            **total_usage,
            "backend_duration": round(backend_elapsed, 2),
            "frontend_duration": round(frontend_elapsed, 2),
            "total_duration": round(backend_elapsed + frontend_elapsed, 2),
            "model": model_id,
            "endpoints_found": len(scan_result.endpoints),
            "models_found": len(scan_result.models),
        }

    # ── Copilot Mode ──────────────────────────────────────────────────

    def _run_copilot(self, job: GenerationJob) -> None:
        """Copilot mode: iterative generate → validate → fix loop."""
        model_id = self._pick_copilot_model(job)
        max_iters = min(job.copilot_max_iterations or 5, 10)
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        total_start = time.time()

        messages = self._build_copilot_initial_messages(job)
        current_code = ""
        last_errors: list[str] = []

        for iteration in range(1, max_iters + 1):
            job.refresh_from_db(fields=["status"])
            if job.status == GenerationJob.Status.CANCELLED:
                logger.info("Job %s cancelled at iteration %d", job.id, iteration)
                return

            job.copilot_current_iteration = iteration
            job.save(update_fields=["copilot_current_iteration", "updated_at"])
            result_writer.publish_progress(
                job,
                iteration,
                max_iters,
                timezone.now().isoformat(),
            )

            stage = f"copilot_iter_{iteration}"
            iter_start = time.time()

            response = self._call_llm(job, model_id, messages, stage=stage)
            iter_elapsed = time.time() - iter_start

            content = OpenRouterClient.extract_content(response)
            usage = OpenRouterClient.extract_usage(response)
            for k in total_usage:
                total_usage[k] += usage.get(k, 0)

            current_code = extract_python_code(content) or content
            errors = self._validate_python_code(current_code)

            CopilotIteration.objects.create(
                job=job,
                iteration_number=iteration,
                action=(CopilotIteration.Action.GENERATE if iteration == 1 else CopilotIteration.Action.FIX),
                llm_request={"messages": messages, "model": model_id},
                llm_response=content[:50000],
                build_success=len(errors) == 0,
                errors_detected=errors,
                fix_applied=(f"Fix attempt for: {'; '.join(last_errors[:3])}" if iteration > 1 else ""),
            )

            logger.info(
                "Copilot iter %d/%d: %d errors, %.1fs",
                iteration,
                max_iters,
                len(errors),
                iter_elapsed,
            )

            if not errors or iteration == max_iters:
                deps = infer_python_dependencies(current_code)
                job.result_data = {
                    "content": current_code,
                    "raw_response": content,
                    "iterations_completed": iteration,
                    "final_errors": errors,
                    "dependencies": deps,
                    "truncated": OpenRouterClient.is_truncated(response),
                }
                job.metrics = {
                    **total_usage,
                    "duration_seconds": round(time.time() - total_start, 2),
                    "model": model_id,
                    "iterations_used": iteration,
                    "final_error_count": len(errors),
                }
                break

            last_errors = errors
            messages = self._build_copilot_fix_messages(
                job,
                current_code,
                errors,
                iteration,
            )

    # ── Copilot helpers ───────────────────────────────────────────────

    @staticmethod
    def _build_copilot_initial_messages(job: GenerationJob) -> list[dict]:
        """Build initial generation messages for copilot mode."""
        system_prompt = (
            "You are an expert full-stack developer. Generate complete, "
            "working application code. Return well-structured code using "
            "annotated markdown code blocks like ```python:app.py\n"
            "Include ALL imports, complete function bodies, seed data, and "
            "error handling. The code must be syntactically valid Python."
        )
        user_prompt = (
            f"Build the following application:\n\n{job.copilot_description}\n\n"
            "Requirements:\n"
            "1. Complete Flask backend in app.py with Flask-SQLAlchemy, "
            "Flask-CORS, JWT auth\n"
            "2. React frontend in App.jsx with Tailwind CSS, dark theme\n"
            "3. Rich data models with 6+ fields, seed data with 15+ records\n"
            "4. CRUD endpoints with search, filter, sort, pagination\n"
            "5. 6+ distinct pages in the frontend\n"
            "6. Production-quality error handling\n\n"
            "Return the code in annotated code blocks:\n"
            "```python:app.py\n... complete backend ...\n```\n"
            "```jsx:App.jsx\n... complete frontend ...\n```"
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    @staticmethod
    def _build_copilot_fix_messages(
        job: GenerationJob,
        code: str,
        errors: list[str],
        iteration: int,
    ) -> list[dict]:
        """Build fix prompt for copilot iteration."""
        error_text = "\n".join(f"- {e}" for e in errors[:10])
        code_preview = code[:15000] if len(code) > 15000 else code

        return [
            {
                "role": "system",
                "content": (
                    "You are an expert Python developer fixing code errors. "
                    "Return the COMPLETE corrected code in a ```python:app.py "
                    "code block. Do not return partial fixes — return the "
                    "entire corrected file."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"The following code has errors (iteration {iteration}):\n\n"
                    f"```python\n{code_preview}\n```\n\n"
                    f"## Errors Found\n{error_text}\n\n"
                    "Fix ALL errors and return the complete corrected code in "
                    "a ```python:app.py code block."
                ),
            },
        ]

    @staticmethod
    def _validate_python_code(code: str) -> list[str]:
        """Validate Python code and return list of error descriptions."""
        errors: list[str] = []
        if not code or not code.strip():
            errors.append("Empty code output")
            return errors

        try:
            ast_module.parse(code)
        except SyntaxError as e:
            errors.append(f"SyntaxError at line {e.lineno}: {e.msg}")
            return errors

        if "pass" in code:
            stubs = re.findall(
                r"def\s+\w+\s*\([^)]*\)\s*:\s*\n\s+(?:pass|\.\.\.)\s*$",
                code,
                re.MULTILINE,
            )
            if len(stubs) > 2:
                errors.append(f"{len(stubs)} stub functions with only pass/...")

        if len(code.strip().split("\n")) < 30:
            errors.append(
                f"Code too short ({len(code.strip().split(chr(10)))} lines); expected 100+ lines for a complete app",
            )

        return errors

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

    @staticmethod
    def _pick_copilot_model(job: GenerationJob) -> str:
        """Choose model for copilot mode."""
        if job.model:
            return job.model.model_id
        if job.copilot_use_open_source:
            return "deepseek/deepseek-chat"
        return "openai/gpt-4o-mini"
