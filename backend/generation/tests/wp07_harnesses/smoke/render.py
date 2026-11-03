"""Render one adapter into a stage directory for a scripted-upstream smoke run (docs/HARNESSES.md).

Run inside the worktree test container (Django settings needed), then run ``run_smoke.sh`` on the host:

    ./.wt-test.sh wp07 -- python backend/generation/tests/wp07_harnesses/smoke/render.py <harness> <out> [model]

``<out>/stage/`` mirrors the in-container paths (``run/``, ``workspace/``), ``<out>/env.list`` is a
``docker --env-file`` and ``<out>/meta.json`` names the image. With ``--capture`` the adapter's pinned
prompt (when its capture file does not exist yet) is replaced by the harness's own default, and the
harness's prompt-dump switch is set where it has one, so the native prompt can be captured.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import django

# The worktree root (/app in the test container) must be importable as the ``backend`` package's parent.
sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
django.setup()

from backend.generation.contracts.library import HarnessPresetContent  # noqa: E402
from backend.generation.contracts.library import ToolPolicy  # noqa: E402
from backend.generation.harnesses import registry  # noqa: E402
from backend.generation.harnesses.base import GATEWAY_HOST  # noqa: E402
from backend.generation.harnesses.base import CapturedPromptMissingError  # noqa: E402
from backend.generation.harnesses.base import sandbox_payload  # noqa: E402
from backend.generation.testing.fake_harness import harness_context  # noqa: E402

BRIEF = (
    "Create a file named hello.txt in the workspace root that contains a greeting, then stop.\n"
    "Do not start any server.\n"
)
SEED = {"README.md": "# Smoke workspace\n\nA tiny seed so the agent has a project to look at.\n"}
_CAPTURE_ENV = {
    "qwen-code": ("QWEN_SYSTEM_MD", "QWEN_WRITE_SYSTEM_MD"),
    "gemini-cli": ("GEMINI_SYSTEM_MD", "GEMINI_WRITE_SYSTEM_MD"),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("harness")
    parser.add_argument("out")
    parser.add_argument("model", nargs="?", default="deepseek/deepseek-v4.1-flash")
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--controlled", action="store_true")
    parser.add_argument("--max-turns", type=int, default=None)
    args = parser.parse_args()

    adapter = registry.get(args.harness)
    version = adapter.version()
    preset = HarnessPresetContent(
        harness=adapter.name,
        harness_version=version,
        controlled=args.controlled,
        command_timeout_s=300 if args.controlled else None,
        tool_policy=ToolPolicy(deny=[]),
        max_turns=args.max_turns,
        subagents=False,
        pin_system_prompt=False,
    )
    captured_placeholder = None
    ctx = harness_context(
        brief=BRIEF,
        model_name=args.model,
        gateway=adapter.gateway_wiring(GATEWAY_HOST),
        preset=preset,
    )
    try:
        rendered = adapter.render(ctx)
    except CapturedPromptMissingError:
        if not args.capture:
            raise
        captured_placeholder = "LLMLAB_CAPTURE_PLACEHOLDER"
        rendered = adapter.render(
            harness_context(
                brief=BRIEF,
                model_name=args.model,
                gateway=adapter.gateway_wiring(GATEWAY_HOST),
                preset=preset,
                system_prompt=captured_placeholder,
            ),
        )
    files = dict(rendered.files)
    env = dict(rendered.env)
    if captured_placeholder is not None:
        # Undo the placeholder so the harness uses its own default prompt.
        for path, data in list(files.items()):
            if data == captured_placeholder.encode():
                del files[path]
        if args.harness in _CAPTURE_ENV:
            pin, dump = _CAPTURE_ENV[args.harness]
            env.pop(pin, None)
            env[dump] = "/logs/native-system.md"
        if args.harness == "codex":
            for path, data in list(files.items()):
                if path.endswith("config.toml"):
                    lines = [ln for ln in data.decode().splitlines() if not ln.startswith("model_instructions_file")]
                    files[path] = ("\n".join(lines) + "\n").encode()
        if args.harness == "opencode":
            msg = "opencode has a captured prompt already"
            raise SystemExit(msg)
    payload = sandbox_payload(
        type(rendered)(files=files, env=env, command=rendered.command, stdin_path=rendered.stdin_path),
        wall_clock_s=300,
    )
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    stage = out / "stage"
    for path, data in payload.files.items():
        target = stage / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for rel, text in SEED.items():
        target = stage / "workspace" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    (out / "env.list").write_text("".join(f"{k}={v}\n" for k, v in sorted(payload.env.items()) if "\n" not in v))
    multiline = {k: v for k, v in payload.env.items() if "\n" in v}
    (out / "meta.json").write_text(
        json.dumps(
            {
                "harness": adapter.name,
                "version": version,
                "image": f"llmlab-v3-wt/{adapter.image_dir}:dev",
                "model": args.model,
                "entrypoint": list(payload.entrypoint),
                "multiline_env": sorted(multiline),
                "capture": captured_placeholder is not None,
            },
            indent=2,
        ),
    )
    if multiline:
        (out / "env.json").write_text(json.dumps(multiline))
    sys.stdout.write(f"rendered {adapter.name} {version} into {out}\n")


if __name__ == "__main__":
    main()
