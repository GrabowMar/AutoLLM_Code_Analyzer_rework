/**
 * The experiment page's research summary, derived from the detail and the
 * `GET /results/` statistics: the design as factor counts, the short
 * identifier, and the "key findings" bullets of the Abstract (leader, effects
 * against the baseline, caveats). Pure functions — the Svelte side only
 * renders the parts.
 */
import type {
  ExperimentDetail,
  ExperimentResults,
  LeaderboardRow,
  PairedRow,
} from "$lib/api/types/experiments";
import {
  CORRECTION_LABEL,
  MIN_APPS_FOR_CI,
  conditionName,
  formatDeltaPp,
  formatP,
  formatPercent,
  plural,
} from "../format";

/** Default significance level when the results carry no analysis-plan alpha. */
export const ALPHA = 0.05;

/** A finding's text: plain strings with emphasised (bold) parts. */
export type FindingPart = string | { strong: string };

export interface Finding {
  kind: "leader" | "effect" | "null" | "interim" | "caveat";
  parts: FindingPart[];
}

/** The design as factor counts: conditions = models × variants (+ ablations). */
export interface DesignShape {
  conditions: number;
  models: number;
  /** Harness presets (agent) or prompt profiles (scaffolding) across the crossed conditions. */
  variants: number;
  /** Conditions with parameter overrides (ablations). */
  ablations: number;
  apps: number;
  repeats: number;
  cells: number;
}

type DesignInput = Pick<
  ExperimentDetail,
  "conditions" | "app_requirement_ids" | "repeats" | "total_cells"
>;

function hasOverrides(params: object | null | undefined): boolean {
  return !!params && Object.keys(params).length > 0;
}

/** The variant a condition runs: its harness preset / harness, or its profile. */
export function variantKey(c: DesignInput["conditions"][number]): string {
  if (c.harness_preset != null) return `preset:${c.harness_preset}`;
  if (c.harness) return `harness:${c.harness}`;
  if (c.profile != null) return `profile:${c.profile}`;
  return "default";
}

export function designShape(e: DesignInput): DesignShape {
  const crossed = e.conditions.filter((c) => !hasOverrides(c.param_overrides));
  const apps = e.app_requirement_ids.length;
  const conditions = e.conditions.length;
  return {
    conditions,
    models: new Set(e.conditions.map((c) => c.model)).size,
    variants: new Set((crossed.length ? crossed : e.conditions).map(variantKey))
      .size,
    ablations: conditions - crossed.length,
    apps,
    repeats: e.repeats,
    cells: Math.max(e.total_cells ?? 0, conditions * apps * e.repeats),
  };
}

/** "EXP-955A5BD5": the first block of the UUID, as a short accession number. */
export function shortExperimentId(id: string): string {
  return `EXP-${id.split("-")[0].toUpperCase()}`;
}

function ciText(row: LeaderboardRow): string {
  const ci = row.n_apps >= MIN_APPS_FOR_CI ? row.pass_rate_ci95 : null;
  if (ci) return ` (95% CI ${formatPercent(ci[0])}–${formatPercent(ci[1])})`;
  return ` (${plural(row.n_apps, "app")} × ${plural(row.repeats_per_app, "repeat")}, no CI)`;
}

function deltaCiText(row: PairedRow): string {
  const ci = row.n_pairs >= MIN_APPS_FOR_CI ? row.ci95 : null;
  return ci
    ? `95% CI ${formatDeltaPp(ci[0])} to ${formatDeltaPp(ci[1])}`
    : `${plural(row.n_pairs, "paired app")}`;
}

function missingTotal(row: LeaderboardRow): number {
  const m = row.n_missing;
  return (
    m.failed +
    m.ungraded +
    m.excluded_invalid +
    m.excluded_truncated +
    m.not_common
  );
}

/** Highest (and, with ≥ 3 conditions, lowest) mean pass rate. */
function leaderFindings(rows: LeaderboardRow[]): Finding[] {
  const scored = rows
    .filter((r) => r.pass_rate_mean != null)
    .sort((a, b) => a.rank - b.rank);
  if (scored.length === 0) return [];
  const top = scored[0];
  const best = top.pass_rate_mean as number;
  if (scored.length === 1) {
    return [
      {
        kind: "leader",
        parts: [
          { strong: top.label },
          ": mean pass rate ",
          { strong: formatPercent(best) },
          `${ciText(top)}.`,
        ],
      },
    ];
  }
  const tied = scored.filter(
    (r) => Math.abs((r.pass_rate_mean as number) - best) < 1e-9,
  );
  const parts: FindingPart[] =
    tied.length > 1
      ? [
          `${tied.length} conditions share the highest mean pass rate, `,
          { strong: formatPercent(best) },
          `: ${tied.map((r) => r.label).join(", ")}.`,
        ]
      : [
          "Highest mean pass rate: ",
          { strong: top.label },
          ", ",
          { strong: formatPercent(best) },
          `${ciText(top)}.`,
        ];
  const last = scored[scored.length - 1];
  if (scored.length >= 3 && tied.length < scored.length) {
    parts.push(
      ` Lowest: ${last.label}, ${formatPercent(last.pass_rate_mean)}.`,
    );
  }
  return [{ kind: "leader", parts }];
}

/** Differences against the baseline (paired by app, adjusted as the plan says, else Holm). */
function effectFindings(
  results: ExperimentResults,
  labelOf: (id: number) => string,
): Finding[] {
  const baselineId = results.paired.baseline_condition_id;
  const rows = results.paired.rows.filter(
    (r) => r.condition_id !== baselineId && r.mean_delta != null,
  );
  if (baselineId == null || rows.length === 0) return [];
  const baseline = labelOf(baselineId);
  const alpha = results.paired.alpha ?? ALPHA;
  const correction =
    CORRECTION_LABEL[results.paired.correction] ??
    results.paired.correction ??
    "Holm";
  const pOf = (r: PairedRow) => r.p_adjusted ?? r.p_holm;
  const largest = [...rows].sort(
    (a, b) =>
      Math.abs(b.mean_delta as number) - Math.abs(a.mean_delta as number),
  )[0];
  const largestParts = (prefix: string, withP: boolean): FindingPart[] => [
    prefix,
    { strong: labelOf(largest.condition_id) },
    " ",
    { strong: formatDeltaPp(largest.mean_delta) },
    ` (${deltaCiText(largest)}${withP && pOf(largest) != null ? `, p = ${formatP(pOf(largest))}` : ""}).`,
  ];

  if (results.interim) {
    return [
      {
        kind: "interim",
        parts: [
          "Interim: conditions are at unequal progress, so tests against ",
          { strong: baseline },
          " are withheld. ",
          ...largestParts("Largest observed difference so far: ", false),
        ],
      },
    ];
  }
  if (rows.every((r) => pOf(r) == null)) {
    return [
      {
        kind: "null",
        parts: [
          "Too few apps graded alongside ",
          { strong: baseline },
          " to test differences. ",
          ...largestParts("Largest observed difference: ", false),
        ],
      },
    ];
  }
  const significant = rows.filter(
    (r) => r.significant ?? (pOf(r) != null && (pOf(r) as number) < alpha),
  );
  if (significant.length === 0) {
    return [
      {
        kind: "null",
        parts: [
          "No condition differs from the baseline ",
          { strong: baseline },
          ` at α = ${alpha} (${correction}, ${results.paired.status ?? "exploratory"}). `,
          ...largestParts("Largest observed difference: ", true),
        ],
      },
    ];
  }
  const strongest = [...significant].sort(
    (a, b) =>
      Math.abs(b.mean_delta as number) - Math.abs(a.mean_delta as number),
  )[0];
  return [
    {
      kind: "effect",
      parts: [
        `${significant.length} of ${plural(rows.length, "condition")} differ from the baseline `,
        { strong: baseline },
        ` (${correction}-adjusted p < ${alpha}, ${results.paired.status ?? "exploratory"}). Largest: `,
        { strong: labelOf(strongest.condition_id) },
        " ",
        { strong: formatDeltaPp(strongest.mean_delta) },
        ` (${deltaCiText(strongest)}, p = ${formatP(pOf(strongest))}).`,
      ],
    },
  ];
}

/** What limits the estimates: missing grades, mixed fingerprints, too few apps. */
function caveatFindings(results: ExperimentResults): Finding[] {
  const rows = results.leaderboard;
  const out: Finding[] = [];
  const cells = rows.reduce((sum, r) => sum + r.n_cells, 0);
  const missing = rows.reduce((sum, r) => sum + missingTotal(r), 0);
  if (missing > 0) {
    out.push({
      kind: "caveat",
      parts: [
        { strong: `${missing} of ${plural(cells, "cell")}` },
        " have no usable grade (failed, ungraded, excluded or outside the common cells) and are left out of the estimates.",
      ],
    });
  }
  const mixed = rows.filter((r) => r.mixed_fingerprints).length;
  if (mixed > 0) {
    out.push({
      kind: "caveat",
      parts: [
        { strong: plural(mixed, "condition") },
        ` ${mixed === 1 ? "mixes" : "mix"} configuration fingerprints: a model or harness changed while it ran.`,
      ],
    });
  }
  const maxApps = Math.max(0, ...rows.map((r) => r.n_apps));
  if (rows.length > 0 && maxApps < MIN_APPS_FOR_CI) {
    out.push({
      kind: "caveat",
      parts: [
        `With ${plural(maxApps, "app")} per condition, confidence intervals are not shown (they need at least ${MIN_APPS_FOR_CI} apps).`,
      ],
    });
  }
  if (results.baseline_source === "post_hoc") {
    out.push({
      kind: "caveat",
      parts: [
        "The baseline was chosen after the fact: comparisons are exploratory.",
      ],
    });
  }
  return out;
}

/**
 * Key findings for the Abstract: the leader, the effects against the
 * baseline, then caveats. Empty while nothing is graded.
 */
export function keyFindings(
  results: ExperimentResults,
  labelOf: (id: number) => string,
): Finding[] {
  if (!results.leaderboard.some((r) => r.n_graded > 0)) return [];
  return [
    ...leaderFindings(results.leaderboard),
    ...effectFindings(results, labelOf),
    ...caveatFindings(results),
  ];
}

/** Condition id → display name (the detail's labels). */
export function conditionLabels(
  e: Pick<ExperimentDetail, "conditions">,
): Map<number, string> {
  return new Map(e.conditions.map((c) => [c.id, conditionName(c)]));
}
