/**
 * Harnesses and config client (spec 9.3), mounted at `/api/generation/`.
 *
 * `/harnesses/` lists every adapter, including disabled ones (`enabled=false` with a `blocked_reason`):
 * show them, never hide them. `/config/` carries the vocabularies and limits the Study builder and the
 * Library editors share (factor keys, auth modes, prose variants, eval levels, knob classes, …).
 */
import { apiFetch } from "../core";
import type { RequestOptions } from "./library";
import { GENERATION_API } from "./library";
import type { GenerationConfigOut, HarnessFactsOut, HarnessOut } from "./types";

export function listHarnesses(options?: RequestOptions): Promise<HarnessOut[]> {
  return apiFetch(`${GENERATION_API}/harnesses/`, {
    signal: options?.signal,
  }).then((res) => res.json());
}

export function getHarnessFacts(
  name: string,
  options?: RequestOptions,
): Promise<HarnessFactsOut> {
  return apiFetch(
    `${GENERATION_API}/harnesses/${encodeURIComponent(name)}/facts/`,
    {
      signal: options?.signal,
    },
  ).then((res) => res.json());
}

let configPromise: Promise<GenerationConfigOut> | null = null;

/** `/config/`, cached for the page's lifetime (pass `fresh` after a stack change). */
export function getGenerationConfig(
  fresh = false,
): Promise<GenerationConfigOut> {
  if (fresh || !configPromise) {
    configPromise = apiFetch(`${GENERATION_API}/config/`)
      .then((res) => res.json() as Promise<GenerationConfigOut>)
      .catch((e) => {
        configPromise = null;
        throw e;
      });
  }
  return configPromise;
}

/**
 * Registry summaries by issue code (`SummaryIssues`/`PlanRail` `summaries` prop). Empty without a config,
 * so callers fall back to their built-in titles.
 */
export function issueSummaries(
  config: Partial<Pick<GenerationConfigOut, "issue_codes">> | null | undefined,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const row of config?.issue_codes ?? []) out[row.code] = row.summary;
  return out;
}
