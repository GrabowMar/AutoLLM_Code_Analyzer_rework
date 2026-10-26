/**
 * Analysis profiles: versioned tool + reviewer + scope + outcome configs.
 *
 * A content change creates version+1 under the same slug; name/description
 * edits change the version in place. Runs and studies pin one version row.
 * System profiles are read-only (clone them). Invalid configs answer 422
 * with `field_errors` keyed `config.<dotted path>` (`apiFieldErrors` gives
 * the same paths).
 */

import {
  getJson,
  query,
  sendJson,
  type AnalysisRequestOptions,
} from "./shared";
import type {
  AnalysisProfile,
  AnalysisProfileDetail,
  ProfileClonePayload,
  ProfileConfigInput,
  ProfileCreatePayload,
  ProfileDefaults,
  ProfileUpdatePayload,
  ProfileValidation,
} from "./types";

/** Latest version per slug: system profiles first, then yours. */
export function listAnalysisProfiles(
  params: { include_archived?: boolean; q?: string } = {},
  options: AnalysisRequestOptions = {},
): Promise<AnalysisProfile[]> {
  return getJson(`/profiles/${query(params)}`, options);
}

/** Any visible version (archived included) with its version history and usage. */
export function getAnalysisProfile(
  id: string,
  options: AnalysisRequestOptions = {},
): Promise<AnalysisProfileDetail> {
  return getJson(`/profiles/${id}/`, options);
}

/** Default config, tool catalog, reviewer options, scope/rules/outcome/design vocabularies. */
export function getProfileDefaults(
  options: AnalysisRequestOptions = {},
): Promise<ProfileDefaults> {
  return getJson("/profiles/defaults/", options);
}

/** Normalize a draft config; 200 with `ok: false` + `errors` when invalid. */
export function validateProfileConfig(
  config: ProfileConfigInput,
  options: AnalysisRequestOptions = {},
): Promise<ProfileValidation> {
  return sendJson("POST", "/profiles/validate/", { config }, options);
}

/** Version 1 of a new profile owned by you (409 when the slug is taken). */
export function createAnalysisProfile(
  payload: ProfileCreatePayload,
): Promise<AnalysisProfile> {
  return sendJson("POST", "/profiles/", payload);
}

/**
 * Save changes; a config change creates a new version (the response is that
 * version), a name/description change edits the latest one.
 */
export function updateAnalysisProfile(
  id: string,
  payload: ProfileUpdatePayload,
): Promise<AnalysisProfileDetail> {
  return sendJson("PUT", `/profiles/${id}/`, payload);
}

/** Archive every version of the slug (pinned runs and studies keep theirs). */
export function archiveAnalysisProfile(
  id: string,
): Promise<{ archived: boolean; slug: string; versions: number }> {
  return sendJson("DELETE", `/profiles/${id}/`);
}

/** A new profile of yours (version 1) with this version's config. */
export function cloneAnalysisProfile(
  id: string,
  payload: ProfileClonePayload = {},
): Promise<AnalysisProfile> {
  return sendJson("POST", `/profiles/${id}/clone/`, payload);
}
