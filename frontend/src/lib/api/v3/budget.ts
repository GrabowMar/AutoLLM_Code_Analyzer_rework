/**
 * Budget client (spec 9.8, live spend contract 4.7), mounted at `/api/generation/`.
 *
 * `getBudgetKnobs` / `explainPolicy` live in `./library` (the Budget preset editor needs them) and are
 * re-exported here. Spend is always shown billed and at list price side by side; a missing value is
 * `null` ("not measured"), never 0.
 */
import { apiFetch } from "../core";
import { GENERATION_API, type RequestOptions } from "./library";
import type {
  BudgetHeaderOut,
  BudgetMeOut,
  CostPriorOut,
  Estimate,
  EstimateIn,
  Page,
  SpendCapIn,
  SpendCapOut,
  SpendCapPatch,
  SpendLedgerEntryOut,
  StudyBudgetOut,
} from "./types";

export { explainPolicy, getBudgetKnobs } from "./library";

const seg = encodeURIComponent;

async function getJson<T>(path: string, options?: RequestOptions): Promise<T> {
  const res = await apiFetch(`${GENERATION_API}${path}`, {
    signal: options?.signal,
  });
  return (await res.json()) as T;
}

async function send<T>(
  method: string,
  path: string,
  body: unknown,
  options?: RequestOptions,
): Promise<T> {
  const res = await apiFetch(`${GENERATION_API}${path}`, {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: options?.signal,
  });
  return (await res.json()) as T;
}

/** `budget_estimate`: `{design}` or `{study, plan_hash}`. */
export function estimateBudget(
  body: EstimateIn,
  options?: RequestOptions,
): Promise<Estimate> {
  return send("POST", "/budget/estimate/", body, options);
}

/** `budget_study`: the live spend payload (spent / reserved / cap, burn rate, ETA, per condition and model). */
export function getStudyBudget(
  studyId: string,
  options?: RequestOptions,
): Promise<StudyBudgetOut> {
  return getJson(`/budget/studies/${seg(studyId)}/`, options);
}

/** `budget_study_ledger`. */
export function getStudyLedger(
  studyId: string,
  params: {
    run?: string;
    kind?: string;
    page?: number;
    page_size?: number;
  } = {},
  options?: RequestOptions,
): Promise<Page<SpendLedgerEntryOut>> {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "")
      search.set(key, String(value));
  }
  const text = search.toString();
  return getJson(
    `/budget/studies/${seg(studyId)}/ledger/${text ? `?${text}` : ""}`,
    options,
  );
}

/** `budget_me`: period totals (billed and list) against the user's caps, and per-study spend. */
export function getMyBudget(options?: RequestOptions): Promise<BudgetMeOut> {
  return getJson("/budget/me/", options);
}

/** `budget_header`: the compact payload of the header spend chip. */
export function getBudgetHeader(
  options?: RequestOptions,
): Promise<BudgetHeaderOut> {
  return getJson("/budget/header/", options);
}

/** `budget_caps_list`. */
export function listSpendCaps(
  options?: RequestOptions,
): Promise<SpendCapOut[]> {
  return getJson("/budget/caps/", options);
}

/** `budget_caps_create` (users: own user caps only, at most a staff cap). */
export function createSpendCap(
  body: SpendCapIn,
  options?: RequestOptions,
): Promise<SpendCapOut> {
  return send("POST", "/budget/caps/", body, options);
}

/** `budget_caps_update`. */
export function updateSpendCap(
  id: number,
  body: SpendCapPatch,
  options?: RequestOptions,
): Promise<SpendCapOut> {
  return send("PATCH", `/budget/caps/${id}/`, body, options);
}

/** `budget_caps_delete`. */
export function deleteSpendCap(
  id: number,
  options?: RequestOptions,
): Promise<{ deleted: number }> {
  return send("DELETE", `/budget/caps/${id}/`, undefined, options);
}

/** `budget_priors`. */
export function listCostPriors(
  params: { harness?: string; model?: string } = {},
  options?: RequestOptions,
): Promise<CostPriorOut[]> {
  const search = new URLSearchParams();
  if (params.harness) search.set("harness", params.harness);
  if (params.model) search.set("model", params.model);
  const text = search.toString();
  return getJson(`/budget/priors/${text ? `?${text}` : ""}`, options);
}
