"""Single-shot slot calls: strict JSON output, used as returned (no repair) (spec sections 7.6 and D13).

``run_stage(run)`` is the generation stage of a ``slot_single_shot`` run (llm lane; called through
``agentic.dispatch.run_stage``). It reads everything from the run's frozen manifest: the rendered stimuli
(``RunManifest.stimuli`` blobs, roles ``slot_single_shot_system`` / ``slot_single_shot_user``), the model and
its frozen pricing, the sampling preset (forced params, seed forwarding, structured output) and the effective
budget policy. It makes exactly one model call (transport errors are retried by the policy's
``retries.upstream_retries``; a parse failure never is), records the ``slot_call_0001`` artifact and the
attempt, then:

* parse ``ok``: assembles the variant (slot rows only), writes its ``SlotVariantResult`` and ends the stage
  with ``evaluate`` (the run waits for the eval lane);
* any other parse status (``invalid_json``, ``schema_violation``, ``empty``, ``refusal``, ``truncated``): outcome
  ``output_invalid`` with that reason (``truncated`` adds the validity note). The output is a data point;
* transport errors after the retries: ``infra_retry`` (requeue or ``infra_error``), never a model outcome;
* refusals the policy makes before calling (sampling ``provider_default`` or temperature 0, input above
  ``call.max_input_tokens``, a call that cannot fit ``run.max_cost_usd``): ``excluded:config_invalid``. These
  are treatment configuration problems that the resolver should already have caught.

Parsing (``parse_output``): one leading/trailing code fence is stripped; anything else around the JSON object is
``invalid_json``; a JSON value that does not match the output schema (extra keys, wrong file set) is
``schema_violation``; ``finish_reason=length`` (or more completion tokens than the cap) is ``truncated``; an
empty answer or empty code is ``empty``; a provider refusal field, or prose that only declines, is ``refusal``.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from django.utils import timezone
from pydantic import ValidationError

from backend.generation import blobs
from backend.generation.contracts.issues import Issue
from backend.generation.contracts.issues import make_issue
from backend.generation.contracts.slot import SlotCodeOutput
from backend.generation.contracts.slot import SlotFilesOutput
from backend.generation.models import GenerationArtifact
from backend.generation.models import RunAttempt
from backend.generation.models import RunManifest
from backend.generation.models.choices import ExclusionReason
from backend.generation.models.choices import Outcome
from backend.generation.models.choices import ParseStatus
from backend.generation.models.choices import PromptRole
from backend.generation.models.choices import Termination
from backend.generation.services import token_metrics
from backend.generation.slot.part import part_of
from backend.generation.slot.prompt import SINGLE_SHOT_EXECUTOR
from backend.generation.slot.prompt import output_contract
from backend.generation.slot.prompt import uses_files_output

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun
    from backend.generation.slot.part import Part

__all__ = [
    "CALL_STAGE",
    "LLMCall",
    "ParseResult",
    "SlotRequest",
    "TransportError",
    "build_request",
    "parse_output",
    "record_call",
    "record_spend",
    "run_stage",
    "sampling_issues",
    "transport",
]

logger = logging.getLogger(__name__)

CALL_STAGE: Final = "slot_call_0001"
CHARS_PER_TOKEN: Final = 3.6
_FENCE_RE: Final = re.compile(r"\A```[A-Za-z0-9_+-]*[ \t]*\r?\n(?P<body>.*?)\r?\n?```\Z", re.S)
_REFUSAL_RE: Final = re.compile(
    r"\b(?:I\s+(?:can(?:no|')t|cannot|am\s+(?:unable|not\s+able)|won't|will\s+not)|I'm\s+(?:sorry|unable|not\s+able))\b",
    re.I,
)
_DEFAULT_RETRY_ON: Final = ("429", "5xx")


class TransportError(Exception):
    """A failed call that never produced a model answer (network, 429/5xx, provider error)."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = True) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass(frozen=True)
class LLMCall:
    response: dict[str, Any]  # OpenAI chat.completion shape
    served: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0


@dataclass(frozen=True)
class SlotRequest:
    """One rendered single-shot request: OpenAI chat body plus what the parser needs."""

    body: dict[str, Any]
    files_mode: bool
    allowlist: tuple[str, ...]
    max_output_tokens: int | None
    structured_output: bool
    est_input_tokens: int


@dataclass(frozen=True)
class ParseResult:
    status: str
    replacement: str | dict[str, str] | None = None
    detail: str = ""


Transport = Callable[["GenerationRun", SlotRequest], LLMCall]


def _default_transport(run: GenerationRun, request: SlotRequest) -> LLMCall:
    """Call the model through ``inference.chat`` (OpenRouter or the run's local endpoint)."""
    from backend.generation.services.openrouter_client import OpenRouterError
    from backend.inference.access import LLMAccessError
    from backend.inference.access import resolve_access
    from backend.inference.chat import chat

    body = dict(request.body)
    messages = body.pop("messages")
    seed = body.pop("seed", None)
    body.pop("model", None)
    started = time.monotonic()
    try:
        access = resolve_access(run.created_by, run.model)
        result = chat(access, messages=messages, params=body, seed=seed, source="slot_call", job=run)
    except LLMAccessError as exc:
        raise TransportError(str(exc), status=exc.status_code, retryable=exc.retryable) from exc
    except OpenRouterError as exc:
        raise TransportError(str(exc), status=exc.status_code) from exc
    return LLMCall(response=result.response, served=dict(result.served), seconds=time.monotonic() - started)


# Module attribute so tests (and the batch submitter's fallbacks) can swap the transport.
transport: Transport = _default_transport


# ---------------------------------------------------------------------------
# Policy and request
# ---------------------------------------------------------------------------


def _effective(manifest: dict[str, Any]) -> dict[str, Any]:
    effective = (manifest.get("resources") or {}).get("effective") or {}
    return effective.get("content", effective) if isinstance(effective, dict) else {}


def _temperature(manifest: dict[str, Any]) -> float | None:
    sampling = manifest.get("sampling") or {}
    value = (sampling.get("forced_params") or {}).get("temperature")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def sampling_issues(sampling_content: dict[str, Any], study_kind: str = "exploratory") -> list[Issue]:
    """``slot_sampling_provider_default`` for a sampling preset slot studies refuse (temperature must be > 0)."""
    policy = sampling_content.get("policy")
    temperature = (sampling_content.get("params") or {}).get("temperature")
    if policy == "provider_default" or temperature is None or temperature <= 0:
        return [
            make_issue(
                "slot_sampling_provider_default",
                study_kind,  # type: ignore[arg-type]
                message="slot studies need an explicit sampling preset with temperature > 0 (default t07)",
                data={"policy": policy, "temperature": temperature},
            ),
        ]
    return []


def _stimuli_texts(run: GenerationRun) -> dict[str, str]:
    row = RunManifest.objects.get(run=run)
    return {record["role"]: blobs.get_text(record["blob"]) for record in row.stimuli or []}


def _max_output_tokens(policy: dict[str, Any]) -> int | None:
    slot_cap = (policy.get("slot") or {}).get("max_output_tokens")
    call_cap = (policy.get("call") or {}).get("max_output_tokens")
    caps = [cap for cap in (slot_cap, call_cap) if isinstance(cap, int) and cap > 0]
    return min(caps) if caps else None


def build_request(run: GenerationRun, manifest: dict[str, Any], stimuli: dict[str, str], part: Part) -> SlotRequest:
    """The chat body of a single-shot call, exactly as the batch submitter also renders it."""
    slot = manifest.get("slot") or {}
    hole_mode = slot.get("hole_mode") or part.default_hole_mode
    sampling = manifest.get("sampling") or {}
    forced = dict(sampling.get("forced_params") or {})
    structured = forced.pop("response_format", None) == "json_schema"
    policy = _effective(manifest)
    system = stimuli.get(PromptRole.SLOT_SINGLE_SHOT_SYSTEM, "")
    user = stimuli.get(PromptRole.SLOT_SINGLE_SHOT_USER, "")
    messages = [m for m in ({"role": "system", "content": system}, {"role": "user", "content": user}) if m["content"]]
    body: dict[str, Any] = {"model": (manifest.get("model") or {}).get("slug") or "", "messages": messages}
    body.update({key: value for key, value in forced.items() if value is not None})
    cap = _max_output_tokens(policy)
    if cap is not None:
        body["max_tokens"] = cap
    if sampling.get("seed_forwarded") and run.seed is not None:
        body["seed"] = run.seed
    contract = output_contract(part, hole_mode, SINGLE_SHOT_EXECUTOR)
    if structured:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "slot_output", "strict": True, "schema": contract["schema"]},
        }
    chars = sum(len(m["content"]) for m in messages)
    return SlotRequest(
        body=body,
        files_mode=uses_files_output(part, hole_mode),
        allowlist=tuple(part.allowlist),
        max_output_tokens=cap,
        structured_output=structured,
        est_input_tokens=math.ceil(chars / CHARS_PER_TOKEN),
    )


def _precall_refusal(manifest: dict[str, Any], request: SlotRequest) -> str | None:
    """Why the policy refuses the call before sending it (never clamps); ``None`` to proceed."""
    temperature = _temperature(manifest)
    if (manifest.get("sampling") or {}).get("policy") == "provider_default" or temperature is None or temperature <= 0:
        return "slot_sampling_provider_default"
    policy = _effective(manifest)
    max_input = (policy.get("call") or {}).get("max_input_tokens")
    if isinstance(max_input, int) and request.est_input_tokens > max_input:
        return "call_input_over_cap"
    pricing = (manifest.get("model") or {}).get("pricing") or {}
    endpoint = (manifest.get("model") or {}).get("endpoint") or {}
    max_cost = (policy.get("run") or {}).get("max_cost_usd")
    if endpoint.get("cost_basis") != "free" and isinstance(max_cost, (int, float)) and request.max_output_tokens:
        worst = (
            request.est_input_tokens * float(pricing.get("input_per_mtok") or 0.0)
            + request.max_output_tokens * float(pricing.get("output_per_mtok") or 0.0)
        ) / 1_000_000
        if worst > max_cost:
            return "budget_exhausted:cost"
    return None


# ---------------------------------------------------------------------------
# Parsing (no repair: D13)
# ---------------------------------------------------------------------------


def _message(response: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    choices = response.get("choices") or [{}]
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message") or {}
    return message.get("content"), message.get("refusal"), choice.get("finish_reason")


def parse_output(response: dict[str, Any], request: SlotRequest) -> ParseResult:  # noqa: PLR0911
    """The parse status and replacement of one answer (strict JSON, optionally in one fenced block)."""
    content, refusal, finish_reason = _message(response)
    completion = int(((response.get("usage") or {}).get("completion_tokens")) or 0)
    if finish_reason == "length" or (request.max_output_tokens and completion > request.max_output_tokens):
        return ParseResult(ParseStatus.TRUNCATED, detail="finish_reason=length")
    if refusal:
        return ParseResult(ParseStatus.REFUSAL, detail="provider refusal")
    text = (content or "").strip()
    if not text:
        return ParseResult(ParseStatus.EMPTY, detail="empty answer")
    fence = _FENCE_RE.match(text)
    inner = fence.group("body").strip() if fence else text
    if not inner.startswith("{"):
        if "{" not in text and _REFUSAL_RE.search(text):
            return ParseResult(ParseStatus.REFUSAL, detail="declined in prose")
        return ParseResult(ParseStatus.INVALID_JSON, detail="the answer is not one JSON object")
    try:
        value = json.loads(inner)
    except ValueError as exc:
        return ParseResult(ParseStatus.INVALID_JSON, detail=str(exc)[:200])
    if not isinstance(value, dict):
        return ParseResult(ParseStatus.SCHEMA_VIOLATION, detail="the JSON value is not an object")
    try:
        if request.files_mode:
            files = SlotFilesOutput.model_validate(value).files
        else:
            code = SlotCodeOutput.model_validate(value).code
    except ValidationError as exc:
        return ParseResult(ParseStatus.SCHEMA_VIOLATION, detail=str(exc.errors()[0].get("msg"))[:200])
    if request.files_mode:
        if set(files) != set(request.allowlist):
            extra = sorted(set(files) - set(request.allowlist))
            missing = sorted(set(request.allowlist) - set(files))
            return ParseResult(ParseStatus.SCHEMA_VIOLATION, detail=f"extra files {extra}, missing files {missing}")
        if not any(text.strip() for text in files.values()):
            return ParseResult(ParseStatus.EMPTY, detail="every file is empty")
        return ParseResult(ParseStatus.OK, replacement=dict(files))
    if not code.strip():
        return ParseResult(ParseStatus.EMPTY, detail="empty code")
    return ParseResult(ParseStatus.OK, replacement=code)


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def _usage(response: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    usage = response.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
    # An unreported reasoning count is not measured (None), never 0 (services.token_metrics).
    reasoning = token_metrics.reasoning_total(
        [(completion, (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"))],
    )
    pricing = (manifest.get("model") or {}).get("pricing") or {}
    list_cost = (
        prompt * float(pricing.get("input_per_mtok") or 0.0)
        + completion * float(pricing.get("output_per_mtok") or 0.0)
    ) / 1_000_000
    endpoint = (manifest.get("model") or {}).get("endpoint") or {}
    if endpoint.get("cost_basis") == "free":
        cost = 0.0
    elif isinstance(usage.get("cost"), (int, float)):
        cost = float(usage["cost"])
    else:
        cost = list_cost
    return {
        "prompt_tokens": prompt,
        "uncached_prompt_tokens": max(prompt - cached, 0),
        "completion_tokens": completion,
        "reasoning_tokens": reasoning,
        "cached_tokens": cached,
        "cost_usd": round(cost, 8),
        "list_cost_usd": round(list_cost, 8),
    }


def record_call(
    run: GenerationRun,
    request: SlotRequest,
    call: LLMCall,
    manifest: dict[str, Any],
    parse: ParseResult,
    *,
    serving_tier: str = "standard",
) -> dict[str, Any]:
    """Write the ``slot_call_0001`` artifact and the attempt row; returns the usage dict."""
    usage = _usage(call.response, manifest)
    _, _, finish_reason = _message(call.response)
    served_model = call.response.get("model") or call.served.get("model") or ""
    provider = call.response.get("provider") or call.served.get("endpoint") or ""
    attempt = int(run.attempt or 0) + 1  # 1-based (GenerationRun.attempt is the 0-based requeue counter)
    GenerationArtifact.objects.filter(run=run, attempt=attempt, stage=CALL_STAGE).delete()
    GenerationArtifact.objects.create(
        run=run,
        attempt=attempt,
        stage=CALL_STAGE,
        request_payload=request.body,
        response_payload=call.response,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        total_cost=usage["cost_usd"],
        telemetry={
            "request_class": "main",
            "finish_reason": finish_reason,
            "response_model": served_model,
            "provider": provider,
            "system_fingerprint": call.response.get("system_fingerprint"),
            "front_protocol": "openai_chat",
            "serving_tier": serving_tier,
            "structured_output": request.structured_output,
            "parse_status": parse.status,
            "latency_s": round(call.seconds, 3),
        },
    )
    RunAttempt.objects.update_or_create(
        run=run,
        attempt=attempt,
        defaults={
            "started_at": run.started_at or timezone.now(),
            "ended_at": timezone.now(),
            "termination": Termination.COMPLETED,
            "served": {
                "providers": {provider: 1} if provider else {},
                "models": {served_model: 1} if served_model else {},
                "response_models": {served_model: 1} if served_model else {},
                "system_fingerprints": [fp] if (fp := call.response.get("system_fingerprint")) else [],
            },
            "usage": {"requests_by_class": {"main": 1}, **usage, "call_truncations": int(parse.status == "truncated")},
        },
    )
    return usage


def record_spend(run: GenerationRun, usage: dict[str, Any], *, serving_tier: str = "standard") -> None:
    """Record the call in the spend ledger and settle the attempt (WP-11; idempotent; never raises).

    One ``usage`` row per call (``dedupe_key=call:{run}:{attempt}:1``; source ``batch`` for a batch answer,
    else ``gateway_final``), then ``settle_attempt`` with the same final usage (adjust 0, the reservation is
    released). ``transitions.finish_stage`` settles again as a no-op; the reconciler repairs a failure here
    from ``RunAttempt.usage``.
    """
    from backend.generation.budget import ledger
    from backend.generation.models.choices import LedgerSource

    attempt = ledger.attempt_no(run)
    source = LedgerSource.BATCH if serving_tier == "batch" else LedgerSource.GATEWAY_FINAL
    try:
        ledger.record_usage(
            run,
            attempt=attempt,
            source=source,
            amount_usd=usage["cost_usd"],
            list_cost_usd=usage["list_cost_usd"],
            tokens_in=usage["prompt_tokens"],
            tokens_out=usage["completion_tokens"],
            requests=1,
            dedupe_key=f"call:{run.pk}:{attempt}:1",
        )
        final = ledger.FinalUsage(
            ledger.to_decimal(usage["cost_usd"]),
            ledger.to_decimal(usage["list_cost_usd"]),
            int(usage["prompt_tokens"]),
            int(usage["completion_tokens"]),
            1,
            source,
        )
        ledger.settle_attempt(run, attempt=attempt, final=final)
    except Exception:
        logger.exception("Recording the spend of slot run %s failed (the reconciler retries)", run.pk)


def _metrics(usage: dict[str, Any], seconds: float, truncated: bool) -> dict[str, Any]:
    return {
        "cost_usd": usage["cost_usd"],
        "list_cost_usd": usage["list_cost_usd"],
        "prompt_tokens": usage["prompt_tokens"],
        "uncached_prompt_tokens": usage["uncached_prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "reasoning_tokens": usage["reasoning_tokens"],
        "cached_tokens": usage["cached_tokens"],
        "total_tokens": usage["prompt_tokens"] + usage["completion_tokens"],
        "requests_by_class": {"main": 1},
        "call_truncations": int(truncated),
        "wall_s": round(seconds, 3),
    }


def finalize_answer(
    run: GenerationRun,
    part: Part,
    manifest: dict[str, Any],
    request: SlotRequest,
    call: LLMCall,
    *,
    serving_tier: str = "standard",
) -> str:
    """Parse, record and assemble one answer; returns the stage outcome (``evaluate`` or ``done``)."""
    from backend.execution import transitions
    from backend.generation.slot.assemble import assemble
    from backend.generation.slot.variants import record_variant

    parse = parse_output(call.response, request)
    usage = record_call(run, request, call, manifest, parse, serving_tier=serving_tier)
    record_spend(run, usage, serving_tier=serving_tier)
    truncated = parse.status == ParseStatus.TRUNCATED
    run.metrics = _metrics(usage, call.seconds, truncated)
    notes = [{"code": "truncated", "level": "warn", "detail": parse.detail}] if truncated else []
    served = call.response.get("model") or ""
    pinned = (manifest.get("model") or {}).get("endpoint", {}).get("served_model_name") or ""
    if served and pinned and served != pinned:
        notes.append({"code": "response_model_differs", "level": "info", "detail": served})
    run.validity = {"termination": Termination.COMPLETED, "truncated": False, "notes": notes}
    run.result = {"termination": Termination.COMPLETED, "steps": [{"step": CALL_STAGE, "status": parse.status}]}
    if parse.status != ParseStatus.OK or parse.replacement is None:
        record_variant(run, part, parse_status=parse.status, replacement=None)
        run.outcome, run.outcome_reason = Outcome.OUTPUT_INVALID, parse.status
        return transitions.OUTCOME_DONE
    rows = assemble(run, parse.replacement)
    texts = {row.path: blobs.get_text(row.blob) for row in rows}
    record_variant(run, part, parse_status=ParseStatus.OK, replacement=parse.replacement, assembled_texts=texts)
    run.outcome, run.outcome_reason = "", ""
    return transitions.OUTCOME_EVALUATE


def _exclude(run: GenerationRun, reason: str) -> None:
    from backend.execution import transitions

    note = reason if reason.startswith("budget_exhausted:") else None
    run.outcome, run.outcome_reason = Outcome.EXCLUDED, ExclusionReason.CONFIG_INVALID
    run.error_message = f"refused before the call: {reason}"
    run.validity = {
        "termination": None,
        "truncated": False,
        "notes": [{"code": note, "level": "warn"}] if note else [],
    }
    transitions.finish_stage(run, transitions.OUTCOME_DONE)


def _retry_on(policy: dict[str, Any]) -> tuple[str, ...]:
    retries = policy.get("retries") or {}
    return tuple(retries.get("retry_on") or _DEFAULT_RETRY_ON)


def _retryable(error: TransportError, retry_on: tuple[str, ...]) -> bool:
    if not error.retryable:
        return False
    if error.status is None:
        return True
    return str(error.status) in retry_on or ("5xx" in retry_on and 500 <= error.status < 600)


def call_with_retries(run: GenerationRun, request: SlotRequest, policy: dict[str, Any]) -> LLMCall:
    """One answer, retrying transport failures per ``retries`` (backoff doubles); raises the last error."""
    retries = policy.get("retries") or {}
    attempts = int(retries.get("upstream_retries", 2)) + 1
    backoff = float(retries.get("backoff_s", 2.0))
    retry_on = _retry_on(policy)
    last: TransportError | None = None
    for index in range(attempts):
        try:
            return transport(run, request)
        except TransportError as exc:
            last = exc
            if index + 1 >= attempts or not _retryable(exc, retry_on):
                break
            time.sleep(backoff * (2**index))
    assert last is not None
    raise last


def run_stage(run: GenerationRun) -> None:
    """Generation stage of a ``slot_single_shot`` run; a parse failure is ``output_invalid``."""
    from backend.execution import transitions

    manifest_row = RunManifest.objects.filter(run=run).first()
    if manifest_row is None or run.slot_target is None:
        run.error_message = "slot run without a manifest or slot target"
        transitions.finish_stage(run, transitions.OUTCOME_FAILED)
        return
    manifest = manifest_row.manifest
    part = part_of(run.slot_target)
    request = build_request(run, manifest, _stimuli_texts(run), part)
    refusal = _precall_refusal(manifest, request)
    if refusal is not None:
        _exclude(run, refusal)
        return
    policy = _effective(manifest)
    try:
        call = call_with_retries(run, request, policy)
    except TransportError as exc:
        transitions.infra_retry(run, transitions.REASON_INFRA, error=str(exc)[:500])
        return
    outcome = finalize_answer(run, part, manifest, request, call)
    transitions.finish_stage(run, outcome)
