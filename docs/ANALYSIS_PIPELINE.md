# Analysis pipeline

How an analysis run executes: per-user Docker workspaces, tool execution, output parsing, and finding persistence.

## Overview

An `AnalysisRun` targets either inline source code or the code of a generation job, and runs a selected list of tools. Each tool produces a `ToolResult`; results are parsed into normalized `Finding` rows (severity, category, file, line, rule) and/or a `metrics` JSON payload for tools that measure rather than flag (radon, jscpd).

Runs are dispatched on a **daemon thread** inside the django process (`dispatch` in `backend/analysis/services/runner.py`), not through Celery. `execute(run)` is also callable synchronously, which is how [automation pipelines](/docs/AUTOMATION_GUIDE) and tests use it.

## Analyzer workspaces

Container tools don't run against your machine directly — each user gets one long-lived, hardened container (`AnalyzerWorkspace`) created from the `backend/analyzer-base:latest` image. The image is built on demand from `backend/analysis/images/analyzer-base` (`ensure_base_image` in `workspace_service.py`).

Tools are installed into the workspace once, via each tool's `install_cmd`, and checked with its `verify_cmd` (`tool_installer.py`). Installed tools persist across runs, so only the first run pays the install cost.

> [!NOTE]
> Install tools one at a time. Concurrent installs can race workspace provisioning.

## Execution flow

For each run, `runner.execute`:

1. Marks the run `running` and publishes to the `analysis:<run_id>` SSE channel.
2. Materializes the target code into files and records which parts were truncated at generation time (surfaced on the result so you don't chase phantom syntax errors).
3. Ensures the workspace container is ready, wipes `/work` from the previous run, and copies the files in.
4. Runs each tool: `run_cmd` with `{target}` replaced by `/work`, bounded by the tool's `run_timeout`. AI tools skip the container and go through `ai_runner.py` instead.
5. Parses stdout with the parser registered under the tool's `parser_key` (`parsers.py`), persists findings and metrics, and finalizes the run status from the per-tool statuses (`completed`, `partial`, or `failed`).

Per-user tool configuration (from the workspace's installed tools, shaped by each tool's `config_schema`) is resolved at run time and passed to the tool.

## The AI reviewer

`llm_review` is a tool with `kind: ai`: instead of a container command, `ai_runner.py` sends the code to an LLM through OpenRouter using the run owner's API key — the same key resolution as generation. Its findings land in the same `Finding` table as every other tool, so AI review is comparable and exportable like the rest.

## Where results go

Findings and metrics feed the statistics dashboards, [model rankings](/docs/ARCHITECTURE), and reports; aggregations count only the **latest completed run per generation job**, so re-analyzing never double-counts. Raw findings are downloadable as CSV/JSON/SARIF via the export endpoints ([API reference](/docs/api-reference)).

For the tool catalog and how to add a tool, see the [Analyzer guide](/docs/ANALYZER_GUIDE).
