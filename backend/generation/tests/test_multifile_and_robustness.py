"""Tests for multi-file parsing, output-guard robustness, and generation-time validation."""

from __future__ import annotations

from unittest import mock

import pytest

from backend.generation.services import generation_validation
from backend.generation.services.code_parser import parse_to_files
from backend.generation.services.code_parser import sanitize_rel_path
from backend.generation.services.openrouter_client import OpenRouterClient
from backend.generation.services.output_guard import call_with_continuations
from backend.generation.services.output_guard import clamped_max_tokens

# ── code_parser.parse_to_files ──────────────────────────────────────


class TestSanitizeRelPath:
    def test_plain_filename(self):
        assert sanitize_rel_path("app.py") == "app.py"

    def test_nested_path(self):
        assert sanitize_rel_path("src/models/user.py") == "src/models/user.py"

    def test_rejects_parent_traversal(self):
        assert sanitize_rel_path("../../etc/passwd") is None

    def test_rejects_absolute_path(self):
        assert sanitize_rel_path("/etc/passwd") is None

    def test_rejects_no_extension(self):
        assert sanitize_rel_path("Makefile") is None

    def test_rejects_windows_drive(self):
        assert sanitize_rel_path("C:\\Windows\\system.py") is None

    def test_strips_backtick_wrapping(self):
        assert sanitize_rel_path("`app.py`") == "app.py"

    def test_rejects_too_deep(self):
        assert sanitize_rel_path("a/b/c/d/e/f/g/h.py") is None


class TestParseToFiles:
    def test_single_backend_file(self):
        backend = "```python:app.py\nfrom flask import Flask\napp = Flask(__name__)\n```"
        result = parse_to_files(backend, None, {"backend_filename": "app.py"})
        assert result["files"]["app.py"].strip() == "from flask import Flask\napp = Flask(__name__)"
        assert result["backend_entry"] == "app.py"
        assert result["backend_files"] == 1

    def test_multi_file_backend_preserved_separately(self):
        backend = (
            "```python:app.py\nfrom models import db\napp = None\n```\n\n"
            "```python:models.py\nimport sqlalchemy\ndb = None\n```"
        )
        result = parse_to_files(backend, None, {"backend_filename": "app.py"})
        assert set(result["files"]) == {"app.py", "models.py"}
        assert "sqlalchemy" in result["files"]["models.py"]
        assert result["backend_files"] == 2

    def test_unnamed_backend_block_uses_stack_entry_name(self):
        backend = "```python\nfrom flask import Flask\napp = Flask(__name__)\n```"
        result = parse_to_files(backend, None, {"backend_filename": "app.py"})
        assert "app.py" in result["files"]

    def test_frontend_files_placed_under_frontend_src(self):
        backend = "```python:app.py\napp = 1\n```"
        frontend = (
            "```jsx:App.jsx\nexport default function App() {}\n```\n```javascript:api.js\nexport const x = 1;\n```"
        )
        result = parse_to_files(backend, frontend, {"backend_filename": "app.py", "frontend_component": "App.jsx"})
        assert "frontend/src/App.jsx" in result["files"]
        assert "frontend/src/api.js" in result["files"]
        assert result["frontend_files"] == 2

    def test_unnamed_frontend_promoted_to_main_component(self):
        backend = "```python:app.py\napp = 1\n```"
        frontend = "```jsx\nexport default function App() {}\n```"
        result = parse_to_files(backend, frontend, {"backend_filename": "app.py", "frontend_component": "App.jsx"})
        assert "frontend/src/App.jsx" in result["files"]

    def test_malicious_filename_falls_back_to_unnamed(self):
        backend = "```python:../../etc/cron.d/evil.py\nimport os\nos.system('rm -rf /')\n```"
        result = parse_to_files(backend, None, {"backend_filename": "app.py"})
        # Rejected path never appears; content is preserved under the safe entry name.
        assert all(".." not in name for name in result["files"])
        assert "app.py" in result["files"]

    def test_dependency_inference_covers_all_python_files(self):
        backend = "```python:app.py\nimport requests\n```\n```python:models.py\nimport yaml\n```"
        result = parse_to_files(backend, None, {"backend_filename": "app.py"})
        assert "requests" in result["backend_dependencies"]
        assert "pyyaml" in result["backend_dependencies"]

    def test_no_frontend_input_yields_no_frontend_files(self):
        backend = "```python:app.py\napp = 1\n```"
        result = parse_to_files(backend, None, {"backend_filename": "app.py"})
        assert result["frontend_files"] == 0
        assert not any(name.startswith("frontend/") for name in result["files"])


# ── output_guard ─────────────────────────────────────────────────────


def _response(content: str, *, finish_reason: str = "stop", tokens: int = 10) -> dict:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": tokens, "completion_tokens": tokens, "total_tokens": tokens * 2, "cost": 0.01},
    }


class TestCallWithContinuations:
    def test_no_truncation_single_call(self):
        calls = []

        def call(messages, stage):
            calls.append(stage)
            return _response("complete output", finish_reason="stop")

        content, _resp, used, usage = call_with_continuations(call, [], stage="backend", limit=1)
        assert content == "complete output"
        assert used == 0
        assert len(calls) == 1
        assert usage["prompt_tokens"] == 10

    def test_truncated_response_triggers_continuation(self):
        responses = [
            _response("part one ", finish_reason="length"),
            _response("part two", finish_reason="stop"),
        ]

        def call(messages, stage):
            return responses.pop(0)

        content, response, used, usage = call_with_continuations(call, [], stage="backend", limit=1)
        assert content == "part one part two"
        assert used == 1
        assert usage["prompt_tokens"] == 20  # summed across both calls
        assert OpenRouterClient.is_truncated(response) is False

    def test_continuation_capped_at_limit(self):
        call_count = {"n": 0}

        def call(messages, stage):
            call_count["n"] += 1
            return _response(f"chunk{call_count['n']} ", finish_reason="length")

        _content, resp, used, _usage = call_with_continuations(call, [], stage="backend", limit=2)
        assert used == 2
        assert call_count["n"] == 3  # initial + 2 continuations
        assert OpenRouterClient.is_truncated(resp) is True

    def test_empty_continuation_stops_immediately(self):
        responses = [_response("part ", finish_reason="length"), _response("", finish_reason="length")]

        def call(messages, stage):
            return responses.pop(0)

        content, _resp, used, _usage = call_with_continuations(call, [], stage="backend", limit=3)
        assert content == "part "
        assert used == 1


class TestClampedMaxTokens:
    def test_no_llm_config_returns_requested(self):
        assert clamped_max_tokens(8000, [{"content": "hi"}], None) == 8000

    def test_clamps_to_max_output_tokens(self):
        result = clamped_max_tokens(32000, [{"content": "hi"}], {"max_output_tokens": 4096})
        assert result == 4096

    def test_clamps_to_context_window_minus_prompt(self):
        long_prompt = "x" * 30000  # ~10k tokens at the 3 chars/token estimate
        result = clamped_max_tokens(32000, [{"content": long_prompt}], {"context_window": 16000})
        assert result < 32000
        assert result >= 256

    def test_never_below_floor(self):
        long_prompt = "x" * 100000
        result = clamped_max_tokens(1000, [{"content": long_prompt}], {"context_window": 8000})
        assert result == 256


# ── generation_validation ───────────────────────────────────────────


class TestValidatePythonFiles:
    def test_valid_file_has_no_errors(self):
        result = generation_validation.validate_python_files({"app.py": "x = 1\n" * 40})
        assert result.passed

    def test_syntax_error_recorded_per_file(self):
        result = generation_validation.validate_python_files({"app.py": "def broken(:\n"})
        assert not result.passed
        assert "app.py" in result.errors

    def test_non_python_files_ignored(self):
        result = generation_validation.validate_python_files({"App.jsx": "export default 1;"})
        assert result.passed
        assert result.errors == {}


class TestValidateFrontendFiles:
    def test_no_checkable_files_passes_trivially(self):
        result = generation_validation.validate_frontend_files({"style.css": "body { color: red; }"})
        assert result.passed
        assert "style.css" in result.skipped

    def test_skips_when_docker_unavailable(self, monkeypatch):
        monkeypatch.setattr(generation_validation.docker_manager, "image_exists", lambda tag: False)
        monkeypatch.setattr(generation_validation.docker_manager, "ping", lambda: False)
        result = generation_validation.validate_frontend_files({"App.jsx": "export default function App() {}"})
        assert result.passed  # unavailable Docker degrades to skip, not failure
        assert "App.jsx" in result.skipped

    def test_records_esbuild_errors(self, monkeypatch):
        # Real esbuild output format: a leading "✘ [ERROR]" summary line
        # followed by a source snippet with no such prefix.
        esbuild_output = (
            '✘ [ERROR] Expected ")" but found end of file\n\n'
            "    input.jsx:1:31:\n"
            "      1 │ export default function App( {}\n"
            "        ╵                                )\n"
        )
        monkeypatch.setattr(generation_validation.docker_manager, "image_exists", lambda tag: True)
        monkeypatch.setattr(
            generation_validation.docker_manager,
            "run_once_with_files",
            lambda *a, **k: {"exit_code": 1, "output": esbuild_output},
        )
        result = generation_validation.validate_frontend_files({"App.jsx": "export default function App( {}"})
        assert not result.passed
        assert result.errors["App.jsx"] == ['✘ [ERROR] Expected ")" but found end of file']

    def test_passes_when_esbuild_exits_zero(self, monkeypatch):
        monkeypatch.setattr(generation_validation.docker_manager, "image_exists", lambda tag: True)
        monkeypatch.setattr(
            generation_validation.docker_manager,
            "run_once_with_files",
            lambda *a, **k: {"exit_code": 0, "output": ""},
        )
        result = generation_validation.validate_frontend_files({"App.jsx": "export default function App() {}"})
        assert result.passed


class TestRepairPromptRoundtrip:
    def test_extract_repaired_file_prefers_matching_name(self):
        response = "```python:app.py\nfixed = True\n```"
        assert generation_validation.extract_repaired_file(response, "app.py") == "fixed = True"

    def test_extract_repaired_file_falls_back_to_only_block(self):
        response = "```\nfixed = True\n```"
        assert generation_validation.extract_repaired_file(response, "app.py") == "fixed = True"

    def test_extract_repaired_file_none_when_no_blocks(self):
        assert generation_validation.extract_repaired_file("no code here", "app.py") is None


@pytest.mark.django_db
def test_build_repair_prompt_renders_errors_and_code():
    from backend.generation.models import ContentBlock
    from backend.generation.tests.factories import ContentBlockFactory

    # Auto-seeding already provides a "repair-instructions" v1 block from
    # data/blocks/validation/repair-instructions.yaml; replace it with
    # controlled test content instead of asserting against real prose.
    ContentBlock.objects.filter(slug="repair-instructions", version=1).delete()
    ContentBlockFactory(
        slug="repair-instructions",
        version=1,
        block_type=ContentBlock.BlockType.VALIDATION,
        content="Fix {{ filename }}:\n{% for e in errors %}- {{ e }}\n{% endfor %}\n{{ code }}",
        is_system=True,
    )
    prompt = generation_validation.build_repair_prompt("app.py", "x = 1", ["SyntaxError: bad"])
    assert "app.py" in prompt
    assert "SyntaxError: bad" in prompt
    assert "x = 1" in prompt


def test_run_once_with_files_removes_container_on_success():
    fake_client = mock.Mock()
    fake_client.containers.run.return_value = mock.Mock()

    with (
        mock.patch("backend.runtime.services.docker_manager.client", return_value=fake_client),
        mock.patch(
            "backend.runtime.services.docker_manager.copy_files_in",
            return_value={"status": "ok"},
        ) as copy_mock,
        mock.patch(
            "backend.runtime.services.docker_manager.exec_in",
            return_value={"exit_code": 0, "output": ""},
        ) as exec_mock,
        mock.patch("backend.runtime.services.docker_manager.remove") as remove_mock,
    ):
        from backend.runtime.services.docker_manager import run_once_with_files

        result = run_once_with_files("image:tag", {"a.js": "1"}, ["esbuild", "a.js"])

    assert result["exit_code"] == 0
    copy_mock.assert_called_once()
    exec_mock.assert_called_once()
    remove_mock.assert_called_once()


def test_run_once_with_files_daemon_unavailable():
    with mock.patch("backend.runtime.services.docker_manager.client", return_value=None):
        from backend.runtime.services.docker_manager import run_once_with_files

        result = run_once_with_files("image:tag", {"a.js": "1"}, ["esbuild", "a.js"])
    assert result["error"] == "Docker daemon unavailable"
    assert result["exit_code"] == -1


# ── orchestrator integration: multi-file result_data, continuation, repair ──


def _api_response(content: str, *, finish_reason: str = "stop") -> dict:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20, "cost": 0.001},
    }


@pytest.fixture
def _no_docker(monkeypatch):
    """Frontend validation degrades to skip — these tests aren't about Docker."""
    monkeypatch.setattr(generation_validation.docker_manager, "ping", lambda: False)
    monkeypatch.setattr(generation_validation.docker_manager, "image_exists", lambda tag: False)


@pytest.mark.django_db
class TestRunScaffoldingMultiFile:
    def _make_job(self):
        from backend.generation.tests.factories import AppRequirementTemplateFactory
        from backend.generation.tests.factories import GenerationJobFactory
        from backend.users.tests.factories import UserFactory

        return GenerationJobFactory(
            mode="scaffolding",
            created_by=UserFactory(),
            app_requirement=AppRequirementTemplateFactory(),
            resolved_bundle={},
        )

    def _service(self, monkeypatch, responses):
        from backend.generation.services.orchestrator import GenerationService

        service = GenerationService()
        service.enable_repair = False
        fake_client = mock.MagicMock()
        fake_client.chat_completion.side_effect = responses
        monkeypatch.setattr(service, "_build_client_for", lambda job: fake_client)
        return service

    def test_result_data_v2_has_files_map(self, _no_docker, monkeypatch):
        job = self._make_job()
        service = self._service(
            monkeypatch,
            [
                _api_response("```python:app.py\nfrom flask import Flask\napp = Flask(__name__)\n```"),
                _api_response("```jsx:App.jsx\nexport default function App() { return null; }\n```"),
            ],
        )

        service._run_scaffolding(job)

        assert job.result_data["result_schema_version"] == 2
        assert "app.py" in job.result_data["files"]
        assert "frontend/src/App.jsx" in job.result_data["files"]
        assert job.result_data["backend_code"]
        assert job.result_data["frontend_code"]
        assert job.metrics["continuations_used"] == 0

    def test_multi_file_backend_all_persisted(self, _no_docker, monkeypatch):
        job = self._make_job()
        service = self._service(
            monkeypatch,
            [
                _api_response(
                    "```python:app.py\nfrom models import db\n```\n```python:models.py\ndb = object()\n```",
                ),
                _api_response("```jsx:App.jsx\nexport default function App() {}\n```"),
            ],
        )

        service._run_scaffolding(job)

        assert "app.py" in job.result_data["files"]
        assert "models.py" in job.result_data["files"]

    def test_truncated_backend_is_stitched_via_continuation(self, _no_docker, monkeypatch):
        job = self._make_job()
        service = self._service(
            monkeypatch,
            [
                _api_response("```python:app.py\nfrom flask import Flask\n", finish_reason="length"),
                _api_response("app = Flask(__name__)\n```", finish_reason="stop"),
                _api_response("```jsx:App.jsx\nexport default function App() {}\n```"),
            ],
        )
        service.continuation_limit = 1

        service._run_scaffolding(job)

        assert job.result_data["backend_truncated"] is False
        assert "app = Flask(__name__)" in job.result_data["files"]["app.py"]
        assert job.metrics["continuations_used"] == 1

    def test_still_truncated_after_exhausting_continuations_is_flagged(self, _no_docker, monkeypatch):
        job = self._make_job()
        service = self._service(
            monkeypatch,
            [
                _api_response("```python:app.py\npart1", finish_reason="length"),
                _api_response("part2", finish_reason="length"),
                _api_response("```jsx:App.jsx\nexport default function App() {}\n```"),
            ],
        )
        service.continuation_limit = 1

        service._run_scaffolding(job)

        assert job.result_data["backend_truncated"] is True

    def test_repair_round_fixes_invalid_python(self, _no_docker, monkeypatch):
        job = self._make_job()
        service = self._service(
            monkeypatch,
            [
                _api_response("```python:app.py\ndef broken(:\n```"),  # invalid syntax
                _api_response("```jsx:App.jsx\nexport default function App() {}\n```"),
                _api_response("```python:app.py\n" + "x = 1\n" * 40 + "```"),  # repair response
            ],
        )
        service.enable_repair = True

        with mock.patch(
            "backend.generation.services.orchestrator.build_repair_prompt",
            return_value="fix it",
        ):
            service._run_scaffolding(job)

        assert job.result_data["files"]["app.py"].strip().startswith("x = 1")
        assert job.result_data["validation"]["passed"] is True
        assert job.metrics["repair_used"] is True

    def test_repair_round_keeps_original_when_fix_still_invalid(self, _no_docker, monkeypatch):
        job = self._make_job()
        service = self._service(
            monkeypatch,
            [
                _api_response("```python:app.py\ndef broken(:\n```"),
                _api_response("```jsx:App.jsx\nexport default function App() {}\n```"),
                _api_response("```python:app.py\ndef still_broken(:\n```"),  # repair still invalid
            ],
        )
        service.enable_repair = True

        with mock.patch(
            "backend.generation.services.orchestrator.build_repair_prompt",
            return_value="fix it",
        ):
            service._run_scaffolding(job)

        assert "broken(:" in job.result_data["files"]["app.py"]
        assert job.result_data["validation"]["passed"] is False
        assert job.metrics["repair_used"] is False
