/**
 * Slots client (spec 9.6, `slot/api.py`; operation ids `slots_*`), mounted at `/api/generation/`.
 *
 * Errors are `{detail, code, data?}`: `address_stale` and `in_use` are 409, invisible rows 404. Validation of a
 * new target runs on the eval lane after commit; the target comes back with `validation.status = "pending"`
 * and `getSlotTarget` re-checks it on every read.
 *
 * `generateParentApp` posts to `POST /studies/adhoc/` (9.4): the slot picker's "Generate a parent app" flow
 * resolves and launches a one-cell app study in one call.
 */
import { apiFetch } from "../core";
import { buildQuery, GENERATION_API, type RequestOptions } from "./samples";
import type {
  AdhocStudyIn,
  AdhocStudyOut,
  ComponentEntry,
  EndpointEntry,
  Page,
  ParentDrawOut,
  ParentSelection,
  SlotFileOut,
  SlotMatchOut,
  SlotParentRow,
  SlotParentsQuery,
  SlotRenderIn,
  SlotRenderOut,
  SlotSetDetailOut,
  SlotSetIn,
  SlotSetOut,
  SlotTargetCreateIn,
  SlotTargetDetailOut,
  SlotTargetIn,
  SlotTargetOut,
  SlotTargetPreviewOut,
  SlotTreeEntry,
  SlotVariantDiffOut,
  SlotVariantRow,
  SlotVariantsQuery,
  SymbolEntry,
  SymbolKind,
} from "./types";

async function getJson<T>(path: string, options?: RequestOptions): Promise<T> {
  const res = await apiFetch(`${GENERATION_API}${path}`, {
    signal: options?.signal,
  });
  return (await res.json()) as T;
}

async function sendJson<T>(
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
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const seg = encodeURIComponent;

// ── Parents ────────────────────────────────────────────────────────────────

export function listSlotParents(
  query: SlotParentsQuery = {},
  options?: RequestOptions,
): Promise<Page<SlotParentRow>> {
  return getJson(`/slots/parents/${buildQuery({ ...query })}`, options);
}

/** Dry draw (nothing is frozen): `{parents, seed, pool_size}`. */
export function drawSlotParents(
  selection: ParentSelection,
  options?: RequestOptions,
): Promise<ParentDrawOut> {
  return sendJson("POST", "/slots/parents/draw/", selection, options);
}

// ── Slot sets ──────────────────────────────────────────────────────────────

/** Freeze one parent (builds its symbol index when missing). */
export function createSlotSet(body: SlotSetIn): Promise<SlotSetOut> {
  return sendJson("POST", "/slots/sets/", body);
}

export function listSlotSets(
  query: {
    parent_run?: string;
    task?: string;
    page?: number;
    page_size?: number;
  } = {},
  options?: RequestOptions,
): Promise<Page<SlotSetOut>> {
  return getJson(`/slots/sets/${buildQuery({ ...query })}`, options);
}

export function getSlotSet(
  setId: string,
  options?: RequestOptions,
): Promise<SlotSetDetailOut> {
  return getJson(`/slots/sets/${seg(setId)}/`, options);
}

export function getSlotSetTree(
  setId: string,
  options?: RequestOptions,
): Promise<SlotTreeEntry[]> {
  return getJson(`/slots/sets/${seg(setId)}/tree/`, options);
}

export function listSlotSetSymbols(
  setId: string,
  query: { path?: string; kind?: SymbolKind } = {},
  options?: RequestOptions,
): Promise<SymbolEntry[]> {
  return getJson(
    `/slots/sets/${seg(setId)}/symbols/${buildQuery({ ...query })}`,
    options,
  );
}

export function listSlotSetEndpoints(
  setId: string,
  options?: RequestOptions,
): Promise<EndpointEntry[]> {
  return getJson(`/slots/sets/${seg(setId)}/endpoints/`, options);
}

export function listSlotSetComponents(
  setId: string,
  options?: RequestOptions,
): Promise<ComponentEntry[]> {
  return getJson(`/slots/sets/${seg(setId)}/components/`, options);
}

export function getSlotSetFile(
  setId: string,
  path: string,
  options?: RequestOptions,
): Promise<SlotFileOut> {
  return getJson(
    `/slots/sets/${seg(setId)}/file/${buildQuery({ path })}`,
    options,
  );
}

/** Hole, context, leak, linked checks, target hash and cost hint of a prospective target (writes nothing). */
export function previewSlotTarget(
  setId: string,
  body: SlotTargetIn,
  options?: RequestOptions,
): Promise<SlotTargetPreviewOut> {
  return sendJson(
    "POST",
    `/slots/sets/${seg(setId)}/preview-target/`,
    body,
    options,
  );
}

/** Save a target and queue its validation (the same part twice returns the existing target). */
export function createSlotTarget(
  setId: string,
  body: SlotTargetCreateIn,
): Promise<SlotTargetOut> {
  return sendJson("POST", `/slots/sets/${seg(setId)}/targets/`, body);
}

// ── Targets ────────────────────────────────────────────────────────────────

export function getSlotTarget(
  targetId: string,
  options?: RequestOptions,
): Promise<SlotTargetDetailOut> {
  return getJson(`/slots/targets/${seg(targetId)}/`, options);
}

/** The same part (same `match_key`) in other parents of the task. */
export function matchSlotTarget(
  targetId: string,
  slotSets: string[] | "same_task" = "same_task",
): Promise<SlotMatchOut> {
  return sendJson("POST", `/slots/targets/${seg(targetId)}/match/`, {
    slot_sets: slotSets,
  });
}

export function revalidateSlotTarget(targetId: string): Promise<SlotTargetOut> {
  return sendJson("POST", `/slots/targets/${seg(targetId)}/revalidate/`, {});
}

/** 204, or 409 `in_use` when a study or variants use the target. */
export function deleteSlotTarget(targetId: string): Promise<void> {
  return sendJson("DELETE", `/slots/targets/${seg(targetId)}/`, undefined);
}

export function renderSlotTarget(
  targetId: string,
  body: SlotRenderIn,
  options?: RequestOptions,
): Promise<SlotRenderOut> {
  return sendJson(
    "POST",
    `/slots/targets/${seg(targetId)}/render/`,
    body,
    options,
  );
}

export function listSlotVariants(
  targetId: string,
  query: SlotVariantsQuery = {},
  options?: RequestOptions,
): Promise<Page<SlotVariantRow>> {
  return getJson(
    `/slots/targets/${seg(targetId)}/variants/${buildQuery({ ...query })}`,
    options,
  );
}

export function getSlotVariantDiff(
  runId: string,
  against: "original" | string = "original",
  options?: RequestOptions,
): Promise<SlotVariantDiffOut> {
  return getJson(
    `/slots/variants/${seg(runId)}/diff/${buildQuery({ against })}`,
    options,
  );
}

// ── Generate a parent app (9.4 adhoc) ──────────────────────────────────────

export function generateParentApp(body: AdhocStudyIn): Promise<AdhocStudyOut> {
  return sendJson("POST", "/studies/adhoc/", { launch: true, ...body });
}
