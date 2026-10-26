"""Agent engine: a coding-agent harness reviews a read-only copy of the app.

Reuses generation's harness adapters, content-addressed images and the
llm-gateway (key custody, pinned model, budgets, calls.jsonl) without
modifying the generation pipeline.

One sample = one sandbox (``agentic_sandbox``): the app's included files as
a disposable, read-only ``/workspace``, a versioned review brief
(``data/review/agent-brief-v1.md``, the same rubric as the single-shot
``review-v1`` prompt) listing the files under review and the findings
schema, and the harness in its review variant (``agentic_harness``). The
files under review are exactly the single-shot engine's
(``prompt.select_files`` minus binary/empty files), and so is the
replicate's seed (``single_shot.file_order_seed``: file order in the brief,
and the LLM seed when the model honours one). The findings document comes
back through the harness's native channel -- a file (opencode,
mini-swe-agent) or the final answer (aider, ask mode) -- and goes through
the same ``findings.parse_payload``.

Outcome rules (a failed sample is never "0 findings"):

* A valid document counts when the harness ended by itself within budget,
  or when the agent declared ``review_complete`` before a timeout/budget stop
  cut it off. A harness's own step/time limit is a budget/timeout stop too
  (mini-swe-agent's ``LimitsExceeded`` exits 0 before the gateway ever sees
  a request over budget), not a natural end.
* Coverage: a document is a review only when it shows one. For a harness
  whose agent decides what it reads (opencode, mini-swe-agent), the
  document must declare ``review_complete``; for every harness, it must
  claim (``files_reviewed``) or be observed (``files_opened``) to have read
  at least one file under review. Otherwise the sample is
  ``invalid_output`` / ``incomplete_review``: a zero-findings vote from a
  review that did not happen is not "nothing found".
* Cited paths are read relative to ``/workspace`` as the brief defines
  them, so the agent's own absolute paths (``/workspace/app.py``, as its
  tools print them) are not graded ``invalid_file``.
* Otherwise the termination decides: ``timed_out`` (wall clock),
  ``budget_exhausted`` (gateway budget), ``failed`` (OOM, harness crash or
  killed -- with the harness log tail -- or no successful LLM call at all,
  classified like single-shot: auth / insufficient_credits /
  model_unavailable / bad_request / api_error).
* A harness that ended by itself without a valid document is repaired at
  most once: a JSON document in its final answer, else (option
  ``finalizer``, within the remaining budget) one structured single-shot
  call turning the final answer into the schema. Either repair marks the
  sample ``repaired``; a failed repair is ``invalid_output`` (``truncated``
  when the final answer hit the output cap).

Usage and cost come from the gateway's call log over every call, failed
ones included, plus the finalizer call (an abandoned finalizer call makes the
spend a lower bound, ``cost_complete=False``); the transcript (call log,
harness events/trajectory/chat history, logs) is returned as artifacts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings

from backend.analysis.engine import sandbox as analysis_sandbox
from backend.analysis.engine.reviewer import SampleOutcome
from backend.analysis.engine.reviewer import client as client_mod
from backend.analysis.engine.reviewer import findings as findings_mod
from backend.analysis.engine.reviewer import prompt as prompt_mod
from backend.analysis.engine.reviewer import single_shot as single_shot_mod
from backend.analysis.engine.reviewer.agentic_harness import BRIEF_TEMPLATE
from backend.analysis.engine.reviewer.agentic_harness import BRIEF_VERSION
from backend.analysis.engine.reviewer.agentic_harness import FINDINGS_PATH
from backend.analysis.engine.reviewer.agentic_harness import REVIEW_HARNESS_REVISION
from backend.analysis.engine.reviewer.agentic_harness import ModelInfo
from backend.analysis.engine.reviewer.agentic_harness import ReviewHarness
from backend.analysis.engine.reviewer.agentic_harness import extract_json_document
from backend.analysis.engine.reviewer.agentic_harness import finalizer_prompt
from backend.analysis.engine.reviewer.agentic_harness import get_review_harness
from backend.analysis.engine.reviewer.agentic_harness import render_brief
from backend.analysis.engine.reviewer.agentic_harness import section_sha256
from backend.analysis.engine.reviewer.agentic_harness import template_sha256
from backend.analysis.engine.reviewer.agentic_sandbox import GATEWAY_MEM_LIMIT
from backend.analysis.engine.reviewer.agentic_sandbox import ReviewSandbox
from backend.analysis.engine.reviewer.agentic_sandbox import ReviewSandboxResult
from backend.analysis.engine.reviewer.agentic_sandbox import SandboxSpec
from backend.analysis.engine.reviewer.agentic_sandbox import gateway_config
from backend.analysis.engine.reviewer.agentic_usage import TOKEN_KEYS
from backend.analysis.engine.reviewer.agentic_usage import CallLog
from backend.generation.services.agent.config import default_provider_routing
from backend.generation.services.agent.harnesses.base import WORKSPACE_DIR
from backend.generation.services.agent.snapshot import ALWAYS_STRIPPED
from backend.generation.services.agent.snapshot import GATEWAY_PARAM_KEYS
from backend.generation.services.agent.snapshot import HARNESS_SAMPLING_KEYS

if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.analysis.engine.types import Sample
    from backend.analysis.engine.types import SampleFile
    from backend.generation.services.agent.harnesses.base import HarnessSummary

logger = logging.getLogger(__name__)

REVIEW_HARNESSES = ("opencode", "mini-swe-agent", "aider")
ENGINE = "agent"
FREEZE_SCHEMA_VERSION = 1
# Per-replicate budget when the plan carries none (reviewer.config's default).
DEFAULT_BUDGET: dict[str, Any] = {"max_cost_usd": 0.5, "max_llm_requests": 40, "wall_clock_seconds": 900}
BUDGET_LIMITS: dict[str, tuple[type, float, float]] = {
    "max_cost_usd": (float, 0.001, 100.0),
    "max_llm_requests": (int, 1, 2000),
    "wall_clock_seconds": (int, 60, 4 * 3600),
    "max_total_tokens": (int, 1000, 200_000_000),
}
DEFAULT_MEM_LIMIT = "1g"
DEFAULT_NANO_CPUS = 1_000_000_000
DEFAULT_CAPACITY_WAIT_SECONDS = 1800
FINALIZER_TIMEOUT_S = 300.0
FINALIZER_MAX_RETRIES = 2
FINALIZER_TEXT_CHARS = 200_000
HEARTBEAT_INTERVAL_S = 10.0
CHARS_PER_TOKEN = 3.5
LOG_TAIL_CHARS = 4000


class HarnessDriftError(ValueError):
    """The harness definition differs from what the plan froze (version, images, templates, revision)."""


# --------------------------------------------------------------------------
# Options + freezing (used by reviewer.config)
# --------------------------------------------------------------------------


def harness_review_options() -> list[dict[str, Any]]:
    """[{name, label, description, version, option_schema, default_options, requires_tool_calling, notes}]."""
    options = []
    for name in REVIEW_HARNESSES:
        harness = get_review_harness(name)
        options.append(
            {
                "name": name,
                "label": harness.label,
                "description": harness.description(),
                "version": harness.version(),
                "option_schema": [f.to_dict() for f in harness.option_fields()],
                "default_options": harness.effective_options(None),
                "requires_tool_calling": harness.requires_tool_calling,
                "output_channel": harness.output_channel,
                "notes": list(harness.notes),
            },
        )
    return options


def normalize_harness_options(harness: str, options: dict[str, Any] | None) -> dict[str, Any]:
    """Validated, sparse review options for *harness* (``ValueError`` listing every problem)."""
    return get_review_harness(harness).normalize_options(options)


def freeze_harness(
    harness: str,
    harness_options: dict[str, Any],
    *,
    model,
    max_output_tokens: int | None = None,
    budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Harness section for the frozen reviewer: version, images (tags), options, brief version/sha.

    *model* is an ``LLMModel`` (or the frozen ``reviewer.model`` dict).
    Raises ``ValueError`` for an unknown harness, invalid options, or a
    model that cannot drive a tool-calling harness. With *max_output_tokens*
    and *budget* (the reviewer's ``params.max_tokens`` / ``budget``) the
    exact harness section is hashed too (``section_sha256``), so a changed
    generation adapter is caught before a sample runs.

    Stored by ``reviewer.config.freeze_reviewer`` as ``reviewer["agent"]``
    (+ ``harness_version`` / ``harness_images`` / ``harness_options``).
    """
    review = get_review_harness(harness)
    options = review.normalize_options(harness_options)
    info = ModelInfo.from_any(model, max_output_tokens=max_output_tokens)
    if review.requires_tool_calling and info.supports_tools is False:
        msg = f"{review.label} needs a tool-calling model; {info.slug} does not support tools"
        raise ValueError(msg)
    version = review.version()
    images = review.images(version)
    frozen: dict[str, Any] = {
        "schema_version": FREEZE_SCHEMA_VERSION,
        "harness": review.name,
        "label": review.label,
        "version": version,
        "review_revision": REVIEW_HARNESS_REVISION,
        "options": options,
        "effective_options": review.effective_options(options),
        "images": {role: images[role] for role in ("base", "harness", "gateway")},
        "image_build": {"harness_dir": images["harness_dir"], "harness_buildargs": images["harness_buildargs"]},
        "brief": {
            "version": BRIEF_VERSION,
            "sha256": template_sha256(BRIEF_TEMPLATE),
            "prompt_sha256": review.prompt_sha256(),
            "templates": review.template_hashes(),
        },
        "output_channel": review.output_channel,
        "findings_path": FINDINGS_PATH if review.output_channel == "file" else None,
        "requires_tool_calling": review.requires_tool_calling,
        "sandbox": _sandbox_limits(),
    }
    if budget is not None:
        limits = _normalize_budget(budget)
        section = review.section(model=info, options=options, max_llm_requests=limits["max_llm_requests"])
        frozen["section_inputs"] = _section_inputs(info, limits)
        frozen["section_sha256"] = section_sha256(section)
    return frozen


# --------------------------------------------------------------------------
# One sample
# --------------------------------------------------------------------------


@dataclass
class _Prepared:
    harness: ReviewHarness
    options: dict[str, Any]
    effective_options: dict[str, Any]
    budget: dict[str, Any]
    model: ModelInfo
    section: dict[str, Any]
    scope_paths: list[str]  # files under review, in presentation order (shuffled with the seed)
    workspace_paths: list[str]
    not_sent: list[dict[str, str]]
    out_of_scope: dict[str, int]
    chars_in_scope: int
    brief: str
    seed: int
    gateway_params: dict[str, Any]  # {forced, stripped, dropped}
    spec: SandboxSpec
    drift_notes: list[str]


def run_sample(
    *,
    frozen: dict[str, Any],
    sample: Sample,
    index: int,
    api_key: str,
    run_id: str,
    is_cancelled: Callable[[], bool],
    heartbeat: Callable[[], None],
    docker_client=None,
) -> SampleOutcome:
    """Run one review replicate in a sandbox; every reviewer or infrastructure failure becomes its status.

    *docker_client* replaces the apps-daemon client (tests).
    """
    model = frozen.get("model") or {}
    outcome = SampleOutcome(
        index=index,
        engine=ENGINE,
        harness=str(frozen.get("harness") or ""),
        harness_version=str(frozen.get("harness_version") or ""),
        model_slug=str(model.get("slug") or ""),
        model_canonical_slug=str(model.get("canonical_slug") or ""),
        prompt_version=BRIEF_VERSION,
        schema_sha256=str(frozen.get("schema_sha256") or ""),
    )
    # Until the sandbox runs nothing can be sent: a sample that fails before it spent nothing (0.0, not unknown).
    outcome.usage = {"cost_usd": 0.0, "llm_calls": 0, "llm_calls_failed": 0, "http_attempts": 0}
    try:
        prepared = _prepare(frozen=frozen, sample=sample, index=index, run_id=run_id)
    except (ValueError, KeyError) as exc:  # HarnessDriftError is a ValueError
        return _fail(outcome, "failed", "invalid_config", f"The frozen reviewer cannot run: {exc}")
    outcome.harness_version = str(prepared.section.get("version") or outcome.harness_version)
    outcome.prompt_sha256 = prepared.harness.prompt_sha256()
    outcome.file_order_seed = prepared.seed
    outcome.coverage = _coverage(prepared)
    if not prepared.scope_paths:
        return _fail(outcome, "failed", "no_files", "no reviewable (non-empty, text) files in scope")
    outcome.artifacts["brief.md"] = prepared.brief.encode("utf-8")
    outcome.artifacts["harness_section.json"] = _json_bytes(prepared.section)
    outcome.artifacts["gateway_config.json"] = _json_bytes(prepared.spec.gateway_config)

    try:
        admitted = _admit(prepared, is_cancelled=is_cancelled, heartbeat=heartbeat)
    except analysis_sandbox.SandboxError as exc:
        return _fail(outcome, "failed", "infra_error", f"apps daemon unavailable: {exc}")
    if not admitted:
        if is_cancelled():
            return _fail(outcome, "cancelled", "cancelled", "review cancelled while waiting for sandbox capacity")
        return _fail(outcome, "failed", "infra_error", "no capacity on the apps daemon for the review sandbox")

    result = _execute(prepared, api_key=api_key, is_cancelled=is_cancelled, heartbeat=heartbeat, client=docker_client)
    try:
        _interpret(
            prepared,
            result,
            outcome=outcome,
            frozen=frozen,
            sample=sample,
            api_key=api_key,
            is_cancelled=is_cancelled,
            heartbeat=heartbeat,
        )
    except Exception as exc:
        # Spend and transcript are already on the outcome: keep them.
        logger.exception("Interpreting review sample %s of run %s failed", index, run_id)
        _fail(outcome, "failed", "engine_error", f"internal error while interpreting the review: {exc}")
    return outcome


def _prepare(*, frozen: dict[str, Any], sample: Sample, index: int, run_id: str) -> _Prepared:
    harness = get_review_harness(str(frozen.get("harness") or ""))
    options = harness.normalize_options(frozen.get("harness_options") or {})
    params = frozen.get("params") or {}
    budget = _normalize_budget(frozen.get("budget") or {})
    model_data = frozen.get("model") or {}
    model = ModelInfo.from_any(model_data, max_output_tokens=params.get("max_tokens"))
    current = freeze_harness(
        harness.name,
        options,
        model=model_data,
        max_output_tokens=params.get("max_tokens"),
        budget=budget,
    )
    drift_notes = _check_drift(frozen, current)

    files, out_of_scope = prompt_mod.select_files(
        sample,
        include_origins=frozen.get("include_origins") or prompt_mod.DEFAULT_INCLUDE_ORIGINS,
        include_kinds=frozen.get("include_kinds") or prompt_mod.DEFAULT_INCLUDE_KINDS,
    )
    reviewable, not_sent = _reviewable(files)
    seed = single_shot_mod.file_order_seed(int(params.get("seed_base") or 0), sample.sample_hash, index)
    scope_paths = sorted(f.path for f in reviewable)
    random.Random(seed).shuffle(scope_paths)  # noqa: S311 - reproducible presentation order, not security
    schema_version = frozen.get("schema_version") or prompt_mod.SCHEMA_VERSION
    schema = prompt_mod.findings_schema(sorted(scope_paths), schema_version)
    brief = render_brief(harness, scope_paths=scope_paths, schema=schema)

    section = harness.section(model=model, options=options, max_llm_requests=budget["max_llm_requests"])
    workspace = {f.path: f.content for f in sample.included()}
    workspace_paths = sorted(workspace)
    gateway_params = _gateway_params(frozen, seed=seed, output_cap=model.output_cap)
    limits = (_recorded_freeze(frozen) or {}).get("sandbox") or current["sandbox"]
    spec = SandboxSpec(
        run_id=str(run_id),
        index=index,
        images={**current["images"], **current["image_build"]},
        env=harness.env(section),
        workspace=workspace,
        out_files=harness.files(section, brief, workspace_paths),
        gateway_config=gateway_config(
            model_slug=model.slug,
            forced_params=gateway_params["forced"],
            stripped_params=gateway_params["stripped"],
            budget=budget,
            title=f"LLM Lab analysis review {str(run_id)[:8]}#{index}",
        ),
        wall_clock_seconds=int(budget["wall_clock_seconds"]),
        mem_limit=str(limits["mem_limit"]),
        nano_cpus=int(limits["nano_cpus"]),
        findings_path=FINDINGS_PATH if harness.output_channel == "file" else None,
    )
    return _Prepared(
        harness=harness,
        options=options,
        effective_options=harness.effective_options(options),
        budget=budget,
        model=model,
        section=section,
        scope_paths=scope_paths,
        workspace_paths=workspace_paths,
        not_sent=not_sent,
        out_of_scope=out_of_scope,
        chars_in_scope=sum(len(prompt_mod.file_text(f) or "") for f in reviewable),
        brief=brief,
        seed=seed,
        gateway_params=gateway_params,
        spec=spec,
        drift_notes=drift_notes,
    )


def _reviewable(files: list[SampleFile]) -> tuple[list[SampleFile], list[dict[str, str]]]:
    """Single-shot's rule: binary and empty files are not under review (recorded as not sent)."""
    reviewable: list[SampleFile] = []
    not_sent: list[dict[str, str]] = []
    for f in files:
        text = prompt_mod.file_text(f)
        if text is None:
            not_sent.append({"path": f.path, "reason": "binary"})
        elif not text.strip():
            not_sent.append({"path": f.path, "reason": "empty"})
        else:
            reviewable.append(f)
    return reviewable, not_sent


def _coverage(prepared: _Prepared) -> dict[str, Any]:
    return {
        "files_in_scope": len(prepared.scope_paths) + len(prepared.not_sent),
        # The agent's workspace holds every file under review; nothing is cut.
        "files_sent": sorted(prepared.scope_paths),
        "files_not_sent": list(prepared.not_sent),
        "files_split": [],
        "chars_sent": prepared.chars_in_scope,
        "chunks": None,  # not chunked: the agent reads the files itself
        "out_of_scope": dict(prepared.out_of_scope),
        "files_in_workspace": len(prepared.workspace_paths),
        "review_complete": None,
    }


def _admit(prepared: _Prepared, *, is_cancelled: Callable[[], bool], heartbeat: Callable[[], None]) -> bool:
    """Wait until apps-dind has memory for the harness + gateway (the executor's shared admission)."""
    mem_bytes = analysis_sandbox.parse_mem(prepared.spec.mem_limit) + analysis_sandbox.parse_mem(GATEWAY_MEM_LIMIT)
    timeout_s = int(getattr(settings, "ANALYSIS_CAPACITY_WAIT_SECONDS", DEFAULT_CAPACITY_WAIT_SECONDS))
    admitted = analysis_sandbox.wait_for_capacity(
        mem_bytes,
        is_cancelled=is_cancelled,
        heartbeat=heartbeat,
        timeout_s=timeout_s,
    )
    return bool(admitted)


def _execute(
    prepared: _Prepared,
    *,
    api_key: str,
    is_cancelled: Callable[[], bool],
    heartbeat: Callable[[], None],
    client,
) -> ReviewSandboxResult:
    """Run the sandbox; one retry when it failed before the harness started (nothing spent yet)."""
    result = ReviewSandbox(prepared.spec, api_key=api_key, client=client).run(
        is_cancelled=is_cancelled,
        heartbeat=heartbeat,
    )
    if result.termination == "infra_error" and not result.started and not is_cancelled():
        logger.warning("Review sandbox failed before start (%s); retrying once", result.error)
        time.sleep(2)
        first_error = result.error
        result = ReviewSandbox(prepared.spec, api_key=api_key, client=client).run(
            is_cancelled=is_cancelled,
            heartbeat=heartbeat,
        )
        if result.termination == "infra_error":
            result.error = f"{result.error} (first attempt: {first_error})"
    return result


# --------------------------------------------------------------------------
# Interpretation
# --------------------------------------------------------------------------


def _interpret(  # noqa: PLR0911 - one return per outcome rule (see the module docstring)
    prepared: _Prepared,
    result: ReviewSandboxResult,
    *,
    outcome: SampleOutcome,
    frozen: dict[str, Any],
    sample: Sample,
    api_key: str,
    is_cancelled: Callable[[], bool],
    heartbeat: Callable[[], None],
) -> None:
    harness = prepared.harness
    log = CallLog.from_logs(result.gateway_logs)
    outcome.usage = _usage(log, result, frozen)
    outcome.providers = log.providers()
    outcome.finish_reasons = log.finish_reasons()
    outcome.latency_seconds = result.wall_seconds if result.started else None
    summary = harness.report(result.outputs)
    last_content, last_finish = log.last_answer()
    final_answer = harness.final_answer(result.outputs, summary, last_content)
    opened, opened_method = harness.files_opened(result.outputs, prepared.workspace_paths)
    _attach_artifacts(outcome, result)

    in_scope = set(prepared.scope_paths)
    stop = harness.self_stop(summary)
    agent: dict[str, Any] = {
        "termination": result.termination,
        "exit_code": result.exit_code,
        "oom_killed": result.oom_killed,
        "wall_seconds": result.wall_seconds,
        "budget": prepared.budget,
        "budget_exhausted": log.budget_exhausted,
        "harness_stop": {"kind": stop[0], "detail": stop[1]} if stop else None,
        "options": prepared.effective_options,
        "gateway": log.gateway_summary(),
        "gateway_params": prepared.gateway_params,
        "harness_report": summary.to_dict(),
        "images": result.images,
        "sandbox": {"mem_limit": prepared.spec.mem_limit, "nano_cpus": prepared.spec.nano_cpus},
        "section_sha256": section_sha256(prepared.section),
        "brief_sha256": hashlib.sha256(prepared.brief.encode("utf-8")).hexdigest(),
        "files_opened": opened,
        "files_opened_method": opened_method,
        "files_opened_in_scope": None if opened is None else sum(1 for p in opened if p in in_scope),
        "drift_notes": prepared.drift_notes,
        "payload_source": None,
        "repair": None,
    }
    outcome.coverage["agent"] = agent

    if result.termination == "infra_error":
        _fail(outcome, "failed", "infra_error", result.error or "the review sandbox failed")
        return
    if result.termination == "cancelled":
        _fail(outcome, "cancelled", "cancelled", "review cancelled")
        return

    # A harness that stopped at its own limit did not finish its review, whatever its exit code says.
    ended_naturally = result.termination == "completed" and not log.budget_exhausted and stop is None
    native, native_source = _native_payload(harness, result.outputs, final_answer)
    invalid: list[str] = []
    if native is not None:
        parsed = _parse(native, prepared=prepared, sample=sample, frozen=frozen, index=outcome.index, errors=invalid)
        if parsed is not None:
            problem = _coverage_problem(prepared, parsed, agent)
            if not problem and (ended_naturally or parsed[2].get("review_complete") is True):
                agent["payload_source"] = native_source
                _succeed(outcome, parsed)
                return
            if problem and ended_naturally:
                # The agent finished, and its own document says it did not review the code.
                agent["payload_source"] = native_source
                _reject_incomplete(outcome, parsed, problem)
                return
            invalid.append(problem or "the agent was stopped before declaring its review complete")
    elif harness.output_channel == "file":
        invalid.append("the agent did not write the findings file")
    else:
        invalid.append("the final answer contains no JSON document")

    if not log.ok_calls:
        _no_successful_call(outcome, result, log)
        return
    if result.termination == "timeout":
        wall = prepared.budget["wall_clock_seconds"]
        _fail(outcome, "timed_out", "timeout", f"the harness hit the review wall clock ({wall} s)")
        return
    if log.budget_exhausted:
        _fail(outcome, "budget_exhausted", "budget", f"the gateway budget ran out ({log.budget_exhausted})")
        return
    if result.termination == "resource_limit":
        mem = prepared.spec.mem_limit
        _fail(outcome, "failed", "oom", f"the harness was killed at the sandbox memory cap ({mem})")
        return
    if result.termination in ("harness_killed", "harness_error"):
        _fail(outcome, "failed", "harness_error", _harness_failure(result, summary))
        return
    if stop is not None:
        # Never repair a review the harness's own limit cut short (as for the gateway's budget).
        _fail_self_stop(outcome, stop, prepared, invalid)
        return

    # The harness ended by itself, within budget, without a valid document.
    if last_finish == "length" and native is None:
        _fail(outcome, "truncated", "truncated", "the final answer hit the output cap (finish_reason=length)")
        return
    if harness.output_channel == "file" and final_answer:
        document = extract_json_document(final_answer)
        if document is not None:
            parsed = _parse(
                document,
                prepared=prepared,
                sample=sample,
                frozen=frozen,
                index=outcome.index,
                errors=invalid,
            )
            if parsed is not None:
                agent["payload_source"] = "final_answer_json"
                agent["repair"] = "final_answer_json"
                outcome.repaired = True
                _accept(prepared, outcome, parsed, agent)
                return
    if not prepared.effective_options.get("finalizer", True):
        _fail(outcome, "invalid_output", "invalid_output", "no valid findings document: " + "; ".join(invalid))
        return
    source_text = _finalizer_source(final_answer, native if harness.output_channel == "file" else None)
    if not source_text:
        _fail(outcome, "invalid_output", "invalid_output", "no findings document and no final answer to repair")
        return
    _run_finalizer(
        prepared,
        outcome=outcome,
        frozen=frozen,
        sample=sample,
        api_key=api_key,
        text=source_text,
        invalid=invalid,
        is_cancelled=is_cancelled,
        heartbeat=heartbeat,
    )


def _coverage_problem(
    prepared: _Prepared,
    parsed: tuple[list[Any], dict[str, int], dict[str, Any]],
    agent: dict[str, Any],
) -> str:
    """Why a valid document is not a review of the files under review ("" = it is).

    An exploring agent chooses what it reads, so the review is complete only
    when it says so (``review_complete``). Any harness must claim
    (``files_reviewed``) or be observed (``files_opened``) to have read at
    least one file under review: an empty document from an agent that read
    nothing is not "nothing found". ``files_opened`` alone is not required:
    it is best effort (OpenCode's read tool only, file names in mini's
    commands), so an agent that read with ``cat`` is not failed for it.
    """
    info = parsed[2]
    if prepared.harness.explores and info.get("review_complete") is not True:
        return "the agent declared its review incomplete (review_complete=false)"
    claimed = set(info.get("files_reviewed") or []) & set(prepared.scope_paths)
    if not claimed and not agent.get("files_opened_in_scope"):
        return "the document claims no file under review as reviewed and none was observed being read"
    return ""


def _accept(
    prepared: _Prepared,
    outcome: SampleOutcome,
    parsed: tuple[list[Any], dict[str, int], dict[str, Any]],
    agent: dict[str, Any],
) -> None:
    """Succeed with a repaired document, unless it does not show a review (then ``incomplete_review``)."""
    problem = _coverage_problem(prepared, parsed, agent)
    if problem:
        _reject_incomplete(outcome, parsed, problem)
    else:
        _succeed(outcome, parsed)


def _reject_incomplete(
    outcome: SampleOutcome,
    parsed: tuple[list[Any], dict[str, int], dict[str, Any]],
    problem: str,
) -> None:
    """A valid document that is not a review: record what it claimed, keep none of its findings."""
    findings, _, info = parsed
    outcome.files_reviewed = sorted(str(p) for p in info.get("files_reviewed") or [])
    outcome.coverage["review_complete"] = info.get("review_complete")
    outcome.coverage["discarded_findings"] = len(findings)
    _fail(outcome, "invalid_output", "incomplete_review", f"not counted as a review: {problem}")


def _fail_self_stop(outcome: SampleOutcome, stop: tuple[str, str], prepared: _Prepared, invalid: list[str]) -> None:
    kind, detail = stop
    why = "; ".join(invalid)
    if kind == "budget":
        limit = prepared.budget["max_llm_requests"]
        _fail(
            outcome,
            "budget_exhausted",
            "budget",
            f"the harness stopped at its own step limit ({detail}, max_llm_requests={limit}): {why}",
        )
    elif kind == "timeout":
        _fail(outcome, "timed_out", "timeout", f"the harness stopped at its own time limit ({detail}): {why}")
    else:
        _fail(outcome, "failed", "harness_error", f"the harness stopped without finishing ({detail}): {why}")


def _usage(log: CallLog, result: ReviewSandboxResult, frozen: dict[str, Any]) -> dict[str, Any]:
    pricing = (frozen.get("model") or {}).get("pricing") or {}
    usage = log.usage(pricing=pricing)
    if result.started and not result.gateway_logs:
        # The harness ran but the gateway's log could not be read: spend is
        # unknown, which is not the same as zero.
        usage.update(dict.fromkeys((*TOKEN_KEYS, "cost_usd", "llm_calls", "llm_calls_failed", "http_attempts")))
        usage["gateway_log_missing"] = True
    elif not result.started:
        usage["cost_usd"] = 0.0  # the harness never ran: nothing was sent
    return usage


def _native_payload(harness: ReviewHarness, outputs: dict[str, bytes], final_answer: str) -> tuple[str | None, str]:
    """The document from the harness's own channel: the findings file, or JSON in the final answer (aider)."""
    if harness.output_channel == "file":
        data = outputs.get("findings.json")
        if data is None:
            return None, "file"
        return data.decode("utf-8", errors="replace"), "file"
    return extract_json_document(final_answer or ""), "answer"


def _parse(
    payload: str,
    *,
    prepared: _Prepared,
    sample: Sample,
    frozen: dict[str, Any],
    index: int,
    errors: list[str],
) -> tuple[list[Any], dict[str, int], dict[str, Any]] | None:
    """parse_payload with the files under review as the allowed citations (others grade invalid_file).

    Paths are read relative to the workspace (``/workspace/app.py`` = ``app.py``), as the brief defines them.
    """
    try:
        findings, grounding, info = findings_mod.parse_payload(
            payload,
            sample=sample,
            frozen=frozen,
            index=index,
            allowed_files=sorted(prepared.scope_paths),
            path_root=WORKSPACE_DIR,
        )
    except findings_mod.InvalidReview as exc:
        errors.append(str(exc)[:2000])
        return None
    return findings, grounding, info or {}


def _succeed(outcome: SampleOutcome, parsed: tuple[list[Any], dict[str, int], dict[str, Any]]) -> None:
    findings, grounding, info = parsed
    outcome.status = "succeeded"
    outcome.failure_reason = ""
    outcome.error_message = ""
    outcome.findings = list(findings)
    outcome.grounding = dict(grounding)
    outcome.files_reviewed = sorted(str(p) for p in info.get("files_reviewed") or [])
    outcome.coverage["review_complete"] = info.get("review_complete")
    outcome.coverage["lenient_json"] = info.get("lenient_json")
    outcome.coverage["files_claimed_not_shown"] = sorted(
        set(outcome.files_reviewed) - set(outcome.coverage["files_sent"]),
    )


def _fail(outcome: SampleOutcome, status: str, reason: str, message: str) -> SampleOutcome:
    outcome.status = status
    outcome.failure_reason = reason
    outcome.error_message = message[:8000]
    outcome.findings = []
    return outcome


def _no_successful_call(outcome: SampleOutcome, result: ReviewSandboxResult, log: CallLog) -> None:
    if result.started and not result.gateway_logs:
        # Without the call log nothing can be said about what the model did.
        _fail(outcome, "failed", "infra_error", "the gateway's call log could not be collected from the sandbox")
    elif result.termination == "timeout":
        _fail(outcome, "timed_out", "timeout", "the harness hit the wall clock before any successful LLM call")
    elif log.budget_exhausted:
        _fail(outcome, "budget_exhausted", "budget", f"the gateway budget ran out ({log.budget_exhausted})")
    elif log.calls:
        message = f"no successful LLM call ({log.first_error() or 'unknown upstream error'})"
        _fail(outcome, "failed", log.failure_kind(), f"{message}. {log.remediation()}".strip())
    else:
        tail = _tail(result.outputs.get("harness.log")) or result.container_logs_tail[-LOG_TAIL_CHARS:]
        _fail(
            outcome,
            "failed",
            "harness_error",
            f"the harness made no LLM call (termination {result.termination}, exit code {result.exit_code}). "
            f"Harness log tail:\n{tail}",
        )


def _harness_failure(result: ReviewSandboxResult, summary: HarnessSummary) -> str:
    errors = "\n".join(summary.errors[:5]) or "(none)"
    tail = _tail(result.outputs.get("harness.log")) or result.container_logs_tail[-LOG_TAIL_CHARS:]
    return (
        f"the harness ended with {result.termination} (exit code {result.exit_code}) without a valid findings "
        f"document.\nReported errors:\n{errors}\nHarness log tail:\n{tail}"
    )


def _tail(data: bytes | None) -> str:
    return (data or b"").decode("utf-8", errors="replace")[-LOG_TAIL_CHARS:]


def _attach_artifacts(outcome: SampleOutcome, result: ReviewSandboxResult) -> None:
    for name in ("calls.jsonl", "gateway_events.jsonl"):
        if name in result.gateway_logs:
            outcome.artifacts[name] = result.gateway_logs[name]
    # One flat segment per artifact name (stored as files, downloadable by name).
    for name, data in sorted(result.outputs.items()):
        if name == "brief.md":
            continue  # attached as rendered
        outcome.artifacts[f"harness-{name}"] = data
    if result.container_logs_tail:
        outcome.artifacts["container.log"] = result.container_logs_tail.encode("utf-8")


# --------------------------------------------------------------------------
# Finalizer
# --------------------------------------------------------------------------


def _finalizer_source(final_answer: str, invalid_document: str | None) -> str:
    parts = []
    if final_answer.strip():
        parts.append(f"Final answer of the review agent:\n{final_answer.strip()}")
    if invalid_document and invalid_document.strip() and invalid_document.strip() != final_answer.strip():
        parts.append(f"Findings document the agent wrote (rejected):\n{invalid_document.strip()}")
    return "\n\n".join(parts)[:FINALIZER_TEXT_CHARS]


def _run_finalizer(
    prepared: _Prepared,
    *,
    outcome: SampleOutcome,
    frozen: dict[str, Any],
    sample: Sample,
    api_key: str,
    text: str,
    invalid: list[str],
    is_cancelled: Callable[[], bool],
    heartbeat: Callable[[], None],
) -> None:
    """One structured single-shot call that turns the agent's final answer into the schema."""
    agent = outcome.coverage["agent"]
    scope = sorted(prepared.scope_paths)
    schema_version = frozen.get("schema_version") or prompt_mod.SCHEMA_VERSION
    response_format = prompt_mod.response_format(frozen.get("output_mode") or "json_schema", scope, schema_version)
    schema = prompt_mod.findings_schema(scope, schema_version)
    user = (
        "Files under review:\n"
        + "\n".join(f"- {path}" for path in scope)
        + f"\n\nJSON Schema:\n{json.dumps(schema, sort_keys=True)}\n\n{text}\n\n"
        + "Why the agent's output was rejected: "
        + "; ".join(invalid)[:2000]
    )
    messages = [{"role": "system", "content": finalizer_prompt()}, {"role": "user", "content": user}]
    params = _finalizer_params(prepared)

    # The finalizer spends from the same per-replicate budget as the harness.
    usage = outcome.usage
    spent = float(usage.get("cost_usd") or 0.0)
    pricing = (frozen.get("model") or {}).get("pricing") or {}
    projected = len(user + messages[0]["content"]) / CHARS_PER_TOKEN * float(pricing.get("input_per_token") or 0.0)
    ok_calls = int(usage.get("llm_calls") or 0) - int(usage.get("llm_calls_failed") or 0)
    if ok_calls >= prepared.budget["max_llm_requests"] or spent + projected > prepared.budget["max_cost_usd"]:
        agent["finalizer"] = {"ok": False, "skipped": "no budget left"}
        _fail(
            outcome,
            "invalid_output",
            "invalid_output",
            "no valid findings document, and no budget left for the finalizer: " + "; ".join(invalid),
        )
        return

    started = time.monotonic()
    call = _call_waiting(
        lambda: client_mod.chat(
            api_key,
            model=prepared.model.slug,
            messages=messages,
            params=params,
            response_format=response_format,
            provider=frozen.get("provider") or default_provider_routing(),
            timeout_s=FINALIZER_TIMEOUT_S,
            max_retries=FINALIZER_MAX_RETRIES,
        ),
        heartbeat=heartbeat,
        is_cancelled=is_cancelled,
        deadline=started + FINALIZER_TIMEOUT_S * (FINALIZER_MAX_RETRIES + 1),
    )
    if isinstance(call, str):
        # The request was sent and its answer (and bill) never collected: spend is a lower bound.
        usage["calls_abandoned"] = int(usage.get("calls_abandoned") or 0) + 1
        usage["cost_complete"] = False
    if call == "cancelled":
        agent["finalizer"] = {"ok": False, "error": "cancelled"}
        _fail(outcome, "cancelled", "cancelled", "review cancelled during the finalizer call")
        return
    if isinstance(call, str):
        agent["finalizer"] = {"ok": False, "error": call}
        _fail(outcome, "invalid_output", "invalid_output", f"no valid findings document; finalizer failed: {call}")
        return
    outcome.latency_seconds = round((outcome.latency_seconds or 0.0) + (time.monotonic() - started), 2)
    _add_call_usage(outcome, call)
    agent["finalizer"] = {
        "ok": call.ok,
        "status_code": call.status_code,
        "error_kind": call.error_kind,
        "finish_reason": call.finish_reason,
        "provider": call.provider,
        "model": call.model,
        "generation_id": call.generation_id,
        "latency_s": call.latency_s,
        "retries": call.retries,
        "error": (call.error or "")[:2000],
        "usage": dict(call.usage or {}),
    }
    outcome.artifacts["finalizer.json"] = _json_bytes(
        {
            "request": call.request or {"messages": messages, "params": params, "response_format": response_format},
            "response": call.raw,
            "attempts": call.attempts,
        },
    )
    if call.finish_reason == "length":
        _fail(outcome, "truncated", "truncated", "the finalizer's answer hit the output cap (finish_reason=length)")
        return
    if not call.ok:
        _fail(
            outcome,
            "invalid_output",
            "invalid_output",
            f"no valid findings document; finalizer failed: {call.error}",
        )
        return
    parsed = _parse(call.content, prepared=prepared, sample=sample, frozen=frozen, index=outcome.index, errors=invalid)
    if parsed is None:
        reasons = "; ".join(invalid)
        _fail(outcome, "invalid_output", "invalid_output", f"no valid findings document after repair: {reasons}")
        return
    agent["payload_source"] = "finalizer"
    agent["repair"] = "finalizer"
    outcome.repaired = True
    _accept(prepared, outcome, parsed, agent)


def _call_waiting(
    fn: Callable[[], Any],
    *,
    heartbeat: Callable[[], None],
    is_cancelled: Callable[[], bool],
    deadline: float,
) -> Any:
    """Run a blocking call on a worker thread, heartbeating meanwhile (the caller owns the DB connection).

    Returns the call's result, or ``"cancelled"`` / ``"timeout"`` when the
    wait was abandoned (the request then finishes in the background).
    """
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ai-review-finalizer")
    try:
        future = pool.submit(fn)
        while True:
            try:
                return future.result(timeout=HEARTBEAT_INTERVAL_S)
            except FuturesTimeout:
                heartbeat()
                if is_cancelled():
                    return "cancelled"
                if time.monotonic() > deadline:
                    return "timeout"
    except Exception as exc:
        logger.exception("Finalizer call raised")
        return f"{type(exc).__name__}: {exc}"
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _finalizer_params(prepared: _Prepared) -> dict[str, Any]:
    """The reviewer's own sampling params (what the gateway forces), at the review output cap."""
    forced = prepared.gateway_params["forced"]
    params = {k: v for k, v in forced.items() if k not in ("provider", "max_tokens")}
    params["max_tokens"] = prepared.model.output_cap
    return params


def _add_call_usage(outcome: SampleOutcome, call) -> None:
    """Fold one direct LLMCall (the finalizer) into the sample's gateway-derived usage (None stays None)."""
    usage = outcome.usage
    raw = dict(call.usage or {})
    for key in TOKEN_KEYS:
        if raw.get(key) is not None:
            usage[key] = int(usage.get(key) or 0) + int(raw[key])
    if raw.get("cost") is not None:
        usage["cost_usd"] = round(float(usage.get("cost_usd") or 0.0) + float(raw["cost"]), 8)
    elif raw.get("prompt_tokens") is not None or raw.get("completion_tokens") is not None:
        usage["cost_estimated"] = True
    usage["llm_calls"] = int(usage.get("llm_calls") or 0) + 1
    usage["llm_calls_failed"] = int(usage.get("llm_calls_failed") or 0) + (0 if call.ok else 1)
    usage["http_attempts"] = int(usage.get("http_attempts") or 0) + max(1, len(call.attempts or []))
    usage["retries"] = int(usage.get("retries") or 0) + int(call.retries or 0)
    usage["finalizer_calls"] = int(usage.get("finalizer_calls") or 0) + 1
    if call.model:
        served = dict(usage.get("served_models") or {})
        served[call.model] = served.get(call.model, 0) + 1
        usage["served_models"] = served
    if call.ok:
        provider = call.provider or "unknown"
        outcome.providers[provider] = outcome.providers.get(provider, 0) + 1
        reason = call.finish_reason or "unknown"
        outcome.finish_reasons[reason] = outcome.finish_reasons.get(reason, 0) + 1


# --------------------------------------------------------------------------
# Plan helpers
# --------------------------------------------------------------------------


def _normalize_budget(budget: dict[str, Any]) -> dict[str, Any]:
    """The per-replicate gateway budget: plan values over the defaults, range-checked."""
    merged = {**DEFAULT_BUDGET, **{k: v for k, v in (budget or {}).items() if v is not None}}
    out: dict[str, Any] = {}
    errors = []
    for key, (kind, low, high) in BUDGET_LIMITS.items():
        if key not in merged:
            continue
        value = merged[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            errors.append(f"budget.{key} must be between {low:g} and {high:g}")
            continue
        out[key] = int(value) if kind is int else float(value)
    if errors:
        raise ValueError("; ".join(errors))
    return out


def _section_inputs(info: ModelInfo, budget: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_slug": info.slug,
        "context_window": info.context_window,
        "output_cap": info.output_cap,
        "max_llm_requests": int(budget["max_llm_requests"]),
    }


def _sandbox_limits() -> dict[str, Any]:
    """Frozen with the plan: the sandbox a review runs in is part of the instrument."""
    return {
        "mem_limit": str(getattr(settings, "ANALYSIS_REVIEW_SANDBOX_MEM_LIMIT", DEFAULT_MEM_LIMIT)),
        "nano_cpus": int(getattr(settings, "ANALYSIS_REVIEW_SANDBOX_NANO_CPUS", DEFAULT_NANO_CPUS)),
        "gateway_mem_limit": GATEWAY_MEM_LIMIT,
        "network_policy": "none",
    }


def _recorded_freeze(frozen: dict[str, Any]) -> dict[str, Any] | None:
    """What freeze_harness returned at plan time (``reviewer["agent"]``)."""
    for key in ("agent", "harness_frozen"):
        value = frozen.get(key)
        if isinstance(value, dict) and "review_revision" in value:
            return value
    return None


def _check_drift(frozen: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Raise HarnessDriftError when the plan's harness identity no longer matches; return info notes."""
    problems: list[str] = []
    notes: list[str] = []
    recorded = _recorded_freeze(frozen)
    version = frozen.get("harness_version") or (recorded or {}).get("version")
    if version and str(version) != current["version"]:
        problems.append(f"harness version {version} -> {current['version']}")
    images = frozen.get("harness_images") or (recorded or {}).get("images") or {}
    for role in ("base", "harness", "gateway"):
        tag = images.get(role) if isinstance(images, dict) else None
        if tag and tag != current["images"][role]:
            problems.append(f"{role} image {tag} -> {current['images'][role]}")
    if recorded:
        if recorded.get("review_revision") != current["review_revision"]:
            problems.append(f"review revision {recorded.get('review_revision')} -> {current['review_revision']}")
        old_prompt = (recorded.get("brief") or {}).get("prompt_sha256")
        if old_prompt and old_prompt != current["brief"]["prompt_sha256"]:
            problems.append("review brief/templates changed")
        if (
            recorded.get("section_sha256")
            and recorded.get("section_inputs") == current.get("section_inputs")
            and recorded["section_sha256"] != current.get("section_sha256")
        ):
            problems.append("harness section changed (generation adapter or review variant)")
    else:
        notes.append("the plan has no recorded harness freeze; only version and images were checked")
    if not version and not images:
        notes.append("the plan records no harness version or images; harness identity not verified")
    if problems:
        msg = "; ".join(problems) + " since the plan was frozen; re-freeze the plan to run it"
        raise HarnessDriftError(msg)
    return notes


def _gateway_params(frozen: dict[str, Any], *, seed: int, output_cap: int) -> dict[str, Any]:
    """What the gateway forces / strips on every harness request (generation's policy, review params).

    The reviewer's explicit params (temperature, reasoning, ...; already
    filtered by model support when the plan was frozen) are forced; every
    other sampling knob a harness sets is stripped (provider_default
    policy), so harnesses differ by design, not by their own sampling
    defaults. The seed is the replicate's, forwarded only when the model
    honours one (``params.seed_forwarded``).
    """
    params = frozen.get("params") or {}
    forced: dict[str, Any] = {k: params[k] for k in GATEWAY_PARAM_KEYS if params.get(k) is not None}
    if params.get("seed_forwarded"):
        forced["seed"] = single_shot_mod.llm_seed(seed)
    forced["provider"] = frozen.get("provider") or default_provider_routing()
    forced["max_tokens"] = int(output_cap)
    stripped = sorted({*ALWAYS_STRIPPED, *(k for k in HARNESS_SAMPLING_KEYS if k not in forced)})
    return {"forced": forced, "stripped": stripped, "dropped": dict(params.get("dropped") or {})}


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True, default=str).encode("utf-8")
