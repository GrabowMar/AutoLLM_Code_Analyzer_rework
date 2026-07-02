"""The 'test' action for a tool: verify availability and run a sample."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import TYPE_CHECKING
from typing import Any

from backend.analysis.services import ai_runner
from backend.analysis.services import parsers
from backend.analysis.services import tool_installer
from backend.analysis.services import workspace_service
from backend.runtime.services import docker_manager

if TYPE_CHECKING:
    from backend.analysis.models import AnalyzerTool
    from backend.analysis.models import AnalyzerWorkspace

logger = logging.getLogger(__name__)

_DEFAULT_SAMPLES = {
    "python": "import hashlib\n\n\ndef hash_pw(p):\n    return hashlib.md5(p.encode()).hexdigest()\n",
    "javascript": "var x = 1\neval('1+1')\n",
}


def test_tool(
    workspace: AnalyzerWorkspace,
    tool: AnalyzerTool,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return ``{available, message, findings, raw_output}`` for *tool*."""
    sample = tool.sample_code or _DEFAULT_SAMPLES.get(tool.target_language, "")

    if tool.kind == "ai":
        output = ai_runner.run(tool, {"sample": sample}, config, workspace.user)
        return {
            "available": not output.has_error,
            "message": output.error or "AI tool reachable",
            "findings": [asdict(f) for f in output.findings],
            "raw_output": output.raw_output,
        }

    available, message = tool_installer.verify(workspace, tool)
    if not available:
        return {
            "available": False,
            "message": message,
            "findings": [],
            "raw_output": {},
        }

    findings: list[dict[str, Any]] = []
    raw: dict[str, Any] = {}
    if sample and tool.run_cmd:
        try:
            container_name = workspace_service.require_ready_container(workspace)
            ext = ".py" if tool.target_language == "python" else ".js"
            filename = tool.sample_filename or f"sample{ext}"
            docker_manager.copy_files_in(
                container_name,
                {filename: sample},
                dest="/tmp/analyzer-test",
            )
            cmd = tool.run_cmd.replace("{target}", "/tmp/analyzer-test")
            result = docker_manager.exec_in(
                container_name,
                cmd,
                timeout_s=min(tool.run_timeout, 60),
                workdir="/tmp/analyzer-test",
            )
            raw = {"exit_code": result.get("exit_code"), "stdout": (result.get("output") or "")[:4000]}
            if tool.parser_key:
                findings = [asdict(f) for f in parsers.parse(tool.parser_key, result.get("output", ""))]
        except RuntimeError as exc:
            return {"available": True, "message": str(exc), "findings": [], "raw_output": {}}

    return {
        "available": True,
        "message": message or "Tool available",
        "findings": findings,
        "raw_output": raw,
    }
