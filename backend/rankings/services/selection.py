"""Stratified quota selection of the study models (spec §3 with Amendment A1).

Pure functions over plain dicts (no database, no network), so a stored
selection replays exactly.

Procedure (A1):

1. **Tiers**: price tertiles of the reference run cost over the eligible frame
   (budget = cheapest ⌊n/3⌋, frontier = dearest ⌊n/3⌋, mid = the rest), or
   explicit USD cut points. Exact quotas per tier (default 3/3/4).
2. **MSS_tier** ranks inside a tier: popularity 0.45, capability 0.30, price
   0.15 (re-normalised inside the tier band), recency 0.10.
3. **Route** per model (part of feasibility): Claude -> the Anthropic key;
   otherwise OpenCode Go when served and its planning cost fits 85% of that
   model's Go monthly limit; otherwise OpenRouter within the shared budget;
   otherwise not selectable.
4. **Quality floor**: capability >= the tier's median capability; a model
   without capability evidence only with top-quartile popularity. The floor
   relaxes per tier (40th, then 33rd percentile) when the quotas cannot be met.
5. **Lexicographic greedy** through the tiers frontier -> mid -> budget, each in
   MSS_tier order: a model is admitted when the caps hold and an exact
   depth-first look-ahead finds a completion meeting every quota and budget.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from typing import Any

from backend.rankings.services.constants import COMPONENTS
from backend.rankings.services.constants import GOALPOSTS
from backend.rankings.services.constants import MSS_TIER_WEIGHTS
from backend.rankings.services.constants import ROUTE_RANK
from backend.rankings.services.constants import RUNS_PER_MODEL
from backend.rankings.services.constants import SELECTION_DEFAULTS
from backend.rankings.services.constants import TIERS
from backend.rankings.services.mss import compute_mss
from backend.rankings.services.mss import percentile

ROUTE_INDEX = {"anthropic": 0, "openrouter": 1, "go": 2}
ORIGIN_INDEX = {"US": 0, "CN": 1}
GOAL_LOW_USD = 10 ** GOALPOSTS["price_log10_usd"][0]
GOAL_HIGH_USD = 10 ** GOALPOSTS["price_log10_usd"][1]
RELAXABLE = (
    "Q-vendor",
    "Q-vendors",
    "Q-open",
    "Q-closed",
    "Q-claude",
    "Q-origin-us",
    "Q-origin-cn",
    "B-anthropic",
    "B-openrouter",
)
SUM_OPTIMAL_NODE_LIMIT = 400_000


class SelectionInfeasibleError(ValueError):
    """No set meets every quota, cap and budget; ``diagnosis`` names the culprits."""

    def __init__(self, message: str, diagnosis: dict[str, Any]):
        super().__init__(message)
        self.diagnosis = diagnosis


# ── Constraints ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Constraints:
    n: int = SELECTION_DEFAULTS["n"]
    tier_quotas: tuple[int, int, int] = tuple(SELECTION_DEFAULTS["tier_quotas"][t] for t in TIERS)
    vendor_max: int | None = SELECTION_DEFAULTS["vendor_max"]
    vendors_min: int = SELECTION_DEFAULTS["vendors_min"]
    open_min: int = SELECTION_DEFAULTS["open_min"]
    closed_min: int = SELECTION_DEFAULTS["closed_min"]
    claude_min: int = SELECTION_DEFAULTS["claude_min"]
    origin_us_min: int = SELECTION_DEFAULTS["origin_us_min"]
    origin_cn_min: int = SELECTION_DEFAULTS["origin_cn_min"]
    # Effective budgets (after the contingency), USD; None = unlimited.
    anthropic_budget: float | None = None
    openrouter_budget: float | None = None

    @classmethod
    def from_params(cls, params: dict[str, Any], budgets: dict[str, Any]) -> Constraints:
        quotas = params.get("tier_quotas") or SELECTION_DEFAULTS["tier_quotas"]
        return cls(
            n=int(params.get("n", SELECTION_DEFAULTS["n"])),
            tier_quotas=tuple(int(quotas[t]) for t in TIERS),
            vendor_max=params.get("vendor_max", SELECTION_DEFAULTS["vendor_max"]),
            vendors_min=int(params.get("vendors_min", SELECTION_DEFAULTS["vendors_min"])),
            open_min=int(params.get("open_min", SELECTION_DEFAULTS["open_min"])),
            closed_min=int(params.get("closed_min", SELECTION_DEFAULTS["closed_min"])),
            claude_min=int(params.get("claude_min", SELECTION_DEFAULTS["claude_min"])),
            origin_us_min=int(params.get("origin_us_min", SELECTION_DEFAULTS["origin_us_min"])),
            origin_cn_min=int(params.get("origin_cn_min", SELECTION_DEFAULTS["origin_cn_min"])),
            anthropic_budget=budgets.get("anthropic_effective"),
            openrouter_budget=budgets.get("openrouter_effective"),
        )

    def relax(self, code: str) -> Constraints:
        changes = {
            "Q-vendor": {"vendor_max": None},
            "Q-vendors": {"vendors_min": 0},
            "Q-open": {"open_min": 0},
            "Q-closed": {"closed_min": 0},
            "Q-claude": {"claude_min": 0},
            "Q-origin-us": {"origin_us_min": 0},
            "Q-origin-cn": {"origin_cn_min": 0},
            "B-anthropic": {"anthropic_budget": None},
            "B-openrouter": {"openrouter_budget": None},
        }[code]
        return replace(self, **changes)

    def active(self, code: str) -> bool:
        return {
            "Q-vendor": self.vendor_max is not None,
            "Q-vendors": self.vendors_min > 0,
            "Q-open": self.open_min > 0,
            "Q-closed": self.closed_min > 0,
            "Q-claude": self.claude_min > 0,
            "Q-origin-us": self.origin_us_min > 0,
            "Q-origin-cn": self.origin_cn_min > 0,
            "B-anthropic": self.anthropic_budget is not None,
            "B-openrouter": self.openrouter_budget is not None,
        }[code]

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "tier_quotas": dict(zip(TIERS, self.tier_quotas, strict=True)),
            "vendor_max": self.vendor_max,
            "vendors_min": self.vendors_min,
            "open_min": self.open_min,
            "closed_min": self.closed_min,
            "claude_min": self.claude_min,
            "origin_us_min": self.origin_us_min,
            "origin_cn_min": self.origin_cn_min,
            "anthropic_budget_effective": self.anthropic_budget,
            "openrouter_budget_effective": self.openrouter_budget,
        }


# ── Tiers, within-tier score, routes, floor ────────────────────────────────


def assign_tiers(
    costs: dict[str, float],
    *,
    cuts: tuple[float, float] | None = None,
    scale: tuple[float, float] = (1.0, 1.0),
) -> tuple[dict[str, str], dict[str, Any]]:
    """Price tier per model and the cut points (``budget <= low < mid <= high < frontier``).

    Without explicit ``cuts`` the tertile rule ranks by cost: budget = first
    ⌊n/3⌋, frontier = last ⌊n/3⌋. ``scale`` moves the cut points (sensitivity).
    """
    ordered = sorted(costs.items(), key=lambda kv: (kv[1], kv[0]))
    n = len(ordered)
    if cuts is None:
        third = n // 3
        if third == 0:
            low = high = max((c for _, c in ordered), default=0.0)
        else:
            low = ordered[third - 1][1]
            high = ordered[n - third - 1][1]
        rule = "tertiles of the reference run cost over the eligible frame"
    else:
        low, high = cuts
        rule = "explicit cut points"
    low, high = low * scale[0], high * scale[1]
    tiers = {}
    for cid, cost in ordered:
        tiers[cid] = "budget" if cost <= low else ("mid" if cost <= high else "frontier")
    info = {
        "rule": rule,
        "budget_max_usd_per_run": round(low, 6),
        "frontier_min_usd_per_run_exclusive": round(high, 6),
        "scale": list(scale),
        "counts": {t: sum(1 for v in tiers.values() if v == t) for t in TIERS},
    }
    return tiers, info


def tier_bands(info: dict[str, Any]) -> dict[str, tuple[float, float]]:
    low, high = info["budget_max_usd_per_run"], info["frontier_min_usd_per_run_exclusive"]
    return {
        "budget": (min(GOAL_LOW_USD, low * 0.5) if low > 0 else GOAL_LOW_USD, max(low, GOAL_LOW_USD * 1.01)),
        "mid": (max(low, 1e-9), max(high, low * 1.01, 1e-8)),
        "frontier": (max(high, 1e-9), max(GOAL_HIGH_USD, high * 1.01)),
    }


def mss_tier(item: dict[str, Any], weights: dict[str, float] = MSS_TIER_WEIGHTS) -> dict[str, Any]:
    scores = {**item["scores"], "price": item["tier_price_score"]}
    return compute_mss({k: scores.get(k) for k in COMPONENTS}, weights)


def go_usage(raw: dict[str, Any], limit: float | None, *, five_hour_share: float, weekly_share: float) -> dict | None:
    pi = raw.get("go_planning_cost_usd")
    per_run = raw.get("go_run_cost_usd")
    if not limit or pi is None:
        return None
    return {
        "monthly_limit_usd": limit,
        "planning_cost_usd": round(pi, 4),
        "share_of_monthly_limit": round(pi / limit, 4),
        "weeks_needed": max(1, math.ceil(pi / (weekly_share * limit))),
        "runs_per_5h": math.floor(five_hour_share * limit / per_run) if per_run else None,
    }


def choose_route(
    item: dict[str, Any],
    *,
    contingency: float,
    study_months: float,
    go_limits_relaxed: bool = False,
) -> dict[str, Any]:
    """Route decision of one model (A1 §4): ``{route, cost, reason, flags}``; route None = not selectable."""
    routes = item.get("routes") or {}
    raw = item["raw"]
    pi = raw.get("planning_cost_usd")
    flags: list[str] = []
    if item["vendor"] == "anthropic" and (routes.get("anthropic") or {}).get("paid"):
        return {"route": "anthropic", "cost": pi, "reason": "Claude model on the user's Anthropic key", "flags": flags}
    go = routes.get("go") or {}
    if go.get("paid"):
        limit = item.get("go_limit_usd")
        go_pi = raw.get("go_planning_cost_usd")
        cap = None if limit is None else (1 - contingency) * limit * study_months
        if go_limits_relaxed or (cap is not None and go_pi is not None and go_pi <= cap):
            return {
                "route": "go",
                "cost": go_pi,
                "reason": "served on OpenCode Go within its monthly limit",
                "flags": flags,
            }
        flags.append("go_limit_exceeded" if limit is not None else "go_limit_unknown")
    if (routes.get("openrouter") or {}).get("paid"):
        return {
            "route": "openrouter",
            "cost": pi,
            "reason": "OpenRouter within the shared budget" + (" (Go limit exceeded)" if flags else ""),
            "flags": flags,
        }
    reason = "budget:B-go" if flags else "no_paid_route"
    return {"route": None, "cost": pi, "reason": reason, "flags": flags}


def floor_thresholds(items: list[dict[str, Any]], levels: dict[str, float | None]) -> dict[str, float | None]:
    out = {}
    for tier in TIERS:
        pct = levels.get(tier)
        values = [
            i["scores"]["capability"] for i in items if i["tier"] == tier and i["scores"]["capability"] is not None
        ]
        out[tier] = None if pct is None else percentile(values, pct)
    return out


def apply_floor(
    items: list[dict[str, Any]],
    levels: dict[str, float | None],
    *,
    popularity_cut: float | None,
) -> dict[str, float | None]:
    """Set ``admissible`` / ``floor_reason`` on every item; returns the per-tier thresholds."""
    thresholds = floor_thresholds(items, levels)
    for item in items:
        thr = thresholds[item["tier"]]
        q = item["scores"]["capability"]
        item["capability_floor"] = thr
        if thr is None:
            item["admissible"], item["floor_reason"] = True, None
        elif q is not None:
            item["admissible"] = q >= thr - 1e-12
            item["floor_reason"] = None if item["admissible"] else f"floor:Q-quality(capability {q:.3f} < {thr:.3f})"
        else:
            pop = item["scores"]["popularity"]
            ok = popularity_cut is not None and pop is not None and pop >= popularity_cut - 1e-12
            item["admissible"] = ok
            item["floor_reason"] = None if ok else "floor:Q-quality(capability missing, popularity below top quartile)"
            if ok and "capability_missing_admitted" not in item["flags"]:
                item["flags"] = [*item["flags"], "capability_missing_admitted"]
    return thresholds


def capability_percentile_in_tier(items: list[dict[str, Any]]) -> None:
    for tier in TIERS:
        members = [i for i in items if i["tier"] == tier and i["scores"]["capability"] is not None]
        values = sorted(i["scores"]["capability"] for i in members)
        for i in items:
            if i["tier"] != tier:
                continue
            q = i["scores"]["capability"]
            i["capability_pct_in_tier"] = (
                None if q is None or not values else round(sum(1 for v in values if v <= q) / len(values), 4)
            )


def order_key(item: dict[str, Any]) -> tuple:
    return (
        TIERS.index(item["tier"]),
        -round(item["mss_tier"] or 0.0, 4),
        -(item["scores"]["popularity"] or 0.0),
        -(item["scores"]["price"] or 0.0),
        ROUTE_RANK.get(item.get("route") or "openrouter", 3),
        item["canonical_id"],
    )


# ── Exact feasibility ──────────────────────────────────────────────────────


@dataclass
class _Compiled:
    ids: list[str]
    tier: list[int]
    vendor: list[int]
    origin: list[int]
    open: list[int]
    claude: list[bool]
    route: list[int]
    cents: list[int]
    vendor_names: list[str]
    index: dict[str, int] = field(default_factory=dict)


def cost_cents(usd: float | None) -> int:
    """A cost in whole cents, rounded UP, so a hard budget is never overshot by rounding."""
    return math.ceil(round((usd or 0.0) * 100, 6))


def cap_cents(usd: float) -> int:
    """A budget cap in whole cents, rounded DOWN (see :func:`cost_cents`)."""
    return math.floor(round(usd * 100, 6))


def compile_items(items: list[dict[str, Any]]) -> _Compiled:
    vendors = sorted({i["vendor"] for i in items})
    vidx = {v: k for k, v in enumerate(vendors)}
    comp = _Compiled(
        ids=[i["canonical_id"] for i in items],
        tier=[TIERS.index(i["tier"]) for i in items],
        vendor=[vidx[i["vendor"]] for i in items],
        origin=[ORIGIN_INDEX.get(i.get("origin") or "", 2) for i in items],
        open=[1 if i.get("open_weights") is True else 0 if i.get("open_weights") is False else -1 for i in items],
        claude=[i["vendor"] == "anthropic" and i.get("route") == "anthropic" for i in items],
        route=[ROUTE_INDEX.get(i.get("route") or "", -1) for i in items],
        cents=[cost_cents(i.get("route_cost")) for i in items],
        vendor_names=vendors,
    )
    comp.index = {cid: k for k, cid in enumerate(comp.ids)}
    return comp


class Problem:
    """Caps, quotas and the exact depth-first feasibility look-ahead over compiled items."""

    def __init__(self, comp: _Compiled, cons: Constraints):
        self.c = comp
        self.cons = cons
        self.q = cons.tier_quotas
        self.nv = len(comp.vendor_names)
        self.anth_cap = None if cons.anthropic_budget is None else cap_cents(cons.anthropic_budget)
        self.or_cap = None if cons.openrouter_budget is None else cap_cents(cons.openrouter_budget)

    # state = (n, t0, t1, t2, open, closed, claude, us, cn, anth_cents, or_cents, vendor_counts tuple)
    def empty(self) -> tuple:
        return (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, (0,) * self.nv)

    def cap_violation(self, s: tuple, k: int) -> str | None:
        c = self.c
        t = c.tier[k]
        if s[1 + t] >= self.q[t]:
            return f"cap:tier_full({TIERS[t]})"
        if self.cons.vendor_max is not None and s[11][c.vendor[k]] >= self.cons.vendor_max:
            return f"cap:Q-vendor({c.vendor_names[c.vendor[k]]})"
        if c.route[k] == 0 and self.anth_cap is not None and s[9] + c.cents[k] > self.anth_cap:
            return "budget:B-anthropic"
        if c.route[k] == 1 and self.or_cap is not None and s[10] + c.cents[k] > self.or_cap:
            return "budget:B-openrouter"
        return None

    def add(self, s: tuple, k: int) -> tuple:
        c = self.c
        tiers = [s[1], s[2], s[3]]
        tiers[c.tier[k]] += 1
        vc = list(s[11])
        vc[c.vendor[k]] += 1
        return (
            s[0] + 1,
            tiers[0],
            tiers[1],
            tiers[2],
            s[4] + (c.open[k] == 1),
            s[5] + (c.open[k] == 0),
            s[6] + c.claude[k],
            s[7] + (c.origin[k] == 0),
            s[8] + (c.origin[k] == 1),
            s[9] + (c.cents[k] if c.route[k] == 0 else 0),
            s[10] + (c.cents[k] if c.route[k] == 1 else 0),
            tuple(vc),
        )

    def unmet(self, s: tuple) -> list[str]:
        cons = self.cons
        out = []
        if (s[1], s[2], s[3]) != self.q:
            out.append("Q-tier")
        if sum(1 for v in s[11] if v) < cons.vendors_min:
            out.append("Q-vendors")
        if s[4] < cons.open_min:
            out.append("Q-open")
        if s[5] < cons.closed_min:
            out.append("Q-closed")
        if s[6] < cons.claude_min:
            out.append("Q-claude")
        if s[7] < cons.origin_us_min:
            out.append("Q-origin-us")
        if s[8] < cons.origin_cn_min:
            out.append("Q-origin-cn")
        return out

    def _suffix(self, rest: list[int]) -> list[tuple]:
        """Per position: (tier counts, open, closed, claude, us, cn, vendor set) of rest[pos:]."""
        c = self.c
        out: list[tuple] = [None] * (len(rest) + 1)  # type: ignore[list-item]
        acc = [0, 0, 0, 0, 0, 0, 0, 0]
        vendors: frozenset[int] = frozenset()
        out[len(rest)] = (tuple(acc), vendors)
        for pos in range(len(rest) - 1, -1, -1):
            k = rest[pos]
            acc = list(acc)
            acc[c.tier[k]] += 1
            acc[3] += c.open[k] == 1
            acc[4] += c.open[k] == 0
            acc[5] += c.claude[k]
            acc[6] += c.origin[k] == 0
            acc[7] += c.origin[k] == 1
            vendors = vendors | {c.vendor[k]}
            out[pos] = (tuple(acc), vendors)
        return out

    def _bounds_ok(self, s: tuple, k_left: int, suffix: tuple) -> bool:
        cons = self.cons
        acc, vendors = suffix
        for t in range(3):
            if self.q[t] - s[1 + t] > acc[t]:
                return False
        deficits = (
            (cons.open_min - s[4], acc[3]),
            (cons.closed_min - s[5], acc[4]),
            (cons.claude_min - s[6], acc[5]),
            (cons.origin_us_min - s[7], acc[6]),
            (cons.origin_cn_min - s[8], acc[7]),
        )
        for need, have in deficits:
            if need > 0 and (need > k_left or need > have):
                return False
        if max(0, cons.open_min - s[4]) + max(0, cons.closed_min - s[5]) > k_left:
            return False
        have_vendors = sum(1 for v in s[11] if v)
        need_vendors = cons.vendors_min - have_vendors
        if need_vendors > 0:
            new = sum(1 for v in vendors if not s[11][v])
            if need_vendors > k_left or need_vendors > new:
                return False
        return True

    def feasible(self, s: tuple, rest: list[int]) -> bool:
        """Exact: is there T ⊆ rest with |S| + |T| = n meeting every cap, quota and budget?"""
        suffix = self._suffix(rest)
        failed: set = set()

        def dfs(state: tuple, start: int) -> bool:
            k_left = self.cons.n - state[0]
            if k_left == 0:
                return not self.unmet(state)
            if len(rest) - start < k_left:
                return False
            key = (start, state)
            if key in failed:
                return False
            if not self._bounds_ok(state, k_left, suffix[start]):
                failed.add(key)
                return False
            for j in range(start, len(rest) - k_left + 1):
                k = rest[j]
                if self.cap_violation(state, k) is None and dfs(self.add(state, k), j + 1):
                    return True
            failed.add(key)
            return False

        return dfs(s, 0)


def run_greedy(
    problem: Problem,
    order: list[int],
    *,
    forced: list[int] | None = None,
) -> dict[str, Any]:
    """Lexicographic greedy with look-ahead over ``order`` (compiled indices of selectable items)."""
    state = problem.empty()
    picks: list[int] = []
    trace: dict[int, dict[str, Any]] = {}
    for k in forced or []:
        violation = problem.cap_violation(state, k)
        if violation:
            msg = f"forced model {problem.c.ids[k]} violates {violation}"
            raise SelectionInfeasibleError(msg, {"forced": violation})
        state = problem.add(state, k)
        picks.append(k)
        trace[k] = {"decision": "forced"}
    taken = set(picks)
    if not problem.feasible(state, [k for k in order if k not in taken]):
        return {"picks": picks, "state": state, "trace": trace, "complete": False}
    for pos, k in enumerate(order):
        if len(picks) == problem.cons.n:
            break
        if k in taken:
            continue
        violation = problem.cap_violation(state, k)
        if violation:
            trace[k] = {"decision": "blocked", "blocked_by": violation}
            continue
        rest = [x for x in order[pos + 1 :] if x not in taken]
        nxt = problem.add(state, k)
        if problem.feasible(nxt, rest):
            state = nxt
            picks.append(k)
            taken.add(k)
            trace[k] = {"decision": "picked"}
        else:
            trace[k] = {"decision": "blocked", "blocked_by": "lookahead", "state": state, "rest": rest}
    return {"picks": picks, "state": state, "trace": trace, "complete": len(picks) == problem.cons.n}


def lookahead_codes(problem: Problem, state: tuple, k: int, rest: list[int]) -> list[str]:
    """Quotas whose relaxation alone would have admitted ``k`` (re-runs the look-ahead per relaxation)."""
    codes = []
    for code in RELAXABLE:
        if not problem.cons.active(code):
            continue
        relaxed = Problem(problem.c, problem.cons.relax(code))
        if relaxed.cap_violation(state, k) is None and relaxed.feasible(relaxed.add(state, k), rest):
            codes.append(code)
    return codes or ["combined"]


# ── Sum-optimal diagnostic ─────────────────────────────────────────────────


def sum_optimal(
    problem: Problem,
    items: list[int],
    values: dict[int, float],
    *,
    forced: list[int] | None = None,
) -> dict[str, Any]:
    """Exact branch-and-bound: the feasible set maximising Σ MSS_tier (diagnostic only).

    ``forced`` picks are in every candidate set (as in the greedy), so the
    optimum is compared like for like.
    """
    forced = list(forced or [])
    start_state = problem.empty()
    start_value = 0.0
    for k in forced:
        if problem.cap_violation(start_state, k) is not None:
            return {"set": None, "sum_mss_tier": None, "exhaustive": True, "nodes": 0}
        start_state = problem.add(start_state, k)
        start_value += values.get(k, 0.0)
    order = sorted((k for k in items if k not in set(forced)), key=lambda k: (-values[k], problem.c.ids[k]))
    n = problem.cons.n
    by_tier: list[list[int]] = [[], [], []]
    for pos, k in enumerate(order):
        by_tier[problem.c.tier[k]].append(pos)
    # prefix sums of values per tier in global order
    prefix = [[0.0] for _ in range(3)]
    for t in range(3):
        for pos in by_tier[t]:
            prefix[t].append(prefix[t][-1] + values[order[pos]])
    # tier_pos[t][pos] = number of tier-t items before global position pos
    tier_pos = [[0] * (len(order) + 1) for _ in range(3)]
    for t in range(3):
        count = 0
        for pos in range(len(order) + 1):
            tier_pos[t][pos] = count
            if pos < len(order) and problem.c.tier[order[pos]] == t:
                count += 1
    suffix = problem._suffix(order)  # noqa: SLF001
    best: dict[str, Any] = {"value": -1.0, "set": None}
    nodes = 0
    exhausted = True

    def bound(state: tuple, pos: int) -> float | None:
        total = 0.0
        for t in range(3):
            need = problem.q[t] - state[1 + t]
            start = tier_pos[t][pos]
            if start + need > len(by_tier[t]):
                return None
            total += prefix[t][start + need] - prefix[t][start]
        return total

    def dfs(state: tuple, pos: int, value: float, chosen: list[int]) -> None:
        nonlocal nodes, exhausted
        nodes += 1
        if nodes > SUM_OPTIMAL_NODE_LIMIT:
            exhausted = False
            return
        k_left = n - state[0]
        if k_left == 0:
            if not problem.unmet(state) and value > best["value"] + 1e-12:
                best["value"], best["set"] = value, list(chosen)
            return
        if len(order) - pos < k_left or not problem._bounds_ok(state, k_left, suffix[pos]):  # noqa: SLF001
            return
        b = bound(state, pos)
        if b is None or value + b <= best["value"] + 1e-12:
            return
        k = order[pos]
        if problem.cap_violation(state, k) is None:
            chosen.append(k)
            dfs(problem.add(state, k), pos + 1, value + values[k], chosen)
            chosen.pop()
        dfs(state, pos + 1, value, chosen)

    dfs(start_state, 0, start_value, list(forced))
    return {
        "set": [problem.c.ids[k] for k in best["set"]] if best["set"] is not None else None,
        "sum_mss_tier": round(best["value"], 6) if best["set"] is not None else None,
        "exhaustive": exhausted,
        "nodes": nodes,
    }


# ── Diagnosis ──────────────────────────────────────────────────────────────


def _min_budget(problem: Problem, items: list[int], field_name: str, upper: float) -> float | None:
    """Smallest budget (USD, cent precision) that makes the set feasible, by bisection; None if none does."""
    lo, hi = 0, round(upper * 100) + 1
    test = Problem(problem.c, replace(problem.cons, **{field_name: hi / 100}))
    if not test.feasible(test.empty(), items):
        return None
    while lo < hi:
        mid = (lo + hi) // 2
        test = Problem(problem.c, replace(problem.cons, **{field_name: mid / 100}))
        if test.feasible(test.empty(), items):
            hi = mid
        else:
            lo = mid + 1
    return lo / 100


def diagnose_infeasible(problem: Problem, items: list[int]) -> dict[str, Any]:
    """Which single relaxation restores feasibility, and the minimal budgets that would."""
    relaxations = []
    for code in RELAXABLE:
        if not problem.cons.active(code):
            continue
        relaxed = Problem(problem.c, problem.cons.relax(code))
        relaxations.append({"constraint": code, "feasible_if_relaxed": relaxed.feasible(relaxed.empty(), items)})
    c = problem.c
    tier_supply = {TIERS[t]: sum(1 for k in items if c.tier[k] == t) for t in range(3)}
    out: dict[str, Any] = {
        "relaxations": relaxations,
        "selectable_per_tier": tier_supply,
        "quota_per_tier": dict(zip(TIERS, problem.q, strict=True)),
    }
    or_total = sum(c.cents[k] for k in items if c.route[k] == 1) / 100
    anth_total = sum(c.cents[k] for k in items if c.route[k] == 0) / 100
    if problem.cons.openrouter_budget is not None:
        out["min_openrouter_budget_effective_usd"] = _min_budget(problem, items, "openrouter_budget", or_total)
    if problem.cons.anthropic_budget is not None:
        out["min_anthropic_budget_effective_usd"] = _min_budget(problem, items, "anthropic_budget", anth_total)
    return out


def explain_constraints(problem: Problem, state: tuple, rerun) -> list[dict[str, Any]]:
    """Satisfied / binding per constraint; ``rerun(code)`` returns the picks with that constraint relaxed."""
    cons = problem.cons
    c = problem.c
    base = rerun(None)
    rows = []
    distinct = sum(1 for v in state[11] if v)
    vendor_counts = {c.vendor_names[i]: v for i, v in enumerate(state[11]) if v}
    values = {
        "Q-tier": (dict(zip(TIERS, state[1:4], strict=True)), dict(zip(TIERS, problem.q, strict=True))),
        "Q-vendor": (max(vendor_counts.values(), default=0), cons.vendor_max),
        "Q-vendors": (distinct, cons.vendors_min),
        "Q-open": (state[4], cons.open_min),
        "Q-closed": (state[5], cons.closed_min),
        "Q-claude": (state[6], cons.claude_min),
        "Q-origin-us": (state[7], cons.origin_us_min),
        "Q-origin-cn": (state[8], cons.origin_cn_min),
        "B-anthropic": (state[9] / 100, cons.anthropic_budget),
        "B-openrouter": (state[10] / 100, cons.openrouter_budget),
    }
    unmet = set(problem.unmet(state))
    for code, (value, limit) in values.items():
        kind = "cap" if code in ("Q-vendor", "B-anthropic", "B-openrouter") else "quota"
        if code == "Q-tier":
            satisfied = "Q-tier" not in unmet
            binding = None  # structural: the stratification itself
        else:
            if code in ("Q-vendor", "B-anthropic", "B-openrouter"):
                satisfied = limit is None or value <= limit + 1e-9
            else:
                satisfied = code not in unmet
            relaxed = rerun(code) if cons.active(code) else base
            binding = relaxed != base
        rows.append(
            {"code": code, "kind": kind, "value": value, "limit": limit, "satisfied": satisfied, "binding": binding},
        )
    for extra in ("Q-quality", "B-go"):
        relaxed = rerun(extra)
        rows.append(
            {
                "code": extra,
                "kind": "floor" if extra == "Q-quality" else "budget",
                "value": None,
                "limit": None,
                "satisfied": True,
                "binding": relaxed != base,
            },
        )
    return rows


def total_runs() -> int:
    return RUNS_PER_MODEL
