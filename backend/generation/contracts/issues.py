"""Issue registry (spec section 6.15) and the typed ``Issue`` payload.

Every resolver, lint and gate issue has a registered code, a default severity
per study kind and an owning module. The registry is the single list the UI's
``SummaryIssues`` reads (served by the config endpoint). ``Issue`` rejects any
code that is not registered and any severity the registry does not allow for
that code, so a new code is added here (through the CHANGES file) before it
is emitted.

Severity semantics: ``error`` blocks launch (section 6.1); ``warn`` and
``info`` are shown. ``None`` for a study kind means the code does not apply to
that kind (the spec's dash). ``alternatives`` lists the other severities an
emitter may choose for a documented reason (for example
``estimate_exceeds_cap`` is ``warn`` when only the p90 exceeds the cap and
``error`` when the expected total does).
"""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from typing import Final

from pydantic import Field
from pydantic import model_validator

from backend.generation.contracts._base import ContractModel
from backend.generation.contracts._base import NonEmptyStr
from backend.generation.contracts._base import Severity
from backend.generation.contracts._base import StudyKind

__all__ = [
    "ISSUE_CODES",
    "Issue",
    "IssueCode",
    "IssueFix",
    "allowed_severities",
    "is_registered",
    "make_issue",
    "registry_payload",
    "severity_for",
]


@dataclass(frozen=True)
class IssueCode:
    """One registered issue code."""

    code: str
    exploratory: Severity | None
    confirmatory: Severity | None
    owner: str
    summary: str
    alternatives: tuple[Severity, ...] = ()

    def severity(self, study_kind: StudyKind) -> Severity | None:
        return self.exploratory if study_kind == "exploratory" else self.confirmatory

    def allowed(self) -> frozenset[str]:
        return frozenset(s for s in (self.exploratory, self.confirmatory, *self.alternatives) if s is not None)


def _c(
    code: str,
    exploratory: Severity | None,
    confirmatory: Severity | None,
    owner: str,
    summary: str,
    alternatives: tuple[Severity, ...] = (),
) -> IssueCode:
    return IssueCode(code, exploratory, confirmatory, owner, summary, alternatives)


_POLICY = "budget/policy.py"
_PLAN = "study/plan.py"

_CORE_CODES: Final[tuple[IssueCode, ...]] = (
    # --- budget/estimate.py (4.4 step 9) -------------------------------------------------------
    _c(
        "estimate_exceeds_cap",
        "warn",
        "warn",
        "budget/estimate.py",
        "warn when p90_total > study cap; error when expected_total > study cap",
        alternatives=("error",),
    ),
    # --- budget/policy.py: policy lint (4.3) ----------------------------------------------------
    _c("context_cap_above_call_cap", "error", "error", _POLICY, "call.max_input_tokens < 1.1 x context cap"),
    _c(
        "call_cap_without_compaction",
        "warn",
        "warn",
        _POLICY,
        "call.max_input_tokens set and the harness has no context knob",
    ),
    _c("reasoning_mode_invalid", "error", "error", _POLICY, "both effort and max_tokens, or neither for its mode"),
    _c("reasoning_budget_too_large", "error", "error", _POLICY, "reasoning.max_tokens >= call.max_output_tokens"),
    _c("reasoning_budget_too_small", "error", "error", _POLICY, "Anthropic models: reasoning.max_tokens < 1024"),
    _c(
        "output_cap_below_harness_min",
        "error",
        "error",
        _POLICY,
        "call.max_output_tokens < HarnessFacts.min_call_output_tokens",
    ),
    _c("slot_output_cap_tight", "warn", "warn", _POLICY, "1.6 x est_tokens(original) + r_avg > slot cap"),
    _c("brief_exceeds_call_cap", "error", "error", _POLICY, "rendered brief + harness overhead > call input cap"),
    _c("slot_context_exceeds_model", "error", "error", _POLICY, "resolved slot context + prompt > model context"),
    _c(
        "model_lacks_tools",
        "error",
        "error",
        _POLICY,
        "agentic harness with a model without tool calling (cell excluded:config_invalid at resolve)",
    ),
    _c("batch_requires_single_shot", "error", "error", _POLICY, "serving_tier=batch with an agentic executor"),
    _c(
        "provider_unpinned",
        "info",
        "error",
        _POLICY,
        "not exactly one routing.only provider with allow_fallbacks=false (unless provider is a random effect)",
    ),
    _c("usd_basis_multi_model", None, "warn", _POLICY, "budget_basis=usd with more than one model level"),
    _c("resources_vary_by_stack", "warn", "error", _POLICY, "sandbox values differ between cells"),
    _c("mixed_serving_tier", "error", "error", _POLICY, "serving tiers differ between compared conditions"),
    _c(
        "model_alias_in_confirmatory",
        None,
        "error",
        _POLICY,
        "model slug ends -latest/:latest or is a router slug",
    ),
    _c(
        "slot_sampling_provider_default",
        "error",
        "error",
        _POLICY,
        "slot study with sampling.policy=provider_default or temperature 0",
    ),
    # --- study/plan.py ----------------------------------------------------------------------------
    _c("harness_not_enabled", "error", "error", _PLAN, "harness adapter has open questions (registry enabled=False)"),
    _c(
        "harness_protocol_unavailable_for_endpoint",
        "error",
        "error",
        _PLAN,
        "the harness front protocol has no available mode for the cell's endpoint",
    ),
    _c("mixed_harness_profiles", "warn", "warn", _PLAN, "harnesses crossed with mixed native/controlled presets"),
    _c("harness_brief_differs", "warn", "warn", _PLAN, "aider crossed with agentic harnesses (prompt confound)"),
    _c("vendor_affinity", "info", "info", _PLAN, "Claude Code or Codex cell with a non-vendor model"),
    _c("auth_flow_missing", "error", "error", _PLAN, "auth_mode spec_defined without task.auth_flow"),
    _c("starter_missing", "error", "error", _PLAN, "the stack profile has no starter at the requested level"),
    _c("incompatible_style", "error", "error", _PLAN, "style not compatible with the stack's frontend"),
    _c("oracle_missing", "warn", "error", _PLAN, "task has no passing oracle for the stack"),
    _c(
        "conformance_missing",
        "warn",
        "error",
        _PLAN,
        "backend framework not covered by a passing tester conformance kit",
    ),
    _c("adaptive_stop_in_confirmatory", None, "error", _PLAN, "adaptive precision stopping in a confirmatory study"),
    _c("mixed_eval_mode", "warn", "error", _PLAN, "slot evaluation modes differ across compared conditions"),
    _c(
        "single_parent",
        "info",
        "error",
        _PLAN,
        "slot study with a single parent (confirmatory: error unless estimand=conditional_on_parent)",
        alternatives=("info",),
    ),
    _c(
        "manual_parent_selection",
        "info",
        "error",
        _PLAN,
        "parents picked manually (confirmatory: error unless estimand=conditional_on_parent)",
        alternatives=("info",),
    ),
    _c("variant_model_equals_parent_model", "warn", "warn", _PLAN, "variant model equals the parent's model"),
    _c("image_built_after_plan", "info", "info", "agentic/images.py", "image digest recorded after plan time"),
    # --- study/expand.py and study/design.py ------------------------------------------------------
    _c("aliasing", "warn", "warn", "study/expand.py", "fractional grid aliases main effects"),
    _c("unbalanced_grid", "warn", "warn", "study/expand.py", "pruned or fractional grid; EMMs flagged unbalanced"),
    _c(
        "combination_pruned",
        "info",
        "info",
        "study/expand.py",
        "an incompatible level combination was dropped (drop_incompatible)",
    ),
    _c("prose_variant_in_unit", "error", "error", "study/design.py", "legacy design shape with per-task variants"),
    _c("mixed_parents", "error", "error", "study/design.py", "slot sets of different tasks in one study"),
    _c(
        "primary_feature_missing",
        "error",
        "error",
        "study/design.py",
        "ci_width_reached (or a confirmatory slot study) without slot_primary_features",
    ),
    # --- results (study/results.py, patterns/prevalence.py) ---------------------------------------
    _c("mixed_eval_suite", "warn", "error", "study/results.py", "compared runs were evaluated under different suites"),
    _c(
        "mixed_config_fingerprint",
        "warn",
        "error",
        "study/results.py",
        "more than one config_fingerprint inside one (condition, unit)",
    ),
    _c("exclusion_rate", "warn", "warn", "study/results.py", "excluded + infra exceed the threshold of a condition"),
    _c(
        "differential_attrition",
        "warn",
        "warn",
        "study/results.py",
        "attrition differs by more than 5 points between compared conditions",
    ),
    _c("low_effective_n", "warn", "warn", "patterns/prevalence.py", "exact-duplicate share > 0.5 (near-copies)"),
    _c("few_clusters", "info", "info", "patterns/prevalence.py", "fewer than 20 clusters: no cluster-robust SE"),
    # --- slot -------------------------------------------------------------------------------------
    _c("slot_tests_uninformative", "warn", "error", "slot/validate.py", "target has no discriminating linked check"),
    _c("slot_leak_verbatim", "error", "error", "slot/leak.py", "verbatim copy of the original elsewhere in context"),
    _c("slot_leak_high", "warn", "warn", "slot/leak.py", "leak_score > 0.3"),
    _c("overlapping_targets", "warn", "warn", "slot/selection.py", "overlapping targets in one SlotSet"),
    # --- library lint (library/lint.py, WP-03): see _LIBRARY_LINT_CODES below ----------------------
    # --- evaluation and harness lint --------------------------------------------------------------
    _c(
        "editor_harness_agent_brief",
        "error",
        "error",
        "library/lint.py",
        "a non-agentic editor harness (aider) with a brief not tagged editor",
    ),
    _c(
        "probe_unvalidated",
        "warn",
        "warn",
        "evaluation/baseline.py",
        "probe kind disabled: no passing vulnerable/fixed mutant pair",
    ),
)

_LINT = "library/lint.py"

# Library and starter lint (``lint.lint(kind, content) -> list[Issue]``, sections 3.4, 8.2, 11.3, 11.4).
# Every code starts with ``lint_``. WP-03 owns this tuple: it may add ``lint_*`` codes here (recording
# them in the CHANGES file) without a WP-01 round trip; codes outside the ``lint_`` namespace stay WP-01's.
# Lint severities do not depend on the study kind, so both columns are equal.
_LIBRARY_LINT_CODES: Final[tuple[IssueCode, ...]] = (
    # --- every kind
    _c("lint_content_invalid", "error", "error", _LINT, "content does not validate against its contract"),
    _c("lint_unknown_library_ref", "error", "error", _LINT, "a library ref names a version that does not exist"),
    _c("lint_library_ref_kind", "error", "error", _LINT, "a library ref names an entity of the wrong role/kind"),
    # --- tasks
    _c("lint_unknown_feature", "error", "error", _LINT, "a task feature is not in the vocabulary (11.3)"),
    _c("lint_unknown_surface", "error", "error", _LINT, "a task surface is neither a CWE id nor in the vocabulary"),
    _c("lint_unknown_endpoint_ref", "error", "error", _LINT, "a check, probe, UI item or auth flow names no endpoint"),
    _c("lint_unknown_entity_ref", "error", "error", _LINT, "owner_entity or a ref field names no entity"),
    _c("lint_duplicate_id", "error", "error", _LINT, "duplicate endpoint, check, probe or UI id"),
    _c("lint_owner_entity_missing", "error", "error", _LINT, "an owner-auth endpoint has no owner_entity"),
    _c("lint_owner_entity_not_owned", "warn", "warn", _LINT, "owner_entity is not owned_by_user"),
    _c("lint_auth_flow_missing", "warn", "warn", _LINT, "the task has user endpoints but no auth_flow"),
    _c("lint_prose_variant_missing", "error", "error", _LINT, "a system task lacks one of the three prose variants"),
    _c(
        "lint_prose_endpoint_unmentioned",
        "warn",
        "warn",
        _LINT,
        "an endpoint is not mentioned in the standard or exhaustive prose",
        alternatives=("error",),
    ),
    _c(
        "lint_prose_forbidden_phrase",
        "error",
        "error",
        _LINT,
        "prose mentions evaluation, tests or a default password",
    ),
    _c("lint_prose_too_long", "warn", "warn", _LINT, "the minimal prose exceeds its word budget"),
    _c("lint_check_unstated", "info", "info", _LINT, "a check is stated in no prose variant (8.2)"),
    # --- prompts and prompt sets
    _c("lint_template_syntax", "error", "error", _LINT, "the template does not parse in the sandboxed engine"),
    _c("lint_undeclared_variable", "error", "error", _LINT, "the template uses an undeclared context variable"),
    _c("lint_unused_variable", "warn", "warn", _LINT, "a declared variable is never used by the template"),
    _c("lint_unknown_variable", "error", "error", _LINT, "a declared variable is not in the render context"),
    _c("lint_prompt_role_missing", "error", "error", _LINT, "the prompt set lacks a role its mode requires"),
    _c("lint_prompt_role_mismatch", "error", "error", _LINT, "a prompt set role points at a template of another role"),
    _c("lint_prompt_mode_mismatch", "error", "error", _LINT, "a template's modes exclude the prompt set's mode"),
    _c(
        "lint_prompt_fragment_missing",
        "error",
        "error",
        _LINT,
        "an app prompt set lacks an auth_contract.* or network.* fragment",
    ),
    # --- stacks, styles and guidance
    _c(
        "lint_fragment_role",
        "error",
        "error",
        _LINT,
        "a fragment ref names a template whose role is not fragment",
    ),
    _c("lint_guidance_fragment_missing", "error", "error", _LINT, "a security hint other than none has no fragment"),
    _c("lint_style_frontend_unknown", "warn", "warn", _LINT, "compatible_frontends names an unknown framework"),
    # --- starter lint, section 11.4
    _c("lint_starter_forbidden_content", "error", "error", _LINT, "a starter file contains forbidden content"),
    _c("lint_starter_security_library", "error", "error", _LINT, "a starter manifest names a denied security library"),
    _c("lint_starter_unpinned_image", "error", "error", _LINT, "a starter FROM is not digest-pinned"),
    _c("lint_starter_file_missing", "error", "error", _LINT, "the dockerfile or a lockfile is not in files"),
    _c("lint_starter_stack_mismatch", "error", "error", _LINT, "the starter tree does not match its stack profile"),
    # --- added by WP-03 (CHANGES "WP-03 decisions")
    _c("lint_starter_blob_missing", "error", "error", _LINT, "a starter file's blob is not in the blob store"),
    _c("lint_template_render_error", "error", "error", _LINT, "the template fails to render with the given context"),
    _c("lint_detector_rule_invalid", "error", "error", _LINT, "a detector rule does not compile or parse"),
    _c("lint_vocabulary_fallback", "info", "info", _LINT, "tasks/vocab.yaml is missing: the built-in vocabulary"),
)
_CODES: Final[tuple[IssueCode, ...]] = (*_CORE_CODES, *_LIBRARY_LINT_CODES)

ISSUE_CODES: Final[Mapping[str, IssueCode]] = {code.code: code for code in _CODES}
if len(ISSUE_CODES) != len(_CODES):  # pragma: no cover - guarded by tests
    msg = "duplicate issue code in the registry"
    raise RuntimeError(msg)


def is_registered(code: str) -> bool:
    return code in ISSUE_CODES


def severity_for(code: str, study_kind: StudyKind) -> Severity | None:
    """Default severity of ``code`` in a study of ``study_kind`` (``None`` = does not apply)."""
    return ISSUE_CODES[code].severity(study_kind)


def allowed_severities(code: str) -> frozenset[str]:
    return ISSUE_CODES[code].allowed()


class IssueFix(ContractModel):
    """A quick fix the UI can offer (e.g. "raise cap to X", "reduce repeats to N")."""

    label: NonEmptyStr
    action: NonEmptyStr
    params: dict[str, Any] = Field(default_factory=dict)


class Issue(ContractModel):
    """A typed issue: ``{code, severity, cells, fix?}`` plus a message and structured context."""

    code: NonEmptyStr
    severity: Severity
    message: str = ""
    cells: list[str] = Field(default_factory=list)
    path: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    fix: list[IssueFix] = Field(default_factory=list)

    @model_validator(mode="after")
    def _registered(self) -> Issue:
        entry = ISSUE_CODES.get(self.code)
        if entry is None:
            msg = f"unregistered issue code {self.code!r} (add it to contracts/issues.py via the CHANGES file)"
            raise ValueError(msg)
        if self.severity not in entry.allowed():
            msg = f"severity {self.severity!r} is not allowed for {self.code!r} (allowed: {sorted(entry.allowed())})"
            raise ValueError(msg)
        return self


def make_issue(
    code: str,
    study_kind: StudyKind = "exploratory",
    *,
    severity: Severity | None = None,
    message: str = "",
    cells: Iterable[str] = (),
    path: str | None = None,
    data: Mapping[str, Any] | None = None,
    fix: Iterable[IssueFix] = (),
) -> Issue:
    """Build an ``Issue`` with the registry's severity for ``study_kind`` (or an allowed override)."""
    entry = ISSUE_CODES.get(code)
    if entry is None:
        msg = f"unregistered issue code {code!r}"
        raise KeyError(msg)
    chosen = severity or entry.severity(study_kind)
    if chosen is None:
        msg = f"issue {code!r} does not apply to {study_kind} studies"
        raise ValueError(msg)
    return Issue(
        code=code,
        severity=chosen,
        message=message,
        cells=list(cells),
        path=path,
        data=dict(data or {}),
        fix=list(fix),
    )


def registry_payload() -> list[dict[str, Any]]:
    """The registry as JSON rows for the config endpoint and the UI."""
    return [
        {
            "code": entry.code,
            "exploratory": entry.exploratory,
            "confirmatory": entry.confirmatory,
            "alternatives": list(entry.alternatives),
            "owner": entry.owner,
            "summary": entry.summary,
        }
        for entry in _CODES
    ]
