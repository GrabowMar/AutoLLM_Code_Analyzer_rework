"""Run copilot generation iterations via Aider."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from django.conf import settings
from django.utils import timezone

from llm_lab.credentials.services.resolver import MissingApiKeyError
from llm_lab.credentials.services.resolver import get_openrouter_key
from llm_lab.generation.models import CopilotIteration
from llm_lab.generation.models import GenerationArtifact
from llm_lab.generation.models import GenerationJob
from llm_lab.generation.services import result_writer
from llm_lab.generation.services.copilot_workspace import CopilotWorkspace
from llm_lab.generation.services.copilot_validation import validate_python_code

logger = logging.getLogger(__name__)


class AiderExecutionError(Exception):
    """Raised when Aider cannot run or fails an iteration."""

    def __init__(
        self,
        message: str,
        *,
        user_facing_message: str | None = None,
        remediation: str | None = None,
    ) -> None:
        super().__init__(message)
        self.user_facing_message = user_facing_message
        self.remediation = remediation

    def display(self) -> str:
        parts = [self.user_facing_message or str(self)]
        if self.remediation:
            parts.append(self.remediation)
        return " ".join(parts)


@dataclass
class AiderIterationResult:
    iteration: int
    transcript: str
    errors: list[str]
    build_output: str
    build_success: bool


def to_aider_model_id(model_id: str) -> str:
    """Map OpenRouter model ids to Aider's openrouter/ prefix."""
    if model_id.startswith("openrouter/"):
        return model_id
    return f"openrouter/{model_id}"


def pick_copilot_model_id(job: GenerationJob) -> str:
    if job.model:
        return job.model.model_id
    if job.copilot_use_open_source:
        return "deepseek/deepseek-chat"
    return "openai/gpt-4o-mini"


def resolve_copilot_api_key(job: GenerationJob) -> str:
    """Resolve the user's stored OpenRouter key (Settings → API Access)."""
    user = job.created_by
    if user is None and job.created_by_id:
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.filter(pk=job.created_by_id).first()
    return get_openrouter_key(user)


def apply_openrouter_key_to_env(api_key: str) -> None:
    """Set environment variables read by Aider/litellm for OpenRouter."""
    os.environ["OPENROUTER_API_KEY"] = api_key


class AiderRunner:
    """Executes the copilot iteration loop using Aider's Python API."""

    def __init__(self, job: GenerationJob, workspace: CopilotWorkspace) -> None:
        self.job = job
        self.workspace = workspace
        self.model_id = pick_copilot_model_id(job)

    def run_loop(self, max_iters: int) -> list[AiderIterationResult]:
        """Run up to *max_iters* generate/fix cycles; return per-iteration results."""
        try:
            api_key = resolve_copilot_api_key(self.job)
            apply_openrouter_key_to_env(api_key)
            logger.info(
                "Copilot job %s using stored OpenRouter credential for user %s",
                self.job.id,
                self.job.created_by_id,
            )
        except MissingApiKeyError as exc:
            raise AiderExecutionError(
                str(exc),
                user_facing_message=str(exc),
                remediation=exc.remediation,
            ) from exc

        self._ensure_aider_available()
        results: list[AiderIterationResult] = []
        last_errors: list[str] = []

        for iteration in range(1, max_iters + 1):
            self.job.refresh_from_db(fields=["status"])
            if self.job.status == GenerationJob.Status.CANCELLED:
                logger.info("Job %s cancelled at iteration %d", self.job.id, iteration)
                return results

            self.job.copilot_current_iteration = iteration
            self.job.save(update_fields=["copilot_current_iteration", "updated_at"])
            result_writer.publish_progress(
                self.job,
                iteration,
                max_iters,
                timezone.now().isoformat(),
            )

            message = self._build_message(iteration, last_errors)
            iter_start = time.time()
            transcript = self._run_aider_message(message, api_key)
            iter_elapsed = time.time() - iter_start

            build_output, compile_ok = self._run_validation_command()
            code = self.workspace.primary_python_path()
            py_source = code.read_text(encoding="utf-8") if code.is_file() else ""
            errors = validate_python_code(py_source)
            if not compile_ok and build_output.strip():
                errors = [*errors, *self._parse_build_errors(build_output)]
            build_success = len(errors) == 0

            action = (
                CopilotIteration.Action.GENERATE
                if iteration == 1
                else CopilotIteration.Action.FIX
            )
            CopilotIteration.objects.create(
                job=self.job,
                iteration_number=iteration,
                action=action,
                llm_request={"message": message[:10000], "model": self.model_id},
                llm_response=transcript[:50000],
                build_output=build_output[:20000],
                build_success=build_success and len(errors) == 0,
                errors_detected=errors,
                fix_applied=(
                    f"Fix attempt for: {'; '.join(last_errors[:3])}" if iteration > 1 else ""
                ),
            )
            GenerationArtifact.objects.create(
                job=self.job,
                stage=f"copilot_iter_{iteration}",
                request_payload={"message": message[:5000], "model": self.model_id},
                response_payload={"transcript": transcript[:20000], "duration": iter_elapsed},
            )

            logger.info(
                "Aider copilot iter %d/%d: %d errors, %.1fs",
                iteration,
                max_iters,
                len(errors),
                iter_elapsed,
            )

            result = AiderIterationResult(
                iteration=iteration,
                transcript=transcript,
                errors=errors,
                build_output=build_output,
                build_success=len(errors) == 0,
            )
            results.append(result)

            if not errors or iteration == max_iters:
                break

            last_errors = errors

        return results

    def _ensure_aider_available(self) -> None:
        try:
            import aider  # noqa: F401
        except ImportError as exc:
            raise AiderExecutionError(
                "aider-chat is not installed",
                user_facing_message="Copilot mode requires the aider-chat package.",
                remediation="Install dependencies with: uv sync",
            ) from exc
        if not shutil_which("git"):
            raise AiderExecutionError(
                "git is not available",
                user_facing_message="Copilot mode requires git in the server environment.",
                remediation="Install git in the Django container or host.",
            )

    def _run_aider_message(self, message: str, api_key: str) -> str:
        from aider.coders import Coder
        from aider.io import InputOutput
        from aider.models import Model

        aider_model = to_aider_model_id(self.model_id)
        fnames = [str(self.workspace.root / f) for f in self.workspace.tracked_files()]

        apply_openrouter_key_to_env(api_key)
        old_cwd = os.getcwd()
        lines: list[str] = []

        def capture(*args: object, **kwargs: object) -> None:
            text = " ".join(str(a) for a in args)
            if text.strip():
                lines.append(text)

        try:
            os.chdir(self.workspace.root)
            io = InputOutput(yes=True, pretty=False)
            io.tool_output = capture  # type: ignore[method-assign]
            model = Model(aider_model)
            if model.missing_keys:
                raise AiderExecutionError(
                    f"OpenRouter API key not visible to Aider: {model.missing_keys}",
                    user_facing_message=(
                        "Copilot could not use your stored OpenRouter API key."
                    ),
                    remediation=(
                        "Confirm your key in Settings → API Access is valid, then retry."
                    ),
                )
            coder = Coder.create(
                main_model=model,
                fnames=fnames,
                io=io,
                auto_commits=False,
                auto_test=settings.AIDER_AUTO_TEST and self.workspace.test_command() is not None,
                test_cmd=self.workspace.test_command() or None,
            )
            result = coder.run(message)
            if result:
                lines.append(str(result))
        except Exception as exc:
            raise AiderExecutionError(
                f"Aider failed: {exc}",
                user_facing_message="Aider could not complete this iteration.",
                remediation="Check OpenRouter API key, model availability, and server logs.",
            ) from exc
        finally:
            os.chdir(old_cwd)

        return "\n".join(lines) if lines else "(no output captured)"

    def _build_message(self, iteration: int, errors: list[str]) -> str:
        if iteration == 1:
            stack = ""
            if self.workspace.is_flask_react():
                stack = (
                    "\n\nStack: Flask 3.x API (all routes under /api/) in app.py, "
                    "React 18 frontend in frontend/src/App.jsx with Tailwind CSS."
                )
            return (
                f"Build the following application:\n\n{self.job.copilot_description}"
                f"{stack}\n\n"
                "Requirements:\n"
                "1. Complete Flask backend with SQLAlchemy, CORS, JWT auth where appropriate\n"
                "2. React frontend with multiple pages and dark theme when applicable\n"
                "3. Rich data models, seed data, CRUD with search/filter/pagination\n"
                "4. Production-quality error handling\n"
                "Edit the existing scaffold files in place."
            )

        error_text = "\n".join(f"- {e}" for e in errors[:10])
        return (
            f"The project has validation errors (iteration {iteration}):\n\n"
            f"{error_text}\n\n"
            "Fix ALL issues. Edit files in place and ensure Python is syntactically valid."
        )

    def _run_validation_command(self) -> tuple[str, bool]:
        cmd = self.workspace.test_command()
        if not cmd or not settings.AIDER_AUTO_TEST:
            return "", True
        try:
            proc = subprocess.run(
                cmd,
                cwd=self.workspace.root,
                shell=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            return "Validation command timed out after 120s", False
        except OSError as exc:
            return str(exc), False
        output = (proc.stdout + proc.stderr)[:10000]
        return output, proc.returncode == 0

    @staticmethod
    def _parse_build_errors(output: str) -> list[str]:
        lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
        return lines[:5] if lines else ["Build/compile validation failed"]


def shutil_which(cmd: str) -> str | None:
    import shutil

    return shutil.which(cmd)
