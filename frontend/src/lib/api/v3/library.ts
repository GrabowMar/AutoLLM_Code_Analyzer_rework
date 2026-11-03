/**
 * Library (spec 9.1) and prompts (9.2) client, mounted at `/api/generation/`.
 *
 * Every helper takes the URL kind (`tasks`, `prompt-sets`, …; see `LIBRARY_API_KINDS`) and an optional
 * `{signal}` so a superseded request (a debounced validate or render) can be aborted. Errors are the
 * `apiFetch` throw: the `ErrorOut` body (`{detail, code, issues?}`), readable with `formatApiError` and
 * `apiErrorStatus` from `$lib/api/core`.
 *
 * `getBudgetKnobs` / `explainPolicy` read the 9.8 budget endpoints the Budget preset editor needs; WP-15's
 * `budget.ts` owns the rest of 9.8 and may re-export these.
 */
import { apiFetch } from "../core";
import type {
  ForkIn,
  KnobsOut,
  LibraryApiKind,
  LibraryCreateIn,
  LibraryDetailOut,
  LibraryDiffOut,
  LibraryItemOut,
  LibraryKind,
  LibraryListQuery,
  LibraryVersionIn,
  PackageImportOut,
  Page,
  PolicyExplainOut,
  PromptDiffOut,
  PromptMode,
  PromptVariable,
  RenderIn,
  RenderOut,
  StarterTreeOut,
  TaskOracleEntry,
  UsageOut,
  ValidateOut,
} from "./types";
import { LIBRARY_API_KINDS, LIBRARY_KIND_API } from "./types";

export const GENERATION_API = "/generation";

export interface RequestOptions {
  signal?: AbortSignal;
}

/** `"task"` → `"tasks"`; URL kinds pass through. */
export function apiKind(kind: LibraryKind | LibraryApiKind): LibraryApiKind {
  return kind in LIBRARY_API_KINDS
    ? (kind as LibraryApiKind)
    : LIBRARY_KIND_API[kind as LibraryKind];
}

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
    body: JSON.stringify(body),
    signal: options?.signal,
  });
  return (await res.json()) as T;
}

function query(
  params: Record<string, string | number | boolean | null | undefined>,
): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (
      value === undefined ||
      value === null ||
      value === "" ||
      value === false
    )
      continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

const seg = encodeURIComponent;

// ── 9.1 Library ───────────────────────────────────────────────────────────

export function listLibrary<K extends LibraryKind>(
  kind: K | LibraryApiKind,
  params: LibraryListQuery = {},
  options?: RequestOptions,
): Promise<Page<LibraryItemOut<K>>> {
  return getJson(`/library/${apiKind(kind)}/${query({ ...params })}`, options);
}

/** Every latest-per-slug row of a kind (follows pages of 200). */
export async function listAllLibrary<K extends LibraryKind>(
  kind: K | LibraryApiKind,
  params: Omit<LibraryListQuery, "page" | "page_size"> = {},
  options?: RequestOptions,
): Promise<LibraryItemOut<K>[]> {
  const items: LibraryItemOut<K>[] = [];
  for (let page = 1; page < 1000; page++) {
    const body = await listLibrary<K>(
      kind,
      { ...params, page, page_size: 200 },
      options,
    );
    items.push(...body.items);
    if (items.length >= body.total || body.items.length === 0) break;
  }
  return items;
}

export function getLibraryDetail<K extends LibraryKind>(
  kind: K | LibraryApiKind,
  slug: string,
  options?: RequestOptions,
): Promise<LibraryDetailOut<K>> {
  return getJson(`/library/${apiKind(kind)}/${seg(slug)}/`, options);
}

export function getLibraryVersion<K extends LibraryKind>(
  kind: K | LibraryApiKind,
  slug: string,
  version: number,
  options?: RequestOptions,
): Promise<LibraryItemOut<K>> {
  return getJson(
    `/library/${apiKind(kind)}/${seg(slug)}/versions/${version}/`,
    options,
  );
}

/** v1 of a user-owned entity (409 `slug_exists`). */
export function createLibraryItem<K extends LibraryKind>(
  kind: K | LibraryApiKind,
  body: LibraryCreateIn<K>,
): Promise<LibraryItemOut<K>> {
  return sendJson("POST", `/library/${apiKind(kind)}/`, body);
}

/**
 * Version+1 from `base_version` (409 `stale_base` when someone saved first; the result has
 * `unchanged: true` and the base's version when the content hashes equal to the base).
 */
export function createLibraryVersion<K extends LibraryKind>(
  kind: K | LibraryApiKind,
  slug: string,
  body: LibraryVersionIn<K>,
): Promise<LibraryItemOut<K>> {
  return sendJson(
    "POST",
    `/library/${apiKind(kind)}/${seg(slug)}/versions/`,
    body,
  );
}

/** Archive or restore every version of the slug (or one `version`); versions are never deleted. */
export function archiveLibraryItem<K extends LibraryKind>(
  kind: K | LibraryApiKind,
  slug: string,
  archived: boolean,
  version?: number,
): Promise<LibraryItemOut<K>> {
  return sendJson("POST", `/library/${apiKind(kind)}/${seg(slug)}/archive/`, {
    archived,
    version: version ?? null,
  });
}

export function setLibraryVisibility<K extends LibraryKind>(
  kind: K | LibraryApiKind,
  slug: string,
  visibility: "private" | "shared",
): Promise<LibraryItemOut<K>> {
  return sendJson(
    "POST",
    `/library/${apiKind(kind)}/${seg(slug)}/visibility/`,
    { visibility },
  );
}

/** Lint + normalize draft content without saving (`{ok, issues, content_hash, content, derived?}`). */
export function validateLibraryContent(
  kind: LibraryKind | LibraryApiKind,
  content: unknown,
  options?: RequestOptions & { system?: boolean },
): Promise<ValidateOut> {
  return sendJson(
    "POST",
    `/library/${apiKind(kind)}/validate/`,
    { content, system: options?.system ?? false },
    options,
  );
}

export function getLibraryUsage(
  kind: LibraryKind | LibraryApiKind,
  slug: string,
  options?: RequestOptions,
): Promise<UsageOut> {
  return getJson(`/library/${apiKind(kind)}/${seg(slug)}/usage/`, options);
}

export function getLibraryDiff(
  kind: LibraryKind | LibraryApiKind,
  slug: string,
  a: number,
  b: number,
  options?: RequestOptions,
): Promise<LibraryDiffOut> {
  return getJson(
    `/library/${apiKind(kind)}/${seg(slug)}/diff/${query({ a, b })}`,
    options,
  );
}

/** Copy a visible version as v1 of a new user-owned slug (`derived_from` set). */
export function forkLibraryItem<K extends LibraryKind>(
  kind: K | LibraryApiKind,
  slug: string,
  body: ForkIn,
): Promise<LibraryItemOut<K>> {
  return sendJson("POST", `/library/${apiKind(kind)}/${seg(slug)}/fork/`, body);
}

export function getTaskOracle(
  slug: string,
  version: number,
  options?: RequestOptions,
): Promise<Record<string, TaskOracleEntry>> {
  return getJson(
    `/library/tasks/${seg(slug)}/versions/${version}/oracle/`,
    options,
  );
}

/** Staff: queue oracle validation runs of a task version on the given stack refs. */
export function queueTaskOracle(
  slug: string,
  version: number,
  stacks: string[],
): Promise<{ queued_runs: string[] }> {
  return sendJson(
    "POST",
    `/library/tasks/${seg(slug)}/versions/${version}/oracle/`,
    { stacks },
  );
}

export function getStarterTree(
  slug: string,
  version: number,
  options?: RequestOptions,
): Promise<StarterTreeOut> {
  return getJson(
    `/library/starters/${seg(slug)}/versions/${version}/tree/`,
    options,
  );
}

/** Raw text of a blob referenced by a visible starter. */
export async function getLibraryBlob(
  sha: string,
  options?: RequestOptions,
): Promise<string> {
  const res = await apiFetch(`${GENERATION_API}/library/blobs/${seg(sha)}/`, {
    signal: options?.signal,
  });
  return res.text();
}

/** A v4 package (YAML text) of `kind:slug@v` refs and, by default, their dependencies. */
export async function exportLibraryPackage(
  refs: string[],
  includeDependencies = true,
): Promise<Blob> {
  const res = await apiFetch(`${GENERATION_API}/library/packages/export/`, {
    method: "POST",
    body: JSON.stringify({ refs, include_dependencies: includeDependencies }),
  });
  return res.blob();
}

/**
 * Import a v4 package. `dry_run` changes nothing and reports what would be created / reused / conflict;
 * a refused apply throws a 409 body that also carries `{created, reused, conflicts}`.
 */
export async function importLibraryPackage(
  yamlText: string,
  mode: "dry_run" | "apply",
  onConflict: "rename" | "error" = "rename",
): Promise<PackageImportOut> {
  const res = await apiFetch(
    `${GENERATION_API}/library/packages/import/${query({ mode, on_conflict: onConflict })}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/x-yaml" },
      body: yamlText,
    },
  );
  return (await res.json()) as PackageImportOut;
}

// ── 9.2 Prompts ───────────────────────────────────────────────────────────

export function getPromptVariables(
  mode?: PromptMode,
  options?: RequestOptions,
): Promise<PromptVariable[]> {
  return getJson(`/prompts/variables/${query({ mode })}`, options);
}

/** Rendered stimuli with source spans, `prompt_hash` and issues (422 with issues when a template fails). */
export function renderPrompts(
  body: RenderIn,
  options?: RequestOptions,
): Promise<RenderOut> {
  return sendJson("POST", "/prompts/render/", body, options);
}

export function diffPrompts(
  a: RenderIn,
  b: RenderIn,
  options?: RequestOptions,
): Promise<PromptDiffOut> {
  return sendJson("POST", "/prompts/diff/", { a, b }, options);
}

// ── 9.8 (read-only parts used by the Budget preset editor) ─────────────────

/** `KNOB_META` rows, the key limits and the three tier presets. */
export function getBudgetKnobs(options?: RequestOptions): Promise<KnobsOut> {
  return getJson("/budget/knobs/", options);
}

/** Effective values, clamps, notes, issues, treatment view and `generation_policy_hash` of a policy ref. */
export function explainPolicy(
  ref: string,
  params: {
    model?: string;
    harness?: string;
    study?: string;
    executor?: string;
  } = {},
  options?: RequestOptions,
): Promise<PolicyExplainOut> {
  return getJson(
    `/budget/policies/${seg(ref)}/explain/${query(params)}`,
    options,
  );
}
