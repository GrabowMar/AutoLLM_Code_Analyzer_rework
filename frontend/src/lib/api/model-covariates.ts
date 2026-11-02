/**
 * Model covariates: external research data stored on a model (Epoch AI,
 * LMArena — `metadata.epoch` / `metadata.lmarena`, imported through a curated
 * name map) and the per-model covariate table (`GET /models/covariates/`).
 *
 * The readers below never trust the stored JSON: a missing or malformed key
 * becomes `null`, never 0.
 */
import { apiFetch } from "./core";

/** How the curated map matched our model to the external entry. */
export type ExternalMatch = "exact" | "variant" | "assumed";

export interface EpochCovariates {
  model: string;
  organization: string | null;
  publication_date: string | null;
  /** Parameters as Epoch reports them (not the checkpoint tensor count). */
  parameters: number | null;
  parameters_notes: string | null;
  /** Active parameters read from `parameters_notes` (only when unambiguous). */
  active_params_parsed: number | null;
  training_compute_flop: number | null;
  training_compute_notes: string | null;
  training_dataset_size: number | null;
  training_dataset_size_unit: string | null;
  model_accessibility: string | null;
  open_model_weights: string | null;
  /** Epoch's own confidence rating (Confident / Likely / Speculative / Unknown). */
  confidence: string | null;
  link: string | null;
  epoch_last_modified: string | null;
  source_url: string | null;
  source_page: string | null;
  license: string | null;
  fetched_at: string | null;
  match: ExternalMatch | null;
  mapping_note: string | null;
}

/** One LMArena leaderboard category; `metric` "rating" (Elo-style) or "score" (agent arena). */
export interface ArenaCategory {
  key: string;
  metric: "rating" | "score";
  arena_name: string | null;
  value: number | null;
  lower: number | null;
  upper: number | null;
  /** Votes (rating arenas) or sessions (agent arena). */
  count: number | null;
  rank: number | null;
  n_ranked: number | null;
  category_min: number | null;
  category_max: number | null;
  publish_date: string | null;
}

export interface LmarenaCovariates {
  categories: ArenaCategory[];
  names: Record<string, string>;
  publish_date: string | null;
  source_url: string | null;
  license: string | null;
  fetched_at: string | null;
  match: ExternalMatch | null;
  mapping_note: string | null;
}

/** Display order and labels of the stored categories. */
export const ARENA_CATEGORY_LABELS: Record<string, string> = {
  webdev: "WebDev",
  webdev_html: "WebDev · HTML",
  webdev_react: "WebDev · React",
  text_overall: "Text · overall",
  text_coding: "Text · coding",
  agent: "Agent arena",
};

function obj(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function match(value: unknown): ExternalMatch | null {
  return value === "exact" || value === "variant" || value === "assumed"
    ? value
    : null;
}

/** `metadata.epoch`, or null when the model has none. */
export function readEpoch(
  metadata: Record<string, unknown> | null | undefined,
): EpochCovariates | null {
  const e = obj(metadata?.epoch);
  const model = str(e?.model);
  if (!e || !model) return null;
  return {
    model,
    organization: str(e.organization),
    publication_date: str(e.publication_date),
    parameters: num(e.parameters),
    parameters_notes: str(e.parameters_notes),
    active_params_parsed: num(e.active_params_parsed),
    training_compute_flop: num(e.training_compute_flop),
    training_compute_notes: str(e.training_compute_notes),
    training_dataset_size: num(e.training_dataset_size),
    training_dataset_size_unit: str(e.training_dataset_size_unit),
    model_accessibility: str(e.model_accessibility),
    open_model_weights: str(e.open_model_weights),
    confidence: str(e.confidence),
    link: str(e.link),
    epoch_last_modified: str(e.epoch_last_modified),
    source_url: str(e.source_url),
    source_page: str(e.source_page),
    license: str(e.license),
    fetched_at: str(e.fetched_at),
    match: match(e.match),
    mapping_note: str(e.mapping_note),
  };
}

/** `metadata.lmarena`, categories in display order; null when there is none. */
export function readLmarena(
  metadata: Record<string, unknown> | null | undefined,
): LmarenaCovariates | null {
  const a = obj(metadata?.lmarena);
  const cats = obj(a?.categories);
  if (!a || !cats) return null;
  const order = Object.keys(ARENA_CATEGORY_LABELS);
  const keys = Object.keys(cats).sort(
    (x, y) =>
      (order.indexOf(x) === -1 ? 99 : order.indexOf(x)) -
      (order.indexOf(y) === -1 ? 99 : order.indexOf(y)),
  );
  const categories: ArenaCategory[] = [];
  for (const key of keys) {
    const c = obj(cats[key]);
    if (!c) continue;
    const isScore = c.metric === "score" || key === "agent";
    const value = num(isScore ? c.score : c.rating);
    if (value == null) continue;
    categories.push({
      key,
      metric: isScore ? "score" : "rating",
      arena_name: str(c.arena_name),
      value,
      lower: num(isScore ? c.score_lower : c.rating_lower),
      upper: num(isScore ? c.score_upper : c.rating_upper),
      count: num(isScore ? c.session_count : c.vote_count),
      rank: num(c.rank),
      n_ranked: num(c.n_ranked),
      category_min: num(c.category_min),
      category_max: num(c.category_max),
      publish_date: str(c.publish_date),
    });
  }
  if (categories.length === 0) return null;
  const names: Record<string, string> = {};
  for (const [config, name] of Object.entries(obj(a.names) ?? {})) {
    if (typeof name === "string" && name) names[config] = name;
  }
  return {
    categories,
    names,
    publish_date: str(a.publish_date),
    source_url: str(a.source_url),
    license: str(a.license),
    fetched_at: str(a.fetched_at),
    match: match(a.match),
    mapping_note: str(a.mapping_note),
  };
}

export interface ExternalView {
  epoch: EpochCovariates | null;
  arena: LmarenaCovariates | null;
  /** Shown from `metadata` (the catalog twin's) because the route has no record of its own. */
  epochInherited: boolean;
  arenaInherited: boolean;
}

/**
 * The records to show for a model: its own (`ownMetadata`) win; without one,
 * `metadata` (a local route's catalog twin) is used and flagged as inherited —
 * the twin's curated match then describes the twin, not this route.
 */
export function externalView(
  metadata: Record<string, unknown> | null | undefined,
  ownMetadata: Record<string, unknown> | null | undefined = null,
): ExternalView {
  const ownEpoch = readEpoch(ownMetadata);
  const ownArena = readLmarena(ownMetadata);
  const epoch = ownEpoch ?? readEpoch(metadata);
  const arena = ownArena ?? readLmarena(metadata);
  return {
    epoch,
    arena,
    epochInherited: ownMetadata != null && !ownEpoch && !!epoch,
    arenaInherited: ownMetadata != null && !ownArena && !!arena,
  };
}

/** The match to display: an inherited record's identity is only assumed. */
export function displayMatch(
  match: ExternalMatch | null,
  inherited: boolean,
): ExternalMatch | null {
  return inherited && match ? "assumed" : match;
}

const SUPERSCRIPT: Record<string, string> = {
  "-": "⁻",
  "0": "⁰",
  "1": "¹",
  "2": "²",
  "3": "³",
  "4": "⁴",
  "5": "⁵",
  "6": "⁶",
  "7": "⁷",
  "8": "⁸",
  "9": "⁹",
};

/** 4.32e24 → "4.32 × 10²⁴" ("—" when null). */
export function fmtScientific(
  n: number | null | undefined,
  digits = 3,
): string {
  if (n == null || !Number.isFinite(n)) return "—";
  if (n === 0) return "0";
  const [mantissa, exponent] = n.toExponential(digits - 1).split("e");
  const exp = Number(exponent);
  const m = String(Number(mantissa));
  if (exp === 0) return m;
  const sup = String(exp)
    .split("")
    .map((ch) => SUPERSCRIPT[ch] ?? ch)
    .join("");
  return `${m} × 10${sup}`;
}

/**
 * Position (0–100%) of `value` on the category range, for CI bars; null when
 * the range is unknown or flat.
 */
export function rangePercent(
  value: number | null,
  min: number | null,
  max: number | null,
): number | null {
  if (value == null || min == null || max == null || max <= min) return null;
  return Math.max(0, Math.min(100, ((value - min) / (max - min)) * 100));
}

// ── Covariate table (GET /models/covariates/) ───────────────────────────────

export type CovariateValue = string | number | boolean | null | string[];

export interface ModelCovariates {
  columns: string[];
  rows: Record<string, CovariateValue>[];
  n_models: number;
  used_only: boolean;
  generated_at: string;
  sources: { name: string; license: string | null; url: string }[];
  notes: string[];
}

/** One covariate row per visible model (`usedOnly`: models with a job of the caller). */
export async function getModelCovariates(
  usedOnly = true,
): Promise<ModelCovariates> {
  const res = await apiFetch(
    `/models/covariates/?format=json&used_only=${usedOnly ? "true" : "false"}`,
  );
  return res.json();
}

/** Same-origin CSV download URL (session cookie auth; formula-safe cells). */
export function getModelCovariatesCsvUrl(usedOnly = true): string {
  return `/api/models/covariates/?format=csv&used_only=${usedOnly ? "true" : "false"}`;
}
