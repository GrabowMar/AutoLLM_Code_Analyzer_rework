"""Pre-run checks: the data behind the pre-research report (owner: API/research stage).

``preflight`` never mutates anything. Result shape::

    {"generated_at", "plan_hash",
     "blocking": [{code, detail}], "warnings": [...], "info": [...],
     "targets": [{job_id, label, group, block, meta: {...target_meta, every GROUPING_KEYS key...},
                  eligible, reasons: [{code, severity: exclude|warn|info, detail}],
                  files, sloc_total, sloc_measured, languages: {lang: sloc}, sample_hash,
                  tools: {slug: "run"|"not_applicable"}}],
     "applicability": {slug: {"run": n, "not_applicable": m}},
     "image": {...sandbox.image_status()},
     "tools": [{slug, name, version, status: ready|image_missing|canary_failed|canary_unknown,
                canary: {passed: bool|None, verdict, missing, unexpected_in_clean, run_id, evaluated_at,
                         config_match, other_config?, ...}}],
     "reviewer": {enabled, engine, harness, model, model_found, structured_outputs, tool_calling,
                  key_present, samples, cost_estimate: {low, expected, high, per_target}},
     "estimates": {targets, eligible, tool_executions, ai_calls, wall_seconds, ai_cost_usd},
     "comparability": {plan_hash, mixed: {prompt_hash: [...], bundle_key: [...], stack_slug: [...],
                       mode: [...], harness: [...]}, notes: [...]},
     "design": {...report.design_matrix(...), complete_blocks, n_groups, min_group_size, balanced, ...}}

Additions to that shape (backward compatible): ``rules`` (the effective
exclusion rules), ``targets[].tool_files`` (files each tool would scan),
``targets[].origins``, ``targets[].ai_cost_estimate``, ``comparability.values``
(distinct values of every identity key, mixed or not), ``design.balanced``
and ``estimates.ai_cost_usd_low/high``. ``design.group_by`` is a list of
factors (the reports stage's vocabulary; groups are labelled like the report).

The design is computed with the report's own ``design_matrix`` over the
eligible targets' ``(group_label, block_label)``, so the preview and the
locked protocol always agree on whether the blocked design is complete. A
canary ``passed`` of ``None`` means no evidence (no self-test, or a tool
without expectations), never a pass. A control vouches only for the tool
configuration it ran: a tool whose plan config (``config_hash``) was never
self-tested is ``canary_unknown`` with verdict ``config_untested`` (warning
``canary_config_untested``), even when the default config passed.

A target whose sample cannot be resolved is listed as ineligible with its
reason; a *platform* failure while resolving (a bug, not a property of the
sample) is blocking, because silently excluding targets would bias the study.
Eligibility comes from ``sample.check_eligibility``; applicability from each
adapter's ``select_files`` on the resolved sample, i.e. exactly what the
executor will run. The analyzer image must be built (tool versions are
provenance) and no planned tool's latest canary on that image may have failed.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db.models import Avg
from django.utils import timezone

from backend.analysis.engine import canary as canary_mod
from backend.analysis.engine import plan as plan_mod
from backend.analysis.engine import sample as sample_mod
from backend.analysis.engine import sandbox
from backend.analysis.engine.adapters import get_adapter
from backend.analysis.engine.reviewer import config as reviewer_config
from backend.analysis.engine.types import Sample
from backend.analysis.engine.types import SampleUnavailable
from backend.analysis.research import report as report_mod

logger = logging.getLogger(__name__)

# Fallback wall-clock guesses when no execution history exists (seconds).
DEFAULT_TOOL_SECONDS = 20.0
DEFAULT_AI_SAMPLE_SECONDS = {"single_shot": 60.0, "agent": 420.0}
RUN_OVERHEAD_SECONDS = 5.0
HISTORY_DAYS = 60

# target_meta keys whose mixing threatens comparability between groups, and
# the design factors under which mixing them is the design, not a confound.
IDENTITY_KEYS = ("prompt_hash", "bundle_key", "stack_slug", "mode", "harness")
EXPECTED_MIX = {
    "bundle_key": ("harness", "condition", "mode"),
    "stack_slug": ("stack",),
    "mode": ("mode", "condition"),
    "harness": ("harness", "condition", "mode"),
}
# target_meta keys every preflight target carries (None when unknown): the
# factors the protocol, the report and progress group and label targets by.
GROUPING_KEYS = (
    "model_slug",
    "model_name",
    "mode",
    "harness",
    "stack_slug",
    "app_slug",
    "condition_id",
    "condition_label",
    "repeat_index",
)
# Aider reviews in a single completion: a reasoning model can spend a 16k
# output cap on reasoning alone (observed with DeepSeek V4.1 Flash at effort
# low: truncated at 16000, complete at 32000).
AIDER_MIN_OUTPUT_TOKENS = 32_000
MIX_EXPLANATIONS = {
    "bundle_key": "Generation methods (bundle keys) differ but are not a grouping factor",
    "stack_slug": "Targets use different stacks: language-specific tools apply to different files; compare "
    "within a stack or use language-agnostic outcomes",
    "mode": "Targets mix generation modes (agent vs scaffolding/custom), which scan different code scopes",
    "harness": "Targets mix agent harnesses but the design does not group by harness",
}


# --------------------------------------------------------------------------
# Labels (shared with study progress and the API)
# --------------------------------------------------------------------------


def target_label(meta: dict[str, Any] | None) -> str:
    """Human label of a target: model · app · harness/mode · repeat · short job id."""
    meta = meta or {}
    if not meta.get("job_id"):
        return "inline source"
    parts = [str(meta.get("model_slug") or meta.get("model_name") or "unknown model")]
    if meta.get("app_slug"):
        parts.append(str(meta["app_slug"]))
    if method := meta.get("harness") or meta.get("mode"):
        parts.append(str(method))
    if meta.get("repeat_index") is not None:
        parts.append(f"r{meta['repeat_index']}")
    parts.append(str(meta["job_id"])[:8])
    return " · ".join(parts)


def group_by_list(design: dict[str, Any] | None) -> list[str]:
    """The grouping factors of an analysis design, normalized like the report normalizes them."""
    return list(report_mod.resolve_design(design)[0]["group_by"])


def group_label(meta: dict[str, Any] | None, group_by: list[str] | str) -> str:
    """The design group of a target, labelled exactly like the report labels it."""
    return report_mod.group_label(meta, [group_by] if isinstance(group_by, str) else list(group_by))


def _issue(code: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "detail": detail, **extra}


# --------------------------------------------------------------------------
# Per-target description
# --------------------------------------------------------------------------


def _sample_stats(smp: Sample | None) -> dict[str, Any]:
    if smp is None:
        return {
            "files": 0,
            "files_total": 0,
            "files_measured": 0,
            "sloc_total": 0,
            "sloc_measured": 0,
            "languages": {},
            "origins": {},
            "sample_hash": "",
            "source": "",
            "warnings": [],
        }
    included = smp.included()
    languages: Counter = Counter()
    origins: Counter = Counter()
    for f in included:
        origins[f.origin] += 1
        if f.sloc:
            languages[f.language] += f.sloc
    return {
        "files": len(included),
        "files_total": len(smp.files),
        "files_measured": sum(1 for f in included if f.measured),
        "sloc_total": sum(f.sloc for f in included),
        "sloc_measured": sum(f.measured_sloc for f in included if f.measured),
        "languages": dict(sorted(languages.items())),
        "origins": dict(sorted(origins.items())),
        "sample_hash": smp.sample_hash,
        "source": smp.source,
        "warnings": list(smp.warnings)[:20],
    }


def describe_job(
    job,
    *,
    scope: dict[str, Any] | None,
    rules: dict[str, Any] | None,
) -> tuple[dict[str, Any], Sample | None]:
    """``(target row, resolved sample or None)`` for one generation job (no tool applicability)."""
    smp: Sample | None = None
    unavailable: dict[str, Any] | None = None
    platform_error: str | None = None
    try:
        smp = sample_mod.resolve_job(job, scope)
    except SampleUnavailable as exc:
        unavailable = {"code": exc.code or "sample_unavailable", "severity": "exclude", "detail": str(exc)}
    except Exception as exc:
        logger.exception("Sample resolution failed for job %s", job.pk)
        platform_error = f"sample resolution failed: {exc}"
    meta: dict[str, Any]
    if smp is not None and smp.meta:
        meta = dict(smp.meta)
    else:
        try:
            meta = sample_mod.target_meta(job)
        except Exception as exc:
            logger.exception("target_meta failed for job %s", job.pk)
            platform_error = platform_error or f"target metadata failed: {exc}"
            meta = {"job_id": str(job.pk), "mode": job.mode, "status": job.status}
    return _target_row(meta, smp, rules, unavailable=unavailable, platform_error=platform_error), smp


def describe_inline(
    files: dict[str, str],
    *,
    scope: dict[str, Any] | None,
    rules: dict[str, Any] | None,
) -> tuple[dict[str, Any], Sample | None]:
    """``(target row, sample)`` for pasted/inline source."""
    smp: Sample | None = None
    unavailable: dict[str, Any] | None = None
    platform_error: str | None = None
    try:
        smp = sample_mod.resolve_inline(files, scope)
    except SampleUnavailable as exc:
        unavailable = {"code": exc.code or "sample_unavailable", "severity": "exclude", "detail": str(exc)}
    except Exception as exc:
        logger.exception("Inline sample resolution failed")
        platform_error = f"sample resolution failed: {exc}"
    meta = dict(smp.meta) if smp is not None and smp.meta else {"job_id": None, "mode": "inline"}
    return _target_row(meta, smp, rules, unavailable=unavailable, platform_error=platform_error), smp


def _reason(raw: Any) -> dict[str, Any]:
    """A target reason with ``code``, ``severity`` and ``detail`` always present."""
    reason = dict(raw) if isinstance(raw, dict) else {"code": str(raw)}
    reason["code"] = str(reason.get("code") or "unknown")
    reason["severity"] = str(reason.get("severity") or "info")
    reason["detail"] = str(reason.get("detail") or "")
    return reason


def _target_row(
    meta: dict[str, Any],
    smp: Sample | None,
    rules: dict[str, Any] | None,
    *,
    unavailable: dict[str, Any] | None,
    platform_error: str | None,
) -> dict[str, Any]:
    reasons: list[dict[str, Any]] = []
    eligible = False
    if platform_error is None:
        try:
            verdict = sample_mod.check_eligibility(meta, smp, rules)
            eligible = bool(verdict.get("eligible")) and smp is not None
            reasons = [_reason(r) for r in verdict.get("reasons") or []]
        except Exception as exc:
            logger.exception("check_eligibility failed for %s", meta.get("job_id"))
            platform_error = f"eligibility check failed: {exc}"
    if unavailable is not None and not any(r["code"] == unavailable["code"] for r in reasons):
        reasons.insert(0, _reason(unavailable))
    if platform_error is not None:
        eligible = False
        reasons.insert(0, _reason({"code": "resolver_error", "severity": "exclude", "detail": platform_error}))
    meta = {**dict.fromkeys(GROUPING_KEYS), **meta}
    return {
        "job_id": meta.get("job_id"),
        "label": target_label(meta),
        "meta": meta,
        "eligible": eligible,
        "reasons": reasons,
        **_sample_stats(smp),
        "tools": {},
        "tool_files": {},
    }


# --------------------------------------------------------------------------
# Plan-level pieces
# --------------------------------------------------------------------------


def _plan_tools(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in (plan or {}).get("tools", []) if isinstance(t, dict) and t.get("slug")]


def _plan_reviewer(plan: dict[str, Any]) -> dict[str, Any] | None:
    reviewer = (plan or {}).get("reviewer") or None
    return reviewer if reviewer and reviewer.get("enabled") else None


def _image_status() -> dict[str, Any]:
    try:
        status = dict(sandbox.image_status())
    except Exception as exc:  # noqa: BLE001 - a dead daemon is a preflight finding, not a crash
        logger.warning("image_status failed: %s", exc)
        return {"daemon_ok": False, "built": False, "error": str(exc)}
    status.setdefault("daemon_ok", True)
    return status


def _key_present(user) -> bool:
    from backend.credentials.services.resolver import MissingApiKeyError
    from backend.credentials.services.resolver import get_openrouter_key

    try:
        return bool(get_openrouter_key(user))
    except MissingApiKeyError:
        return False


def _requires_tool_calling(harness: str | None) -> bool:
    if not harness:
        return False
    try:
        from backend.analysis.engine.reviewer import agentic

        for option in agentic.harness_review_options():
            if option.get("name") == harness:
                return bool(option.get("requires_tool_calling"))
    except Exception as exc:  # noqa: BLE001 - unknown harness metadata: assume tool calling is needed
        logger.warning("harness_review_options failed: %s", exc)
    return True


def _will_reason(reviewer: dict[str, Any]) -> bool:
    """Whether review calls will reason: an explicit effort other than ``none``, else the model default.

    Same rule as the reviewer's cost estimate: a model that accepts
    reasoning reasons at its default effort unless the plan sets one.
    """
    explicit = (reviewer.get("params") or {}).get("reasoning")
    if isinstance(explicit, dict) and explicit.get("effort"):
        return explicit["effort"] != "none"
    supports = (reviewer.get("model") or {}).get("supports") or {}
    return bool(supports.get("reasoning")) and supports.get("reasoning_default_effort") != "none"


def _typical_durations(slugs: list[str]) -> dict[str, float]:
    from backend.analysis.models import ToolResult

    if not slugs:
        return {}
    since = timezone.now() - timedelta(days=HISTORY_DAYS)
    rows = (
        ToolResult.objects.filter(
            tool_slug__in=slugs,
            status=ToolResult.Status.SUCCEEDED,
            duration_seconds__isnull=False,
            finished_at__gte=since,
        )
        .values("tool_slug")
        .annotate(avg=Avg("duration_seconds"))
    )
    return {r["tool_slug"]: float(r["avg"]) for r in rows if r["avg"] is not None}


def _disabled_tools(slugs: list[str]) -> list[str]:
    from backend.analysis.models import AnalyzerTool

    if not slugs:
        return []
    return sorted(AnalyzerTool.objects.filter(slug__in=slugs, is_enabled=False).values_list("slug", flat=True))


def _sum_estimates(estimates: list[dict[str, Any]]) -> dict[str, Any]:
    def total(key: str) -> float:
        return round(sum(float(e.get(key) or 0.0) for e in estimates), 6)

    calls = 0
    for e in estimates:
        value = e.get("calls")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            calls += int(value)
    return {
        "low": total("low"),
        "expected": total("expected"),
        "high": total("high"),
        "per_target": round(total("expected") / len(estimates), 6) if estimates else None,
        "calls": calls,
        "targets": len(estimates),
    }


# --------------------------------------------------------------------------
# Comparability and design
# --------------------------------------------------------------------------


def _comparability(rows: list[dict[str, Any]], plan_hash: str, group_by: list[str]) -> dict[str, Any]:
    values: dict[str, list[str]] = {}
    for key in IDENTITY_KEYS:
        values[key] = sorted({str(r["meta"].get(key)) for r in rows if r["meta"].get(key) not in (None, "")})
    mixed = {k: v for k, v in values.items() if len(v) > 1}
    notes: list[dict[str, Any]] = []
    # Different apps legitimately have different prompt hashes; what matters
    # is prompt material differing *within* an app across the groups.
    per_app: dict[str, set[str]] = {}
    for r in rows:
        if r["meta"].get("prompt_hash"):
            app = report_mod.factor_value(r["meta"], "app")
            per_app.setdefault(app, set()).add(str(r["meta"]["prompt_hash"]))
    if varying := sorted(app for app, hashes in per_app.items() if len(hashes) > 1):
        prompt_variants = "condition" in group_by
        notes.append(
            _issue(
                "comparability_prompt_hash",
                f"Prompt material differs within app(s) {', '.join(varying)}: targets of one app did not all "
                "receive the same prompt"
                + (" (expected when conditions are prompt variants)" if prompt_variants else ""),
                severity="info" if prompt_variants else "warning",
                apps=varying,
            ),
        )
    for key, message in MIX_EXPLANATIONS.items():
        if key in mixed and not any(g in EXPECTED_MIX[key] for g in group_by):
            detail = f"{message}: {', '.join(mixed[key])}"
            notes.append(_issue(f"comparability_{key}", detail, severity="warning"))
    return {"plan_hash": plan_hash, "values": values, "mixed": mixed, "notes": notes}


def _design(rows: list[dict[str, Any]], design: dict[str, Any], notes: list[str]) -> dict[str, Any]:
    """The pre-run design of the eligible *rows*, computed like the locked protocol computes it.

    *design* is ``report.resolve_design`` output. ``report.design_matrix``
    never pairs a target without a known block and needs at least two
    blocks, so the preview cannot call a design complete that the protocol
    then runs unblocked.
    """
    matrix = report_mod.design_matrix([(r["group"], r["block"]) for r in rows], design)
    groups = matrix["groups"]
    replicates = matrix["replicates"] or {}
    return {
        **matrix,
        "complete_blocks": matrix["complete"],
        "n_groups": len(groups),
        "min_group_size": min(groups.values()) if groups else 0,
        "balanced": matrix["complete"] and replicates.get("min") == replicates.get("max"),
        "primary_outcome": design["primary_outcome"],
        "outcomes": design["outcomes"],
        "alpha": design["alpha"],
        "correction": design["correction"],
        "bootstrap_resamples": design["bootstrap_resamples"],
        "notes": notes,
    }


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def _note_bucket(note: dict[str, Any], blocking: list, warnings: list, info: list) -> list:
    if note.get("severity") == "blocking":
        return blocking
    return warnings if note.get("code") == "jobs_unavailable" else info


def _apply_tools(
    rows: list[dict[str, Any]],
    samples: list[Sample | None],
    tools: list[dict[str, Any]],
    adapters: dict[str, Any],
    reviewer: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Fill each row's ``tools``/``tool_files`` (+ AI cost estimate); returns the eligible rows' estimates."""
    ai_slug = (reviewer or {}).get("tool_slug") or "ai_review"
    estimates: list[dict[str, Any]] = []
    for row, smp in zip(rows, samples, strict=True):
        if smp is None:
            continue
        for tool in tools:
            slug = tool["slug"]
            adapter = adapters.get(slug)
            if adapter is None:
                row["tools"][slug] = "unavailable"
                continue
            try:
                selected = adapter.select_files(smp, tool.get("config") or {})
            except Exception as exc:
                logger.exception("select_files failed for %s", slug)
                row["tools"][slug] = "error"
                row["reasons"].append({"code": "applicability_error", "severity": "warn", "detail": f"{slug}: {exc}"})
                continue
            row["tools"][slug] = "run" if selected else "not_applicable"
            row["tool_files"][slug] = len(selected)
        if reviewer:
            # The engines' exact selection (origins + kinds, minus binary and
            # blank files): what will be sent and paid for, and the reviewer's
            # not_applicable verdict when nothing is left.
            chars, n_files = reviewer_config.review_inputs(reviewer, smp)
            row["tools"][ai_slug] = "run" if n_files else "not_applicable"
            row["tool_files"][ai_slug] = n_files
            if row["eligible"] and n_files:
                try:
                    estimate = reviewer_config.estimate_cost(reviewer, chars=chars, files=n_files)
                except Exception as exc:  # noqa: BLE001 - an estimate is advisory
                    logger.warning("estimate_cost failed: %s", exc)
                    estimate = {}
                row["ai_cost_estimate"] = estimate
                if estimate:
                    estimates.append(estimate)
    return estimates


def _canary_entry(evaluation: dict[str, Any] | None) -> dict[str, Any]:
    """A tool's latest self-test verdict as the protocol's controls read it.

    ``passed`` is True/False only when a self-test checked expectations *at
    the plan's tool configuration*; ``None`` (no self-test, a tool without
    expectations, or ``config_untested``: only another configuration was
    self-tested) is no evidence.
    """
    if not isinstance(evaluation, dict):
        return {
            "passed": None,
            "verdict": "untested",
            "missing": [],
            "unexpected_in_clean": [],
            "run_id": None,
            "evaluated_at": None,
        }
    verdict = evaluation.get("verdict") or ("passed" if evaluation.get("passed") else "failed")
    return {
        **evaluation,
        "passed": True if verdict == "passed" else False if verdict == "failed" else None,
        "verdict": verdict,
        "missing": list(evaluation.get("missing") or []),
        "unexpected_in_clean": list(evaluation.get("unexpected_in_clean") or []),
        "run_id": evaluation.get("run_id"),
        "evaluated_at": evaluation.get("evaluated_at"),
    }


def _tool_readiness(
    tools: list[dict[str, Any]],
    image: dict[str, Any],
    planned_tag: str,
) -> list[dict[str, Any]]:
    try:
        # A control vouches only for the configuration it ran: match on the plan tools' config_hash.
        canaries = canary_mod.latest_per_tool(
            planned_tag or None,
            config_hashes={tool["slug"]: str(tool.get("config_hash") or "") for tool in tools},
        )
    except Exception as exc:  # noqa: BLE001 - missing canary data must not crash the preflight
        logger.warning("canary lookup failed: %s", exc)
        canaries = {}
    manifest = image.get("manifest") if isinstance(image.get("manifest"), dict) else None
    rows = []
    for tool in tools:
        slug = tool["slug"]
        canary = _canary_entry(canaries.get(slug))
        if not image.get("built"):
            status = "image_missing"
        elif canary["passed"] is None:
            status = "canary_unknown"
        elif not canary["passed"]:
            status = "canary_failed"
        else:
            status = "ready"
        rows.append(
            {
                "slug": slug,
                "name": tool.get("name") or slug,
                "kind": tool.get("kind") or "",
                "category": tool.get("category") or "",
                "required": bool(tool.get("required", True)),
                "version": str(tool.get("version") or sandbox.tool_version(manifest, slug)),
                "version_source": tool.get("version_source") or "",
                "config_hash": tool.get("config_hash") or "",
                "status": status,
                "canary": canary,
            },
        )
    return rows


def preflight(
    *,
    user,
    plan: dict[str, Any] | None,
    jobs: list | None = None,
    inline: dict[str, str] | None = None,
    exclusion_rules: dict[str, Any] | None = None,
    analysis_design: dict[str, Any] | None = None,
    plan_error: dict[str, Any] | None = None,
    notes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Pre-run checks for *jobs* (and/or *inline* source) under a frozen *plan*.

    *plan_error* explains a missing plan (``{code, detail}``; becomes
    blocking); *notes* are target-resolution notes (``severity: blocking`` is
    blocking, ``jobs_unavailable`` a warning, anything else info).
    """
    plan = plan or {}
    rules = {**sample_mod.DEFAULT_EXCLUSION_RULES, **(exclusion_rules or {})}
    # The report's own normalization: groups and blocks below are labelled
    # exactly as the protocol and the report will label them.
    design, design_notes = report_mod.resolve_design(analysis_design)
    group_by = design["group_by"]
    blocking: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    info: list[dict[str, Any]] = []
    for note in notes or []:
        _note_bucket(note, blocking, warnings, info).append({k: v for k, v in note.items() if k != "severity"})

    tools = _plan_tools(plan)
    reviewer = _plan_reviewer(plan)
    scope = plan.get("scope") or None
    if not plan:
        blocking.append(dict(plan_error or {"code": "no_plan", "detail": "No analysis plan could be frozen."}))
    elif not tools and not reviewer:
        blocking.append(_issue("empty_plan", "The profile enables no tool and no AI reviewer."))
    for note in plan.get("notes") or []:
        if isinstance(note, dict) and note.get("code"):
            warnings.append(_issue(f"plan_{note['code']}", str(note.get("detail") or "")))
    plan_hash = plan_mod.plan_hash(plan) if plan else ""

    adapters: dict[str, Any] = {}
    for tool in tools:
        try:
            adapters[tool["slug"]] = get_adapter(tool["slug"])
        except KeyError:
            blocking.append(_issue("tool_unavailable", f"No adapter is registered for tool '{tool['slug']}'."))

    # ---- targets ----------------------------------------------------------
    rows: list[dict[str, Any]] = []
    samples: list[Sample | None] = []
    for job in jobs or []:
        row, smp = describe_job(job, scope=scope, rules=rules)
        rows.append(row)
        samples.append(smp)
    if inline is not None:
        row, smp = describe_inline(inline, scope=scope, rules=rules)
        rows.append(row)
        samples.append(smp)
    for row in rows:
        row["group"] = report_mod.group_label(row["meta"], group_by)
        row["block"] = report_mod.block_label(row["meta"], design["block_by"])
    cost_estimates = _apply_tools(rows, samples, tools, adapters, reviewer)
    eligible_rows = [r for r in rows if r["eligible"]]
    if not rows:
        blocking.append(_issue("no_targets", "The target spec matches no completed generation job."))
    elif not eligible_rows:
        blocking.append(_issue("no_eligible_targets", "No target passes the exclusion rules."))
    errored = [r["job_id"] or "inline" for r in rows if any(x.get("code") == "resolver_error" for x in r["reasons"])]
    if errored:
        blocking.append(
            _issue(
                "resolver_error",
                f"{len(errored)} target(s) could not be resolved because of a platform error; excluding them "
                "would bias the study.",
                job_ids=errored[:100],
            ),
        )
    if excluded := [r for r in rows if not r["eligible"]]:
        codes = Counter(
            reason.get("code") for r in excluded for reason in r["reasons"] if reason.get("severity") == "exclude"
        )
        summary = ", ".join(f"{code}: {n}" for code, n in sorted(codes.items()))
        warnings.append(
            _issue(
                "targets_excluded",
                f"{len(excluded)} of {len(rows)} target(s) are excluded" + (f" ({summary})" if summary else ""),
                reasons=dict(codes),
            ),
        )
    if with_warnings := [r for r in eligible_rows if r["warnings"]]:
        warnings.append(
            _issue(
                "sample_warnings",
                f"{len(with_warnings)} eligible target(s) have sample resolution warnings (see targets[].warnings)",
            ),
        )

    # ---- applicability ----------------------------------------------------
    ai_slug = (reviewer or {}).get("tool_slug") or "ai_review"
    applicability: dict[str, dict[str, int]] = {}
    for slug in [t["slug"] for t in tools] + ([ai_slug] if reviewer else []):
        counter = Counter(r["tools"].get(slug, "unknown") for r in eligible_rows)
        applicability[slug] = {"run": counter.get("run", 0), "not_applicable": counter.get("not_applicable", 0)}
        applicability[slug].update({k: v for k, v in counter.items() if k not in ("run", "not_applicable")})
    for tool in tools:
        if eligible_rows and not applicability.get(tool["slug"], {}).get("run"):
            (warnings if tool.get("required", True) else info).append(
                _issue(
                    "tool_never_applicable",
                    f"{tool['slug']} has no file in scope in any eligible target (it will be not_applicable).",
                    tool=tool["slug"],
                ),
            )

    # ---- image + tool readiness --------------------------------------------
    image = _image_status()
    planned_tag = (plan.get("analyzer_image") or {}).get("tag") or ""
    if tools:
        if not image.get("daemon_ok", True):
            blocking.append(
                _issue("docker_unavailable", f"The analysis Docker daemon is unreachable: {image.get('error', '')}"),
            )
        elif not image.get("built"):
            blocking.append(
                _issue(
                    "image_missing",
                    "The analyzer image is not built; build it (POST /api/analysis/image/build/) so tool versions "
                    "are read from the image and frozen into the plan.",
                ),
            )
        if planned_tag and image.get("tag") and image.get("tag") != planned_tag:
            blocking.append(
                _issue(
                    "image_changed",
                    f"The plan was frozen for {planned_tag} but the current image is {image.get('tag')}.",
                ),
            )
    tool_rows = _tool_readiness(tools, image, planned_tag)
    if disabled := _disabled_tools([t["slug"] for t in tools]):
        warnings.append(
            _issue(
                "tool_disabled",
                f"Disabled in the tool catalog but part of the plan: {', '.join(disabled)}.",
                tools=disabled,
            ),
        )
    if failed := [t["slug"] for t in tool_rows if t["status"] == "canary_failed"]:
        blocking.append(
            _issue(
                "canary_failed",
                f"The latest self-test failed for: {', '.join(failed)}. Fix the tools or remove them from the "
                "profile; a failing control invalidates their measurements.",
                tools=failed,
            ),
        )
    unknown_rows = [t for t in tool_rows if t["status"] == "canary_unknown"]
    if other_config := [t["slug"] for t in unknown_rows if t["canary"]["verdict"] == "config_untested"]:
        warnings.append(
            _issue(
                "canary_config_untested",
                f"The self-test checked {', '.join(other_config)} only at another configuration (self-tests run "
                "the default profile's tool configs), so no positive control covers this plan's configuration: "
                "zero findings from these tools carry no passing control.",
                tools=other_config,
            ),
        )
    if unknown := [t["slug"] for t in unknown_rows if t["slug"] not in other_config]:
        warnings.append(
            _issue(
                "canary_unknown",
                f"No passing self-test on this analyzer image for: {', '.join(unknown)} "
                "(run one: POST /api/analysis/selftest/).",
                tools=unknown,
            ),
        )

    # ---- reviewer -----------------------------------------------------------
    reviewer_out: dict[str, Any] = {"enabled": False}
    if reviewer:
        reviewer_out = _reviewer_section(user, reviewer, cost_estimates, blocking, warnings, info)

    # ---- estimates ------------------------------------------------------------
    estimates = _estimates(rows, eligible_rows, tools, reviewer, reviewer_out)

    # ---- comparability + design -----------------------------------------------
    comparability = _comparability(eligible_rows, plan_hash, group_by)
    warnings.extend(n for n in comparability["notes"] if n.get("severity") == "warning")
    info.extend(n for n in comparability["notes"] if n.get("severity") != "warning")
    design_out = _design(eligible_rows, design, design_notes)
    if eligible_rows and design_out["n_groups"] < 2:
        info.append(
            _issue("single_group", "All eligible targets fall in one group: descriptive statistics only."),
        )
    elif design_out["block_by"] and eligible_rows and not design_out["complete_blocks"]:
        warnings.append(
            _issue(
                "incomplete_blocks",
                f"The blocked (Friedman) analysis by {design_out['block_by']} is not possible "
                f"({design_out['reason']}); the report will use an unblocked test.",
            ),
        )

    return {
        "generated_at": timezone.now().isoformat(),
        "plan_hash": plan_hash,
        "blocking": blocking,
        "warnings": warnings,
        "info": info,
        "rules": rules,
        "targets": rows,
        "applicability": applicability,
        "image": image,
        "tools": tool_rows,
        "reviewer": reviewer_out,
        "estimates": estimates,
        "comparability": comparability,
        "design": design_out,
    }


def _reviewer_section(
    user,
    reviewer: dict[str, Any],
    cost_estimates: list[dict[str, Any]],
    blocking: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    info: list[dict[str, Any]],
) -> dict[str, Any]:
    model = reviewer.get("model") or {}
    supports = model.get("supports") or {}
    engine = reviewer.get("engine") or "single_shot"
    harness = reviewer.get("harness") or None
    key_present = _key_present(user)
    model_found = bool(model.get("id"))
    if not key_present:
        blocking.append(
            _issue(
                "reviewer_key_missing",
                "The AI reviewer is enabled but no OpenRouter API key is available (Settings → API Access).",
            ),
        )
    if not model_found:
        blocking.append(
            _issue("reviewer_model_unknown", f"Reviewer model '{model.get('slug', '')}' is not in the catalog."),
        )
    if engine == "agent" and _requires_tool_calling(harness) and not supports.get("tools"):
        blocking.append(
            _issue(
                "reviewer_tool_calling",
                f"Harness '{harness}' needs a model with tool calling; '{model.get('slug', '')}' has none.",
            ),
        )
    max_tokens = (reviewer.get("params") or {}).get("max_tokens")
    if (
        engine == "agent"
        and harness == "aider"
        and isinstance(max_tokens, int)
        and max_tokens < AIDER_MIN_OUTPUT_TOKENS
        and _will_reason(reviewer)
    ):
        warnings.append(
            _issue(
                "reviewer_output_cap",
                f"Aider reviews in one completion and '{model.get('slug', '')}' reasons before answering: with "
                f"max_output_tokens {max_tokens} the reasoning can use the whole cap and the replicate fails as "
                "truncated (observed with DeepSeek V4.1 Flash at effort low: 16000 truncated, 32000 sufficed). "
                f"Raise the reviewer's max_output_tokens to at least {AIDER_MIN_OUTPUT_TOKENS} or lower its "
                "reasoning effort.",
                max_output_tokens=max_tokens,
                recommended=AIDER_MIN_OUTPUT_TOKENS,
            ),
        )
    if engine == "single_shot" and model_found and not supports.get("structured_outputs"):
        info.append(
            _issue(
                "reviewer_json_fallback",
                "The reviewer model lacks structured outputs; responses are validated locally (json_object).",
            ),
        )
    # estimate_cost caps every per-sample value at the budget, so reaching
    # the cap means the replicate is likely to stop with 'budget'.
    cap = (reviewer.get("budget") or {}).get("max_cost_usd")
    if isinstance(cap, (int, float)) and not isinstance(cap, bool) and cap > 0:
        per_sample = [e.get("per_sample") or {} for e in cost_estimates]
        if any(float(p.get("expected") or 0.0) >= cap * 0.999 for p in per_sample):
            warnings.append(
                _issue(
                    "reviewer_budget",
                    f"The expected cost of some review replicates reaches the ${cap:.2f} budget; they will likely "
                    "fail with 'budget'. Raise the budget or reduce the reviewed files.",
                ),
            )
        elif any(float(p.get("high") or 0.0) >= cap * 0.999 for p in per_sample):
            info.append(
                _issue("reviewer_budget_worst_case", f"In the worst case a replicate may hit the ${cap:.2f} budget."),
            )
    return {
        "enabled": True,
        "required": bool(reviewer.get("required")),
        "engine": engine,
        "harness": harness,
        "harness_version": reviewer.get("harness_version") or "",
        "model": model.get("slug") or "",
        "model_canonical_slug": model.get("canonical_slug") or "",
        "model_found": model_found,
        "structured_outputs": bool(supports.get("structured_outputs")),
        "tool_calling": bool(supports.get("tools")),
        "key_present": key_present,
        "samples": int(reviewer.get("samples") or 1),
        "prompt_version": reviewer.get("prompt_version") or "",
        "budget": reviewer.get("budget") or {},
        "pricing": model.get("pricing") or {},
        "cost_estimate": _sum_estimates(cost_estimates),
    }


def _estimates(
    rows: list[dict[str, Any]],
    eligible_rows: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    reviewer: dict[str, Any] | None,
    reviewer_out: dict[str, Any],
) -> dict[str, Any]:
    static_slugs = [t["slug"] for t in tools]
    durations = _typical_durations(static_slugs)
    ai_slug = (reviewer or {}).get("tool_slug") or "ai_review"
    samples = int((reviewer or {}).get("samples") or 1)
    ai_sample_seconds = DEFAULT_AI_SAMPLE_SECONDS.get((reviewer or {}).get("engine") or "single_shot", 120.0)
    serial_seconds = 0.0
    tool_executions = 0
    ai_reviews = 0
    for r in eligible_rows:
        serial_seconds += RUN_OVERHEAD_SECONDS
        for slug in static_slugs:
            if r["tools"].get(slug) == "run":
                tool_executions += 1
                serial_seconds += durations.get(slug, DEFAULT_TOOL_SECONDS)
        if reviewer and r["tools"].get(ai_slug) == "run":
            ai_reviews += 1
            serial_seconds += ai_sample_seconds * samples
    max_concurrent = max(int(getattr(settings, "ANALYSIS_MAX_CONCURRENT_RUNS", 2) or 1), 1)
    cost = reviewer_out.get("cost_estimate") or {}
    return {
        "targets": len(rows),
        "eligible": len(eligible_rows),
        "excluded": len(rows) - len(eligible_rows),
        "tool_executions": tool_executions,
        "ai_reviews": ai_reviews,
        "ai_samples": ai_reviews * samples if reviewer else 0,
        "ai_calls": cost.get("calls") or 0,
        "wall_seconds": round(serial_seconds / max_concurrent, 1),
        "ai_cost_usd": cost.get("expected") if reviewer else 0.0,
        "ai_cost_usd_low": cost.get("low") if reviewer else 0.0,
        "ai_cost_usd_high": cost.get("high") if reviewer else 0.0,
        "sloc_measured": sum(r["sloc_measured"] for r in eligible_rows),
        "files": sum(r["files"] for r in eligible_rows),
    }
