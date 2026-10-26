/**
 * Frozen analysis plans in use (`GET /plans/`): which plans an experiment's,
 * batch's or study's jobs were analysed under and how much of the set each
 * covers. Equal `plan_hash` means comparable measurements.
 */

import { getJson, query, type AnalysisRequestOptions } from "./shared";
import type { PlanUsageList, RunPurpose } from "./types";

export interface PlanListParams {
  experiment_id?: string;
  batch_id?: string;
  study_id?: string;
  /** Default: every purpose but canary. */
  purpose?: RunPurpose | RunPurpose[];
}

/** Plans of your runs (staff: all), newest first, at most 100. */
export function listAnalysisPlans(
  params: PlanListParams = {},
  options: AnalysisRequestOptions = {},
): Promise<PlanUsageList> {
  return getJson(`/plans/${query(params)}`, options);
}
