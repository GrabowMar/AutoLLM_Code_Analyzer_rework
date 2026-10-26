/**
 * Target selection for studies: the experiments, batches, models, harnesses,
 * stacks, apps and modes a user can analyse, and a live resolution of a
 * target spec into candidate jobs with eligibility and sample totals.
 */

import { getJson, sendJson, type AnalysisRequestOptions } from "./shared";
import type {
  TargetOptions,
  TargetResolvePayload,
  TargetResolveResult,
} from "./types";

/** Your experiments, batches, models, harnesses, stacks, apps and modes with completed-job counts. */
export function getTargetOptions(
  options: AnalysisRequestOptions = {},
): Promise<TargetOptions> {
  return getJson("/targets/options/", options);
}

/**
 * Candidate jobs of a target spec (paginated; samples are resolved for the
 * requested page only). Invalid specs answer 422 with `target_spec.*`
 * field paths. Pass a `signal` to drop superseded live previews.
 */
export function resolveTargets(
  payload: TargetResolvePayload,
  options: AnalysisRequestOptions = {},
): Promise<TargetResolveResult> {
  return sendJson("POST", "/targets/resolve/", payload, options);
}
