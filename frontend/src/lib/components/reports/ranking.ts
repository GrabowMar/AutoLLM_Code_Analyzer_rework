/**
 * What-if ranking: the study report's TOPSIS ranking recomputed in the browser with the
 * reader's weights. A port of `backend/statistics/services/mcdm.py` (`topsis_scores`,
 * `_ranks`): vector normalisation, ideal / anti-ideal per criterion direction, competition
 * ranks (1-2-2-4). With the report's own weights it reproduces the report's scores.
 */

export interface Criterion {
  key: string;
  label: string;
  direction: "benefit" | "cost";
  weight: number;
}

export interface RankingRow {
  model: string;
  topsis_rank?: number | null;
  topsis_score?: number | null;
  [key: string]: unknown;
}

export interface RankingData {
  ranking: RankingRow[];
  criteria: Criterion[];
}

/** The study report's ranking block, or null when the report has none (or no criteria). */
export function rankingData(
  reportData: Record<string, unknown> | null | undefined,
): RankingData | null {
  const r = reportData?.ranking as Partial<RankingData> | undefined;
  if (
    !r ||
    !Array.isArray(r.ranking) ||
    !Array.isArray(r.criteria) ||
    r.ranking.length === 0 ||
    r.criteria.length === 0
  )
    return null;
  return { ranking: r.ranking, criteria: r.criteria };
}

/**
 * Readable names of the ranking criteria the study report computes, for criteria whose
 * label is only their key (a report's own `ranking_criteria` may omit labels). The
 * final-assessment keys (`*_itt`, `list_cost_per_app`) say what they count.
 */
export const CRITERION_LABELS: Record<string, string> = {
  deploy_rate: "Deploy rate | evaluated",
  deploy_rate_itt: "Deploy rate, failures counted as 0",
  pass_rate: "Pass rate | evaluated",
  pass_rate_itt: "Pass rate, failures counted as 0",
  functional_passed: "Fully correct | evaluated",
  functional_passed_itt: "Fully correct, failures counted as 0",
  unique_issues_per_app: "Unique issues / app",
  issues_per_kloc: "Code issues / kLOC",
  security_per_app: "Security issues / app",
  cost_per_app: "Cost / app billed (USD)",
  list_cost_per_app: "Cost / app at list price (USD)",
};

/** A criterion's label: the report's own, else a known key's name, else the key. */
export function criterionLabel(c: Pick<Criterion, "key" | "label">): string {
  if (c.label && c.label !== c.key) return c.label;
  return CRITERION_LABELS[c.key] ?? c.key;
}

/**
 * One sentence on what the criteria count, when they include the final-assessment ones
 * (whole-cohort rates, cost at list price); null for every other ranking (older reports too).
 */
export function criteriaBasis(
  criteria: Pick<Criterion, "key">[],
): string | null {
  const keys = new Set(criteria.map((c) => c.key));
  const parts: string[] = [];
  if ([...keys].some((k) => k.endsWith("_itt")))
    parts.push(
      "rates count known output, build and start failures as 0 over every usable app",
    );
  if (keys.has("list_cost_per_app"))
    parts.push(
      "cost is every run's tokens at the model's public list prices (billed cost is not comparable across routes)",
    );
  if (parts.length === 0) return null;
  const text = parts.join("; ");
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}.`;
}

const TIE_TOLERANCE = 1e-12;

/** Weights normalised to sum 1 (all zero: equal weights). */
export function normalise(weights: number[]): number[] {
  const total = weights.reduce((a, w) => a + Math.max(0, w), 0);
  if (total <= 0) return weights.map(() => 1 / weights.length);
  return weights.map((w) => Math.max(0, w) / total);
}

/** TOPSIS closeness per model; models missing a criterion value are left out (and listed). */
export function topsis(
  rows: RankingRow[],
  criteria: Criterion[],
  weights: number[],
): { scores: Map<string, number>; dropped: string[] } {
  const w = normalise(weights);
  const complete: [string, number[]][] = [];
  const dropped: string[] = [];
  for (const r of rows) {
    const vec = criteria.map((c) => r[c.key]);
    if (vec.some((v) => typeof v !== "number" || Number.isNaN(v)))
      dropped.push(r.model);
    else complete.push([r.model, vec as number[]]);
  }
  const scores = new Map<string, number>();
  if (complete.length === 0) return { scores, dropped };
  const norms = criteria.map(
    (_, j) => Math.sqrt(complete.reduce((a, [, v]) => a + v[j] ** 2, 0)) || 1,
  );
  const weighted = complete.map(
    ([m, v]) =>
      [m, v.map((x, j) => (w[j] * x) / norms[j])] as [string, number[]],
  );
  const ideal: number[] = [];
  const anti: number[] = [];
  criteria.forEach((c, j) => {
    const col = weighted.map(([, v]) => v[j]);
    const hi = Math.max(...col);
    const lo = Math.min(...col);
    ideal.push(c.direction === "benefit" ? hi : lo);
    anti.push(c.direction === "benefit" ? lo : hi);
  });
  for (const [m, v] of weighted) {
    const dPlus = Math.sqrt(v.reduce((a, x, j) => a + (x - ideal[j]) ** 2, 0));
    const dMinus = Math.sqrt(v.reduce((a, x, j) => a + (x - anti[j]) ** 2, 0));
    scores.set(m, dPlus + dMinus ? dMinus / (dPlus + dMinus) : 0.5);
  }
  return { scores, dropped };
}

/** Competition ranks (1-2-2-4), best score first; names break display ties stably. */
export function competitionRanks(
  scores: Map<string, number>,
): Map<string, number> {
  const order = [...scores.keys()].sort(
    (a, b) => scores.get(b)! - scores.get(a)! || a.localeCompare(b),
  );
  const out = new Map<string, number>();
  let groupScore: number | null = null;
  let groupRank = 0;
  order.forEach((m, i) => {
    const s = scores.get(m)!;
    if (groupScore === null || Math.abs(s - groupScore) > TIE_TOLERANCE) {
      groupScore = s;
      groupRank = i + 1;
    }
    out.set(m, groupRank);
  });
  return out;
}
