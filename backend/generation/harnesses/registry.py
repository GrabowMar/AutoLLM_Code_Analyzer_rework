"""Harness registry: every adapter, and whether a study may use it (spec sections 5.3 and 6.15).

An adapter is **enabled** only when it asks to be (``wants_enabled``), its facts have no open questions,
the gateway can serve its front protocol for some endpoint (a translate-only harness needs its translator
in ``settings.GATEWAY_TRANSLATORS``) and the operator has not switched it off
(``settings.AGENT_HARNESSES_DISABLED``). Registering an adapter
that asks to be enabled while ``facts.open_questions`` is non-empty is refused (``HarnessRegistryError``):
an unverified harness must never reach a study by accident. A disabled adapter is still listed, with its
``blocked_reason``, and the plan resolver turns it into the issue ``harness_not_enabled``
(:func:`plan_issues`).

Two narrower gates (E2E-13): ``endpoint_open_questions`` holds questions that concern one endpoint kind only
(Claude Code's OpenRouter ``/v1/messages`` passthrough): cells on that kind get ``harness_not_enabled``, the
others run. ``exploratory_only`` marks an enabled adapter that is not verified for confirmatory use yet (no
real-model smoke run, a known tool-surface caveat): cells get ``harness_exploratory_only`` (warn in an
exploratory study, error in a confirmatory one).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from backend.generation.contracts.issues import Issue
from backend.generation.contracts.issues import make_issue
from backend.generation.contracts.outcome import Termination
from backend.generation.harnesses.aider import AiderAdapter
from backend.generation.harnesses.claude_code import ClaudeCodeAdapter
from backend.generation.harnesses.codex import CodexAdapter
from backend.generation.harnesses.gemini_cli import GeminiCliAdapter
from backend.generation.harnesses.goose import GooseAdapter
from backend.generation.harnesses.mini_swe_agent import MiniSweAgentAdapter
from backend.generation.harnesses.opencode import OpenCodeAdapter
from backend.generation.harnesses.qwen_code import QwenCodeAdapter

if TYPE_CHECKING:
    from backend.generation.harnesses.base import HarnessAdapter

__all__ = [
    "ADAPTER_CLASSES",
    "DEFAULT_HARNESS",
    "HarnessRegistryError",
    "RegistryEntry",
    "adapters",
    "enabled_adapters",
    "entries",
    "entry",
    "get",
    "is_enabled",
    "plan_issues",
    "require_enabled",
    "servable",
]

ADAPTER_CLASSES: Final[tuple[type[HarnessAdapter], ...]] = (
    OpenCodeAdapter,
    MiniSweAgentAdapter,
    AiderAdapter,
    ClaudeCodeAdapter,
    CodexAdapter,
    GooseAdapter,
    QwenCodeAdapter,
    GeminiCliAdapter,
)
DEFAULT_HARNESS: Final = "opencode"  # the default harness factor level is opencode-native (section 5.5)
_TERMINATIONS: Final = frozenset(t.value for t in Termination)


class HarnessRegistryError(ValueError):
    """An adapter declaration the registry refuses (enabled with open questions, bad facts, duplicates)."""


@dataclass(frozen=True)
class RegistryEntry:
    adapter: HarnessAdapter
    enabled: bool
    blocked_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        """``GET /harnesses/`` row (section 9.3)."""
        adapter = self.adapter
        return {
            **adapter.to_dict(),
            "enabled": self.enabled,
            "blocked_reason": self.blocked_reason,
            "versions": list(adapter.versions),
            "pinned_version": adapter.version(),
            "open_questions": list(adapter.facts.open_questions),
            "endpoint_open_questions": {k: list(v) for k, v in adapter.endpoint_open_questions.items()},
            "exploratory_only": adapter.exploratory_only or None,
        }


def validate(adapter: HarnessAdapter) -> None:
    """Refuse an adapter whose declaration breaks the registry rules (raises ``HarnessRegistryError``)."""
    facts = adapter.facts
    name = adapter.name
    if adapter.wants_enabled and facts.open_questions:
        msg = f"{name}: cannot be enabled while facts.open_questions is non-empty ({len(facts.open_questions)} open)"
        raise HarnessRegistryError(msg)
    if not adapter.wants_enabled and not adapter.blocked_reason and not facts.open_questions:
        msg = f"{name}: a disabled adapter needs a blocked_reason"
        raise HarnessRegistryError(msg)
    unknown_kinds = set(adapter.endpoint_open_questions) - set(ENDPOINT_KINDS)
    if unknown_kinds:
        msg = f"{name}: endpoint_open_questions keys must be endpoint kinds {ENDPOINT_KINDS}: {sorted(unknown_kinds)}"
        raise HarnessRegistryError(msg)
    if facts.front_protocol != adapter.protocol:
        msg = f"{name}: facts.front_protocol {facts.front_protocol!r} != adapter.protocol {adapter.protocol!r}"
        raise HarnessRegistryError(msg)
    bad_codes = {code: meaning for code, meaning in facts.exit_codes.items() if meaning not in _TERMINATIONS}
    if bad_codes:
        msg = f"{name}: facts.exit_codes must map to Termination values, got {bad_codes}"
        raise HarnessRegistryError(msg)
    if facts.min_call_output_tokens < 1:
        msg = f"{name}: facts.min_call_output_tokens must be positive"
        raise HarnessRegistryError(msg)
    if not adapter.versions:
        msg = f"{name}: no verified versions"
        raise HarnessRegistryError(msg)


ENDPOINT_KINDS: Final = ("openrouter", "local_proxy")


def servable(adapter: HarnessAdapter, translators: frozenset[str]) -> bool:
    """True when the gateway can serve the adapter's front protocol for at least one endpoint kind."""
    return any(adapter.protocol_mode(endpoint_kind=kind, translators=translators) for kind in ENDPOINT_KINDS)


def _blocked_reason(
    adapter: HarnessAdapter,
    disabled: Iterable[str],
    translators: frozenset[str] = frozenset(),
) -> str | None:
    if adapter.name in set(disabled):
        return "disabled by the operator (AGENT_HARNESSES_DISABLED)"
    if adapter.facts.open_questions:
        reason = adapter.blocked_reason or "facts have open questions"
        return f"{reason}; open questions: " + "; ".join(adapter.facts.open_questions)
    reasons = []
    if not adapter.wants_enabled:
        reasons.append(adapter.blocked_reason)
    if not servable(adapter, translators):
        # A translate-only harness (Gemini CLI) waits for WP-06's translator, whatever its facts say.
        reasons.append(
            f"the gateway has no {adapter.protocol} translator yet (settings.GATEWAY_TRANSLATORS)",
        )
    return "; ".join(r for r in reasons if r) or None


@cache
def _adapters() -> tuple[HarnessAdapter, ...]:
    seen: set[str] = set()
    out: list[HarnessAdapter] = []
    for cls in ADAPTER_CLASSES:
        adapter = cls()
        validate(adapter)
        if adapter.name in seen:
            msg = f"duplicate harness name {adapter.name!r}"
            raise HarnessRegistryError(msg)
        seen.add(adapter.name)
        out.append(adapter)
    return tuple(out)


def entries() -> list[RegistryEntry]:
    """Every adapter with its enabled flag (the operator switch is read at call time)."""
    from django.conf import settings

    disabled = tuple(getattr(settings, "AGENT_HARNESSES_DISABLED", ()) or ())
    translators = _gateway_translators()
    out = []
    for adapter in _adapters():
        reason = _blocked_reason(adapter, disabled, translators)
        out.append(RegistryEntry(adapter=adapter, enabled=reason is None, blocked_reason=reason))
    return out


def entry(name: str) -> RegistryEntry:
    for item in entries():
        if item.adapter.name == name:
            return item
    msg = f"unknown harness {name!r} (known: {', '.join(a.name for a in _adapters())})"
    raise KeyError(msg)


def get(name: str) -> HarnessAdapter:
    """The adapter named ``name`` (raises ``KeyError`` when unknown), enabled or not."""
    return entry(name).adapter


def adapters() -> list[HarnessAdapter]:
    """Every registered adapter, enabled or not."""
    return list(_adapters())


def enabled_adapters() -> list[HarnessAdapter]:
    return [item.adapter for item in entries() if item.enabled]


def is_enabled(name: str) -> bool:
    return entry(name).enabled


def require_enabled(name: str) -> HarnessAdapter:
    """The adapter, or ``ValueError`` with its blocked reason (run-time guard; the resolver uses issues)."""
    item = entry(name)
    if not item.enabled:
        msg = f"harness {name!r} is not enabled: {item.blocked_reason}"
        raise ValueError(msg)
    return item.adapter


def _gateway_translators() -> frozenset[str]:
    """Front protocols the gateway can translate (WP-06 publishes ``settings.GATEWAY_TRANSLATORS``)."""
    from django.conf import settings

    return frozenset(getattr(settings, "GATEWAY_TRANSLATORS", ()) or ())


def plan_issues(
    harness: str,
    *,
    model_slug: str,
    endpoint_kind: str = "openrouter",
    max_output_tokens: int | None = None,
    model_supports_tools: bool | None = None,
    study_kind: str = "exploratory",
    cells: Iterable[str] = (),
) -> list[Issue]:
    """Harness issues for one cell (for ``study/plan.py``): enabled, protocol mode, output cap, tools, affinity.

    Codes: ``harness_not_enabled``, ``harness_protocol_unavailable_for_endpoint``,
    ``output_cap_below_harness_min``, ``model_lacks_tools`` and ``vendor_affinity`` (info, Claude Code /
    Codex / Gemini CLI with a model from another vendor).
    """
    cell_keys = list(cells)
    kind: Any = study_kind
    item = entry(harness)
    adapter = item.adapter
    issues: list[Issue] = []
    if not item.enabled:
        issues.append(
            make_issue(
                "harness_not_enabled",
                kind,
                message=f"{adapter.label} is not enabled: {item.blocked_reason}",
                cells=cell_keys,
                data={"harness": harness},
            ),
        )
    # Every non-OpenRouter endpoint (ollama, openai_compatible) is served through the local proxy.
    upstream_kind = "openrouter" if endpoint_kind == "openrouter" else "local_proxy"
    endpoint_questions = adapter.endpoint_open_questions.get(upstream_kind, ())
    if item.enabled and endpoint_questions:
        issues.append(
            make_issue(
                "harness_not_enabled",
                kind,
                message=(
                    f"{adapter.label} is not enabled for an {upstream_kind} endpoint; open questions: "
                    + "; ".join(endpoint_questions)
                ),
                cells=cell_keys,
                data={"harness": harness, "endpoint_kind": upstream_kind, "reason": "endpoint_open_questions"},
            ),
        )
    if item.enabled and adapter.exploratory_only:
        issues.append(
            make_issue(
                "harness_exploratory_only",
                kind,
                message=f"{adapter.label} is enabled for exploratory studies only: {adapter.exploratory_only}",
                cells=cell_keys,
                data={"harness": harness},
            ),
        )
    if adapter.protocol_mode(endpoint_kind=endpoint_kind, translators=_gateway_translators()) is None:
        issues.append(
            make_issue(
                "harness_protocol_unavailable_for_endpoint",
                kind,
                message=(
                    f"{adapter.label} speaks {adapter.protocol} ({'/'.join(adapter.facts.protocol_modes)}), "
                    f"which the gateway cannot serve for a {endpoint_kind} endpoint yet"
                ),
                cells=cell_keys,
                data={"harness": harness, "endpoint_kind": endpoint_kind, "protocol": adapter.protocol},
            ),
        )
    minimum = adapter.facts.min_call_output_tokens
    if max_output_tokens is not None and max_output_tokens < minimum:
        issues.append(
            make_issue(
                "output_cap_below_harness_min",
                kind,
                message=f"call.max_output_tokens {max_output_tokens} < {adapter.label}'s minimum {minimum}",
                cells=cell_keys,
                path="call.max_output_tokens",
                data={"harness": harness, "min_call_output_tokens": minimum, "max_output_tokens": max_output_tokens},
            ),
        )
    if model_supports_tools is False and adapter.capabilities.requires_tool_calling:
        issues.append(
            make_issue(
                "model_lacks_tools",
                kind,
                message=f"{adapter.label} needs tool calling; {model_slug} does not support it",
                cells=cell_keys,
                data={"harness": harness, "model": model_slug},
            ),
        )
    affinity = adapter.facts.vendor_affinity
    if affinity and not _same_vendor(affinity, model_slug):
        issues.append(
            make_issue(
                "vendor_affinity",
                kind,
                message=(
                    f"{adapter.label} is built for {affinity} models; {model_slug} carries a harness-model confound"
                ),
                cells=cell_keys,
                data={"harness": harness, "vendor_affinity": affinity, "model": model_slug},
            ),
        )
    return issues


_VENDOR_PREFIXES: Final = {
    "anthropic": ("anthropic/", "claude"),
    "openai": ("openai/", "gpt-", "o1", "o3", "o4", "codex"),
    "google": ("google/", "gemini"),
}


def _same_vendor(vendor: str, model_slug: str) -> bool:
    slug = model_slug.lower()
    return any(slug.startswith(prefix) for prefix in _VENDOR_PREFIXES.get(vendor, (f"{vendor}/",)))
