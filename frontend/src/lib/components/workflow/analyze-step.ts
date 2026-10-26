/**
 * Analyze-step config of an automation pipeline (`dispatch_analyze`):
 *
 * - `profile`: analysis profile slug (latest version) or a version id (pinned).
 * - `tool_slugs`: restrict the profile (or, without one, the research
 *   default) to these tools; `ai_review` keeps the AI reviewer on.
 * - `generation_job_id`: a job id or `{{steps.<step>.output.generation_job_id}}`;
 *   empty uses the pipeline's `generation_job_id` parameter.
 * - `thresholds`: max counted static findings per severity (or `total`).
 * - `timeout_seconds`, `fail_on_partial`, `fail_on_threshold`.
 *
 * Saved pipelines may still hold v1 keys (`analyzers`, `profile_id`,
 * `profile_name`, `generation_job`, `live_target`); {@link migrateAnalyzeConfig}
 * rewrites them.
 */

export const AI_REVIEW_SLUG = "ai_review";

/** v1 tool names the backend still maps (`_LEGACY_TOOL_ALIASES`). */
const LEGACY_TOOL_ALIASES: Record<string, string> = {
  llm_review: AI_REVIEW_SLUG,
  detect_secrets: "detect-secrets",
};

export const THRESHOLD_KEYS = [
  "critical",
  "high",
  "medium",
  "low",
  "info",
  "total",
] as const;
export type ThresholdKey = (typeof THRESHOLD_KEYS)[number];

/** Backend `ANALYZE_DEFAULT_TIMEOUT_SECONDS` / `ANALYZE_MAX_TIMEOUT_SECONDS`. */
export const ANALYZE_DEFAULT_TIMEOUT_SECONDS = 4 * 3600;
export const ANALYZE_MAX_TIMEOUT_SECONDS = 10 * 3600;

export const LEGACY_KEYS = [
  "analyzers",
  "profile_id",
  "profile_name",
  "generation_job",
  "live_target",
] as const;

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_RE.test(value);
}

export function isTemplate(value: unknown): boolean {
  return typeof value === "string" && value.includes("{{");
}

/** The profile reference (slug or version id) the backend will use, or "". */
export function profileRef(config: Record<string, unknown>): string {
  for (const key of ["profile", "profile_id", "profile_slug"]) {
    const value = config[key];
    if (value !== undefined && value !== null && value !== "") {
      return String(value);
    }
  }
  return "";
}

/** The tool restriction, or null when the step runs the profile's tools. */
export function toolRestriction(
  config: Record<string, unknown>,
): string[] | null {
  const key =
    "tool_slugs" in config
      ? "tool_slugs"
      : "analyzers" in config
        ? "analyzers"
        : null;
  if (!key) return null;
  const raw = config[key];
  if (raw === null || raw === undefined) return null;
  const list = typeof raw === "string" ? raw.split(",") : raw;
  if (!Array.isArray(list)) return [];
  const slugs: string[] = [];
  for (const item of list) {
    if (typeof item !== "string") continue;
    const slug = LEGACY_TOOL_ALIASES[item.trim()] ?? item.trim();
    if (slug && !slugs.includes(slug)) slugs.push(slug);
  }
  return slugs;
}

export function thresholdsOf(
  config: Record<string, unknown>,
): Partial<Record<ThresholdKey, number>> {
  const raw = config.thresholds;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const out: Partial<Record<ThresholdKey, number>> = {};
  for (const key of THRESHOLD_KEYS) {
    const value = (raw as Record<string, unknown>)[key];
    if (typeof value === "number") out[key] = value;
  }
  return out;
}

export function hasLegacyKeys(config: Record<string, unknown>): boolean {
  return LEGACY_KEYS.some((key) => key in config);
}

/**
 * v1 keys rewritten to the v2 vocabulary: `analyzers` → `tool_slugs`
 * (aliases applied), `profile_id` → `profile`, `generation_job` →
 * `generation_job_id`; `live_target` and `profile_name` are dropped (live
 * scanning no longer exists; the name is display-only).
 */
export function migrateAnalyzeConfig(
  config: Record<string, unknown>,
): Record<string, unknown> {
  const next: Record<string, unknown> = { ...config };
  if ("analyzers" in next) {
    if (!("tool_slugs" in next))
      next.tool_slugs = toolRestriction(config) ?? [];
    delete next.analyzers;
  }
  if ("profile_id" in next) {
    if (!profileRef({ profile: next.profile }) && next.profile_id) {
      next.profile = String(next.profile_id);
    }
    delete next.profile_id;
  }
  if ("generation_job" in next) {
    if (!next.generation_job_id && next.generation_job) {
      next.generation_job_id = next.generation_job;
    }
    delete next.generation_job;
  }
  delete next.live_target;
  delete next.profile_name;
  return next;
}
