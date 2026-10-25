"""Agent mode: config, harnesses, brief, snapshot identity, gateway policy, run queue, evidence, API."""

# Fixture tables (endpoints, SSE chunks, call records) stay one record per line.
# ruff: noqa: E501

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

from backend.generation.models import GenerationArtifact
from backend.generation.models import GenerationJob
from backend.generation.services.agent.config import merge_agent_config
from backend.generation.services.agent.config import validate_agent_config
from backend.generation.services.agent.harnesses import HARNESSES
from backend.generation.services.agent.harnesses import get_harness
from backend.generation.services.agent.harnesses.base import proxy_env
from backend.generation.services.agent.harnesses.base import run_script
from backend.generation.services.agent.harnesses.opencode import DISABLED_TOOLS
from backend.generation.services.agent.harnesses.opencode import parse_events
from backend.generation.services.agent.runner import AgentRunError
from backend.generation.services.agent.runner import AgentRunner
from backend.generation.services.agent.runner import assess_validity
from backend.generation.services.agent.runner import validate_python_sources
from backend.generation.services.agent.sandbox import SandboxResult
from backend.generation.services.agent.snapshot import apply_agent_snapshot_to_job
from backend.generation.services.agent.snapshot import build_agent_snapshot
from backend.generation.services.agent.snapshot import reseed_agent_snapshot
from backend.generation.services.agent.workspace import collect
from backend.generation.services.agent.workspace import make_tar
from backend.generation.services.agent.workspace import read_tar
from backend.generation.services.job_cloning import clone_job
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import ExperimentConditionFactory
from backend.generation.tests.factories import ExperimentFactory
from backend.generation.tests.factories import GenerationProfileFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

SUPPORTED_WITH_SEED = ["temperature", "top_p", "seed", "tools", "tool_choice", "max_tokens", "reasoning"]


@pytest.fixture(autouse=True)
def _no_auto_evaluation(settings):
    # Evaluation needs the apps daemon; its own tests switch it back on.
    settings.AGENT_AUTO_EVALUATE = False


def _model(**kw):
    kw.setdefault("model_id", "deepseek/deepseek-v4.1-flash")
    kw.setdefault("metadata", {"openrouter_supported_parameters": SUPPORTED_WITH_SEED})
    return LLMModelFactory(**kw)


def _app(**kw):
    kw.setdefault("is_default", True)  # a catalog spec: usable by every user
    return AppRequirementTemplateFactory(
        slug=kw.pop("slug", "todo"),
        name="Todo",
        description="Track tasks",
        backend_requirements=kw.pop(
            "backend_requirements",
            ["Access control: allow public read-only todos; require auth for create/update/delete"],
        ),
        api_endpoints=kw.pop(
            "api_endpoints",
            [
                {
                    "method": "GET",
                    "path": "/api/todos",
                    "description": "List",
                    "request": None,
                    "response": {"items": []},
                },
            ],
        ),
        admin_api_endpoints=kw.pop(
            "admin_api_endpoints",
            [{"method": "GET", "path": "/api/admin/todos", "description": "Admin", "request": None, "response": {}}],
        ),
        data_model={"name": "Todo", "fields": {"id": "int"}},
        **kw,
    )


def _snapshot(model=None, app=None, **kw):
    return build_agent_snapshot(
        app_requirement=app if app is not None else _app(),
        description=kw.pop("description", ""),
        profile=kw.pop("profile", None),
        stack_slug=kw.pop("stack_slug", "flask-react"),
        model=model or _model(),
        max_tokens=kw.pop("max_tokens", 32000),
        experiment_seed=kw.pop("experiment_seed", 7),
        llm_overrides=kw.pop("llm_overrides", None),
        agent_overrides=kw.pop("agent_overrides", None),
    )


# ── config ─────────────────────────────────────────────────────────────


class TestAgentConfig:
    def test_validates_ranges_keys_and_policies(self):
        assert validate_agent_config({"max_llm_requests": 10, "network_policy": "none", "harness": "aider"}) == {
            "max_llm_requests": 10,
            "network_policy": "none",
            "harness": "aider",
        }
        with pytest.raises(ValueError, match="wall_clock_seconds") as exc:
            validate_agent_config({"wall_clock_seconds": 5, "bogus": 1})
        assert "bogus" in str(exc.value)
        for bad, key in (
            ({"network_policy": "internet"}, "network_policy"),
            ({"harness": "cursor"}, "harness"),
            ({"sampling_policy": "hot"}, "sampling_policy"),
        ):
            with pytest.raises(ValueError, match=key):
                validate_agent_config(bad)

    def test_integral_floats_are_accepted_for_int_limits(self):
        assert validate_agent_config({"max_llm_requests": 20.0}) == {"max_llm_requests": 20}

    def test_merge_layers_over_settings_defaults(self, settings):
        settings.AGENT_MAX_COST_USD = 1.5
        merged = merge_agent_config({"max_cost_usd": None}, {"max_llm_requests": 9})
        assert merged["max_cost_usd"] == 1.5
        assert merged["max_llm_requests"] == 9
        assert merged["harness"] == "opencode"
        assert merged["sampling_policy"] == "provider_default"


# ── brief + snapshot ───────────────────────────────────────────────────


class TestBrief:
    def test_states_spec_contract_and_autonomy_without_coaching(self):
        brief = _snapshot()["brief"]["text"]
        assert '# Task: build "Todo"' in brief
        assert "nobody will answer questions" in brief
        assert "### GET /api/todos" in brief
        assert "GET /api/health" in brief
        assert "POST /api/auth/register" in brief
        assert "Authorization: Bearer <token>" in brief
        assert "ADMIN_PASSWORD" in brief
        assert "checks it automatically" in brief
        assert "`Dockerfile`" in brief
        assert "300+" not in brief
        assert "lines" not in brief.lower()

    def test_spec_defined_auth_is_used_instead_of_the_default(self):
        app = _app(
            slug="auth",
            api_endpoints=[
                {"method": "POST", "path": "/api/auth/register", "description": "r", "request": {"email": "string", "password": "string"}, "response": {}},
                {"method": "POST", "path": "/api/auth/login", "description": "l", "request": {"email": "string", "password": "string"}, "response": {"token": "jwt"}},
            ],
        )  # fmt: skip
        snap = _snapshot(app=app)
        assert snap["contract"]["identity_field"] == "email"
        assert snap["contract"]["admin_identity"] == "admin@example.com"
        assert "defined in the API contract above" in snap["brief"]["text"]

    def test_public_app_without_admin_gets_no_accounts_section(self):
        app = _app(slug="pub", backend_requirements=["1. Book model"], admin_api_endpoints=[])
        snap = _snapshot(app=app)
        assert snap["contract"]["accounts"] is False
        assert "## Accounts" not in snap["brief"]["text"]

    def test_task_description_is_added_to_the_spec_not_swapped_in(self):
        model, app = _model(), _app()
        plain = _snapshot(model=model, app=app)["brief"]
        with_context = _snapshot(model=model, app=app, description="  Prefer a dark colour scheme.  ")["brief"]
        assert with_context["version"] == 3
        assert "Additional context" not in plain["text"]
        # The spec's own description stays; the task description follows it.
        text = with_context["text"]
        assert "Track tasks\n\n## Additional context\nPrefer a dark colour scheme.\n\n## Backend requirements" in text
        # Everything else is unchanged.
        assert text.replace("## Additional context\nPrefer a dark colour scheme.\n\n", "") == plain["text"]

    def test_free_form_description_is_the_task_itself(self):
        snap = build_agent_snapshot(
            app_requirement=None,
            description="Build a URL shortener.",
            profile=None,
            stack_slug="generic-python",
            model=_model(),
            max_tokens=32000,
        )
        assert "Additional context" not in snap["brief"]["text"]
        assert "nobody will answer questions or review intermediate steps. " in snap["brief"]["text"]
        assert "\n\nBuild a URL shortener.\n\n" in snap["brief"]["text"]

    @pytest.mark.parametrize(
        ("server_kind", "filename", "port", "expected"),
        [
            ("uvicorn", "main.py", 9000, "uvicorn main:app --host 0.0.0.0 --port 9000"),
            ("python", "server.py", 8000, "python server.py"),
        ],
    )
    def test_user_stack_states_the_start_command_its_dockerfile_runs(self, server_kind, filename, port, expected):
        from backend.generation.services.agent.brief import stack_contract

        config = {
            "has_frontend": False,
            "backend_filename": filename,
            "default_port": port,
            "server_kind": server_kind,
        }
        contract = stack_contract("my-user-stack", config)
        assert contract["start_command"] == expected
        assert contract["backend_summary"] == "The backend is a Python web application."

    def test_builtin_stack_keeps_its_stated_start_command(self):
        from backend.generation.services.agent.brief import stack_contract

        contract = stack_contract("fastapi-react", {"has_frontend": True, "server_kind": "python"})
        assert contract["start_command"] == "uvicorn app:app --host 0.0.0.0 --port 8000"

    def test_user_stack_brief_uses_the_stack_row(self):
        from backend.runtime.models import Stack

        Stack.objects.create(
            slug="uvi-user-stack",
            version=1,
            name="Uvi",
            has_frontend=False,
            default_port=8000,
            backend_filename="main.py",
            backend_base_image="python:3.12-slim",
            server_kind="uvicorn",
            files={"requirements.txt": "fastapi\n"},
            dockerfile_mode=Stack.DockerfileMode.GENERATED,
            is_builtin=False,
            is_approved=True,
        )
        brief = _snapshot(stack_slug="uvi-user-stack")["brief"]["text"]
        assert "it is started with `uvicorn main:app --host 0.0.0.0 --port 8000`" in brief


class TestAgentSnapshot:
    def test_prompt_hash_is_the_task_identity_across_models_harnesses_seeds(self):
        app = _app()
        a = _snapshot(model=_model(), app=app, experiment_seed=1)
        b = _snapshot(model=_model(model_id="openai/gpt-x"), app=app, experiment_seed=2)
        c = _snapshot(model=_model(), app=app, agent_overrides={"harness": "mini-swe-agent"})
        assert a["prompt_hash"] == b["prompt_hash"] == c["prompt_hash"]
        assert len({a["run_fingerprint"], b["run_fingerprint"], c["run_fingerprint"]}) == 3
        assert a["method_key"] == "agent:opencode@1.18.35"
        assert c["method_key"].startswith("agent:mini-swe-agent@")

    def test_budget_changes_prompt_and_fingerprint(self):
        app, model = _app(), _model()
        a = _snapshot(model=model, app=app)
        b = _snapshot(model=model, app=app, agent_overrides={"max_llm_requests": 7})
        assert a["prompt_hash"] != b["prompt_hash"]  # the brief states the budget
        assert a["run_fingerprint"] != b["run_fingerprint"]

    def test_same_config_same_fingerprint(self):
        app, model = _app(), _model()
        assert _snapshot(model=model, app=app)["run_fingerprint"] == _snapshot(model=model, app=app)["run_fingerprint"]

    def test_opencode_prompt_is_its_own_default_pinned_for_every_model_and_subagent(self):
        app = _app()
        a = _snapshot(model=_model(), app=app)
        b = _snapshot(model=_model(model_id="anthropic/claude-x"), app=app)
        prompt = a["harness"]["system_prompt"]
        assert prompt.startswith("You are opencode, an interactive CLI tool")
        assert b["harness"]["system_prompt"] == prompt
        agents = a["harness"]["config"]["agent"]
        assert agents["build"]["prompt"] == agents["general"]["prompt"] == prompt
        assert a["harness"]["config"]["small_model"] == a["harness"]["config"]["model"]
        assert set(a["harness"]["config"]["tools"]) == set(DISABLED_TOOLS)
        assert a["harness"]["config"]["permission"]["doom_loop"] == "deny"

    def test_image_chain_is_recorded(self):
        images = _snapshot()["images"]
        assert images["base"].startswith("llmlab/agent-base:")
        assert images["harness"].startswith("llmlab/agent-opencode:")
        assert images["gateway"].startswith("llmlab/llm-gateway:")

    def test_no_real_key_anywhere_in_snapshot(self):
        assert "sk-or-" not in json.dumps(_snapshot())

    def test_no_implicit_sampling_params(self):
        gw = _snapshot(model=_model(max_output_tokens=64000))["gateway"]
        assert "temperature" not in gw["forced_params"]
        assert gw["forced_params"]["seed"] == 7
        assert gw["forced_params"]["max_tokens"] == 32000
        assert gw["forced_params"]["provider"]["require_parameters"] is True
        # provider_default: whatever the harness sends for sampling is stripped
        assert {"temperature", "top_p", "reasoning_effort"} <= set(gw["stripped_params"])
        assert {"seed", "provider", "stop"} <= set(gw["stripped_params"])

    def test_explicit_sampling_is_forced_not_stripped(self):
        gw = _snapshot(llm_overrides={"temperature": 0.2})["gateway"]
        assert gw["forced_params"]["temperature"] == 0.2
        assert "temperature" not in gw["stripped_params"]

    def test_harness_native_policy_lets_harness_sampling_through(self):
        gw = _snapshot(agent_overrides={"sampling_policy": "harness_native"})["gateway"]
        assert "temperature" not in gw["stripped_params"]

    def test_unsupported_params_are_dropped_and_recorded(self):
        model = _model(
            model_id="anthropic/claude-x",
            metadata={"openrouter_supported_parameters": ["temperature", "tools"]},
        )
        gw = _snapshot(model=model, llm_overrides={"top_p": 0.9, "stop": ["x"]})["gateway"]
        assert "seed" not in gw["forced_params"]
        assert "top_p" not in gw["forced_params"]
        assert set(gw["dropped_params"]) == {"seed", "top_p", "stop"}

    def test_output_cap_respects_model_limit(self):
        model = _model(max_output_tokens=8000)
        assert _snapshot(model=model)["gateway"]["forced_params"]["max_tokens"] == 8000

    def test_tool_calling_harness_rejects_tool_less_model_but_aider_accepts_it(self):
        model = _model(supports_function_calling=False)
        with pytest.raises(ValueError, match="tool-calling"):
            _snapshot(model=model)
        snap = _snapshot(model=model, app=_app(slug="todo-aider"), agent_overrides={"harness": "aider"})
        assert snap["harness"]["name"] == "aider"

    def test_canonical_slug_provenance(self):
        model = _model(canonical_slug="deepseek_deepseek-v4.1-flash-20260910")
        assert _snapshot(model=model)["llm"]["model_canonical_slug"] == "deepseek_deepseek-v4.1-flash-20260910"

    def test_profile_contributes_stack_and_llm_config_only(self):
        profile = GenerationProfileFactory(scaffolding_slug="fastapi-react", llm_config={"temperature": 0.9})
        snap = _snapshot(profile=profile, stack_slug="")
        assert snap["scaffolding_slug"] == "fastapi-react"
        assert snap["gateway"]["forced_params"]["temperature"] == 0.9
        assert snap["method_key"].startswith(f"{profile.slug}@")

    def test_free_form_task_without_app_spec(self):
        snap = build_agent_snapshot(
            app_requirement=None,
            description="Build a URL shortener.",
            profile=None,
            stack_slug="generic-python",
            model=_model(),
            max_tokens=32000,
        )
        assert "Build a URL shortener." in snap["brief"]["text"]

    def test_needs_spec_or_description(self):
        with pytest.raises(ValueError, match="app spec or a task description"):
            build_agent_snapshot(
                app_requirement=None,
                description="  ",
                profile=None,
                stack_slug="flask-react",
                model=_model(),
                max_tokens=100,
            )

    def test_reseed_keeps_prompt_changes_fingerprint(self):
        model = _model()
        snap = _snapshot(model=model)
        other = reseed_agent_snapshot(snap, 99)
        assert other["prompt_hash"] == snap["prompt_hash"]
        assert other["gateway"]["forced_params"]["seed"] == 99
        assert other["run_fingerprint"] != snap["run_fingerprint"]
        assert snap["gateway"]["forced_params"]["seed"] == 7


# ── harness adapters ────────────────────────────────────────────────────


class TestHarnesses:
    def test_registry(self):
        assert set(HARNESSES) == {"opencode", "mini-swe-agent", "aider"}
        assert get_harness("aider").requires_tool_calling is False
        with pytest.raises(ValueError, match="Unknown harness"):
            get_harness("nope")

    @pytest.mark.parametrize("name", ["opencode", "mini-swe-agent", "aider"])
    def test_brief_never_on_a_command_line(self, name):
        snap = _snapshot(agent_overrides={"harness": name}, model=_model())
        adapter = get_harness(name)
        files = adapter.files(snap["harness"], snap["brief"]["text"])
        command = files["harness.sh"].decode()
        assert snap["brief"]["text"].splitlines()[0] not in command
        assert command.startswith("cd /workspace\nexec ")
        assert files["brief.md"].decode() == snap["brief"]["text"]

    def test_mini_task_travels_as_config_file(self):
        snap = _snapshot(agent_overrides={"harness": "mini-swe-agent", "max_llm_requests": 42})
        files = get_harness("mini-swe-agent").files(snap["harness"], snap["brief"]["text"])
        assert json.loads(files["mini-task.yaml"])["run"]["task"] == snap["brief"]["text"]
        config = json.loads(files["mini-llmlab.yaml"])
        assert config["agent"]["step_limit"] == 42
        assert config["agent"]["mode"] == "yolo"
        assert config["model"]["model_kwargs"]["api_base"] == "http://llm-gateway:8080/api/v1"

    def test_aider_pins_edit_format_and_models(self):
        snap = _snapshot(agent_overrides={"harness": "aider"})
        args = snap["harness"]["args"]
        assert args[args.index("--edit-format") + 1] == "diff"
        assert args[args.index("--weak-model") + 1] == args[args.index("--model") + 1]
        assert "--no-suggest-shell-commands" in args
        assert get_harness("aider").workspace_artifacts == (".aider*",)
        files = get_harness("aider").files(snap["harness"], snap["brief"]["text"])
        meta = json.loads(files["aider-model-metadata.json"])
        info = meta["openai/deepseek/deepseek-v4.1-flash"]
        assert info["max_output_tokens"] == snap["gateway"]["max_output_tokens"]
        assert info["max_input_tokens"] > 0

    def test_parse_opencode_events(self):
        events = "\n".join(
            json.dumps(e)
            for e in [
                {
                    "type": "tool_use",
                    "sessionID": "ses_1",
                    "part": {"tool": "write", "state": {"status": "completed"}},
                },
                {"type": "tool_use", "sessionID": "ses_1", "part": {"tool": "bash", "state": {"status": "error"}}},
                {"type": "step_finish", "sessionID": "ses_1", "part": {"reason": "tool-calls"}},
                {"type": "text", "sessionID": "ses_1", "part": {"text": "Done: built the app."}},
                {"type": "step_finish", "sessionID": "ses_1", "part": {"reason": "stop"}},
            ]
        )
        summary = parse_events(events + "\nnot json\n").to_dict()
        assert summary["steps"] == 2
        assert summary["tool_calls"] == {"write": 1, "bash": 1}
        assert summary["tool_errors"] == 1
        assert summary["final_message"] == "Done: built the app."
        assert summary["unparsed_lines"] == 1

    def test_parse_mini_trajectory(self):
        traj = {
            "info": {"exit_status": "Submitted", "submission": "", "model_stats": {"api_calls": 3}},
            "messages": [
                {"role": "assistant", "content": "look", "tool_calls": [{"function": {"name": "bash"}}]},
                {"role": "assistant", "content": "done now", "tool_calls": [{"function": {"name": "bash"}}]},
            ],
        }
        summary = get_harness("mini-swe-agent").parse_report({"traj.json": json.dumps(traj).encode()})
        assert summary.exit_status == "Submitted"
        assert summary.steps == 3
        assert summary.tool_calls == {"bash": 2}
        assert summary.final_message == "done now"
        assert summary.errors == []

    def test_parse_aider_output(self):
        out = (
            b"Tokens: 2.6k sent, 370 received.\nApplied edit to app.py\nApplied edit to util.py\n"
            b"  const [error, setError] = useState('');\nlitellm.APIError: upstream down\n"
        )
        summary = get_harness("aider").parse_report({"events.jsonl": out})
        assert summary.steps == 1
        assert summary.tool_calls == {"edit": 2}
        assert summary.errors == ["litellm.APIError: upstream down"]

    def test_entry_script_runs_harness_file_and_excludes_artifacts(self):
        script = run_script(wall_clock_seconds=60, artifacts=(".aider*",))
        assert "sh /out/harness.sh" in script
        assert '".aider*"' in script
        assert "$(cat" not in script

    def test_proxy_env_spares_loopback(self):
        env = proxy_env("packages")
        assert "HTTP_PROXY" not in env
        assert "localhost" in env["NO_PROXY"]
        assert proxy_env("none") == {}


# ── workspace ─────────────────────────────────────────────────────────


class TestWorkspace:
    def test_tar_round_trip(self):
        tar = make_tar({"a.py": b"x", "frontend/src/App.jsx": b"y"})
        assert read_tar([tar], strip_prefix=".") == {"a.py": b"x", "frontend/src/App.jsx": b"y"}

    def test_collect_classifies_and_filters(self):
        seed = {"app.py": b"old", "requirements.txt": b"flask", "Dockerfile": b"FROM x", "README.md": b"r"}
        final = {
            "app.py": b"new",
            "requirements.txt": b"flask",
            "Dockerfile": b"FROM evil",
            "models.py": b"class M: ...",
            "node_modules/x/index.js": b"junk",
            "frontend/dist/app.js": b"built",
            "data/app.db": b"\x00sqlite",
            ".aider.tags.cache.v4/cache.db": b"x",
            "logo.png": b"\x89PNG\x00\x00",
            "big.txt": b"a" * (600 * 1024),
        }
        ws = collect(final, seed, protected=("Dockerfile",), has_frontend=True, artifacts=(".aider*",))
        assert set(ws.files) == {"app.py", "requirements.txt", "models.py"}
        assert ws.manifest["app.py"]["origin"] == "modified"
        assert ws.manifest["requirements.txt"]["origin"] == "scaffold"
        assert ws.manifest["models.py"]["origin"] == "added"
        assert ws.protected_modified == ["Dockerfile"]
        assert ws.deleted_scaffold_files == ["README.md"]
        assert {s["path"]: s["reason"] for s in ws.skipped} == {"logo.png": "binary", "big.txt": "too_large"}


def test_agent_static_checks_have_no_length_rule():
    result = validate_python_sources(
        {
            "extensions.py": "from flask_sqlalchemy import SQLAlchemy\ndb = SQLAlchemy()\n",
            "broken.py": "def f(:\n",
            "stubs.py": "def a():\n    pass\ndef b():\n    ...\ndef c():\n    pass\n",
        },
    )
    assert "extensions.py" not in result.errors
    assert "SyntaxError" in result.errors["broken.py"][0]
    assert "stub" in result.errors["stubs.py"][0]


# ── gateway (stdlib module, loaded straight from the image dir) ─────────


@pytest.fixture
def gateway(monkeypatch, tmp_path):
    config = {
        "model": "deepseek/deepseek-v4.1-flash",
        "forced_params": {"seed": 7, "max_tokens": 32000, "provider": {"require_parameters": True}},
        "stripped_params": ["seed", "provider", "stop", "temperature", "top_p"],
        "budget": {"max_requests": 2, "max_cost_usd": 1.0},
        "log_path": str(tmp_path / "calls.jsonl"),
        "events_path": str(tmp_path / "events.jsonl"),
        "retry_backoff_s": 0,
    }
    monkeypatch.setenv("GATEWAY_CONFIG", json.dumps(config))
    # No __pycache__ next to the image source: it would change the image's content hash.
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    path = Path(__file__).resolve().parents[1] / "images" / "llm-gateway" / "gateway.py"
    spec = importlib.util.spec_from_file_location("llm_gateway_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["llm_gateway_under_test"] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("llm_gateway_under_test", None)


class TestGateway:
    def test_rewrite_pins_model_strips_harness_sampling_and_forces_policy(self, gateway):
        body, diff = gateway._rewrite(
            {"model": "openrouter/small", "temperature": 0, "top_p": 0.5, "seed": 1, "stop": ["x"], "stream": True},
        )
        assert body["model"] == "deepseek/deepseek-v4.1-flash"
        assert "temperature" not in body
        assert "top_p" not in body
        assert body["seed"] == 7
        assert body["max_tokens"] == 32000
        assert body["usage"] == {"include": True}
        assert body["stream_options"]["include_usage"] is True
        assert diff["model"] == "openrouter/small"
        assert diff["stripped"]["temperature"] == 0

    def test_budget_counts_successes_and_in_flight_not_failures(self, gateway):
        assert gateway._budget_reason() is None
        gateway._state["attempts"] = 10  # failed attempts alone do not spend the step budget
        assert gateway._budget_reason() is None
        gateway._state["requests"] = 1
        gateway._state["inflight"] = 1
        assert gateway._budget_reason() == "max_requests"
        gateway._state.update(requests=0, inflight=0, attempts=gateway.MAX_ATTEMPTS)
        assert gateway._budget_reason() == "max_attempts"
        gateway._state.update(attempts=0, cost=1.5)
        assert gateway._budget_reason() == "max_cost_usd"

    def test_transient_upstream_errors_are_retried(self, gateway):
        class Resp:
            def __init__(self, status):
                self.status = status

            def read(self):
                return b"{}"

        class Conn:
            def close(self):
                pass

        seq = iter([Resp(503), Resp(429), Resp(200)])
        handler = gateway.Handler.__new__(gateway.Handler)
        handler._open_upstream = lambda data, stream: (Conn(), next(seq))
        handler._relay = lambda body, conn, resp: (resp.status, {"usage": None})
        status, _summary, retries = handler._forward({"stream": False})
        assert (status, retries) == (200, 2)

    def test_stream_collector_rebuilds_completion(self, gateway):
        collector = gateway._StreamCollector()
        chunks = [
            {"id": "gen-1", "provider": "Novita", "model": "m", "choices": [{"delta": {"content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo", "tool_calls": [{"index": 0, "id": "c1", "function": {"name": "bash", "arguments": '{"a"'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ": 1}"}}]}, "finish_reason": "tool_calls"}]},
            {"usage": {"prompt_tokens": 10, "completion_tokens": 3, "cost": 0.001}, "choices": []},
        ]  # fmt: skip
        raw = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        for i in range(0, len(raw), 7):
            collector.feed(raw[i : i + 7].encode())
        out = collector.summary()
        assert out["content"] == "Hello"
        assert out["tool_calls"] == [{"id": "c1", "name": "bash", "arguments": '{"a": 1}'}]
        assert out["usage"]["cost"] == 0.001


# ── runner: evidence → result_data / metrics / status ──────────────────


def _call(seq, *, status=200, provider="Novita", cost=0.001, error=None, diff=None):
    response = {
        "id": f"gen-{seq}",
        "provider": provider,
        "model": "deepseek/deepseek-v4.1-flash",
        "content": "",
        "tool_calls": [{"id": "c", "name": "write", "arguments": "{}"}],
        "finish_reason": "tool_calls",
        "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost": cost},
        "error": error,
    }
    return {
        "seq": seq,
        "status": status,
        "request": {"model": "deepseek/deepseek-v4.1-flash", "messages": [{"role": "user", "content": "x"}]},
        "policy_diff": diff or {},
        "response": response,
        "duration_s": 1.5,
        "upstream_retries": 0,
    }


def _result(calls, *, termination="completed", events=None, workspace=None):
    jsonl = "\n".join(json.dumps(c) for c in calls).encode()
    return SandboxResult(
        termination=termination,
        exit_code=0,
        wall_seconds=12.5,
        workspace=workspace if workspace is not None else {"app.py": b"print('hi')\n", "Dockerfile": b"FROM x"},
        outputs={
            "events.jsonl": (
                b'{"type":"step_finish","part":{"reason":"stop"}}\n{"type":"text","part":{"text":"done"}}\n'
            ),
            "exit.json": b'{"exit_code": 0, "agent_seconds": 11}',
            "diffstat.txt": b" app.py | 1 +\n",
        },
        gateway_logs={
            "calls.jsonl": jsonl,
            "gateway_events.jsonl": "\n".join(json.dumps(e) for e in events or []).encode(),
        },
        images={"harness": "llmlab/agent-opencode:abc", "harness_id": "sha256:1"},
    )


@pytest.fixture
def agent_job(settings, tmp_path):
    settings.GENERATION_RUNS_ROOT = str(tmp_path / "runs")
    job = GenerationJob.objects.create(
        mode=GenerationJob.Mode.AGENT,
        created_by=UserFactory(),
        model=_model(),
        stack_slug="generic-python",
        app_requirement=_app(),
    )
    return apply_agent_snapshot_to_job(job)


@pytest.fixture
def no_frontend_validator(monkeypatch):
    from backend.generation.services.generation_validation import ValidationResult

    monkeypatch.setattr(
        "backend.generation.services.agent.runner.validate_frontend_files",
        lambda files: ValidationResult(),
    )


class TestRunnerRecording:
    def test_completed_run_is_recorded(self, agent_job, no_frontend_validator):
        seed = {"Dockerfile": b"FROM x", "app.py": b"pass\n"}
        AgentRunner(agent_job)._record(
            agent_job.resolved_bundle,
            seed,
            _result([_call(1), _call(2, provider="DeepInfra")]),
        )
        rd = agent_job.result_data
        assert rd["materialize"] == "verbatim"
        assert rd["files"] == {"app.py": "print('hi')\n"}
        agent = rd["agent"]
        assert agent["termination"] == "completed"
        assert agent["providers"] == {"Novita": 1, "DeepInfra": 1}
        assert agent["llm_seconds"] == 3.0
        assert agent["sampling_policy"] == "provider_default"
        assert agent_job.metrics["cost"] == pytest.approx(0.002)
        assert agent_job.metrics["llm_calls"] == 2
        calls_path = Path(agent["run_record"]["_dir"]) / "llm_calls.jsonl.gz"
        assert len(gzip.decompress(calls_path.read_bytes()).splitlines()) == 2
        assert GenerationArtifact.objects.filter(job=agent_job, stage__startswith="agent_call_").count() == 2

    def test_budget_stop_is_a_sample_not_a_failure(self, agent_job, no_frontend_validator):
        events = [{"event": "budget_exhausted", "reason": "max_requests"}]
        AgentRunner(agent_job)._record(agent_job.resolved_bundle, {}, _result([_call(1)], events=events))
        assert agent_job.result_data["agent"]["termination"] == "budget_exhausted"

    def test_harness_sampling_strips_are_counted(self, agent_job, no_frontend_validator):
        calls = [_call(1, diff={"stripped": {"temperature": 0}}), _call(2, diff={"model": "other/small"})]
        AgentRunner(agent_job)._record(agent_job.resolved_bundle, {}, _result(calls))
        agent = agent_job.result_data["agent"]
        assert agent["harness_sampling_stripped"] == {"temperature": 1}
        assert agent["harness_policy_overrides"] == {"model": 1}

    def test_no_successful_call_is_an_infrastructure_failure(self, agent_job):
        calls = [_call(1, status=401, error={"message": "User not found."})]
        with pytest.raises(AgentRunError, match="User not found") as exc:
            AgentRunner(agent_job)._record(agent_job.resolved_bundle, {}, _result(calls))
        assert "API key" in exc.value.remediation


class TestValidity:
    def _notes(self, snapshot, result):
        return {n["code"]: n["severity"] for n in assess_validity(snapshot, result)["notes"]}

    def test_cost_cap_is_flagged_request_cap_is_not(self):
        snap = {"gateway": {"dropped_params": {}}, "llm": {"model_slug": "m"}}
        base = {
            "agent": {"termination": "budget_exhausted", "served_models": {"m": 3}},
            "evaluation": {"status": "passed"},
        }
        cost = self._notes(snap, {**base, "agent": {**base["agent"], "budget_exhausted": "max_cost_usd"}})
        reqs = self._notes(snap, {**base, "agent": {**base["agent"], "budget_exhausted": "max_requests"}})
        assert cost["budget:max_cost_usd"] == "warn"
        assert reqs["budget:max_requests"] == "info"

    def test_truncated_responses_are_flagged(self):
        snap = {"gateway": {"dropped_params": {}}, "llm": {"model_slug": "m"}}
        notes = self._notes(snap, {"agent": {"truncated_calls": 1}, "evaluation": {"status": "failed"}})
        assert notes["output_truncated"] == "warn"

    def test_evaluation_infra_error_excludes_the_sample(self):
        snap = {"gateway": {"dropped_params": {"seed": "x"}}, "llm": {"model_slug": "m"}}
        verdict = assess_validity(
            snap,
            {"agent": {"served_models": {"other": 1}}, "evaluation": {"status": "infra_error"}},
        )
        codes = {n["code"] for n in verdict["notes"]}
        assert verdict["valid"] is False
        assert {"evaluation_infra_error", "params_not_applied", "served_model_differs"} <= codes


class TestRunnerExecute:
    def _fake_sandbox(self, monkeypatch, result):
        monkeypatch.setattr("backend.credentials.services.resolver.get_openrouter_key", lambda user: "sk-test")
        monkeypatch.setattr(
            "backend.generation.services.agent.runner.seed_files",
            lambda job: {"app.py": b"pass\n", "Dockerfile": b"FROM x"},
        )
        fake = mock.MagicMock()
        fake.return_value.run.return_value = result
        monkeypatch.setattr("backend.generation.services.agent.runner.AgentSandbox", fake)
        return fake

    def test_sandbox_result_flows_to_completed_job(self, agent_job, no_frontend_validator, monkeypatch):
        from backend.generation.services.orchestrator import GenerationService

        fake = self._fake_sandbox(monkeypatch, _result([_call(1)]))
        GenerationService().execute(agent_job)
        agent_job.refresh_from_db()
        assert agent_job.status == GenerationJob.Status.COMPLETED
        agent = agent_job.result_data["agent"]
        assert agent["termination"] == "completed"
        assert agent["schedule"]["attempt"] == 0
        assert agent["validity"]["valid"] is True
        assert {n["code"] for n in agent["validity"]["notes"]} == {"not_evaluated"}
        wiring = fake.call_args.kwargs["wiring"]
        assert (wiring.api_key, wiring.network, wiring.image_local) == ("sk-test", "bridge", False)
        assert fake.call_args.args == (str(agent_job.id), 0)

    def test_auto_evaluation_runs_inside_the_slot(self, agent_job, no_frontend_validator, monkeypatch, settings):
        from backend.generation.services.orchestrator import GenerationService

        settings.AGENT_AUTO_EVALUATE = True
        self._fake_sandbox(monkeypatch, _result([_call(1)]))
        calls = []

        def fake_evaluate(job, **_kwargs):
            calls.append(job.id)
            job.result_data = {**job.result_data, "evaluation": {"status": "passed"}}

        monkeypatch.setattr("backend.generation.services.evaluation.runner.evaluate_job", fake_evaluate)
        GenerationService().execute(agent_job)
        agent_job.refresh_from_db()
        assert calls == [agent_job.id]
        assert "not_evaluated" not in {n["code"] for n in agent_job.result_data["agent"]["validity"]["notes"]}

    def test_cancelled_during_run_stays_cancelled(self, agent_job, monkeypatch):
        from backend.generation.services.orchestrator import GenerationService

        self._fake_sandbox(monkeypatch, _result([], termination="cancelled"))
        GenerationService().execute(agent_job)
        agent_job.refresh_from_db()
        assert agent_job.status == GenerationJob.Status.CANCELLED

    def test_missing_key_fails_with_remediation(self, agent_job, settings):
        from backend.generation.services.orchestrator import GenerationService

        settings.OPENROUTER_API_KEY = ""
        GenerationService().execute(agent_job)
        agent_job.refresh_from_db()
        assert agent_job.status == GenerationJob.Status.FAILED
        assert agent_job.result_data["error_remediation"]
        assert agent_job.attempt == 0  # a configuration error is not retried

    def test_local_run_after_a_proxy_infra_error_is_interrupted(self, agent_job, no_frontend_validator, monkeypatch):
        from backend.inference.gateway_wiring import GatewayWiring

        self._fake_sandbox(monkeypatch, _result([_call(1)]))
        wiring = GatewayWiring(
            image_local=True,
            upstream="http://proxy/v1",
            api_key="",
            network="llmlab-gw-egress",
            extra_env={"LLMLAB_UPSTREAM_TOKEN": "t"},
            upstream_timeout_s=1200,
        )
        monkeypatch.setattr("backend.inference.gateway_wiring.for_job", lambda job, snapshot: wiring)
        agent_job.resolved_bundle = {
            **agent_job.resolved_bundle,
            "endpoint": {"slug": "gpu", "served_model_name": "q"},
        }
        agent_job.status = GenerationJob.Status.RUNNING
        agent_job.execution = {"infra_errors": [{"code": "endpoint_unavailable", "status": 503}]}
        agent_job.save()
        assert AgentRunner(agent_job).run_stage() == "infra_interrupted"
        agent_job.refresh_from_db()
        assert agent_job.result_data["agent"]["termination"] == "completed"  # the sample was recorded first

    def test_transport_failures_without_a_successful_call_are_infra(self, agent_job):
        calls = [_call(1, status=503, error={"code": "endpoint_unavailable", "message": "relay gone"})]
        with pytest.raises(AgentRunError) as exc:
            AgentRunner(agent_job)._record(agent_job.resolved_bundle, {}, _result(calls))
        assert exc.value.infra is True
        calls = [_call(1, status=400, error={"message": "context too long"})]
        with pytest.raises(AgentRunError) as exc:
            AgentRunner(agent_job)._record(agent_job.resolved_bundle, {}, _result(calls))
        assert exc.value.infra is False

    def test_contended_and_retried_runs_are_flagged(self):
        snap = {"gateway": {"dropped_params": {}}, "llm": {"model_slug": "m"}}
        result = {"agent": {"wall_seconds": 100}, "evaluation": {"status": "passed"}}
        notes = {
            n["code"]: n["severity"]
            for n in assess_validity(snap, result, execution={"llm_queue_wait_s": 30}, attempt=1)["notes"]
        }
        assert (notes["contended"], notes["infra_retried"]) == ("warn", "warn")

    def test_old_snapshot_schema_is_not_replayed(self, agent_job):
        from backend.generation.services.orchestrator import GenerationService

        agent_job.resolved_bundle = {**agent_job.resolved_bundle, "bundle_schema_version": 1}
        agent_job.save()
        GenerationService().execute(agent_job)
        agent_job.refresh_from_db()
        assert agent_job.status == GenerationJob.Status.FAILED
        assert "earlier agent-mode version" in agent_job.error_message


# ── cloning / runtime / experiments / API ─────────────────────────────


class TestCloningAndRuntime:
    def test_rerun_reseeds_and_replay_keeps_seed(self, agent_job):
        rerun = clone_job(agent_job, agent_job.created_by, reuse_snapshot=True, new_seed=True)
        replay = clone_job(agent_job, agent_job.created_by, reuse_snapshot=True, new_seed=False)
        original = agent_job.resolved_bundle
        assert rerun.prompt_hash == agent_job.prompt_hash
        assert rerun.resolved_bundle["run_fingerprint"] != original["run_fingerprint"]
        assert rerun.resolved_bundle["gateway"]["forced_params"]["seed"] == rerun.experiment_seed
        assert replay.resolved_bundle["run_fingerprint"] == original["run_fingerprint"]

    def test_retry_resolves_a_fresh_agent_snapshot(self, agent_job):
        retry = clone_job(agent_job, agent_job.created_by, reuse_snapshot=False, new_seed=True)
        assert retry.resolved_bundle["mode"] == "agent"
        assert retry.resolved_bundle["harness"]["name"] == agent_job.resolved_bundle["harness"]["name"]

    def test_verbatim_materialize_keeps_platform_dockerfile(self, agent_job, tmp_path):
        from backend.runtime.services.scaffolding import prepare_build_dir

        agent_job.result_data = {
            "result_schema_version": 2,
            "materialize": "verbatim",
            "files": {
                "app.py": "AGENT CODE\n",
                "pkg/mod.py": "x = 1\n",
                "Dockerfile": "FROM evil",
                "../escape.py": "no",
            },
        }
        out = prepare_build_dir(agent_job, tmp_path / "build")
        assert (out / "app.py").read_text() == "AGENT CODE\n"
        assert (out / "pkg" / "mod.py").is_file()
        assert "evil" not in (out / "Dockerfile").read_text()
        assert not (tmp_path / "escape.py").exists()


class TestAgentApi:
    @pytest.fixture
    def auth_client(self, client):
        user = UserFactory()
        client.force_login(user)
        return client, user

    def _post(self, client, payload):
        with (
            mock.patch("backend.generation.api.views.custom.has_resolvable_key", return_value=True),
            mock.patch("backend.generation.api.views.custom.dispatch_job") as dispatch,
        ):
            res = client.post("/api/generation/jobs/agent/", data=json.dumps(payload), content_type="application/json")
        return res, dispatch

    def test_batch_uses_common_random_numbers_and_blocked_order(self, auth_client):
        client, user = auth_client
        app = _app()
        models = [_model(), _model(model_id="vendor/other")]
        res, dispatch = self._post(
            client,
            {
                "stack_slug": "flask-react",
                "app_requirement_ids": [app.id],
                "model_ids": [m.id for m in models],
                "harnesses": ["opencode", "aider"],
                "trials": 2,
                "base_seed": 123,
                "agent_config": {"max_llm_requests": 20},
            },
        )
        assert res.status_code == 200, res.content
        assert res.json()["job_count"] == 8
        jobs = list(GenerationJob.objects.filter(created_by=user))
        assert {j.resolved_bundle["harness"]["name"] for j in jobs} == {"opencode", "aider"}
        for trial in (0, 1):
            cell = [j for j in jobs if j.repeat_index == trial]
            assert len({j.experiment_seed for j in cell}) == 1  # same seed for every condition
        assert len({j.experiment_seed for j in jobs}) == 2
        assert len({j.prompt_hash for j in jobs}) == 1
        dispatched = [c.args[0].repeat_index for c in dispatch.call_args_list]
        assert dispatched == sorted(dispatched)  # all of trial 0 before any of trial 1

    def test_rejects_models_a_tool_calling_harness_cannot_use(self, auth_client):
        client, _ = auth_client
        model = _model(supports_function_calling=False)
        res, _ = self._post(client, {"description": "Build x", "model_ids": [model.id]})
        assert res.status_code == 400
        assert "tool-calling" in res.json()["detail"]
        res, _ = self._post(client, {"description": "Build x", "model_ids": [model.id], "harnesses": ["aider"]})
        assert res.status_code == 200

    def test_rejects_bad_agent_config_and_unknown_harness(self, auth_client):
        client, _ = auth_client
        model = _model()
        res, _ = self._post(
            client,
            {"description": "x", "model_ids": [model.id], "agent_config": {"max_cost_usd": 1000}},
        )
        assert res.status_code == 400
        res, _ = self._post(client, {"description": "x", "model_ids": [model.id], "harnesses": ["cursor"]})
        assert res.status_code == 400

    def test_harness_list(self, auth_client):
        client, _ = auth_client
        res = client.get("/api/generation/agent/harnesses/")
        assert {h["name"] for h in res.json()} == {"opencode", "mini-swe-agent", "aider"}

    def test_copilot_endpoint_is_retired(self, auth_client):
        client, _ = auth_client
        assert (
            client.post("/api/generation/jobs/copilot/", data="{}", content_type="application/json").status_code == 410
        )

    def test_evaluate_endpoint_queues_completed_jobs_only(self, auth_client, agent_job):
        client, user = auth_client
        agent_job.created_by = user
        agent_job.save()
        url = f"/api/generation/jobs/{agent_job.id}/evaluate/"
        assert client.post(url).status_code == 400
        agent_job.status = GenerationJob.Status.COMPLETED
        agent_job.save()
        with mock.patch("backend.generation.api.views.jobs.dispatch_evaluation") as dispatch:
            assert client.post(url).status_code == 200
        dispatch.assert_called_once()

    def test_run_record_download_checks_sha(self, auth_client, agent_job, no_frontend_validator):
        client, user = auth_client
        agent_job.created_by = user
        agent_job.save()
        AgentRunner(agent_job)._record(agent_job.resolved_bundle, {}, _result([_call(1)]))
        agent_job.save()
        assert client.get(f"/api/generation/jobs/{agent_job.id}/run-record/changes.patch").status_code == 200
        assert client.get(f"/api/generation/jobs/{agent_job.id}/run-record/_dir").status_code == 404
        path = Path(agent_job.result_data["agent"]["run_record"]["_dir"]) / "changes.patch"
        path.write_bytes(b"tampered")
        assert client.get(f"/api/generation/jobs/{agent_job.id}/run-record/changes.patch").status_code == 409


class TestAgentExperiments:
    def _experiment(self, **kw):
        user = UserFactory()
        experiment = ExperimentFactory(created_by=user, repeats=2, base_seed=5, **kw)
        experiment.app_requirements.set([_app(), _app(slug="todo2")])
        return experiment, user

    def test_launch_creates_agent_jobs_with_condition_harness_crn_and_blocks(self):
        from backend.generation.services.experiments import launch_experiment

        experiment, user = self._experiment(agent_config={"max_cost_usd": 0.25})
        profile = GenerationProfileFactory()
        model = _model()
        ExperimentConditionFactory(experiment=experiment, model=model, profile=profile, harness="opencode")
        ExperimentConditionFactory(experiment=experiment, model=model, profile=profile, harness="mini-swe-agent")
        with (
            mock.patch("backend.execution.api.wake"),
            mock.patch("backend.credentials.services.resolver.has_resolvable_key", return_value=True),
        ):
            launch_experiment(experiment, user)
        jobs = list(experiment.jobs.all())
        assert len(jobs) == 8
        assert {j.resolved_bundle["harness"]["name"] for j in jobs} == {"opencode", "mini-swe-agent"}
        assert {j.resolved_bundle["agent"]["max_cost_usd"] for j in jobs} == {0.25}
        for app_id in {j.app_requirement_id for j in jobs}:
            for repeat in (0, 1):
                cell = [j for j in jobs if j.app_requirement_id == app_id and j.repeat_index == repeat]
                assert len({j.experiment_seed for j in cell}) == 1
        # Executor-owned, in repeat blocks: every repeat-0 cell runs before any repeat-1 cell.
        assert all(j.queued_at is not None for j in jobs)
        order = [j.repeat_index for j in sorted(jobs, key=lambda j: (j.sequence or 0, j.queued_at))]
        assert order == sorted(order)

    def test_preview_reports_comparability(self):
        from backend.generation.services.experiments import preview_experiment

        experiment, _ = self._experiment(llm_defaults={"temperature": 0.2})
        profile = GenerationProfileFactory()
        ExperimentConditionFactory(experiment=experiment, model=_model(), profile=profile)
        no_temp = _model(model_id="o/reasoner", metadata={"openrouter_supported_parameters": ["tools", "seed"]})
        ExperimentConditionFactory(experiment=experiment, model=no_temp, profile=profile)
        report = preview_experiment(experiment)["comparability"]
        assert "forced_params" in report["differs_on"]
        assert report["has_problems"] is True
        assert any("temperature" in p for row in report["conditions"] for p in row["problems"])


def test_sync_keeps_variant_models_distinct():
    from backend.llm_models.models import LLMModel
    from backend.llm_models.services.sync import upsert_openrouter_models

    slug = "deepseek/deepseek-v4.1-flash-20260910"
    payload = [
        {"id": "deepseek/deepseek-v4.1-flash", "canonical_slug": slug, "supported_parameters": ["tools"]},
        {"id": "deepseek/deepseek-v4.1-flash:batch", "canonical_slug": slug, "supported_parameters": ["tools"]},
    ]
    assert upsert_openrouter_models(payload) == 2
    assert set(LLMModel.objects.values_list("model_id", flat=True)) >= {
        "deepseek/deepseek-v4.1-flash",
        "deepseek/deepseek-v4.1-flash:batch",
    }
