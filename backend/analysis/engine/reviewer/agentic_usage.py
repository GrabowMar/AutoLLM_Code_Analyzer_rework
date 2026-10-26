"""The gateway's call log -> usage, cost, providers and failure evidence of one review sample.

The llm-gateway writes every exchange to ``/logs/calls.jsonl`` and its own
events (budget exhaustion, upstream retries, rejected paths) to
``/logs/gateway_events.jsonl``. That log, not the harness's self-report, is
the authoritative record of what the reviewer cost and which provider
served it.

Unlike generation's ``AgentRunner._usage`` (successful calls only), usage
here sums **every** logged call: an error response can still be billed, and
the gateway's own budget accounting counts it too, so recorded spend never
under-reports what the budget enforced. Calls whose usage is missing (a
relay broken mid-stream) are counted separately instead of as zero tokens.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from dataclasses import field
from typing import Any

# A successful exchange: HTTP 200 without an error body.
OK_STATUS = 200
TOKEN_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens", "cached_tokens")


def jsonl(data: bytes | None) -> list[dict[str, Any]]:
    """Rows of a JSON-lines blob; unparseable or non-object lines are skipped.

    Rows end at ``\n`` only. ``str.splitlines`` also breaks at U+2028,
    U+2029 and U+0085, which JSON leaves unescaped inside strings (the
    gateway writes ``ensure_ascii=False``; Node's ``JSON.stringify`` never
    escapes them): one such character in a logged prompt or answer would cut
    the row in two, both halves would fail to parse, and the call, with its
    tokens and cost, would silently vanish from the sample's usage.
    """
    rows: list[dict[str, Any]] = []
    for line in (data or b"").decode("utf-8", errors="replace").split("\n"):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def call_ok(call: dict[str, Any]) -> bool:
    return call.get("status") == OK_STATUS and not (call.get("response") or {}).get("error")


@dataclass
class CallLog:
    """Everything a sample needs from the gateway logs."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_logs(cls, gateway_logs: dict[str, bytes]) -> CallLog:
        return cls(
            calls=jsonl(gateway_logs.get("calls.jsonl")),
            events=jsonl(gateway_logs.get("gateway_events.jsonl")),
        )

    @property
    def ok_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if call_ok(c)]

    @property
    def budget_exhausted(self) -> str | None:
        """The gateway's reason (max_requests / max_cost_usd / ...) once the budget ran out."""
        for event in self.events:
            if event.get("event") == "budget_exhausted":
                return str(event.get("reason") or "unknown")
        return None

    def usage(self, *, pricing: dict[str, Any] | None = None) -> dict[str, Any]:
        """Token and cost totals over every logged call, in the reviewer's usage shape.

        Same keys and semantics as the single-shot engine: a token count no
        call reported stays ``None`` (never 0); ``cost_usd`` is OpenRouter's
        billed ``usage.cost``, and a call with token counts but no cost is
        priced from the frozen *pricing* snapshot (``cost_estimated``).
        ``retries`` are the gateway's own upstream retries, so
        ``http_attempts`` = calls + retries.
        """
        tokens: dict[str, int | None] = dict.fromkeys(TOKEN_KEYS)
        cost = 0.0
        estimated = 0
        without_usage = 0
        retries = 0
        truncated = 0
        seconds = 0.0
        in_price = float((pricing or {}).get("input_per_token") or 0.0)
        out_price = float((pricing or {}).get("output_per_token") or 0.0)
        for call in self.calls:
            response = call.get("response") or {}
            retries += int(call.get("upstream_retries") or 0)
            seconds += float(call.get("duration_s") or 0.0)
            if response.get("finish_reason") == "length":
                truncated += 1
            usage = response.get("usage")
            if not isinstance(usage, dict):
                without_usage += 1
                continue
            values = {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
            }
            if values["total_tokens"] is None and values["prompt_tokens"] is not None:
                values["total_tokens"] = int(values["prompt_tokens"]) + int(values["completion_tokens"] or 0)
            for key, value in values.items():
                if value is not None:
                    tokens[key] = (tokens[key] or 0) + int(value)
            if usage.get("cost") is not None:
                cost += float(usage["cost"])
            elif values["prompt_tokens"] is not None or values["completion_tokens"] is not None:
                cost += (
                    int(values["prompt_tokens"] or 0) * in_price + int(values["completion_tokens"] or 0) * out_price
                )
                estimated += 1
        return {
            **tokens,
            # Nothing logged = nothing forwarded upstream (0.0); calls that all
            # lack usage = spend unknown (None); some lack it = a lower bound.
            "cost_usd": None if self.calls and without_usage == len(self.calls) else round(cost, 8),
            "cost_complete": without_usage == 0,
            "cost_estimated": estimated > 0,
            "cost_estimated_calls": estimated,
            "llm_calls": len(self.calls),
            "llm_calls_failed": sum(1 for c in self.calls if not call_ok(c)),
            "http_attempts": len(self.calls) + retries,
            "retries": retries,
            "served_models": self.served_models(),
            "calls_without_usage": without_usage,
            "truncated_calls": truncated,
            "llm_seconds": round(seconds, 2),
        }

    def providers(self) -> dict[str, int]:
        """Which upstream provider answered each successful call."""
        return dict(Counter((c.get("response") or {}).get("provider") or "unknown" for c in self.ok_calls))

    def served_models(self) -> dict[str, int]:
        return dict(Counter((c.get("response") or {}).get("model") or "unknown" for c in self.ok_calls))

    def finish_reasons(self) -> dict[str, int]:
        return dict(Counter((c.get("response") or {}).get("finish_reason") or "unknown" for c in self.ok_calls))

    def last_answer(self) -> tuple[str, str]:
        """``(content, finish_reason)`` of the last successful call (the model's final answer)."""
        for call in reversed(self.ok_calls):
            response = call.get("response") or {}
            return str(response.get("content") or ""), str(response.get("finish_reason") or "")
        return "", ""

    def first_error(self) -> str:
        """The first upstream error message, for samples without a single successful call."""
        for call in self.calls:
            response = call.get("response") or {}
            err = response.get("error") or call.get("error")
            if err:
                message = err.get("message") if isinstance(err, dict) else str(err)
                return f"HTTP {call.get('status')}: {message}"[:2000]
        return ""

    def failure_kind(self) -> str:
        """Why no call succeeded, in the single-shot engine's sample failure vocabulary.

        ``auth`` / ``insufficient_credits`` / ``model_unavailable`` recur on
        every replicate, so ``run_review`` stops early on them.
        """
        statuses = {c.get("status") for c in self.calls}
        if statuses & {401, 403}:
            return "auth"
        if 402 in statuses:
            return "insufficient_credits"
        if 404 in statuses:
            return "model_unavailable"
        if 400 in statuses:
            return "bad_request"
        return "api_error"

    def remediation(self) -> str:
        statuses = {c.get("status") for c in self.calls}
        if statuses & {401, 403}:
            return "Check the OpenRouter API key in Settings -> API Access."
        if 402 in statuses:
            return "The OpenRouter account has insufficient credits."
        if 404 in statuses:
            return "No provider serves this model under the reviewer's routing policy; relax reviewer.provider."
        return ""

    def gateway_summary(self) -> dict[str, Any]:
        """Gateway-side facts worth keeping next to the usage (validity evidence)."""
        return {
            "budget_exhausted": self.budget_exhausted,
            "rejected_paths": sorted(
                {str(e.get("path", "")) for e in self.events if e.get("event") == "rejected_path"},
            ),
            "rejected_connects": sorted(
                {str(e.get("target", "")) for e in self.events if e.get("event") == "rejected_connect"},
            ),
            "upstream_retry_events": sum(1 for e in self.events if e.get("event") == "upstream_retry"),
            "served_models": self.served_models(),
            "policy_model_overrides": sum(1 for c in self.calls if "model" in (c.get("policy_diff") or {})),
        }
