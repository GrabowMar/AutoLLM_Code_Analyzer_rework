/**
 * Typed client of the analysis API v2 (`/api/analysis/`).
 *
 * Import from `$lib/api/analysis` (this index) or from a domain module
 * (`$lib/api/analysis/runs`). The client is deliberately not re-exported
 * from the `client.ts` barrel: its generic names (`Page`, `Finding`, ...)
 * would clash with other domains' types.
 */

export * from "./types";
export {
  ANALYSIS_URL,
  downloadAnalysisFile,
  query as analysisQuery,
  type AnalysisRequestOptions,
} from "./shared";
export * from "./catalog";
export * from "./plans";
export * from "./profiles";
export * from "./runs";
export * from "./studies";
export * from "./targets";
