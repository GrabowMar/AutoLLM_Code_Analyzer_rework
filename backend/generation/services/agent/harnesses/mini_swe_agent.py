"""mini-swe-agent (https://mini-swe-agent.com): the SWE-agent team's minimal agent.

A ~100-line loop with a single ``bash`` tool and no editing helpers, widely
used as a neutral baseline ("bash-only" leaderboards). It runs with its
native ``mini.yaml`` prompts and loop; only the integration points are set:

* model via LiteLLM's OpenAI-compatible client pointed at the job gateway,
* ``mode: yolo`` / ``confirm_exit: false`` (no human to confirm),
* ``step_limit`` = the run's request budget (the gateway enforces it too),
  ``cost_limit`` off (the gateway owns cost),
* the brief goes in as a config file (``run.task``), never argv.

Native behaviour kept on purpose (and so part of what is compared): each
command runs in a fresh subshell with mini's default 30 s timeout.
"""

from __future__ import annotations

import json
from typing import Any

from backend.generation.services.agent.harnesses.base import DUMMY_API_KEY
from backend.generation.services.agent.harnesses.base import GATEWAY_BASE_URL
from backend.generation.services.agent.harnesses.base import OUTPUT_DIR
from backend.generation.services.agent.harnesses.base import Harness
from backend.generation.services.agent.harnesses.base import HarnessSummary

MINI = "/opt/harness/venv/bin/mini"
TRAJECTORY = f"{OUTPUT_DIR}/traj.json"


class MiniSweAgentHarness(Harness):
    name = "mini-swe-agent"
    label = "mini-swe-agent"
    image = "agent-mini-swe-agent"
    version_setting = "AGENT_MINI_SWE_AGENT_VERSION"
    version_build_arg = "MINI_SWE_AGENT_VERSION"
    requires_tool_calling = True

    def snapshot_section(
        self,
        *,
        model_slug: str,
        context_window: int,
        max_output_tokens: int,
        agent_config: dict[str, Any],
        title: str,
    ) -> dict[str, Any]:
        version = self.version()
        overrides = {
            "agent": {
                "mode": "yolo",
                "confirm_exit": False,
                "step_limit": int(agent_config["max_llm_requests"]),
                "cost_limit": 0,
                "output_path": TRAJECTORY,
            },
            "model": {
                "model_name": f"openai/{model_slug}",
                "cost_tracking": "ignore_errors",
                "model_kwargs": {"api_base": GATEWAY_BASE_URL, "api_key": DUMMY_API_KEY, "drop_params": True},
            },
        }
        return {
            "name": self.name,
            "version": version,
            "system_prompt_source": f"mini-swe-agent {version} built-in mini.yaml templates (native)",
            "base_config": "mini.yaml",
            "config": overrides,
            # LiteLLM would otherwise download its model price map at start-up.
            "env": {
                "MSWEA_CONFIGURED": "true",
                "MSWEA_SILENT_STARTUP": "1",
                "TERM": "dumb",
                "LITELLM_LOCAL_MODEL_COST_MAP": "True",
            },
            "title": title,
        }

    def files(self, section: dict[str, Any], brief: str) -> dict[str, bytes]:
        return {
            **super().files(section, brief),
            "mini-llmlab.yaml": json.dumps(section["config"]).encode(),
            # JSON is YAML: the brief travels as data, never on a command line.
            "mini-task.yaml": json.dumps({"run": {"task": brief}}).encode(),
        }

    def command(self, section: dict[str, Any]) -> str:
        return (
            f"{MINI} -c {section['base_config']} -c {OUTPUT_DIR}/mini-llmlab.yaml "
            f"-c {OUTPUT_DIR}/mini-task.yaml --exit-immediately"
        )

    def parse_report(self, outputs: dict[str, bytes]) -> HarnessSummary:
        summary = HarnessSummary()
        try:
            traj = json.loads(outputs.get("traj.json") or b"{}")
        except ValueError:
            summary.errors.append("trajectory is not JSON")
            return summary
        info = traj.get("info") or {}
        summary.exit_status = str(info.get("exit_status") or "")
        stats = info.get("model_stats") or {}
        summary.steps = int(stats.get("api_calls") or 0)
        last_text = ""
        for message in traj.get("messages") or []:
            if message.get("role") != "assistant":
                continue
            calls = message.get("tool_calls") or []
            for call in calls:
                tool = (call.get("function") or {}).get("name") or "bash"
                summary.tool_calls[tool] = summary.tool_calls.get(tool, 0) + 1
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                last_text = content
        summary.final_message = str(info.get("submission") or "") or last_text
        if summary.exit_status and summary.exit_status not in ("Submitted", "LimitsExceeded"):
            summary.errors.append(summary.exit_status)
        return summary
