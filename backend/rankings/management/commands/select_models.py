"""Select the study models by MSS v2 stratified quota sampling (docs/design/MSS_V2.md).

Usage:
    python manage.py select_models [--n 10] [--json] [--dry-run] [--no-sensitivity]
        [--draws 10000] [--uniform-draws 2000] [--spec-draws 2000] [--seed 20261109]
        [--include-previews] [--exclude ID ...] [--force ID ...] [--set KEY=VALUE ...]
        [--anthropic-budget 200] [--openrouter-budget X] [--offline] [--user 1]
        [--study-months 1] [--tier-cuts LOW,HIGH] [--as-of YYYY-MM-DD] [--output PATH]

Budgets (Amendment A1): Anthropic ``--anthropic-budget`` and the OpenRouter
credit, each with a 15% contingency. The OpenRouter credit is read live from
``GET /api/v1/credits`` with the stored key of ``--user`` (never printed);
``--openrouter-budget`` is the fallback when the live read fails or
``--offline`` is given.

``--set`` overrides a constraint (``vendor_max=2``, ``open_min=4``,
``contingency=0.1``, ``quality_floor=false`` ...). ``--force`` pre-seats a
model (recorded as a researcher override; constraints still apply).

``--dry-run`` reads the database only (no snapshot, no metadata write) and
works against a read-only database; without it a ``draft`` selection snapshot
is created and its id printed.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.utils import timezone

from backend.rankings.services.constants import SENSITIVITY_DRAWS
from backend.rankings.services.constants import SENSITIVITY_SEED
from backend.rankings.services.constants import TIERS
from backend.rankings.services.study_selection import build_inputs
from backend.rankings.services.study_selection import default_params
from backend.rankings.services.study_selection import resolve_budgets
from backend.rankings.services.study_selection import run_selection

SETTABLE = {
    "vendor_max",
    "vendors_min",
    "open_min",
    "closed_min",
    "claude_min",
    "origin_us_min",
    "origin_cn_min",
    "contingency",
    "quality_floor",
    "floor_percentiles",
    "missing_capability_popularity_pct",
    "tier_quotas",
    "dirichlet_concentration",
}


def _parse_set(items: list[str]) -> dict[str, Any]:
    out = {}
    for item in items:
        key, sep, value = item.partition("=")
        key = key.strip()
        if not sep or key not in SETTABLE:
            msg = f"--set {item!r}: use KEY=VALUE with KEY in {sorted(SETTABLE)}"
            raise CommandError(msg)
        try:
            out[key] = json.loads(value)
        except json.JSONDecodeError:
            out[key] = value
    return out


class Command(BaseCommand):
    help = "Select the study models (MSS v2, stratified quotas, hard budgets) and explain every decision."

    def add_arguments(self, parser):
        parser.add_argument(
            "--n",
            type=int,
            default=None,
            help="Number of models (default 10 = 3 frontier/3 mid/4 budget).",
        )
        parser.add_argument("--json", action="store_true", help="Print the full payload as JSON.")
        parser.add_argument("--output", default=None, help="Also write the JSON payload to this path.")
        parser.add_argument("--dry-run", action="store_true", help="Write nothing (no snapshot).")
        parser.add_argument("--no-sensitivity", action="store_true", help="Skip the sensitivity analysis.")
        parser.add_argument("--draws", type=int, default=SENSITIVITY_DRAWS["S1"], help="S1 Dirichlet draws.")
        parser.add_argument("--uniform-draws", type=int, default=SENSITIVITY_DRAWS["S2"], help="S2 simplex draws.")
        parser.add_argument("--spec-draws", type=int, default=SENSITIVITY_DRAWS["S3"], help="S3 specification draws.")
        parser.add_argument("--seed", type=int, default=SENSITIVITY_SEED)
        parser.add_argument("--include-previews", action="store_true", help="Admit prerelease builds (E5).")
        parser.add_argument("--exclude", action="append", default=[], help="Exclude this canonical id (repeatable).")
        parser.add_argument("--force", action="append", default=[], help="Pre-seat this canonical id (repeatable).")
        parser.add_argument("--set", action="append", default=[], help="Override a constraint, KEY=VALUE.")
        parser.add_argument(
            "--anthropic-budget",
            type=float,
            default=None,
            help="Anthropic balance, USD (default 200).",
        )
        parser.add_argument("--openrouter-budget", type=float, default=None, help="Fallback OpenRouter credit, USD.")
        parser.add_argument("--offline", action="store_true", help="Do not read the live OpenRouter credit.")
        parser.add_argument("--user", type=int, default=1, help="User whose stored OpenRouter key reads the credit.")
        parser.add_argument(
            "--study-months",
            type=float,
            default=None,
            help="Months the Go runs may span (default 1).",
        )
        parser.add_argument(
            "--anthropic-cache-hit",
            type=float,
            default=None,
            help="Prompt-cache hit share to plan the Claude runs at (default: the planning basket's 0.8).",
        )
        parser.add_argument("--tier-cuts", default=None, help="Explicit USD/run cut points LOW,HIGH.")
        parser.add_argument("--as-of", default=None, help="Snapshot date (default today, UTC).")

    def handle(self, *args, **options):
        as_of = dt.date.fromisoformat(options["as_of"]) if options["as_of"] else timezone.now().date()
        overrides = _parse_set(options["set"])
        if options["n"] is not None and "tier_quotas" not in overrides:
            if options["n"] != 10:
                msg = "--n other than 10 needs --set tier_quotas={...} summing to N"
                raise CommandError(msg)
        tier_cuts = None
        if options["tier_cuts"]:
            try:
                low, high = (float(x) for x in options["tier_cuts"].split(","))
            except ValueError as exc:
                msg = "--tier-cuts must be LOW,HIGH in USD per run"
                raise CommandError(msg) from exc
            tier_cuts = [low, high]
        params = default_params(
            n=options["n"],
            include_previews=options["include_previews"],
            exclude=options["exclude"],
            force=options["force"],
            seed=options["seed"],
            draws={"S1": options["draws"], "S2": options["uniform_draws"], "S3": options["spec_draws"]},
            anthropic_budget=options["anthropic_budget"],
            study_months=options["study_months"],
            tier_cuts=tier_cuts,
            anthropic_cache_hit=options["anthropic_cache_hit"],
            **overrides,
        )
        quotas = params["tier_quotas"]
        if sum(int(quotas[t]) for t in TIERS) != int(params["n"]):
            msg = f"tier quotas {quotas} do not sum to n={params['n']}"
            raise CommandError(msg)

        live = None
        if not options["offline"]:
            live = self._live_credit(options["user"])
        budgets = resolve_budgets(
            {**params, "openrouter_budget": None},
            live_credit=live,
            fallback_openrouter=options["openrouter_budget"],
        )
        params["openrouter_budget_fallback"] = options["openrouter_budget"]
        inputs = build_inputs(as_of, include_previews=options["include_previews"], budgets=budgets)
        if inputs.get("market") is None:
            self.stderr.write(
                self.style.WARNING(
                    "No popularity market snapshot: run import_popularity first. Every model lacks popularity "
                    "and is excluded at E8 (insufficient evidence).",
                ),
            )
        payload = run_selection(
            inputs,
            params,
            sensitivity=not options["no_sensitivity"],
            progress=lambda run: self.stderr.write(f"sensitivity {run} done"),
        )
        for warning in payload.get("warnings") or []:
            self.stderr.write(self.style.WARNING(f"WARNING {warning}"))
        if options["output"]:
            Path(options["output"]).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        if options["json"]:
            self.stdout.write(json.dumps(payload, indent=2, default=str))
        else:
            self.stdout.write(render_text(payload))
        if options["dry_run"]:
            self.stdout.write(self.style.SUCCESS("DRY RUN — no snapshot written"))
            return
        from backend.rankings.services.snapshot import create_selection_snapshot

        snapshot = create_selection_snapshot(payload, params=params, inputs=inputs)
        self.stdout.write(
            self.style.SUCCESS(f"draft selection snapshot {snapshot.pk} ({snapshot.inputs_sha256[:12]})"),
        )

    def _live_credit(self, user_id: int) -> dict[str, Any] | None:
        from backend.rankings.services.study_selection import openrouter_credit

        credit = openrouter_credit(user_id, allow_network=True)
        if credit is None:
            self.stderr.write(
                self.style.WARNING(
                    f"live OpenRouter credit unavailable for user {user_id}; using --openrouter-budget",
                ),
            )
        return credit


# ── Text rendering ─────────────────────────────────────────────────────────


def _f(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _usd(value: Any) -> str:
    return "—" if value is None else f"${value:,.2f}"


def render_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    add = lines.append
    add(f"MSS {payload['version']} study selection, as of {payload['as_of']}")
    add("")
    add("== Flow (PRISMA-style)")
    for step in payload["flow"]:
        excluded = step.get("excluded") or []
        codes: dict[str, int] = {}
        for e in excluded:
            code = e["code"].split(":", 1)[0]
            codes[code] = codes.get(code, 0) + 1
        detail = ", ".join(f"{k} {v}" for k, v in sorted(codes.items()))
        add(
            f"  {step['step']:6s} {step['remaining']:>4}  {step['label']}"
            + (f"  [excluded: {detail}]" if detail else ""),
        )
    tiers = payload.get("tiers") or {}
    add("")
    add(
        f"== Tiers ({tiers.get('rule')}): budget <= {_usd(tiers.get('budget_max_usd_per_run'))}/run"
        f" < mid <= {_usd(tiers.get('frontier_min_usd_per_run_exclusive'))}/run < frontier;"
        f" counts {tiers.get('counts')}",
    )
    floor = payload.get("floor") or {}
    add(
        f"   quality floor percentiles per tier: {floor.get('levels')}; relaxations:"
        f" {floor.get('relaxations') or 'none'}",
    )
    budgets = payload.get("budgets") or {}
    add(
        f"   budgets: Anthropic {_usd(budgets.get('anthropic_usd'))} (effective"
        f" {_usd(budgets.get('anthropic_effective'))}),"
        f" OpenRouter {_usd(budgets.get('openrouter_available_usd'))} (effective"
        f" {_usd(budgets.get('openrouter_effective'))};"
        f" {budgets.get('openrouter_source')})",
    )

    eligible = [c for c in payload["candidates"] if c.get("eligible")]
    eligible.sort(key=lambda c: (TIERS.index(c["tier"]) if c.get("tier") in TIERS else 9, -(c.get("mss_tier") or 0)))
    add("")
    add(f"== Eligible frame ({len(eligible)})")
    add(
        f"  {'tier':8s} {'model':44s} {'MSS':>5s} {'MSS_t':>5s} {'P':>4s} {'C':>4s} {'Q':>4s} {'R':>4s} {'cov':>4s}"
        f" {'$/run':>7s} {'π30':>8s} {'route':10s} {'open':5s} floor",
    )
    for c in eligible:
        s = c["scores"]
        add(
            f"  {c.get('tier', '?'):8s} {c['canonical_id'][:44]:44s} {_f(c.get('mss'))[:5]:>5s}"
            f" {_f(c.get('mss_tier'))[:5]:>5s}"
            f" {_f(s['popularity'], 2):>4s} {_f(s['price'], 2):>4s} {_f(s['capability'], 2):>4s}"
            f" {_f(s['recency'], 2):>4s}"
            f" {_f(c.get('coverage'), 2):>4s} {_f(c['raw'].get('run_cost_usd'), 3):>7s}"
            f" {_usd(c.get('route_cost_usd')):>8s}"
            f" {c.get('route') or 'none'!s:10s} {str(c.get('open_weights'))[:5]:5s}"
            f" {'ok' if c.get('admissible') else (c.get('floor_reason') or '-')}",
        )
    if payload.get("infeasible"):
        add("")
        add("== INFEASIBLE: no set meets every quota, cap and budget")
        add(json.dumps(payload["infeasible"], indent=2, default=str))
        add("")
        _sources(payload, add)
        return "\n".join(lines)

    selection = payload["selection"]
    add("")
    add("== Picks (grouped by tier)")
    for p in selection["picks"]:
        go = p.get("go_usage") or {}
        go_txt = (
            f" go {100 * go['share_of_monthly_limit']:.0f}% of ${go['monthly_limit_usd']:g}/mo, {go['weeks_needed']}"
            f" wk, {go['runs_per_5h']} runs/5h"
            if go
            else ""
        )
        freq = p.get("selection_frequency")
        add(
            f"  {p['tier']:8s} #{p['tier_rank']:<2} {p['canonical_id']:40s} MSS_t {_f(p['mss_tier'])} MSS"
            f" {_f(p['mss'])}"
            f" [{p['admitted_by']}] {p['vendor']}/{p['origin']} open={p['open_weights']} route={p['route']}"
            f" π30={_usd(p['planning_cost_usd'])}{go_txt}"
            + (f" freq={freq:.2f} {p.get('robustness')} [{p.get('robustness_basis')}]" if freq is not None else "")
            + (" FLOOR-SENSITIVE(S6)" if p.get("floor_sensitive") else "")
            + (f" flags={','.join(p['flags'])}" if p["flags"] else ""),
        )
    use = selection["budget_use"]
    add(
        f"  budget use: Anthropic {_usd(use['anthropic']['planned_usd'])} of {_usd(use['anthropic']['effective_usd'])}"
        " effective;"
        f" OpenRouter {_usd(use['openrouter']['planned_usd'])} of {_usd(use['openrouter']['effective_usd'])}"
        " effective",
    )
    anth = use["anthropic"]
    if anth.get("break_even_cache_hit") is not None:
        add(
            f"  Anthropic at cache hit {anth.get('cache_hit_assumed')}: at 0% the Claude picks cost"
            f" {_usd(anth.get('planned_h0_usd'))};"
            f" B-anthropic holds down to a cache-hit share of {anth['break_even_cache_hit']:.0%}",
        )
    add(
        f"  origin mix {selection['origin_mix']}; open-weight mix {selection['open_weight_mix']}; ties at 4 dp:"
        f" {selection['ties_at_4dp'] or 'none'}",
    )
    add("")
    add("== Higher-ranked but not picked")
    for b in payload["blocked"] or []:
        add(
            f"  {b['tier']:8s} #{b['tier_rank']:<2} {b['canonical_id']:40s} MSS_t {_f(b['mss_tier'])} "
            f" {b['blocked_by']}",
        )
    if not payload["blocked"]:
        add("  none")
    add("")
    add("== Alternates")
    for tier, rows in payload["alternates"]["next_in_tier"].items():
        add(
            f"  {tier}: "
            + ", ".join(
                f"{r['canonical_id']} ({_f(r['mss_tier'])}{'' if r['selectable'] else ', ' + str(r['note'])})"
                for r in rows
            ),
        )
    for r in payload["alternates"]["replacements"]:
        if not r["feasible"]:
            add(f"  without {r['replace']}: no feasible replacement (the quotas and budgets need it)")
            continue
        add(
            f"  without {r['replace']}: enters {', '.join(r['with']) or '-'}"
            + (f", also leaves {r['also_leaves']}" if r.get("also_leaves") else ""),
        )
    add("")
    add("== Constraints")
    for c in payload["constraint_status"]:
        add(
            f"  {c['code']:13s} {c['kind']:6s} value={c['value']} limit={c['limit']} satisfied={c['satisfied']}"
            f" binding={c['binding']}",
        )
    opt = payload.get("sum_optimal") or {}
    add("")
    add(
        f"== Sum-optimal diagnostic: Σ MSS_tier greedy {opt.get('sum_mss_tier_greedy')} vs optimum"
        f" {opt.get('sum_mss_tier')}"
        f" (exhaustive={opt.get('exhaustive')}); differs={opt.get('differs_from_greedy')}",
    )
    if opt.get("differs_from_greedy"):
        add(f"   optimum set: {opt.get('set')}")
    sens = payload.get("sensitivity")
    if sens:
        add("")
        add("== Sensitivity")
        for key in ("S1", "S2", "S3"):
            run = sens.get(key)
            if not run:
                continue
            add(
                f"  {key} ({run['draws']} draws, {run['infeasible_draws']} infeasible): Jaccard mean"
                f" {run['jaccard_mean']} p5 {run['jaccard_p5']};"
                f" tau_b mean {run['kendall_tau_b_mean']}; RBO mean {run['rbo_mean']}",
            )
            add(
                "     "
                + ", ".join(
                    f"{f['canonical_id']}={f['frequency']:.2f}{'*' if f['picked'] else ''}"
                    for f in run["frequencies"][:16]
                ),
            )
        for key in ("S4", "S5", "S6"):
            for change in sens.get(key, {}).get("changes", []):
                if not change.get("feasible", True) or change.get("entered") or change.get("left"):
                    add(
                        f"  {key} {change['change']}: +{change.get('entered')} -{change.get('left')}"
                        + ("" if change.get("feasible", True) else " INFEASIBLE"),
                    )
        if sens.get("contenders"):
            add(
                "  contenders (not picked, S1 >= 0.40): "
                + ", ".join(f"{c['canonical_id']} {c['frequency']:.2f}" for c in sens["contenders"]),
            )
    add("")
    _sources(payload, add)
    validity = payload.get("validity") or {}
    add(f"  validity: {json.dumps(validity, default=str)}")
    for warning in payload.get("warnings") or []:
        add(f"WARNING {warning}")
    return "\n".join(lines)


def _sources(payload: dict[str, Any], add) -> None:
    add("== Sources")
    for s in payload.get("sources") or []:
        add(
            f"  {s['name']}: as_of {s.get('as_of')}"
            + (f"; window {s.get('window')}" if s.get("window") else "")
            + (f"; {s.get('license')}" if s.get("license") else ""),
        )
