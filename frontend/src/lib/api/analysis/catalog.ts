/**
 * Tool catalog, analyzer image, tool self-test (canary) and reviewer options.
 *
 * Tools are baked into one pinned analyzer image; nothing is installed per
 * user. A tool's `version` is verified only when `version_source` is
 * `image_manifest`, and its `canary` is the latest self-test verdict on the
 * current image (`null`: no evidence, which is not a pass).
 */

import {
  getJson,
  query,
  segment,
  sendJson,
  type AnalysisRequestOptions,
} from "./shared";
import type {
  AnalysisRunListItem,
  AnalysisTool,
  ImageStatus,
  ReviewerModelCapabilities,
  ReviewerModelSearch,
  ReviewerOptions,
  SelftestLatest,
} from "./types";

/** Every catalog tool (static, metrics and the AI reviewer) in display order. */
export function listAnalysisTools(
  options: AnalysisRequestOptions = {},
): Promise<AnalysisTool[]> {
  return getJson("/tools/", options);
}

export function getAnalysisTool(
  slug: string,
  options: AnalysisRequestOptions = {},
): Promise<AnalysisTool> {
  return getJson(`/tools/${segment(slug)}/`, options);
}

/**
 * The analyzer image: tag, built?, manifest (tool versions, rule-pack
 * hashes), build state. Cached ~15 s server-side unless `refresh`.
 */
export function getAnalyzerImage(
  params: { refresh?: boolean } = {},
  options: AnalysisRequestOptions = {},
): Promise<ImageStatus> {
  return getJson(`/image/${query(params)}`, options);
}

/**
 * Build the image in the background. Answers the current status: already
 * built (200), or `building: true` (202); poll {@link getAnalyzerImage}.
 * Throws `{detail}` (503) when the analysis Docker daemon is unreachable.
 */
export function buildAnalyzerImage(): Promise<ImageStatus> {
  return sendJson("POST", "/image/build/");
}

/**
 * Run the canary fixtures through the real pipeline (default: every static
 * tool). Returns the queued self-test run; open it with `getAnalysisRun`.
 */
export function startSelftest(
  toolSlugs?: string[],
): Promise<AnalysisRunListItem> {
  return sendJson(
    "POST",
    "/selftest/",
    toolSlugs && toolSlugs.length > 0 ? { tool_slugs: toolSlugs } : undefined,
  );
}

/**
 * Latest finished self-test and the per-tool verdicts (default: on the
 * current image). Throws (503) when the canary expectations are missing.
 */
export function getLatestSelftest(
  params: { image_tag?: string; any_image?: boolean } = {},
  options: AnalysisRequestOptions = {},
): Promise<SelftestLatest> {
  return getJson(`/selftest/latest/${query(params)}`, options);
}

/** Engines, harnesses (option schemas), default model, limits, and whether a key is configured. */
export function getReviewerOptions(
  options: AnalysisRequestOptions = {},
): Promise<ReviewerOptions> {
  return getJson("/reviewer/options/", options);
}

/**
 * What the AI reviewer can ask of one model (catalog or a local model you
 * can see): structured outputs, tools, the reasoning efforts to offer,
 * context window, pricing. An unknown model is 200 with `in_catalog: false`
 * and `resolvable: false` (capabilities null = unknown, never false).
 */
export function getReviewerModelCapabilities(
  modelSlug: string,
  options: AnalysisRequestOptions = {},
): Promise<ReviewerModelCapabilities> {
  const path = modelSlug
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
  return getJson(`/reviewer/model-capabilities/${path}`, options);
}

/** Models the reviewer can use (OpenRouter catalog plus your visible local models), with capabilities. */
export function searchReviewerModels(
  params: { q?: string; limit?: number } = {},
  options: AnalysisRequestOptions = {},
): Promise<ReviewerModelSearch> {
  return getJson(`/reviewer/models/${query(params)}`, options);
}
