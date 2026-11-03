"""``usable`` and the typed validity notes of a generation attempt (spec section 6.13).

``usable = outcome in {tested, build_fail, start_fail, output_invalid}`` (``contracts.outcome.is_usable``):
those outcomes are model behaviour and are analysed as data. ``infra_error`` and ``excluded`` never are.

The notes written here come from the generation stage only (termination, gateway call log, manifest).
Evaluation adds its own (``lock_source:*``, ``adherence:*``, ``eval_reused``, ``skeleton_equal_baseline``)
through :func:`add_notes`, which keeps existing notes.

Note semantics (all computed from facts, never from what a harness claims about itself):

* truncation kinds: ``wall_clock``, ``idle_timeout``, ``max_turns``, ``budget_exhausted:<kind>``;
* ``harness_error``, ``harness_crash``, ``ended_on_llm_error`` (from the Termination x outcome row);
* ``call_truncations:<n>``: successful calls that ended with ``finish_reason=length``;
* ``served_model_differs``: a call was served by another model than the manifest's frozen
  ``model.endpoint.served_model_name`` (never compared with the requested slug, which providers rename);
* ``response_model_differs``: the calls of one attempt reported more than one response model;
* ``provider_mixed``: more than one served provider answered the attempt's calls;
* ``translation_lossy``: a translated call dropped fields (section 5.4 rule 4);
* ``routing_partial``: the gateway could not apply every routing field (appendix B);
* ``vendor_affinity``: a vendor-affine harness (Claude Code, Codex) ran another vendor's model;
* ``unsupported_knob:<key>``: a policy knob the harness cannot map (gateway limits apply instead);
* ``call_cap_without_compaction``: a per-call input cap on a harness that cannot compact its context.
"""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Mapping
from typing import TYPE_CHECKING
from typing import Any
from typing import Final
from typing import Literal

from backend.generation.contracts.outcome import TERMINATION_OUTCOME
from backend.generation.contracts.outcome import TRUNCATING_TERMINATIONS
from backend.generation.contracts.outcome import Note
from backend.generation.contracts.outcome import Termination
from backend.generation.contracts.outcome import Validity
from backend.generation.contracts.outcome import is_usable
from backend.generation.contracts.outcome import note_code_valid

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunAttempt

__all__ = [
    "NOTE_LEVELS",
    "add_notes",
    "build_validity",
    "call_ok",
    "is_usable",
    "note",
    "policy_knob",
    "validity_for",
]

Level = Literal["info", "warn", "error"]

NOTE_LEVELS: Final[Mapping[str, Level]] = {
    "wall_clock": "info",
    "idle_timeout": "info",
    "max_turns": "info",
    "budget_exhausted": "info",
    "harness_error": "warn",
    "harness_crash": "warn",
    "ended_on_llm_error": "error",
    "call_truncations": "warn",
    "served_model_differs": "warn",
    "response_model_differs": "warn",
    "provider_mixed": "info",
    "translation_lossy": "warn",
    "routing_partial": "warn",
    "vendor_affinity": "info",
    "unsupported_knob": "info",
    "call_cap_without_compaction": "warn",
    "depcache_unavailable": "warn",
}
# Harness knobs whose absence makes a per-call input cap bite without compaction (section 5.3, mini-swe).
_COMPACTION_KNOBS: Final = frozenset(
    {"compaction", "context.compaction", "context_limit", "context.max_context_tokens"},
)


def note(code: str, *, detail: str | None = None, level: Level | None = None) -> Note:
    """A typed note with the default level of its (base) code."""
    base = code.partition(":")[0]
    return Note(code=code, level=level or NOTE_LEVELS.get(base, "info"), detail=detail)


def call_ok(call: Mapping[str, Any]) -> bool:
    """A gateway call that succeeded (HTTP < 400 with a usage block)."""
    status = call.get("status")
    return isinstance(status, int) and status < 400 and call.get("usage") is not None


def policy_knob(effective: Mapping[str, Any] | None, dotted: str) -> Any:
    """A knob value from ``manifest.resources.effective``.

    Accepts the three shapes the platform writes: an ``EffectivePolicy`` dump (``{"content": {...}}``), a bare
    ``ResourcePolicyContent`` dict, or the flat knob view (``{"run.max_llm_requests": 40}``). ``None`` when
    the knob is not recorded.
    """
    if not isinstance(effective, Mapping):
        return None
    if dotted in effective:
        return effective[dotted]
    data: Any = effective.get("content") if isinstance(effective.get("content"), Mapping) else effective
    for part in dotted.split("."):
        if not isinstance(data, Mapping) or part not in data:
            return None
        data = data[part]
    return data


def _vendor(slug: str | None) -> str | None:
    if not slug or "/" not in slug:
        return None
    return slug.split("/", 1)[0].lower()


def build_validity(
    *,
    termination: Termination | str | None,
    budget_exhausted: str | None = None,
    calls: Iterable[Mapping[str, Any]] = (),
    manifest: Mapping[str, Any] | None = None,
    extra: Iterable[Note] = (),
) -> Validity:
    """The validity of one generation attempt from its termination, gateway calls and manifest."""
    manifest = manifest or {}
    notes: list[Note] = []
    term = Termination(termination) if termination else None
    if term is not None:
        rule = TERMINATION_OUTCOME[term]
        if rule.note == "budget_exhausted":
            kind = (budget_exhausted or "").removeprefix("budget_exhausted:") or "requests"
            notes.append(note(f"budget_exhausted:{kind}"))
        elif rule.note:
            notes.append(note(rule.note))
    ok_calls = [call for call in calls if call_ok(call)]
    truncations = sum(1 for call in ok_calls if call.get("finish_reason") == "length")
    if truncations:
        notes.append(note(f"call_truncations:{truncations}"))
    model = manifest.get("model") or {}
    served_name = ((model.get("endpoint") or {}).get("served_model_name")) or None
    if served_name:
        served = {str(c.get("served_model") or c.get("upstream_model") or "") for c in ok_calls} - {""}
        if served - {served_name}:
            detail = ", ".join(sorted(served - {served_name}))
            notes.append(note("served_model_differs", detail=f"served {detail}; frozen {served_name}"))
    response_models = {str(c.get("response_model")) for c in ok_calls if c.get("response_model")}
    if len(response_models) > 1:
        notes.append(note("response_model_differs", detail=", ".join(sorted(response_models))))
    providers = {str(c.get("served_provider")) for c in ok_calls if c.get("served_provider")}
    if len(providers) > 1:
        notes.append(note("provider_mixed", detail=", ".join(sorted(providers))))
    if any(any((call.get("translation_dropped") or {}).values()) for call in calls):
        notes.append(note("translation_lossy"))
    if any(call.get("routing_partial") for call in calls):
        notes.append(note("routing_partial"))
    harness = manifest.get("harness") or {}
    affinity = harness.get("vendor_affinity")
    if affinity and _vendor(model.get("slug")) not in {None, str(affinity).lower()}:
        notes.append(note("vendor_affinity", detail=f"{harness.get('name')} is {affinity}-affine"))
    unsupported = [str(knob) for knob in harness.get("unsupported_knobs") or []]
    for knob in unsupported:
        code = f"unsupported_knob:{knob}"
        if note_code_valid(code):
            notes.append(note(code))
    effective = (manifest.get("resources") or {}).get("effective")
    if _COMPACTION_KNOBS & set(unsupported) and policy_knob(effective, "call.max_input_tokens"):
        notes.append(note("call_cap_without_compaction"))
    notes.extend(extra)
    return Validity(termination=term, truncated=term in TRUNCATING_TERMINATIONS, notes=_dedupe(notes))


def _dedupe(notes: Iterable[Note]) -> list[Note]:
    seen: dict[str, Note] = {}
    for item in notes:
        seen.setdefault(item.code, item)
    return list(seen.values())


def add_notes(validity: Mapping[str, Any] | Validity | None, notes: Iterable[Note]) -> dict[str, Any]:
    """``validity`` plus ``notes`` (existing codes win), as a JSON dict for ``GenerationRun.validity``."""
    current = validity if isinstance(validity, Validity) else Validity.model_validate(validity or {})
    merged = current.model_copy(update={"notes": _dedupe([*current.notes, *notes])})
    return merged.model_dump(mode="json")


def validity_for(run: GenerationRun, attempt: RunAttempt) -> Validity:
    """Termination, truncation and notes of ``attempt`` (its calls are read from its evidence directory)."""
    from backend.generation.agentic import evidence

    manifest_row = getattr(run, "manifest", None)
    manifest = manifest_row.manifest if manifest_row is not None else {}
    calls = evidence.read_calls(run.pk, attempt.attempt)
    return build_validity(
        termination=attempt.termination or None,
        budget_exhausted=attempt.budget_exhausted or None,
        calls=calls,
        manifest=manifest,
    )
