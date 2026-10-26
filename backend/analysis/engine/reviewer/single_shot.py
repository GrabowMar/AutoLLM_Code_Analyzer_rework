"""Single-shot engine: line-numbered code in deterministic chunks, structured output.

One replicate:

1. **Scope** — :func:`prompt.select_files` (included files of the included
   origins/kinds). Binary or empty files are recorded as not sent.
2. **Order** — files sorted by path, then shuffled with a per-replicate seed
   derived from (seed_base, sample_hash, index), so position bias varies
   across replicates but every replicate is reproducible; the seed is
   recorded (``file_order_seed``) and also forwarded as the LLM ``seed``
   when the model honours one.
3. **Chunks** — files packed greedily, in that order, into chunks of at most
   the frozen effective chunk size (configured budget, or less when the
   model's context is smaller). A file larger than a chunk is split by line
   ranges (keeping its real line numbers). Nothing is ever cut silently:
   ``coverage`` lists what was sent, split and not sent (with reasons).
4. **Calls** — one structured call per chunk (``json_schema`` strict with the
   chunk's paths as the ``file`` enum when the model supports structured
   outputs, else ``json_object``/prompt + local validation). An answer that
   fails validation gets exactly one repair call (``repaired``); a second
   failure is ``invalid_output``. ``finish_reason == "length"`` is
   ``truncated``. Empty content, error bodies, refusals are ``failed``.
5. **Budget** — before every call: calls < max_llm_requests, spend so far +
   the next call's input cost <= max_cost_usd, wall clock left; the
   heartbeat runs and cancellation is checked while a call is in flight.

Every call (request without credentials, raw response, attempts) is kept as
an artifact. A failed replicate returns no findings: a partial review is
not a review.

Spend: ``cost_usd`` is the billed (or catalog-estimated) cost of the
attempts that reported usage. An attempt whose spend is unknown (no HTTP
response, e.g. a read timeout the provider may still have run; an HTTP 200
without ``usage``) and a call abandoned in flight (cancel or wall clock: the
request finishes in the background, unrecorded) make it a lower bound
(``cost_complete=False``), or None when no attempt reported usage at all; an
HTTP error response is not billed; a replicate that sent nothing spent 0.0. An abandoned call's chunk was
sent, so its files count as sent (``files_unanswered``), not as not reached.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

from backend.analysis.engine.reviewer import SampleOutcome
from backend.analysis.engine.reviewer import client
from backend.analysis.engine.reviewer import findings as findings_mod
from backend.analysis.engine.reviewer import grounding
from backend.analysis.engine.reviewer import prompt
from backend.analysis.engine.reviewer.config import CHARS_PER_TOKEN
from backend.analysis.engine.reviewer.config import DEFAULT_MAX_OUTPUT_TOKENS
from backend.analysis.engine.reviewer.config import MIN_CHUNK_CHARS
from backend.analysis.engine.reviewer.config import ROUTED_PRICE_FACTOR
from backend.analysis.engine.reviewer.config import effective_chunk_chars

if TYPE_CHECKING:
    from backend.analysis.engine.types import Sample
    from backend.analysis.engine.types import SampleFile

logger = logging.getLogger(__name__)

ENGINE = "single_shot"
HEARTBEAT_INTERVAL_S = 10.0
# Per-call HTTP read timeout (a reasoning model can think for minutes
# before the first byte); the replicate's wall clock bounds it further.
REQUEST_TIMEOUT_S = 600.0
MIN_REQUEST_TIMEOUT_S = 30.0
MAX_HTTP_RETRIES = 3
CHUNK_SEPARATOR = "\n\n"

# client error_kind -> (ReviewSample status, failure_reason)
_CALL_FAILURES = {
    "auth": ("failed", "auth"),
    "credits": ("failed", "insufficient_credits"),
    "not_found": ("failed", "model_unavailable"),
    "bad_request": ("failed", "bad_request"),
    "empty": ("failed", "empty_output"),
    "refusal": ("failed", "refusal"),
    "content_filter": ("failed", "content_filter"),
}
_DEFAULT_CALL_FAILURE = ("failed", "api_error")


@dataclass
class Unit:
    """A renderable piece of one file: the whole file or a line range of it."""

    path: str
    start: int
    end: int
    total: int
    text: str

    @property
    def chars(self) -> int:
        return len(self.text)


@dataclass
class Chunk:
    units: list[Unit] = field(default_factory=list)

    @property
    def paths(self) -> list[str]:
        return list(dict.fromkeys(u.path for u in self.units))

    @property
    def chars(self) -> int:
        return sum(u.chars for u in self.units) + len(CHUNK_SEPARATOR) * max(0, len(self.units) - 1)

    def render(self) -> str:
        return CHUNK_SEPARATOR.join(u.text for u in self.units)


# --------------------------------------------------------------------------
# Planning: pure functions, no I/O
# --------------------------------------------------------------------------


def file_order_seed(seed_base: int, sample_hash: str, index: int) -> int:
    """Deterministic 48-bit seed of one replicate (fits ReviewSample.file_order_seed)."""
    digest = hashlib.sha256(f"{seed_base}:{sample_hash}:{index}".encode()).digest()
    return int.from_bytes(digest[:6], "big")


def llm_seed(order_seed: int) -> int:
    return order_seed % (2**31 - 1)


def chunk_chars_for(frozen: dict[str, Any]) -> int:
    """The frozen effective chunk size (recomputed for plans frozen without it)."""
    chunking = frozen.get("chunking") or {}
    if chunking.get("effective_chars"):
        return int(chunking["effective_chars"])
    model = frozen.get("model") or {}
    return max(
        MIN_CHUNK_CHARS,
        effective_chunk_chars(
            chunk_char_budget=int(frozen.get("chunk_char_budget") or 300_000),
            context_window=int(model.get("context_window") or 128_000),
            max_tokens=int((frozen.get("params") or {}).get("max_tokens") or DEFAULT_MAX_OUTPUT_TOKENS),
            prompt_version=frozen.get("prompt_version") or prompt.PROMPT_VERSION,
            schema_version=frozen.get("schema_version") or prompt.SCHEMA_VERSION,
        ),
    )


def _split_file(f: SampleFile, lines: list[str], char_budget: int) -> list[Unit] | None:
    """Line-range parts of a file too big for one chunk; None when one line alone exceeds a chunk."""
    total = len(lines)
    width = max(4, len(str(total)))
    lang = f"{f.language}, " if f.language else ""
    # Upper bound of a part's header + footer (line numbers at full width).
    frame = len(f"=== FILE: {f.path} ({lang}lines {total}-{total} of {total}) ===\n\n=== END FILE: {f.path} ===")
    capacity = char_budget - frame
    parts: list[Unit] = []
    start, used = 1, 0
    for n, line in enumerate(lines, start=1):
        cost = width + 2 + len(line) + 1
        if cost > capacity:
            return None
        if used + cost > capacity:
            parts.append(_unit(f, lines, start, n - 1))
            start, used = n, 0
        used += cost
    parts.append(_unit(f, lines, start, total))
    return parts


def _unit(f: SampleFile, lines: list[str], start: int, end: int) -> Unit:
    text = prompt.render_lines(f.path, lines, start=start, end=end, language=f.language)
    return Unit(path=f.path, start=start, end=end, total=len(lines), text=text)


def plan_chunks(files: list[SampleFile], *, char_budget: int, seed: int) -> tuple[list[Chunk], dict[str, Any]]:
    """Shuffle (seeded), render and pack files into chunks; returns (chunks, coverage)."""
    order = sorted(files, key=lambda f: f.path)
    random.Random(seed).shuffle(order)  # noqa: S311 - reproducible order, not security
    units: list[Unit] = []
    not_sent: list[dict[str, str]] = []
    split: list[dict[str, Any]] = []
    for f in order:
        text = prompt.file_text(f)
        if text is None:
            not_sent.append({"path": f.path, "reason": "binary"})
            continue
        lines = prompt.split_lines(text)
        if not any(line.strip() for line in lines):
            not_sent.append({"path": f.path, "reason": "empty"})
            continue
        whole = _unit(f, lines, 1, len(lines))
        if whole.chars <= char_budget:
            units.append(whole)
            continue
        parts = _split_file(f, lines, char_budget)
        if parts is None:
            not_sent.append({"path": f.path, "reason": "line_exceeds_chunk"})
            continue
        split.append({"path": f.path, "parts": len(parts)})
        units.extend(parts)

    chunks: list[Chunk] = []
    current = Chunk()
    for unit in units:
        if current.units and current.chars + len(CHUNK_SEPARATOR) + unit.chars > char_budget:
            chunks.append(current)
            current = Chunk()
        current.units.append(unit)
    if current.units:
        chunks.append(current)
    coverage = {
        "files_in_scope": len(files),
        "files_planned": len({u.path for u in units}),
        "files_sent": [],
        "files_not_sent": not_sent,
        "files_split": split,
        "chars_sent": 0,
        "chunks": len(chunks),
        "chunks_completed": 0,
        "chunk_char_budget": char_budget,
        "chunk_files": [c.paths for c in chunks],
        "review_complete": None,
    }
    return chunks, coverage


def reviewable(frozen: dict[str, Any], sample: Sample) -> bool:
    """Whether a replicate would send anything (else the review is not applicable)."""
    files, _ = prompt.select_files(
        sample,
        include_origins=frozen.get("include_origins") or prompt.DEFAULT_INCLUDE_ORIGINS,
        include_kinds=frozen.get("include_kinds") or prompt.DEFAULT_INCLUDE_KINDS,
    )
    for f in files:
        text = prompt.file_text(f)
        if text is not None and text.strip():
            return True
    return False


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


def _now() -> float:
    """The replicate's clock (a seam for wall-clock tests)."""
    return time.monotonic()


def _call_waiting(
    fn: Callable[[], client.LLMCall],
    *,
    heartbeat: Callable[[], None],
    is_cancelled: Callable[[], bool],
    deadline: float,
) -> client.LLMCall | str:
    """Run a blocking call in a worker thread, heartbeating meanwhile.

    Returns the call, or ``"cancelled"`` / ``"timeout"`` when the wait was
    abandoned (the HTTP request finishes in the background and is discarded;
    the heartbeat and cancel checks stay on this thread, which owns the DB
    connection).
    """
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ai-review-call")
    try:
        future = pool.submit(fn)
        while True:
            try:
                return future.result(timeout=HEARTBEAT_INTERVAL_S)
            except FuturesTimeout:
                heartbeat()
                if is_cancelled():
                    return "cancelled"
                if _now() > deadline:
                    return "timeout"
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


class _Run:
    """Mutable state of one replicate."""

    def __init__(self, frozen: dict[str, Any], out: SampleOutcome) -> None:
        self.frozen = frozen
        self.out = out
        pricing = (frozen.get("model") or {}).get("pricing") or {}
        self.in_price = float(pricing.get("input_per_token") or 0.0)
        self.out_price = float(pricing.get("output_per_token") or 0.0)
        self.calls = 0
        self.calls_failed = 0
        self.cost = 0.0
        self.cost_estimated = False
        # The same tokens at the frozen catalog price: billed cost depends on
        # the provider that served each call, so reports can show the gap.
        self.catalog_cost = 0.0
        self.tokens: dict[str, int | None] = dict.fromkeys(
            ("prompt_tokens", "completion_tokens", "reasoning_tokens", "cached_tokens", "total_tokens"),
        )
        self.http_attempts = 0
        self.retries = 0
        # Attempts that reported usage / did not (spend unknown), calls abandoned in flight.
        self.priced_attempts = 0
        self.unpriced_attempts = 0
        self.abandoned = 0
        self.providers: Counter[str] = Counter()
        self.finish_reasons: Counter[str] = Counter()
        self.served_models: Counter[str] = Counter()

    @property
    def requests_sent(self) -> int:
        return self.calls + self.abandoned

    def abandon(self, name: str, reason: str) -> None:
        """A call whose wait was abandoned (cancel / wall clock): sent, but its answer and bill are never seen."""
        self.abandoned += 1
        artifact = {"abandoned": reason, "note": "the request was sent; its response and cost were not collected"}
        self.out.artifacts[name] = json.dumps(artifact, indent=1).encode("utf-8")

    def record(self, call: client.LLMCall, name: str) -> None:
        self.calls += 1
        self.calls_failed += not call.ok
        self.http_attempts += len(call.attempts)
        self.retries += call.retries
        for attempt in call.attempts or [{"usage": call.usage, "status_code": call.status_code}]:
            if _priced(attempt.get("usage")):
                self.priced_attempts += 1
            elif attempt.get("status_code") in (None, 200):
                # No response at all (the provider may have run it) or an answer without usage: unknown.
                self.unpriced_attempts += 1
            # else: an HTTP error response, which OpenRouter does not bill.
        usage = call.usage or {}
        for key in self.tokens:
            if usage.get(key) is not None:
                self.tokens[key] = (self.tokens[key] or 0) + int(usage[key])
        catalog = None
        if usage.get("prompt_tokens") is not None or usage.get("completion_tokens") is not None:
            catalog = (usage.get("prompt_tokens") or 0) * self.in_price + (
                usage.get("completion_tokens") or 0
            ) * self.out_price
            self.catalog_cost += catalog
        if usage.get("cost") is not None:
            self.cost += float(usage["cost"])
        elif catalog is not None:
            # No billed cost reported: price the tokens with the frozen snapshot.
            self.cost += catalog
            self.cost_estimated = True
        if call.provider:
            self.providers[call.provider] += 1
        if call.finish_reason:
            self.finish_reasons[call.finish_reason] += 1
        if call.model:
            self.served_models[call.model] += 1
        artifact = {
            "request": call.request,
            "response": call.raw,
            "ok": call.ok,
            "error": call.error,
            "error_kind": call.error_kind,
            "finish_reason": call.finish_reason,
            "provider": call.provider,
            "model": call.model,
            "generation_id": call.generation_id,
            "latency_s": call.latency_s,
            "attempts": call.attempts,
        }
        self.out.artifacts[name] = json.dumps(artifact, ensure_ascii=False, indent=1, default=str).encode("utf-8")

    def usage(self) -> dict[str, Any]:
        unknown = self.unpriced_attempts + self.abandoned
        if not self.requests_sent:
            cost, catalog = 0.0, 0.0  # nothing was sent
        elif unknown and not self.priced_attempts:
            cost, catalog = None, None  # requests went out, none reported what it cost
        else:
            cost, catalog = round(self.cost, 8), round(self.catalog_cost, 8)
        return {
            **self.tokens,
            "cost_usd": cost,
            "cost_estimated": self.cost_estimated,
            "cost_complete": unknown == 0,
            "catalog_cost_usd": catalog,
            "llm_calls": self.calls,
            "llm_calls_failed": self.calls_failed,
            "calls_abandoned": self.abandoned,
            "attempts_without_usage": self.unpriced_attempts,
            "http_attempts": self.http_attempts,
            "retries": self.retries,
            "served_models": dict(self.served_models),
        }


def _priced(usage: Any) -> bool:
    """Whether an attempt reported what it cost (billed cost or token counts to price)."""
    return isinstance(usage, dict) and any(
        usage.get(key) is not None for key in ("cost", "prompt_tokens", "completion_tokens")
    )


def _new_outcome(frozen: dict[str, Any], index: int) -> SampleOutcome:
    model = frozen.get("model") or {}
    return SampleOutcome(
        index=index,
        engine=ENGINE,
        model_slug=model.get("slug", ""),
        model_canonical_slug=model.get("canonical_slug", ""),
        status="failed",
        prompt_version=frozen.get("prompt_version", ""),
        prompt_sha256=frozen.get("prompt_sha256", ""),
        schema_sha256=frozen.get("schema_sha256", ""),
    )


def _request_params(frozen: dict[str, Any], seed: int) -> dict[str, Any]:
    p = frozen.get("params") or {}
    params: dict[str, Any] = {"max_tokens": int(p.get("max_tokens") or DEFAULT_MAX_OUTPUT_TOKENS)}
    if p.get("temperature") is not None:
        params["temperature"] = p["temperature"]
    if p.get("reasoning"):
        params["reasoning"] = p["reasoning"]
    if p.get("seed_forwarded"):
        params["seed"] = llm_seed(seed)
    return params


def run_sample(
    *,
    frozen: dict[str, Any],
    sample: Sample,
    index: int,
    api_key: str,
    is_cancelled: Callable[[], bool],
    heartbeat: Callable[[], None],
    session: Any = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SampleOutcome:
    started = _now()
    out = _new_outcome(frozen, index)
    budget = frozen.get("budget") or {}
    max_calls = int(budget.get("max_llm_requests") or 40)
    max_cost = float(budget.get("max_cost_usd") or 0.5)
    deadline = started + float(budget.get("wall_clock_seconds") or 900)
    prompt_version = frozen.get("prompt_version") or prompt.PROMPT_VERSION
    schema_version = frozen.get("schema_version") or prompt.SCHEMA_VERSION
    mode = frozen.get("output_mode") or "json_schema"
    model_slug = (frozen.get("model") or {}).get("slug", "")

    files, skipped = prompt.select_files(
        sample,
        include_origins=frozen.get("include_origins") or prompt.DEFAULT_INCLUDE_ORIGINS,
        include_kinds=frozen.get("include_kinds") or prompt.DEFAULT_INCLUDE_KINDS,
    )
    seed = file_order_seed(int((frozen.get("params") or {}).get("seed_base") or 0), sample.sample_hash, index)
    out.file_order_seed = seed
    chunks, coverage = plan_chunks(files, char_budget=chunk_chars_for(frozen), seed=seed)
    coverage["out_of_scope"] = skipped
    out.coverage = coverage
    state = _Run(frozen, out)
    system = prompt.system_prompt(prompt_version)
    params = _request_params(frozen, seed)

    def fail(status: str, reason: str, message: str) -> None:
        out.status, out.failure_reason, out.error_message = status, reason, message[:2000]

    def call(  # noqa: PLR0911 - one early return per stop condition
        messages: list[dict[str, Any]],
        allowed: list[str],
        name: str,
    ) -> client.LLMCall | None:
        """One budgeted, heartbeated call; None (with the failure set) when it could not be made."""
        if is_cancelled():
            fail("cancelled", "cancelled", "review cancelled")
            return None
        heartbeat()
        if state.calls >= max_calls:
            fail("budget_exhausted", "budget", f"max_llm_requests={max_calls} reached")
            return None
        # Input cost of the next call at the typical routed price (catalog x factor):
        # a call that cannot be afforded is not started.
        projected = sum(len(m["content"]) for m in messages) / CHARS_PER_TOKEN * state.in_price * ROUTED_PRICE_FACTOR
        if state.cost + projected > max_cost:
            fail(
                "budget_exhausted",
                "budget",
                f"next call (~${projected:.4f} input) would exceed max_cost_usd={max_cost} (spent ${state.cost:.4f})",
            )
            return None
        remaining = deadline - _now()
        if remaining <= 0:
            fail("timed_out", "timeout", "wall_clock_seconds exhausted")
            return None
        timeout = max(MIN_REQUEST_TIMEOUT_S, min(REQUEST_TIMEOUT_S, remaining))
        response_format = prompt.response_format(mode, allowed, schema_version)
        result = _call_waiting(
            lambda: client.chat(
                api_key,
                model=model_slug,
                messages=messages,
                params=params,
                response_format=response_format,
                provider=frozen.get("provider"),
                timeout_s=timeout,
                max_retries=MAX_HTTP_RETRIES,
                session=session,
                sleep=sleep,
            ),
            heartbeat=heartbeat,
            is_cancelled=is_cancelled,
            deadline=deadline,
        )
        if result == "cancelled":
            state.abandon(name, "cancelled")
            fail("cancelled", "cancelled", "review cancelled during an LLM call")
            return None
        if result == "timeout":
            state.abandon(name, "timeout")
            fail("timed_out", "timeout", "wall_clock_seconds exhausted during an LLM call")
            return None
        state.record(result, name)
        if result.finish_reason == "length":
            fail("truncated", "truncated", f"answer hit max_tokens={params['max_tokens']} (finish_reason=length)")
            return None
        if not result.ok:
            status, reason = _CALL_FAILURES.get(result.error_kind, _DEFAULT_CALL_FAILURE)
            fail(status, reason, result.error or "LLM call failed")
            return None
        return result

    collected: list = []
    counts = grounding.empty_counts()
    reviewed: set[str] = set()
    complete = True
    sent: list[str] = []
    unanswered: list[str] = []
    chars_sent = 0
    finished = False
    if not chunks:
        fail("failed", "no_files", "no reviewable (non-empty, text) files in scope")
    elif len(chunks) > max_calls:
        fail("budget_exhausted", "budget", f"{len(chunks)} chunks need more calls than max_llm_requests={max_calls}")
    else:
        for number, chunk in enumerate(chunks, start=1):
            allowed = chunk.paths
            user = prompt.user_prompt(
                files_block=chunk.render(),
                file_count=len(allowed),
                chunk_index=number,
                chunk_count=len(chunks),
                version=prompt_version,
            )
            messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            calls_before, abandoned_before = state.calls, state.abandoned
            answer = call(messages, allowed, f"call-{number:02d}.json")
            if state.requests_sent > calls_before + abandoned_before:
                # The chunk reached the model (even if the answer was unusable or never came back).
                sent.extend(p for p in allowed if p not in sent)
                chars_sent += chunk.chars
                if state.abandoned > abandoned_before:
                    unanswered.extend(p for p in allowed if p not in unanswered)
            if answer is None:
                break
            try:
                parsed = findings_mod.parse_payload(
                    answer.content,
                    sample=sample,
                    frozen=frozen,
                    index=index,
                    allowed_files=allowed,
                )
            except findings_mod.InvalidReview as exc:
                logger.info("AI review sample %d chunk %d invalid, repairing: %s", index, number, exc)
                repair_messages = [
                    *messages,
                    {"role": "assistant", "content": answer.content},
                    {"role": "user", "content": prompt.repair_prompt(exc.errors, prompt_version)},
                ]
                answer = call(repair_messages, allowed, f"call-{number:02d}-repair.json")
                if answer is None:
                    break
                try:
                    parsed = findings_mod.parse_payload(
                        answer.content,
                        sample=sample,
                        frozen=frozen,
                        index=index,
                        allowed_files=allowed,
                    )
                except findings_mod.InvalidReview as again:
                    fail("invalid_output", "invalid_output", f"invalid after one repair: {again}")
                    break
                out.repaired = True
            found, chunk_counts, info = parsed
            collected.extend(found)
            for key, value in chunk_counts.items():
                counts[key] = counts.get(key, 0) + value
            reviewed.update(info["files_reviewed"])
            complete = complete and info["review_complete"]
            coverage["chunks_completed"] = number
        else:
            finished = True

    if finished:
        out.status, out.failure_reason, out.error_message = "succeeded", "", ""
        out.findings = collected
        out.grounding = counts
        coverage["review_complete"] = complete
    else:
        out.grounding = grounding.empty_counts()
    coverage["files_sent"] = sorted(sent)
    coverage["files_unanswered"] = sorted(unanswered)
    coverage["chars_sent"] = chars_sent
    not_reached = [p for c in chunks for p in c.paths if p not in sent]
    coverage["files_not_sent"] = [
        *coverage["files_not_sent"],
        *({"path": p, "reason": "not_reached"} for p in dict.fromkeys(not_reached)),
    ]
    coverage["files_claimed_not_shown"] = sorted(reviewed - set(sent))
    out.files_reviewed = sorted(reviewed)
    out.usage = state.usage()
    out.providers = dict(state.providers)
    out.finish_reasons = dict(state.finish_reasons)
    out.latency_seconds = round(_now() - started, 3)
    return out
