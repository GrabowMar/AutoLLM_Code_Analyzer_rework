"""Per-adapter ``render()`` tests: snapshots against the smoke runs and the section 5.2 rules."""

from __future__ import annotations

import dataclasses
import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from backend.generation.harnesses import registry
from backend.generation.harnesses.base import BRIEF_PATH
from backend.generation.harnesses.base import HOME_DIR
from backend.generation.harnesses.base import captured_prompt
from backend.generation.harnesses.base import command_script
from backend.generation.harnesses.base import json_bytes
from backend.generation.harnesses.codex import CONFIG_PATH as CODEX_CONFIG
from backend.generation.harnesses.codex import INSTRUCTIONS_PATH as CODEX_INSTRUCTIONS
from backend.generation.harnesses.gemini_cli import AUX_MODEL_ALIASES
from backend.generation.harnesses.gemini_cli import SETTINGS_PATH as GEMINI_SETTINGS
from backend.generation.harnesses.gemini_cli import SYSTEM_SETTINGS
from backend.generation.harnesses.gemini_cli import WORKSPACE_GUARD
from backend.generation.harnesses.opencode import CONFIG_DIR_SETUP as OPENCODE_SETUP
from backend.generation.harnesses.opencode import CONFIG_PATH as OPENCODE_CONFIG
from backend.generation.harnesses.opencode import GLOBAL_CONFIG_DIR as OPENCODE_GLOBAL_DIR
from backend.generation.harnesses.opencode import PREBAKED_CONFIG_DIR as OPENCODE_PREBAKED

from .conftest import ALL_HARNESSES
from .conftest import FIXTURE_DIRS
from .conftest import FIXTURES
from .conftest import SMOKE_BRIEF
from .conftest import SMOKED
from .conftest import make_ctx

MODEL = "vendor/some-model-7b"
IMAGES = Path(__file__).resolve().parents[2] / "images"


def _render(harness: str, **kwargs):
    return registry.get(harness).render(make_ctx(harness, **kwargs))


@pytest.mark.parametrize("harness", SMOKED)
def test_render_record_is_equal_for_every_run_of_a_cell(harness):
    """The run id stays out of the command and config files, so the plan-time hashes hold per cell (WP-10)."""
    adapter = registry.get(harness)
    a = make_ctx(harness)
    b = dataclasses.replace(a, run_id="11111111-1111-4111-8111-111111111111")
    assert adapter.render_record(adapter.render(a)) == adapter.render_record(adapter.render(b))


@pytest.mark.parametrize("harness", SMOKED)
def test_render_matches_the_smoke_run_snapshot(harness):
    """The command and env the smoke run executed are exactly what render() produces today."""
    fixture = FIXTURES / FIXTURE_DIRS[harness]
    rendered = _render(harness)
    assert command_script(rendered).decode() == (fixture / "command.sh").read_text()
    assert rendered.env == json.loads((fixture / "env.json").read_text())


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_brief_never_in_argv(harness):
    brief = "UNIQUE-BRIEF-MARKER build a todo app; kill python3 app.py"
    rendered = _render(harness, brief=brief)
    assert "UNIQUE-BRIEF-MARKER" not in rendered.command
    assert all("UNIQUE-BRIEF-MARKER" not in value for value in rendered.env.values())
    assert rendered.files[BRIEF_PATH] == brief.encode()
    # The brief reaches the harness as stdin or a file the command names.
    assert rendered.stdin_path == BRIEF_PATH or BRIEF_PATH in rendered.command or harness == "mini-swe-agent"


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_render_is_deterministic_and_home_is_fresh(harness):
    first, second = _render(harness), _render(harness)
    assert first == second
    assert first.env["HOME"] == HOME_DIR
    for path in first.files:
        assert not path.startswith("/workspace"), path  # config lives outside the workspace


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_model_and_aux_models_are_the_run_model(harness):
    rendered = _render(harness, model=MODEL)
    blob = rendered.command + json.dumps(rendered.env) + b"".join(rendered.files.values()).decode("utf-8", "replace")
    assert MODEL in blob
    adapter = registry.get(harness)
    for knob in adapter.facts.aux_model_knobs:
        if knob in rendered.env:
            assert rendered.env[knob] == MODEL, knob


def test_harness_side_cost_caps_are_never_set():
    for harness in ALL_HARNESSES:
        rendered = _render(harness)
        assert "--max-budget-usd" not in rendered.command
        if harness == "mini-swe-agent":
            config = json.loads(next(v for k, v in rendered.files.items() if k.endswith("mini-llmlab.yaml")))
            assert config["agent"]["cost_limit"] == 0


class TestOpenCode:
    def test_config(self):
        rendered = _render("opencode", model=MODEL)
        config = json.loads(rendered.files[OPENCODE_CONFIG])
        assert config == json.loads(rendered.env["OPENCODE_CONFIG_CONTENT"])
        assert config["small_model"] == config["model"] == f"openrouter/{MODEL}"
        assert config["tools"]["webfetch"] is False
        assert config["tools"]["task"] is False  # subagents off by default
        assert config["agent"]["build"]["prompt"] == captured_prompt("opencode", "1.18.35")
        assert config["provider"]["openrouter"]["options"]["baseURL"] == "http://llm-gateway:8080/api/v1"
        assert rendered.env["OPENCODE_DISABLE_AUTOUPDATE"] == "1"

    def test_knobs(self):
        rendered = _render(
            "opencode",
            subagents=True,
            command_timeout_s=300,
            policy_updates={
                "context.compaction": "off",
                "context.max_context_tokens": 50_000,
                "time.command_timeout_s": None,  # the preset's 300 s applies only when the policy is null
            },
        )
        config = json.loads(rendered.files[OPENCODE_CONFIG])
        assert "task" not in config["tools"]
        assert config["provider"]["openrouter"]["models"]["deepseek/deepseek-v4.1-flash"]["limit"]["context"] == 50_000
        assert rendered.env["OPENCODE_DISABLE_AUTOCOMPACT"] == rendered.env["OPENCODE_DISABLE_PRUNE"] == "1"
        assert rendered.env["OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS"] == "300000"
        rendered = _render("opencode", command_timeout_s=300, policy_updates={"time.command_timeout_s": 120})
        assert rendered.env["OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS"] == "120000"  # the policy overrides
        assert "--title llm-lab-run" in rendered.command  # constant: command_sha holds for every run of a cell

    def test_prompt_set_override(self):
        rendered = _render("opencode", system_prompt="You build apps.")
        assert json.loads(rendered.files[OPENCODE_CONFIG])["agent"]["general"]["prompt"] == "You build apps."

    def test_global_config_dir_is_prebaked_so_no_npm_install_runs(self):
        """OpenCode npm-installs ``@opencode-ai/plugin`` into ``$XDG_CONFIG_HOME/opencode`` unless node_modules and
        a covering package-lock.json are there (lead decision: no CONNECT registry.npmjs.org). The image pre-bakes
        that directory for the pinned version and the command sets it up before ``exec opencode``."""
        rendered = _render("opencode")
        setup, run = rendered.command.splitlines()
        assert setup == f"{OPENCODE_SETUP} || exit 70"
        assert run.startswith("exec opencode run ")
        assert f"{rendered.env['XDG_CONFIG_HOME']}/opencode" == OPENCODE_GLOBAL_DIR
        for name in ("package.json", "package-lock.json", ".gitignore"):
            assert f"{OPENCODE_PREBAKED}/{name}" in setup
        assert f"ln -s {OPENCODE_PREBAKED}/node_modules {OPENCODE_GLOBAL_DIR}/node_modules" in setup
        dockerfile = (IMAGES / "agent-opencode" / "Dockerfile").read_text()
        assert f"mkdir -p {OPENCODE_PREBAKED}" in dockerfile
        assert '"@opencode-ai/plugin@${OPENCODE_VERSION}"' in dockerfile
        # The root-run installs must not leave root-owned entries in the harness HOME (E2E-6: cp exit 70).
        assert "find /run/home -mindepth 1 -delete" in dockerfile.split("USER node")[0]


class TestMiniSweAgent:
    def config(self, **kwargs):
        rendered = _render("mini-swe-agent", **kwargs)
        return json.loads(next(v for k, v in rendered.files.items() if k.endswith("mini-llmlab.yaml"))), rendered

    def test_step_limit_never_exceeds_the_request_cap(self):
        config, _ = self.config(policy_updates={"run.max_turns": None, "run.max_llm_requests": 30})
        assert config["agent"]["step_limit"] == 30
        config, _ = self.config(policy_updates={"run.max_turns": 12, "run.max_llm_requests": 30})
        assert config["agent"]["step_limit"] == 12

    def test_command_timeout_and_task_file(self):
        config, rendered = self.config(command_timeout_s=300, policy_updates={"time.command_timeout_s": None})
        assert config["environment"] == {"timeout": 300}
        config, _ = self.config(command_timeout_s=300, policy_updates={"time.command_timeout_s": 120})
        assert config["environment"] == {"timeout": 120}  # time.command_timeout_s overrides the preset
        task = json.loads(next(v for k, v in rendered.files.items() if k.endswith("mini-task.yaml")))
        assert task == {"run": {"task": SMOKE_BRIEF}}
        assert "-o /logs/traj.json" in rendered.command


class TestAider:
    def test_args_and_metadata(self):
        rendered = _render("aider", model=MODEL, options={"edit_format": "whole", "map_tokens": 2048})
        assert f"--weak-model openai/{MODEL}" in rendered.command
        assert f"--editor-model openai/{MODEL}" in rendered.command
        assert "--edit-format whole" in rendered.command
        assert "--map-tokens 2048" in rendered.command
        assert "--no-check-update" in rendered.command
        assert "--analytics-disable" in rendered.command
        assert "--message-file /run/brief.md" in rendered.command
        assert "rm -rf .aider.tags.cache.v4" in rendered.command  # never left in the sample

    def test_slot_allowlist_restricts_the_added_files(self):
        from backend.generation.contracts.harness import SlotAgentContext
        from backend.generation.contracts.slot import SlotAgentTarget

        target = SlotAgentTarget.model_construct()
        ctx = make_ctx("aider")
        import dataclasses

        ctx = dataclasses.replace(ctx, slot=SlotAgentContext(target=target, allowlist=("backend/app.py",)))
        rendered = registry.get("aider").restrict_paths(ctx)
        assert rendered is not None
        assert rendered.command.split("\n")[0].endswith(" backend/app.py")
        assert registry.get("opencode").restrict_paths(ctx) is None


class TestClaudeCode:
    def test_command_and_env(self):
        rendered = _render("claude-code", model=MODEL)
        assert rendered.command.startswith("exec claude --bare -p --output-format stream-json --verbose")
        assert "--permission-mode bypassPermissions" in rendered.command
        assert "--disallowedTools WebFetch,WebSearch,Task,Agent" in rendered.command
        assert rendered.env["ANTHROPIC_BASE_URL"] == "http://llm-gateway:8080"
        assert rendered.env["ANTHROPIC_API_KEY"] == "sk-llmlab-dummy"  # --bare reads only this key
        for knob in ("ANTHROPIC_SMALL_FAST_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL"):
            assert rendered.env[knob] == MODEL
        assert rendered.env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
        assert rendered.env["DISABLE_AUTOUPDATER"] == rendered.env["DISABLE_TELEMETRY"] == "1"

    def test_knobs(self):
        rendered = _render(
            "claude-code",
            subagents=True,
            pin_system_prompt=True,
            tool_policy={"allow": ["Bash", "Read", "Edit", "Write"]},
            policy_updates={
                "reasoning.mode": "max_tokens",
                "reasoning.effort": None,
                "reasoning.max_tokens": 2048,
                "caching.prompt_cache": "off",
                "context.compaction": "off",
            },
        )
        assert "Task" not in rendered.command
        assert "--tools Bash,Read,Edit,Write" in rendered.command
        assert "--system-prompt-file /run/harness/claude-system.md" in rendered.command
        assert rendered.files["/run/harness/claude-system.md"].decode() == captured_prompt("claude-code", "2.1.316")
        assert rendered.env["MAX_THINKING_TOKENS"] == "2048"
        assert rendered.env["DISABLE_PROMPT_CACHING"] == rendered.env["DISABLE_AUTO_COMPACT"] == "1"


class TestCodex:
    def test_config_toml(self):
        rendered = _render("codex", model=MODEL)
        config = tomllib.loads(rendered.files[CODEX_CONFIG].decode())
        assert config["model"] == MODEL
        assert config["approval_policy"] == "never"
        assert config["web_search"] == "disabled"
        assert config["project_doc_max_bytes"] == 0
        assert config["features"]["multi_agent"] is False
        assert config["features"]["image_generation"] is False
        provider = config["model_providers"]["llmlab"]
        assert provider["wire_api"] == "responses"
        assert provider["request_max_retries"] == provider["stream_max_retries"] == 0
        assert config["model_instructions_file"] == CODEX_INSTRUCTIONS
        assert rendered.files[CODEX_INSTRUCTIONS].decode() == captured_prompt("codex", "0.166.0")
        assert rendered.env["CODEX_HOME"] == f"{HOME_DIR}/.codex"
        assert rendered.command.endswith("--sandbox danger-full-access -")
        assert "--strict-config" in rendered.command

    def test_reasoning_and_compaction(self):
        rendered = _render("codex", policy_updates={"reasoning.mode": "effort", "reasoning.effort": "high"})
        assert tomllib.loads(rendered.files[CODEX_CONFIG].decode())["model_reasoning_effort"] == "high"
        rendered = _render("codex", policy_updates={"context.compaction": "off"})
        assert tomllib.loads(rendered.files[CODEX_CONFIG].decode())["model_auto_compact_token_limit"] == 2**31 - 1

    def test_unmapped_knobs_are_recorded(self):
        """5.2 rule 5: an active knob Codex cannot apply is reported, never silently dropped."""
        adapter = registry.get("codex")
        ctx = make_ctx("codex", policy_updates={"time.command_timeout_s": 120})
        assert "time.command_timeout_s" in adapter.unsupported_knobs(ctx)
        ctx = make_ctx("codex", policy_updates={"reasoning.mode": "max_tokens", "reasoning.max_tokens": 2048})
        assert "reasoning" in adapter.unsupported_knobs(ctx)
        assert "model_reasoning_effort" not in tomllib.loads(adapter.render(ctx).files[CODEX_CONFIG].decode())
        ctx = make_ctx("codex", policy_updates={"reasoning.mode": "effort", "reasoning.effort": "high"})
        assert "reasoning" not in adapter.unsupported_knobs(ctx)

    def test_fairness_gaps_keep_it_blocked(self):
        questions = " ".join(registry.get("codex").facts.open_questions)
        assert "apply_patch" in questions
        assert "skills" in questions
        assert not registry.is_enabled("codex")


class TestGoose:
    def test_env_and_command(self):
        rendered = _render("goose", model=MODEL, options={"max_tool_repetitions": 4})
        env = rendered.env
        assert env["GOOSE_PROVIDER"] == "openai"
        assert env["OPENAI_HOST"] == "http://llm-gateway:8080"
        assert env["OPENAI_BASE_PATH"] == "api/v1/chat/completions"
        assert env["GOOSE_MODEL"] == env["GOOSE_SUBAGENT_MODEL"] == MODEL
        assert env["GOOSE_DISABLE_SESSION_NAMING"] == "true"
        assert env["CONTEXT_FILE_NAMES"] == "[]"
        assert "--no-profile" in rendered.command
        assert "--with-builtin developer" in rendered.command
        assert "--max-tool-repetitions 4" in rendered.command
        assert rendered.command.endswith("-i /run/brief.md")

    def test_compaction_off(self):
        assert (
            _render("goose", policy_updates={"context.compaction": "off"}).env["GOOSE_AUTO_COMPACT_THRESHOLD"] == "0"
        )


class TestQwenCode:
    def test_command_and_pinned_prompt(self):
        rendered = _render("qwen-code", model=MODEL)
        assert rendered.command.startswith("exec qwen --bare --approval-mode yolo --auth-type openai")
        assert "--exclude-tools web_fetch,web_search,task,agent" in rendered.command
        assert "--telemetry false" in rendered.command
        assert rendered.env["QWEN_SYSTEM_MD"] == "/run/harness/qwen-system.md"
        assert rendered.files["/run/harness/qwen-system.md"].decode() == captured_prompt("qwen-code", "0.26.6")
        assert rendered.env["OPENAI_MODEL"] == MODEL
        assert rendered.stdin_path == BRIEF_PATH

    def test_unmappable_knobs_are_reported(self):
        unsupported = registry.get("qwen-code").unsupported_knobs(make_ctx("qwen-code"))
        assert "call.max_output_tokens" in unsupported  # --bare ignores settings.json (smoke run)


class TestGeminiCli:
    def _settings(self, rendered):
        return json.loads(rendered.files[GEMINI_SETTINGS])

    def test_command_env_and_loopback(self):
        rendered = _render("gemini-cli", model=MODEL)
        assert rendered.command.endswith(f"exec gemini --skip-trust --approval-mode=yolo -o stream-json -m {MODEL}")
        assert "(while :; do node /run/harness/loopback.js; sleep 0.2; done) &" in rendered.command
        assert rendered.stdin_path == BRIEF_PATH  # piped stdin is the prompt (no -p)
        assert rendered.env["GOOGLE_GEMINI_BASE_URL"] == "http://127.0.0.1:18080"
        assert rendered.env["GEMINI_SANDBOX"] == "false"
        assert rendered.env["GEMINI_TELEMETRY_ENABLED"] == "false"
        assert rendered.env["GEMINI_SYSTEM_MD"] == "/run/harness/gemini-system.md"
        assert "GEMINI_WRITE_SYSTEM_MD" not in rendered.env
        assert rendered.files["/run/harness/gemini-system.md"].decode() == captured_prompt("gemini-cli", "0.66.0")
        forwarder = rendered.files["/run/harness/loopback.js"]
        assert b"process.title = 'llmlab-loopback'" in forwarder
        assert b"'llm-gateway:8080'.split(':')" in forwarder
        assert b"server.listen(18080, '127.0.0.1')" in forwarder

    def test_loopback_follows_the_gateway_wiring(self):
        adapter = registry.get("gemini-cli")
        ctx = dataclasses.replace(make_ctx("gemini-cli"), gateway=adapter.gateway_wiring("gw-7:9090"))
        rendered = adapter.render(ctx)
        assert b"'gw-7:9090'.split(':')" in rendered.files["/run/harness/loopback.js"]
        assert rendered.env["GOOGLE_GEMINI_BASE_URL"] == "http://127.0.0.1:18080"

    def test_user_settings_carry_only_per_run_values(self):
        settings = self._settings(_render("gemini-cli", model=MODEL, max_turns=12))
        assert set(settings) == {"model", "modelConfigs", "tools"}
        assert settings["model"]["name"] == MODEL
        assert settings["model"]["maxSessionTurns"] == 12
        assert settings["tools"]["exclude"] == ["web_fetch", "google_web_search", "activate_skill", "invoke_agent"]
        assert "core" not in settings["tools"]
        # The static switches live in the image's system settings, never in a file the agent could edit.
        assert not set(settings) & (set(SYSTEM_SETTINGS) - {"model", "tools"})

    def test_every_auxiliary_model_alias_is_pinned_to_the_run_model(self):
        overrides = self._settings(_render("gemini-cli", model=MODEL))["modelConfigs"]["overrides"]
        pinned = {o["match"]["model"]: o["modelConfig"].get("model") for o in overrides}
        for alias in AUX_MODEL_ALIASES:
            assert pinned[alias] == MODEL, alias
        main = next(o for o in overrides if o["match"]["model"] == MODEL)
        assert main["modelConfig"] == {"generateContentConfig": {"maxOutputTokens": 8192}}  # frugal call cap
        assert any("chat-compression-default" in knob for knob in registry.get("gemini-cli").facts.aux_model_knobs)

    def test_turn_cap_and_tool_policy(self):
        rendered = _render(
            "gemini-cli",
            max_turns=50,
            tool_policy={"deny": ["write_file"], "allow": ["read_file", "run_shell_command"]},
            policy_updates={"run.max_turns": 20},
        )
        settings = self._settings(rendered)
        assert settings["model"]["maxSessionTurns"] == 20  # the stricter of preset and policy
        assert "write_file" in settings["tools"]["exclude"]
        assert settings["tools"]["core"] == ["read_file", "run_shell_command"]

    def test_compression_threshold_is_the_native_rule_on_the_real_window(self):
        adapter = registry.get("gemini-cli")
        uncapped = make_ctx("gemini-cli", policy_updates={"context.max_context_tokens": None})
        ctx = dataclasses.replace(uncapped, model_context_window=131_072)
        assert adapter.compression_threshold(ctx) == round(0.5 * 131_072 / 1_048_576, 6)
        capped = make_ctx("gemini-cli", policy_updates={"context.max_context_tokens": 64_000})
        assert adapter.compression_threshold(capped) == round(0.5 * 64_000 / 1_048_576, 6)
        off = make_ctx("gemini-cli", policy_updates={"context.compaction": "off"})
        assert adapter.compression_threshold(off) == 1.0

    def test_unmappable_knobs_are_reported(self):
        adapter = registry.get("gemini-cli")
        ctx = make_ctx(
            "gemini-cli",
            subagents=True,
            policy_updates={"context.max_context_tokens": 64_000, "context.compaction": "auto"},
        )
        unsupported = adapter.unsupported_knobs(ctx)
        # Compaction never fires in a headless single-prompt run (smoke probe); subagents stay off.
        for key in ("context.max_context_tokens", "context.compaction", "subagents", "time.command_timeout_s"):
            assert key in unsupported
        assert "run.max_turns" not in unsupported
        assert not adapter.capabilities.supports_compaction_control

    def test_workspace_project_config_guard(self):
        command = _render("gemini-cli").command
        assert command.startswith(WORKSPACE_GUARD + "\n")
        assert "-name GEMINI.md -print -quit" in command
        assert "$(find" not in command.split("\n", 1)[0]  # find output is never word-split by a for loop
        assert "exit 52" in command  # the CLI's own config-error code; a harness error, never silent

    @pytest.mark.parametrize(
        ("layout", "refused"),
        [
            ((), False),
            (("src/app.py",), False),
            (("node_modules/pkg/GEMINI.md",), False),
            (("GEMINI.md",), True),
            (("sub/GEMINI.md",), True),
            (("a b/GEMINI.md",), True),
            (("my docs/deep er/GEMINI.md",), True),
            (("x\ty/GEMINI.md",), True),
            (("*/GEMINI.md",), True),
            ((".gemini/settings.json",), True),
            ((".gemini/.env",), True),
        ],
    )
    def test_workspace_guard_runs_under_sh(self, tmp_path, layout, refused):
        """Execute the rendered guard (not substrings of it) under sh in a workspace laid out as given."""
        command = _render("gemini-cli").command
        guard = command[: len(WORKSPACE_GUARD)]
        assert guard == WORKSPACE_GUARD
        for rel in layout:
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("pinned prompt must not be bypassed\n")
        result = subprocess.run(  # noqa: S603 - fixed argv, test-only
            ["/bin/sh", "-c", guard + "\necho guard-passed"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if refused:
            assert result.returncode == 52, result
            assert "guard-passed" not in result.stdout
            assert "refusing to run" in result.stderr
        else:
            assert result.returncode == 0, result
            assert result.stdout.strip() == "guard-passed"

    def test_system_settings_file_in_the_image_matches_the_adapter(self):
        """The image bakes SYSTEM_SETTINGS (root-owned /etc/gemini-cli); the two copies may never drift."""
        baked = IMAGES / "agent-gemini-cli" / "system-settings.json"
        assert baked.read_bytes() == json_bytes(SYSTEM_SETTINGS)
        dockerfile = (IMAGES / "agent-gemini-cli" / "Dockerfile").read_text()
        assert "COPY --chown=root:root system-settings.json /etc/gemini-cli/settings.json" in dockerfile
        assert SYSTEM_SETTINGS["privacy"]["usageStatisticsEnabled"] is False
        assert SYSTEM_SETTINGS["telemetry"]["enabled"] is False
        assert SYSTEM_SETTINGS["general"]["enableAutoUpdate"] is False
        assert SYSTEM_SETTINGS["general"]["maxAttempts"] == 1  # retries belong to the gateway
        assert SYSTEM_SETTINGS["model"] == {"skipNextSpeakerCheck": True, "disableLoopDetection": True}
        assert SYSTEM_SETTINGS["experimental"]["enableAgents"] is False
        assert SYSTEM_SETTINGS["skills"]["enabled"] is False
        assert SYSTEM_SETTINGS["admin"]["mcp"]["enabled"] is False

    def test_prompt_set_override_replaces_the_pin(self):
        rendered = _render("gemini-cli", system_prompt="Pinned.")
        assert rendered.files["/run/harness/gemini-system.md"] == b"Pinned."


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_render_record_leaves_the_brief_out(harness):
    adapter = registry.get(harness)
    record = adapter.render_record(_render(harness))
    assert BRIEF_PATH not in record["config_files_sha"]
    assert len(record["command_sha"]) == 64
    other = adapter.render_record(_render(harness, brief="another brief"))
    assert other == record  # the brief is a stimulus, not harness config
