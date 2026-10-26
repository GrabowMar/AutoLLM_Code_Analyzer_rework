"""Review variants of the generation harnesses: sections, commands, brief, payload extraction."""

from __future__ import annotations

import json
import shlex

import pytest

from backend.analysis.engine.reviewer import prompt
from backend.analysis.engine.reviewer.agentic_harness import FINDINGS_PATH
from backend.analysis.engine.reviewer.agentic_harness import REVIEW_HARNESSES
from backend.analysis.engine.reviewer.agentic_harness import ModelInfo
from backend.analysis.engine.reviewer.agentic_harness import brief_schema
from backend.analysis.engine.reviewer.agentic_harness import extract_json_document
from backend.analysis.engine.reviewer.agentic_harness import get_review_harness
from backend.analysis.engine.reviewer.agentic_harness import render
from backend.analysis.engine.reviewer.agentic_harness import render_brief
from backend.generation.services.agent.harnesses import get_harness
from backend.generation.services.agent.images import harness_image_plan
from backend.generation.services.agent.images import image_tag

MODEL = ModelInfo(slug="deepseek/deepseek-v4.1-flash", context_window=1_048_576, output_cap=16000, supports_tools=True)
FILES = ["app.py", "frontend/src/App.jsx", "frontend/src/api.js"]


class _Row:
    """An LLMModel-like object (attributes only)."""

    model_id = "deepseek/deepseek-v4.1-flash"
    context_window = 1_048_576
    max_output_tokens = 943_718
    supports_function_calling = True


# --------------------------------------------------------------------------
# Templates and model facts
# --------------------------------------------------------------------------


def test_render_requires_exactly_the_given_placeholders():
    assert render("a @@x@@ b {{task}}", {"x": "1"}) == "a 1 b {{task}}"
    with pytest.raises(ValueError, match="missing"):
        render("@@x@@ @@y@@", {"x": "1"})
    with pytest.raises(ValueError, match="unused"):
        render("@@x@@", {"x": "1", "z": "2"})


def test_model_info_is_identical_from_a_catalog_row_and_the_frozen_dict():
    frozen = {
        "slug": "deepseek/deepseek-v4.1-flash",
        "context_window": 1_048_576,
        "max_output_tokens": 943_718,
        "supports": {"tools": True},
    }
    assert ModelInfo.from_any(_Row(), max_output_tokens=16000) == ModelInfo.from_any(frozen, max_output_tokens=16000)
    capped = ModelInfo.from_any({**frozen, "max_output_tokens": 8000}, max_output_tokens=16000)
    assert capped.output_cap == 8000
    unknown = ModelInfo.from_any({"slug": "x/y"})
    assert unknown.context_window == 128_000
    assert unknown.supports_tools is None
    with pytest.raises(ValueError, match="slug"):
        ModelInfo.from_any({})


# --------------------------------------------------------------------------
# Per-harness sections
# --------------------------------------------------------------------------


def test_opencode_review_permissions_never_ask_and_allow_only_the_findings_file():
    harness = get_review_harness("opencode")
    section = harness.section(model=MODEL, options={}, max_llm_requests=30)
    config = section["config"]
    permission = config["permission"]
    assert permission["read"] == "allow"  # incl. *.env, which OpenCode would otherwise ask about
    assert permission["edit"] == {"*": "deny", "*.llmlab-review/findings.json": "allow"}
    assert permission["bash"] == "allow"
    for key in ("webfetch", "websearch", "external_directory", "question", "doom_loop", "plan_enter"):
        assert permission[key] == "deny"
    # The env var OpenCode actually reads carries the same review config.
    assert json.loads(section["env"]["OPENCODE_CONFIG_CONTENT"]) == config
    # Pinned generation behaviour is kept: model, prompt for build/general, disabled tools.
    assert config["model"] == "openrouter/deepseek/deepseek-v4.1-flash"
    assert config["agent"]["build"]["prompt"] == section["system_prompt"]
    assert config["tools"] == {"webfetch": False, "websearch": False, "skill": False}
    assert section["title"] == "llm-lab-review"
    assert section["review"]["findings_path"] == FINDINGS_PATH
    command = harness.command(section, FILES)
    assert command.startswith("opencode run --format json --pure")
    assert command.endswith("< /out/brief.md")


def test_opencode_review_section_does_not_touch_the_generation_section():
    harness = get_review_harness("opencode")
    harness.section(model=MODEL, options={}, max_llm_requests=30)
    generation = get_harness("opencode").snapshot_section(
        model_slug=MODEL.slug,
        context_window=MODEL.context_window,
        max_output_tokens=MODEL.output_cap,
        agent_config={"max_llm_requests": 30},
        title="t",
    )
    assert generation["config"]["permission"]["edit"] == "allow"


def test_mini_review_replaces_the_instance_template_and_collects_the_trajectory():
    harness = get_review_harness("mini-swe-agent")
    section = harness.section(model=MODEL, options={"command_timeout_seconds": 60}, max_llm_requests=30)
    agent = section["config"]["agent"]
    template = agent["instance_template"]
    assert template.startswith("{{task}}")
    assert "edit the source code" not in template
    assert f"echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat {FINDINGS_PATH}" in template
    assert "@@" not in template
    assert agent["step_limit"] == 30
    assert agent["mode"] == "yolo"
    assert section["config"]["environment"] == {"timeout": 60}
    files = harness.files(section, "BRIEF", FILES)
    assert json.loads(files["mini-llmlab.yaml"])["agent"]["instance_template"] == template
    assert json.loads(files["mini-task.yaml"]) == {"run": {"task": "BRIEF"}}
    # The CLI's own --output default would otherwise override agent.output_path.
    assert files["harness.sh"].decode().rstrip().endswith("--exit-immediately -o /out/traj.json")


def test_aider_review_is_ask_mode_without_git_and_reads_every_file_read_only():
    harness = get_review_harness("aider")
    section = harness.section(model=MODEL, options={}, max_llm_requests=30)
    args = section["args"]
    assert args[args.index("--chat-mode") + 1] == "ask"
    assert args[args.index("--map-tokens") + 1] == "0"
    assert "--no-git" in args
    assert "--edit-format" not in args
    assert args[args.index("--message-file") + 1] == "/out/brief.md"
    paths = ["app.py", "src/it's here.js", "a b.py"]
    command = harness.command(section, paths)
    tokens = shlex.split(command)
    reads = [tokens[i + 1] for i, t in enumerate(tokens) if t == "--read"]
    assert reads == sorted(paths)
    files = harness.files(section, "BRIEF", paths)
    metadata = json.loads(files["aider-model-metadata.json"])
    assert metadata["openai/deepseek/deepseek-v4.1-flash"]["max_output_tokens"] == 16000
    assert harness.output_channel == "answer"


def test_review_options_offer_the_finalizer_and_only_meaningful_adapter_options():
    assert get_review_harness("opencode").normalize_options({"finalizer": True}) == {}
    assert get_review_harness("opencode").normalize_options({"finalizer": False}) == {"finalizer": False}
    mini = get_review_harness("mini-swe-agent")
    assert mini.normalize_options({"command_timeout_seconds": 45}) == {"command_timeout_seconds": 45}
    assert mini.adapter_options({"command_timeout_seconds": 45, "finalizer": False}) == {"command_timeout_seconds": 45}
    with pytest.raises(ValueError, match="edit_format"):
        get_review_harness("aider").normalize_options({"edit_format": "whole"})
    with pytest.raises(ValueError, match="finalizer must be true or false"):
        get_review_harness("aider").normalize_options({"finalizer": "yes"})
    with pytest.raises(ValueError, match="Unknown review harness"):
        get_review_harness("claude-code")


@pytest.mark.parametrize("name", sorted(REVIEW_HARNESSES))
def test_images_are_generations_content_addressed_tags(name):
    harness = get_review_harness(name)
    version = harness.version()
    adapter = get_harness(name)
    plan = harness_image_plan(adapter.image, adapter.build_args(version))
    images = harness.images(version)
    assert images["base"] == plan[0][2]
    assert images["harness"] == plan[1][2]
    assert images["gateway"] == image_tag("llm-gateway")
    assert images["harness_dir"] == adapter.image


@pytest.mark.parametrize("name", sorted(REVIEW_HARNESSES))
def test_prompt_hash_covers_every_template_the_harness_uses(name):
    harness = get_review_harness(name)
    templates = harness.templates()
    assert templates[0] == "agent-brief-v1.md"
    assert "agent-finalizer-v1.md" in templates
    assert len(harness.prompt_sha256()) == 64
    assert set(harness.template_hashes()) == set(templates)
    if name == "mini-swe-agent":
        assert "agent-mini-instance-v1.md" in templates
    expected_output = "agent-output-answer-v1.md" if name == "aider" else "agent-output-file-v1.md"
    assert expected_output in templates


# --------------------------------------------------------------------------
# Brief
# --------------------------------------------------------------------------


def test_brief_lists_the_files_in_order_and_shows_the_schema_without_single_shot_wording():
    schema = prompt.findings_schema(sorted(FILES))
    order = ["frontend/src/api.js", "app.py", "frontend/src/App.jsx"]
    brief = render_brief(get_review_harness("opencode"), scope_paths=order, schema=schema)
    listed = [line[3:-1] for line in brief.splitlines() if line.startswith("- `")]
    assert listed == order
    assert FINDINGS_PATH in brief
    assert "@@" not in brief
    assert "FILE header" not in brief
    shown = json.loads(brief.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert shown["properties"]["findings"]["items"]["properties"]["file"]["enum"] == sorted(FILES)
    assert shown["required"] == schema["required"]

    answer = render_brief(get_review_harness("aider"), scope_paths=order, schema=schema)
    assert FINDINGS_PATH not in answer
    assert "```json fenced block" in answer


def test_brief_schema_drops_annotations_only():
    schema = {
        "type": "object",
        "description": "doc",
        "properties": {"description": {"type": "string", "description": "a field named description"}},
    }
    assert brief_schema(schema) == {"type": "object", "properties": {"description": {"type": "string"}}}


# --------------------------------------------------------------------------
# Final answers
# --------------------------------------------------------------------------


DOC = {"findings": [], "files_reviewed": ["app.py"], "review_complete": True}


def test_extract_json_document_prefers_the_whole_answer_then_the_last_fenced_block():
    assert json.loads(extract_json_document(json.dumps(DOC))) == DOC
    text = f'Draft:\n```json\n{{"findings": 1}}\n```\nFinal:\n```json\n{json.dumps(DOC)}\n```\n'
    assert json.loads(extract_json_document(text)) == DOC


def test_extract_json_document_finds_the_findings_object_in_prose_with_code():
    text = (
        "Looking at `function f() { return {a: 1}; }` I found problems.\n"
        f"Here is the result: {json.dumps(DOC)} -- done."
    )
    assert json.loads(extract_json_document(text)) == DOC


def test_extract_json_document_returns_none_without_a_document():
    assert extract_json_document("") is None
    assert extract_json_document("No issues found.") is None
    assert extract_json_document('{"truncated": [1, 2') is None


def test_opencode_files_opened_come_from_read_tool_calls():
    events = [
        {"type": "tool_use", "part": {"tool": "read", "state": {"input": {"filePath": "/workspace/app.py"}}}},
        {"type": "tool_use", "part": {"tool": "read", "state": {"input": {"filePath": "/etc/passwd"}}}},
        {"type": "tool_use", "part": {"tool": "grep", "state": {"input": {"pattern": "x"}}}},
        {"type": "text", "part": {"text": "done"}},
    ]
    outputs = {"events.jsonl": "\n".join(json.dumps(e) for e in events).encode() + b"\nnot json\n"}
    opened, method = get_review_harness("opencode").files_opened(outputs, FILES)
    assert opened == ["app.py"]
    assert method == "read_tool_calls"


def test_mini_files_opened_come_from_bash_commands_and_final_answer_from_submission():
    traj = {
        "info": {"exit_status": "Submitted", "submission": json.dumps(DOC), "model_stats": {"api_calls": 3}},
        "messages": [
            {
                "role": "assistant",
                "content": "look",
                "tool_calls": [{"function": {"arguments": '{"command": "nl -ba app.py"}'}}],
            },
            {"role": "user", "content": "output"},
        ],
    }
    harness = get_review_harness("mini-swe-agent")
    outputs = {"traj.json": json.dumps(traj).encode()}
    opened, method = harness.files_opened(outputs, FILES)
    assert opened == ["app.py"]
    assert method == "command_mentions"
    summary = harness.report(outputs)
    assert harness.final_answer(outputs, summary, "") == json.dumps(DOC)
    assert harness.files_opened({"traj.json": b"not json"}, FILES) == (None, "not_observable")


def test_aider_final_answer_is_the_gateway_record_of_the_completion():
    harness = get_review_harness("aider")
    outputs = {"events.jsonl": b"Aider v0.86.2\nTokens: 9k sent\n"}
    summary = harness.report(outputs)
    assert harness.final_answer(outputs, summary, "the model's reply") == "the model's reply"
    assert harness.files_opened(outputs, FILES) == (sorted(FILES), "all_files_in_context")


def test_a_broken_self_report_never_raises():
    harness = get_review_harness("mini-swe-agent")
    summary = harness.report({"traj.json": b"[1, 2]"})
    assert summary.errors
