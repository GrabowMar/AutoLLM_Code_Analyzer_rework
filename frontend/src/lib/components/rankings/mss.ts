/**
 * MSS v2 presentation helpers (pure, unit-tested): component and tier labels,
 * the rankings page's sort keys → backend `SORT_KEY_MAP`, number formats,
 * readable reasons for exclusions / blocked picks / constraints, and the
 * confirmation body. Missing data is `null` everywhere and is shown as
 * "no data", never as 0.
 */
import type { Tone } from "$lib/constants/colors";
import {
  MSS_TIER_WEIGHTS,
  MSS_WEIGHTS,
  PRICE_TIERS,
  type ConstraintStatus,
  type MssComponent,
  type PriceTier,
  type RankingRow,
  type RouteKind,
  type SelectionCandidate,
  type SelectionOverride,
  type SelectionPayload,
  type SelectionPick,
  type SelectionSensitivity,
} from "$lib/api/rankings";

export const NO_DATA = "no data";

// ── Components ──────────────────────────────────────────────────────────────

export interface ComponentMeta {
  key: MssComponent;
  label: string;
  /** One letter for dense tables (P, C, Q, R as in the spec). */
  letter: string;
  field: keyof RankingRow & `${MssComponent}_score`;
  /** Source and transform, for header tooltips and the method tab. */
  help: string;
}

export const COMPONENTS: ComponentMeta[] = [
  {
    key: "popularity",
    label: "Popularity",
    letter: "P",
    field: "popularity_score",
    help: "OpenRouter usage over 28 days (Data API rankings-daily, CC BY 4.0): geometric mean of token share and spend share, exposure-adjusted, days off the top-50 imputed as half that day's smallest listed volume. log10 share, 0.01% = 0 and 10% = 1.",
  },
  {
    key: "price",
    label: "Price",
    letter: "C",
    field: "price_score",
    help: "Cost of one reference OpenCode run (0.9M prompt tokens, 36k output, 80% cache hits) at first-party list prices incl. cache read/write. log10 USD, $0.01 = 1 and $10 = 0: cheaper scores higher.",
  },
  {
    key: "capability",
    label: "Capability",
    letter: "Q",
    field: "capability_score",
    help: "LMArena WebDev rating (CC BY 4.0) at the reasoning effort we call, linear: 1450 = 0 and 1850 = 1.",
  },
  {
    key: "recency",
    label: "Recency",
    letter: "R",
    field: "recency_score",
    help: "Days since the pinned version's release, linear: today = 1, 365 days = 0.",
  },
];

export function componentMeta(key: MssComponent): ComponentMeta {
  return COMPONENTS.find((c) => c.key === key) ?? COMPONENTS[0];
}

/** "40%" from 0.4. */
export function weightLabel(w: number | null | undefined): string {
  return w == null ? "—" : `${Math.round(w * 100)}%`;
}

/** The weights a payload used, else the nominal ones. */
export function payloadWeights(payload: SelectionPayload | null | undefined): {
  global: Record<MssComponent, number>;
  tier: Record<MssComponent, number>;
} {
  return {
    global: payload?.params?.weights ?? MSS_WEIGHTS,
    tier: payload?.params?.tier_weights ?? MSS_TIER_WEIGHTS,
  };
}

// ── Sorting (rankings page → backend SORT_KEY_MAP) ──────────────────────────

export type RankingSortKey =
  | "mss"
  | "composite"
  | "empirical"
  | "popularity"
  | "price"
  | "run_cost"
  | "capability"
  | "recency"
  | "coverage"
  | "context"
  | "name"
  | "apps";

/**
 * The backend sorts `price` by the run cost, so the Price *score* column sorts
 * with the direction flipped (a high price score is a low cost).
 */
export const SORT_KEYS: Record<
  RankingSortKey,
  { backend: string; invert?: boolean; label: string }
> = {
  mss: { backend: "mss", label: "MSS" },
  composite: { backend: "composite", label: "Composite" },
  empirical: { backend: "empirical", label: "Empirical" },
  popularity: { backend: "popularity", label: "Popularity" },
  price: { backend: "price", invert: true, label: "Price score" },
  run_cost: { backend: "price", label: "$/run" },
  capability: { backend: "capability", label: "Capability" },
  recency: { backend: "recency", label: "Recency" },
  coverage: { backend: "coverage", label: "Coverage" },
  context: { backend: "context", label: "Context" },
  name: { backend: "name", label: "Name" },
  apps: { backend: "apps", label: "Apps" },
};

export function backendSort(
  key: RankingSortKey,
  dir: "asc" | "desc",
): { sort_by: string; sort_dir: "asc" | "desc" } {
  const spec = SORT_KEYS[key] ?? SORT_KEYS.mss;
  const flipped = dir === "asc" ? "desc" : "asc";
  return { sort_by: spec.backend, sort_dir: spec.invert ? flipped : dir };
}

/** The natural first direction of a column: cheapest first for $/run, A–Z for names, else highest first. */
export function firstSortDir(key: RankingSortKey): "asc" | "desc" {
  return key === "run_cost" || key === "name" ? "asc" : "desc";
}

export function isRankingSortKey(
  v: string | null | undefined,
): v is RankingSortKey {
  return !!v && Object.prototype.hasOwnProperty.call(SORT_KEYS, v);
}

// ── Formatting ──────────────────────────────────────────────────────────────

/** A 0–1 score as 0–100 with one decimal; null stays null (callers print "no data"). */
export function fmtScore(v: number | null | undefined): string | null {
  return v == null ? null : (v * 100).toFixed(1);
}

export function fmtUsd(v: number | null | undefined, digits?: number): string {
  if (v == null) return "—";
  const d = digits ?? (Math.abs(v) < 1 ? 3 : 2);
  return `$${v.toFixed(d)}`;
}

/** Small market shares: 0.01737 → "1.74%", 0.00089 → "0.089%". */
export function fmtShare(v: number | null | undefined): string {
  if (v == null) return "—";
  const p = v * 100;
  if (p === 0) return "0%";
  if (p >= 10) return `${p.toFixed(1)}%`;
  if (p >= 0.1) return `${p.toFixed(2)}%`;
  return `${p.toPrecision(2)}%`;
}

/** A fraction as a whole percent: 0.483 → "48%". */
export function fmtPct(v: number | null | undefined, digits = 0): string {
  return v == null ? "—" : `${(v * 100).toFixed(digits)}%`;
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  return v == null ? "—" : v.toFixed(digits);
}

/** "2026-11-09T09:22:19+00:00" → "2026-11-09"; lists of dates are joined. */
export function fmtDate(v: string | string[] | null | undefined): string {
  if (v == null || v === "") return "—";
  if (Array.isArray(v)) {
    const days = [...new Set(v.map((d) => String(d).slice(0, 10)))].sort();
    if (days.length === 0) return "—";
    return days.length === 1
      ? days[0]
      : `${days[0]} – ${days[days.length - 1]}`;
  }
  return String(v).slice(0, 10);
}

// ── Tiers, routes, origin ───────────────────────────────────────────────────

export const TIER_META: Record<PriceTier, { label: string; order: number }> = {
  frontier: { label: "Frontier", order: 0 },
  mid: { label: "Mid", order: 1 },
  budget: { label: "Budget", order: 2 },
};

export function tierLabel(t: string | null | undefined): string {
  return t && t in TIER_META ? TIER_META[t as PriceTier].label : "—";
}

/** "$0.12 – $0.60 / run" for a tier band. */
export function tierBand(
  tiers:
    | {
        bands_usd?: Partial<Record<PriceTier, [number, number]>>;
        counts?: Partial<Record<PriceTier, number>>;
      }
    | null
    | undefined,
  tier: PriceTier,
): string {
  const band = tiers?.bands_usd?.[tier];
  // An empty tier (e.g. an empty frame before the importers ran) has no meaningful band.
  if (!band || tiers?.counts?.[tier] === 0) return "";
  const [lo, hi] = band;
  if (tier === "budget") return `≤ ${fmtUsd(hi)}/run`;
  if (tier === "frontier") return `> ${fmtUsd(lo)}/run`;
  return `${fmtUsd(lo)} – ${fmtUsd(hi)}/run`;
}

export const ROUTE_LABELS: Record<RouteKind, string> = {
  go: "OpenCode Go",
  anthropic: "Anthropic key",
  openrouter: "OpenRouter",
};

export function routeLabel(r: string | null | undefined): string {
  if (!r) return "no route";
  return ROUTE_LABELS[r as RouteKind] ?? r;
}

export function weightsLabel(open: boolean | null | undefined): string {
  return open == null ? "—" : open ? "open" : "closed";
}

// ── Exclusions, flags, blocked reasons ──────────────────────────────────────

const EXCLUSION_LABELS: Record<string, string> = {
  not_in_frame: "Not a canonical model",
  alias: "Alias",
  batch_api: "Batch API copy",
  router: "Router",
  unlinked_local: "Local row without a catalog twin",
  merged_into: "Prerelease of",
  no_route: "No study route",
  no_paid_route: "No paid route",
  no_tool_calling: "No tool calling",
  context_too_small: "Context < 128k",
  not_text_chat: "No text in/out",
  text_io: "No text in/out",
  deprecated: "Deprecated",
  free_only: "Free-only",
  prerelease: "Preview",
  too_old: "Older than 12 months",
  no_release_date: "No release date",
  superseded: "Superseded",
  superseded_by: "Superseded by",
  curated_exclude: "Curated exclusion",
  insufficient_evidence: "No popularity or price data",
};

/** "superseded_by:z-ai/glm-5.3" → { label: "Superseded by", target: "z-ai/glm-5.3" }. */
export function exclusionLabel(code: string | null | undefined): {
  label: string;
  target: string | null;
} {
  if (!code) return { label: "Excluded", target: null };
  const i = code.indexOf(":");
  const base = i >= 0 ? code.slice(0, i) : code;
  const target = i >= 0 ? code.slice(i + 1) : null;
  return { label: EXCLUSION_LABELS[base] ?? base.replace(/_/g, " "), target };
}

export const FLAG_LABELS: Record<string, { label: string; help: string }> = {
  censored: {
    label: "censored",
    help: "Some days off OpenRouter's top-50 list were imputed (left-censored).",
  },
  short_exposure: {
    label: "short exposure",
    help: "Listed for fewer days than the window; shares are over the days it was available.",
  },
  lmarena_proxy: {
    label: "LMArena proxy",
    help: "Popularity proxied from LMArena interactions (no OpenRouter usage).",
  },
  capability_missing: {
    label: "no capability",
    help: "No LMArena WebDev rating: capability is left out and the weights renormalised.",
  },
  capability_missing_admitted: {
    label: "admitted without capability",
    help: "No capability evidence, admitted past the quality floor because its popularity is in the eligible frame's top quartile.",
  },
  go_limit_exceeded: {
    label: "over Go limit",
    help: "Its 30 runs exceed the OpenCode Go monthly limit for this model.",
  },
  go_limit_unknown: {
    label: "Go limit unknown",
    help: "No published OpenCode Go monthly limit for this model.",
  },
};

export function flagLabel(flag: string): { label: string; help: string } {
  return FLAG_LABELS[flag] ?? { label: flag.replace(/_/g, " "), help: "" };
}

export const CONSTRAINT_LABELS: Record<
  string,
  { label: string; help: string }
> = {
  "Q-tier": {
    label: "Tier quotas",
    help: "Exactly this many models per price tier (frontier / mid / budget).",
  },
  "Q-vendor": {
    label: "Per vendor",
    help: "At most this many models from one vendor.",
  },
  "Q-vendors": {
    label: "Vendors",
    help: "At least this many distinct vendors.",
  },
  "Q-open": {
    label: "Open weights",
    help: "At least this many open-weight models.",
  },
  "Q-closed": {
    label: "Closed weights",
    help: "At least this many closed-weight models.",
  },
  "Q-claude": {
    label: "Claude",
    help: "At least this many Claude models on the Anthropic key.",
  },
  "Q-origin-us": {
    label: "US origin",
    help: "At least this many US-origin models (0 = off; the mix is reported).",
  },
  "Q-origin-cn": {
    label: "China origin",
    help: "At least this many China-origin models (0 = off; the mix is reported).",
  },
  "B-anthropic": {
    label: "Anthropic budget",
    help: "Planned cost of the Claude picks within the Anthropic balance after the contingency.",
  },
  "B-openrouter": {
    label: "OpenRouter budget",
    help: "Planned cost of the OpenRouter-routed picks within the OpenRouter credit after the contingency.",
  },
  "B-go": {
    label: "Go monthly limits",
    help: "Each Go-routed pick's 30 runs fit its OpenCode Go monthly limit after the contingency.",
  },
  "Q-quality": {
    label: "Quality floor",
    help: "Capability at or above the tier median (missing capability only with top-quartile popularity).",
  },
};

export function constraintLabel(code: string): { label: string; help: string } {
  return CONSTRAINT_LABELS[code] ?? { label: code, help: "" };
}

/** Value and limit of a constraint as one short string ("$63.23 / $85.00", "7 ≥ 6", "3/3/4"). */
export function constraintFigure(c: ConstraintStatus): string {
  const fmt = (v: unknown): string => {
    if (v == null) return "—";
    if (typeof v === "number")
      return c.code.startsWith("B-") ? fmtUsd(v, 2) : String(v);
    if (typeof v === "object") {
      const o = v as Record<string, unknown>;
      if (PRICE_TIERS.every((t) => t in o))
        return PRICE_TIERS.map((t) => String(o[t])).join("/");
      return JSON.stringify(v);
    }
    return String(v);
  };
  if (c.value == null && c.limit == null)
    return c.satisfied ? "met" : "not met";
  if (c.kind === "cap" || c.code.startsWith("B-"))
    return `${fmt(c.value)} ≤ ${fmt(c.limit)}`;
  if (c.code === "Q-tier") return fmt(c.value);
  return `${fmt(c.value)} ≥ ${fmt(c.limit)}`;
}

export interface BlockedReason {
  kind: "floor" | "budget" | "cap" | "lookahead" | "route" | "other";
  label: string;
  detail: string;
}

/**
 * `floor:Q-quality(capability 0.392 < 0.407)` → Quality floor · capability 0.392 < 0.407;
 * `budget:B-openrouter` → OpenRouter budget; `cap:Q-vendor(xiaomi)` → Per vendor · xiaomi;
 * `lookahead:Q-open,Q-claude` → Leaves quotas unmeetable · Open weights, Claude.
 */
export function describeBlocked(
  code: string | null | undefined,
): BlockedReason {
  if (!code) return { kind: "other", label: "—", detail: "" };
  if (code === "no_paid_route")
    return { kind: "route", label: "No paid route", detail: "" };
  const colon = code.indexOf(":");
  const prefix = colon >= 0 ? code.slice(0, colon) : "";
  const rest = colon >= 0 ? code.slice(colon + 1) : code;
  const m = rest.match(/^([^(]+)(?:\((.*)\))?$/);
  const inner = (m?.[1] ?? rest).trim();
  const paren = (m?.[2] ?? "").trim();
  switch (prefix) {
    case "floor":
      return {
        kind: "floor",
        label: constraintLabel(inner).label,
        detail: paren,
      };
    case "budget":
      return {
        kind: "budget",
        label: constraintLabel(inner).label,
        detail: paren,
      };
    case "cap":
      return {
        kind: "cap",
        label: constraintLabel(inner).label,
        detail: paren,
      };
    case "lookahead":
      return {
        kind: "lookahead",
        label: "Leaves quotas unmeetable",
        detail: rest
          .split(",")
          .map((c) => constraintLabel(c.trim()).label)
          .join(", "),
      };
    default:
      return { kind: "other", label: code.replace(/_/g, " "), detail: "" };
  }
}

/** `rank` / `forced` / `quota:Q-open,Q-claude` → a readable admission reason. */
export function admittedLabel(code: string | null | undefined): string {
  if (!code || code === "rank") return "by rank";
  if (code === "forced") return "forced";
  if (code.startsWith("quota:")) {
    return `quota: ${code
      .slice(6)
      .split(",")
      .map((c) => constraintLabel(c.trim()).label)
      .join(", ")}`;
  }
  return code;
}

// ── Robustness ──────────────────────────────────────────────────────────────

export function robustnessTone(cls: string | null | undefined): Tone {
  switch (cls) {
    case "robust":
      return "success";
    case "borderline":
      return "warning";
    case "fragile":
      return "danger";
    case "contender":
      return "info";
    default:
      return "neutral";
  }
}

export interface FrequencyTableRow {
  canonical_id: string;
  picked: boolean;
  cls: string | null;
  s1: number | null;
  s2: number | null;
  s3: number | null;
}

/** One row per model seen in S1/S2/S3, S1's class; picks first, then by S1 frequency. */
export function frequencyTable(
  s: SelectionSensitivity | null | undefined,
): FrequencyTableRow[] {
  if (!s) return [];
  const rows = new Map<string, FrequencyTableRow>();
  const runs = [
    ["s1", s.S1],
    ["s2", s.S2],
    ["s3", s.S3],
  ] as const;
  for (const [key, run] of runs) {
    for (const f of run?.frequencies ?? []) {
      const row = rows.get(f.canonical_id) ?? {
        canonical_id: f.canonical_id,
        picked: f.picked,
        cls: null,
        s1: null,
        s2: null,
        s3: null,
      };
      row[key] = f.frequency;
      if (key === "s1") row.cls = f.class;
      row.picked = row.picked || f.picked;
      rows.set(f.canonical_id, row);
    }
  }
  return [...rows.values()].sort(
    (a, b) =>
      Number(b.picked) - Number(a.picked) ||
      (b.s1 ?? -1) - (a.s1 ?? -1) ||
      a.canonical_id.localeCompare(b.canonical_id),
  );
}

// ── Picks and candidates ────────────────────────────────────────────────────

export function picksByTier(
  picks: SelectionPick[] | null | undefined,
): { tier: PriceTier; picks: SelectionPick[] }[] {
  return PRICE_TIERS.map((tier) => ({
    tier,
    picks: (picks ?? [])
      .filter((p) => p.tier === tier)
      .sort((a, b) => a.tier_rank - b.tier_rank),
  }));
}

export function candidateIndex(
  payload: SelectionPayload | null | undefined,
): Map<string, SelectionCandidate> {
  return new Map((payload?.candidates ?? []).map((c) => [c.canonical_id, c]));
}

/** "Anthropic: Claude Opus 5.5" → "Claude Opus 5.5"; falls back to the id's last segment. */
export function shortName(
  id: string,
  candidates?: Map<string, SelectionCandidate>,
): string {
  const name = candidates?.get(id)?.display_name;
  if (name) {
    const i = name.indexOf(": ");
    return i > 0 && i <= 40 ? name.slice(i + 2) : name;
  }
  return id.includes("/") ? id.slice(id.indexOf("/") + 1) : id;
}

/** The model page id of a canonical model (its anchor row when the selection knows it). */
export function modelPageId(
  id: string,
  candidates?: Map<string, SelectionCandidate>,
): string {
  return candidates?.get(id)?.anchor_model_id ?? id;
}

/** Share of a route budget used, 0–1 (null without an effective budget). */
export function budgetFraction(
  planned: number | null | undefined,
  effective: number | null | undefined,
): number | null {
  if (planned == null || effective == null || effective <= 0) return null;
  return planned / effective;
}

// ── Preview controls ────────────────────────────────────────────────────────

/** Tier quotas for N in the A1 proportions (3/3/4 at N = 10): frontier and mid 30% each, the rest budget. */
export function defaultQuotas(n: number): Record<PriceTier, number> {
  const total = Math.max(0, Math.floor(n));
  const frontier = Math.round(total * 0.3);
  const mid = Math.round(total * 0.3);
  return { frontier, mid, budget: Math.max(0, total - frontier - mid) };
}

/** null when valid, else why not (the backend refuses quotas that don't sum to N). */
export function quotaProblem(
  n: number | null,
  q: Record<PriceTier, number | null>,
): string | null {
  if (n == null || !Number.isFinite(n) || n < 1) return "N must be at least 1.";
  for (const t of PRICE_TIERS) {
    const v = q[t];
    if (v == null || !Number.isInteger(v) || v < 0)
      return `${tierLabel(t)} quota must be a whole number ≥ 0.`;
  }
  const sum = PRICE_TIERS.reduce((s, t) => s + (q[t] ?? 0), 0);
  if (sum !== n) return `Tier quotas sum to ${sum}, not N = ${n}.`;
  return null;
}

// ── Confirmation ────────────────────────────────────────────────────────────

export interface SwapChoice {
  /** The model that takes the pick's place; empty = keep the pick. */
  with: string;
  reason: string;
}

/**
 * The confirm body from the nominal picks and the swaps the researcher chose.
 * Problems (a swap without a reason, a model listed twice) come back as messages;
 * the backend checks the same rules.
 */
export function confirmBody(
  nominal: string[],
  swaps: Record<string, SwapChoice>,
): { final_ids: string[]; overrides: SelectionOverride[]; problems: string[] } {
  const final_ids: string[] = [];
  const overrides: SelectionOverride[] = [];
  const problems: string[] = [];
  for (const id of nominal) {
    const swap = swaps[id];
    if (swap?.with && swap.with !== id) {
      final_ids.push(swap.with);
      const reason = swap.reason.trim();
      if (!reason) problems.push(`Give a reason for replacing ${id}.`);
      overrides.push({ replace: id, with: swap.with, reason });
    } else {
      final_ids.push(id);
    }
  }
  const seen = new Set<string>();
  for (const id of final_ids) {
    if (seen.has(id)) problems.push(`${id} is in the final list twice.`);
    seen.add(id);
  }
  return { final_ids, overrides, problems };
}
