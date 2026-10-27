/**
 * App spec editing: a form draft ⇄ the API payload, and the checks of
 * `backend/generation/data/requirements/schema.json` (errors block a save;
 * warnings say what the conformance suite cannot grade).
 *
 * Schema v3 lists any number of data models (`data_models`) and gives each
 * endpoint `auth`, `query` and `success_status`. Legacy v1/v2 specs (one
 * `data_model` object) still load and save in their own shape. The
 * conventions `data_lint.spec_convention_issues` adds for v3 are mirrored
 * where they need no prose parsing; the server reports the rest on save.
 */
import type {
  AppRequirementTemplate,
  AppSpecDataModel,
  AppSpecDifficulty,
} from "$lib/api/generation";

export const HTTP_METHODS = [
  "GET",
  "POST",
  "PUT",
  "PATCH",
  "DELETE",
  "WS",
] as const;
export type HttpMethod = (typeof HTTP_METHODS)[number];

export const ENDPOINT_AUTH = ["public", "user", "admin"] as const;
export type EndpointAuth = (typeof ENDPOINT_AUTH)[number];

export const DIFFICULTIES: AppSpecDifficulty[] = [
  "basic",
  "standard",
  "advanced",
];

/** The current spec schema version (`schema.json`); new specs start on it. */
export const SPEC_SCHEMA_VERSION = 3;
export const SPEC_SCHEMA_VERSIONS = [1, 2, 3] as const;

const SLUG_RE = /^[a-z][a-z0-9]*(_[a-z0-9]+)*$/;
const PARAM_RE = /:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\}/g;
const HEALTH_PATH = "/api/health";
/** Endpoint keys the form edits; any others are kept as they are. */
const ENDPOINT_KEYS = new Set([
  "method",
  "path",
  "description",
  "auth",
  "query",
  "success_status",
  "request",
  "response",
]);

export interface EndpointDraft {
  /** Stable key for keyed each blocks; never sent. */
  key: number;
  method: HttpMethod;
  path: string;
  description: string;
  /** "" = not stated (legacy specs). */
  auth: EndpointAuth | "";
  /** Query parameters, one `name: description` per line. */
  query: string;
  /** "" = not stated (200). */
  successStatus: string;
  /** JSON text; "" means null. */
  request: string;
  response: string;
  /** Keys the form does not edit, sent back unchanged. */
  extra: Record<string, unknown>;
}

export interface FieldDraft {
  key: number;
  name: string;
  type: string;
}

export interface ModelDraft {
  key: number;
  name: string;
  description: string;
  fields: FieldDraft[];
}

export interface SpecDraft {
  name: string;
  slug: string;
  category: string;
  difficulty: AppSpecDifficulty;
  description: string;
  schemaVersion: number;
  /** One requirement per line. */
  backend: string;
  frontend: string;
  admin: string;
  models: ModelDraft[];
  endpoints: EndpointDraft[];
  adminEndpoints: EndpointDraft[];
}

export interface SpecPayload {
  name: string;
  slug: string;
  category: string;
  difficulty: AppSpecDifficulty;
  description: string;
  spec_schema_version: number;
  backend_requirements: string[];
  frontend_requirements: string[];
  admin_requirements: string[];
  api_endpoints: Endpoint[];
  admin_api_endpoints: Endpoint[];
  /** v3 (or several models, or model descriptions): every model. */
  data_models?: AppSpecDataModel[];
  /** Legacy shape: the one model, or `{}`. */
  data_model?:
    | { name: string; fields: Record<string, string> }
    | Record<string, never>;
}

export interface Endpoint {
  method: string;
  path: string;
  description: string;
  auth?: EndpointAuth;
  query?: Record<string, string>;
  success_status?: number;
  request?: unknown;
  response?: unknown;
  [key: string]: unknown;
}

export interface SpecIssue {
  /** Form field id the issue belongs to ("" for the whole spec). */
  field: string;
  message: string;
  level: "error" | "warning";
}

let nextKey = 1;
const key = () => nextKey++;

export function emptyEndpoint(
  method: HttpMethod = "GET",
  auth: EndpointAuth | "" = "",
): EndpointDraft {
  return {
    key: key(),
    method,
    path: "/api/",
    description: "",
    auth,
    query: "",
    successStatus: "",
    request: "",
    response: "",
    extra: {},
  };
}

/** The health check every v3 spec lists (the evaluation polls it). */
export function healthEndpoint(): EndpointDraft {
  return {
    ...emptyEndpoint("GET", "public"),
    path: HEALTH_PATH,
    description: "Health check",
    response: jsonText({ status: "healthy" }),
  };
}

export function emptyField(): FieldDraft {
  return { key: key(), name: "", type: "" };
}

export function emptyModel(): ModelDraft {
  return { key: key(), name: "", description: "", fields: [emptyField()] };
}

function jsonText(value: unknown): string {
  return value === null || value === undefined
    ? ""
    : JSON.stringify(value, null, 2);
}

function record(raw: unknown): Record<string, unknown> {
  return raw && typeof raw === "object" && !Array.isArray(raw)
    ? (raw as Record<string, unknown>)
    : {};
}

function endpointDraft(raw: unknown): EndpointDraft {
  const e = record(raw);
  const method = String(e.method ?? "GET").toUpperCase();
  const auth = String(e.auth ?? "");
  return {
    key: key(),
    method: (HTTP_METHODS as readonly string[]).includes(method)
      ? (method as HttpMethod)
      : "GET",
    path: String(e.path ?? ""),
    description: String(e.description ?? ""),
    auth: (ENDPOINT_AUTH as readonly string[]).includes(auth)
      ? (auth as EndpointAuth)
      : "",
    query: Object.entries(record(e.query))
      .map(([name, text]) => `${name}: ${String(text ?? "")}`)
      .join("\n"),
    successStatus:
      e.success_status === undefined || e.success_status === null
        ? ""
        : String(e.success_status),
    request: jsonText(e.request),
    response: jsonText(e.response),
    extra: Object.fromEntries(
      Object.entries(e).filter(([k]) => !ENDPOINT_KEYS.has(k)),
    ),
  };
}

function modelDraft(raw: unknown): ModelDraft {
  const m = record(raw);
  return {
    key: key(),
    name: typeof m.name === "string" ? m.name : "",
    description: typeof m.description === "string" ? m.description : "",
    fields: Object.entries(record(m.fields)).map(([name, type]) => ({
      key: key(),
      name,
      type: String(type ?? ""),
    })),
  };
}

/** Every data model of *spec*: `data_models` when present, else the legacy `data_model`. */
function specModels(spec: Partial<AppRequirementTemplate> | null): unknown[] {
  if (Array.isArray(spec?.data_models) && spec.data_models.length) {
    return spec.data_models;
  }
  const legacy = spec?.data_model;
  if (Array.isArray(legacy)) return legacy;
  return legacy && Object.keys(legacy).length ? [legacy] : [];
}

/** A draft of *spec*, or an empty one. `copy` clears the slug and renames. */
export function specToDraft(
  spec: Partial<AppRequirementTemplate> | null,
  copy = false,
): SpecDraft {
  return {
    name: copy && spec?.name ? `${spec.name} (copy)` : (spec?.name ?? ""),
    slug: copy ? "" : (spec?.slug ?? ""),
    category: spec?.category ?? "",
    difficulty: spec?.difficulty ?? "",
    description: spec?.description ?? "",
    // A new spec starts on the current format; stored ones keep theirs (1 when unset).
    schemaVersion: spec ? (spec.spec_schema_version ?? 1) : SPEC_SCHEMA_VERSION,
    backend: (spec?.backend_requirements ?? []).join("\n"),
    frontend: (spec?.frontend_requirements ?? []).join("\n"),
    admin: (spec?.admin_requirements ?? []).join("\n"),
    models: specModels(spec).map(modelDraft),
    endpoints: spec
      ? (spec.api_endpoints ?? []).map(endpointDraft)
      : [healthEndpoint()],
    adminEndpoints: (spec?.admin_api_endpoints ?? []).map(endpointDraft),
  };
}

function lines(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
}

function parseJson(text: string): { ok: true; value: unknown } | { ok: false } {
  if (!text.trim()) return { ok: true, value: null };
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch {
    return { ok: false };
  }
}

/** `name: description` lines → entries; `null` for a line without both parts. */
function queryEntries(text: string): [string, string][] | null {
  const out: [string, string][] = [];
  for (const line of lines(text)) {
    const at = line.indexOf(":");
    const name = at > 0 ? line.slice(0, at).trim() : "";
    const description = at > 0 ? line.slice(at + 1).trim() : "";
    if (!name || !description) return null;
    out.push([name, description]);
  }
  return out;
}

function endpointPayload(e: EndpointDraft): Endpoint {
  const request = parseJson(e.request);
  const response = parseJson(e.response);
  const query = queryEntries(e.query) ?? [];
  const out: Endpoint = {
    ...e.extra,
    method: e.method,
    path: e.path.trim(),
    description: e.description.trim(),
  };
  if (e.auth) out.auth = e.auth;
  if (query.length) out.query = Object.fromEntries(query);
  if (e.successStatus.trim()) out.success_status = Number(e.successStatus);
  out.request = request.ok ? request.value : null;
  out.response = response.ok ? response.value : null;
  return out;
}

function modelPayload(m: ModelDraft): AppSpecDataModel {
  const fields = Object.fromEntries(
    m.fields
      .filter((f) => f.name.trim())
      .map((f) => [f.name.trim(), f.type.trim()]),
  );
  const out: AppSpecDataModel = { name: m.name.trim(), fields };
  if (m.description.trim()) out.description = m.description.trim();
  return out;
}

/** Models that say something (an untouched new model row is dropped). */
function filledModels(draft: SpecDraft): ModelDraft[] {
  return draft.models.filter(
    (m) =>
      m.name.trim() ||
      m.description.trim() ||
      m.fields.some((f) => f.name.trim() || f.type.trim()),
  );
}

/** Whether *draft* saves as `data_models` (v3, several models, or a model description). */
function usesModelList(draft: SpecDraft, models: ModelDraft[]): boolean {
  return (
    draft.schemaVersion >= SPEC_SCHEMA_VERSION ||
    models.length > 1 ||
    models.some((m) => m.description.trim())
  );
}

/** The API payload for *draft*. Call only when `specIssues` has no errors. */
export function draftToPayload(draft: SpecDraft): SpecPayload {
  const models = filledModels(draft);
  const payload: SpecPayload = {
    name: draft.name.trim(),
    slug: draft.slug.trim(),
    category: draft.category.trim(),
    difficulty: draft.difficulty,
    description: draft.description.trim(),
    spec_schema_version: draft.schemaVersion,
    backend_requirements: lines(draft.backend),
    frontend_requirements: lines(draft.frontend),
    admin_requirements: lines(draft.admin),
    api_endpoints: draft.endpoints.map(endpointPayload),
    admin_api_endpoints: draft.adminEndpoints.map(endpointPayload),
  };
  if (usesModelList(draft, models)) {
    payload.data_models = models.map(modelPayload);
  } else {
    const [only] = models.map(modelPayload);
    payload.data_model = only ? { name: only.name, fields: only.fields } : {};
  }
  return payload;
}

/** App spec slugs are snake_case, like the seeded spec files. */
export function specSlugFromName(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^[^a-z]+/, "")
    .replace(/_+$/, "")
    .slice(0, 200);
}

const normPath = (path: string) =>
  path
    .trim()
    .replace(PARAM_RE, ":p")
    .replace(/(.)\/$/, "$1");

function endpointIssues(
  list: EndpointDraft[],
  prefix: "ep" | "aep",
  v3: boolean,
  seen: Map<string, string>,
  issues: SpecIssue[],
) {
  list.forEach((e, i) => {
    const id = `${prefix}-${e.key}`;
    const label = `${prefix === "ep" ? "Endpoint" : "Admin endpoint"} ${i + 1}`;
    const path = e.path.trim();
    if (!/^\/api\//.test(path)) {
      issues.push({
        field: `${id}-path`,
        message: `${label}: path must start with /api/`,
        level: "error",
      });
    }
    if (!e.description.trim()) {
      issues.push({
        field: `${id}-desc`,
        message: `${label}: description is required`,
        level: "error",
      });
    }
    if (!parseJson(e.request).ok) {
      issues.push({
        field: `${id}-request`,
        message: `${label}: request is not valid JSON`,
        level: "error",
      });
    }
    if (!parseJson(e.response).ok) {
      issues.push({
        field: `${id}-response`,
        message: `${label}: response is not valid JSON`,
        level: "error",
      });
    }
    if (queryEntries(e.query) === null) {
      issues.push({
        field: `${id}-query`,
        message: `${label}: write each query parameter as "name: what it does"`,
        level: "error",
      });
    }
    const status = e.successStatus.trim();
    if (status && !/^\d+$/.test(status)) {
      issues.push({
        field: `${id}-status`,
        message: `${label}: success status must be a number`,
        level: "error",
      });
    } else if (status && (Number(status) < 200 || Number(status) > 399)) {
      issues.push({
        field: `${id}-status`,
        message: `${label}: success status must be 200-399`,
        level: "error",
      });
    }
    const sig = `${e.method} ${normPath(path)}`;
    const first = seen.get(sig);
    if (first) {
      issues.push({
        field: `${id}-path`,
        message: `${label}: ${e.method} ${path} is already ${first}`,
        level: v3 ? "error" : "warning",
      });
    } else {
      seen.set(sig, label.toLowerCase());
    }
    if (!v3) return;
    if (!e.auth) {
      issues.push({
        field: `${id}-auth`,
        message: `${label}: choose who may call it (public, user or admin)`,
        level: "error",
      });
    }
    if (prefix === "aep") {
      if (!path.startsWith("/api/admin/")) {
        issues.push({
          field: `${id}-path`,
          message: `${label}: admin paths start with /api/admin/`,
          level: "error",
        });
      }
      if (e.auth && e.auth !== "admin") {
        issues.push({
          field: `${id}-auth`,
          message: `${label}: admin endpoints are admin-only`,
          level: "error",
        });
      }
    } else if (path.startsWith("/api/admin") || e.auth === "admin") {
      issues.push({
        field: `${id}-path`,
        message: `${label}: admin-only endpoints go under Admin endpoints, at /api/admin/…`,
        level: "error",
      });
    }
    if (
      status === "204" &&
      parseJson(e.response).ok &&
      e.response.trim() &&
      !["{}", "[]", "null"].includes(e.response.replace(/\s/g, ""))
    ) {
      issues.push({
        field: `${id}-response`,
        message: `${label}: a 204 answer has no body; clear the response sample`,
        level: "error",
      });
    }
  });
}

function modelIssues(draft: SpecDraft, issues: SpecIssue[]): number {
  const modelNames = new Set<string>();
  let fieldCount = 0;
  const models = filledModels(draft);
  models.forEach((m, mi) => {
    const label =
      models.length > 1 ? `Model ${m.name.trim() || mi + 1}` : "Data model";
    const names = new Set<string>();
    m.fields.forEach((f, i) => {
      const name = f.name.trim();
      if (!name && f.type.trim()) {
        issues.push({
          field: `field-${f.key}-name`,
          message: `${label} field ${i + 1}: name is required`,
          level: "error",
        });
      }
      if (name && !f.type.trim()) {
        issues.push({
          field: `field-${f.key}-type`,
          message: `${label} field ${name}: type is required`,
          level: "error",
        });
      }
      if (name && names.has(name)) {
        issues.push({
          field: `field-${f.key}-name`,
          message: `${label} field ${name} is listed twice`,
          level: "error",
        });
      }
      if (name) names.add(name);
    });
    fieldCount += names.size;
    const modelName = m.name.trim();
    if (!modelName) {
      issues.push({
        field: `model-${m.key}-name`,
        message: `${label}: name the model`,
        level: "error",
      });
    } else if (modelNames.has(modelName)) {
      issues.push({
        field: `model-${m.key}-name`,
        message: `Model ${modelName} is listed twice`,
        level: "error",
      });
    }
    if (modelName) modelNames.add(modelName);
    if (!names.size) {
      issues.push({
        field: `model-${m.key}-name`,
        message: `${label}: add at least one field`,
        level: "error",
      });
    }
  });
  return fieldCount;
}

/**
 * Problems with *draft*. `takenSlugs` are the slugs already visible (checked
 * only for a new spec; the server also refuses slugs other users hold).
 */
export function specIssues(
  draft: SpecDraft,
  opts: { isNew: boolean; takenSlugs?: Iterable<string> } = { isNew: true },
): SpecIssue[] {
  const issues: SpecIssue[] = [];
  const v3 = draft.schemaVersion >= SPEC_SCHEMA_VERSION;
  if (!draft.name.trim()) {
    issues.push({
      field: "spec-name",
      message: "Name is required",
      level: "error",
    });
  }
  if (opts.isNew) {
    const slug = draft.slug.trim();
    if (!SLUG_RE.test(slug)) {
      issues.push({
        field: "spec-slug",
        message:
          "Slug: snake_case, starting with a letter (e.g. crud_todo_list)",
        level: "error",
      });
    } else if ([...(opts.takenSlugs ?? [])].includes(slug)) {
      issues.push({
        field: "spec-slug",
        message: "Slug already in use",
        level: "error",
      });
    }
  }

  const fieldCount = modelIssues(draft, issues);

  const seen = new Map<string, string>();
  endpointIssues(draft.endpoints, "ep", v3, seen, issues);
  endpointIssues(draft.adminEndpoints, "aep", v3, seen, issues);

  if (v3) {
    // What data_lint adds for v3: the evaluation's health poll and access-control parse.
    if (
      !draft.endpoints.some(
        (e) => e.method === "GET" && e.path.trim() === HEALTH_PATH,
      )
    ) {
      issues.push({
        field: "",
        message: `Add GET ${HEALTH_PATH} (public): the evaluation polls it`,
        level: "error",
      });
    }
    const first = (lines(draft.backend)[0] ?? "").toLowerCase();
    if (!first.startsWith("access control:")) {
      issues.push({
        field: "spec-backend",
        message:
          'The first backend requirement is "Access control: …" (who may read and write what)',
        level: "error",
      });
    } else if (!first.includes("require auth") && !first.includes("restrict")) {
      issues.push({
        field: "spec-backend",
        message:
          'The access-control line says what it restricts ("require auth for …" or "restrict … to …")',
        level: "error",
      });
    }
  }

  // What the hidden conformance suite needs to grade a run.
  if (draft.endpoints.length === 0) {
    issues.push({
      field: "",
      message: "No API endpoints: agent runs on this spec cannot be graded",
      level: "warning",
    });
  }
  if (!lines(draft.backend).length || !lines(draft.frontend).length) {
    issues.push({
      field: "",
      message:
        "Backend and frontend requirements should each have at least one line",
      level: "warning",
    });
  }
  if (!fieldCount) {
    issues.push({
      field: "",
      message: "No data model fields",
      level: v3 ? "error" : "warning",
    });
  }
  if (!draft.category.trim()) {
    issues.push({
      field: "spec-category",
      message: "No category",
      level: "warning",
    });
  }
  if (!draft.description.trim()) {
    issues.push({
      field: "spec-desc",
      message: "No description",
      level: "warning",
    });
  }
  return issues;
}

/** The spec as a JSON document, in the seeded files' key order. */
export function draftToJson(draft: SpecDraft): string {
  const p = draftToPayload(draft);
  const doc: Record<string, unknown> = {
    slug: p.slug,
    name: p.name,
    category: p.category,
    description: p.description,
  };
  if (p.difficulty) doc.difficulty = p.difficulty;
  doc.spec_schema_version = p.spec_schema_version;
  Object.assign(doc, {
    backend_requirements: p.backend_requirements,
    frontend_requirements: p.frontend_requirements,
    admin_requirements: p.admin_requirements,
    api_endpoints: p.api_endpoints,
    admin_api_endpoints: p.admin_api_endpoints,
  });
  if (p.data_models) doc.data_models = p.data_models;
  else doc.data_model = p.data_model;
  return JSON.stringify(doc, null, 2);
}

/**
 * A draft from a JSON spec document (a seeded file or `draftToJson` output).
 * Throws an Error with a user-facing message when it is not a spec object.
 */
export function jsonToDraft(text: string): SpecDraft {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (e) {
    throw new Error(`Not valid JSON: ${(e as Error).message}`);
  }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("A spec is a JSON object");
  }
  const o = raw as Record<string, unknown>;
  const arr = (k: string) => {
    const v = o[k];
    if (v === undefined || v === null) return [];
    if (!Array.isArray(v)) throw new Error(`${k} must be a list`);
    return v;
  };
  const str = (k: string) => (typeof o[k] === "string" ? (o[k] as string) : "");
  const difficulty = str("difficulty");
  if (difficulty && !DIFFICULTIES.includes(difficulty as AppSpecDifficulty)) {
    throw new Error(`difficulty must be one of ${DIFFICULTIES.join(", ")}`);
  }
  if (
    o.data_model !== undefined &&
    o.data_model !== null &&
    (typeof o.data_model !== "object" || Array.isArray(o.data_model))
  ) {
    throw new Error("data_model must be an object");
  }
  const version = o.spec_schema_version ?? 1;
  if (!(SPEC_SCHEMA_VERSIONS as readonly unknown[]).includes(version)) {
    throw new Error(
      `spec_schema_version must be one of ${SPEC_SCHEMA_VERSIONS.join(", ")}`,
    );
  }
  return specToDraft({
    name: str("name"),
    slug: str("slug"),
    category: str("category"),
    difficulty: difficulty as AppSpecDifficulty,
    description: str("description"),
    spec_schema_version: version as number,
    backend_requirements: arr("backend_requirements").map(String),
    frontend_requirements: arr("frontend_requirements").map(String),
    admin_requirements: arr("admin_requirements").map(String),
    api_endpoints: arr("api_endpoints"),
    admin_api_endpoints: arr("admin_api_endpoints"),
    data_model: (o.data_model ?? {}) as Record<string, unknown>,
    data_models: arr("data_models") as AppSpecDataModel[],
  });
}

/** Every data model of a stored spec (for list views). */
export function specDataModels(
  spec: Partial<AppRequirementTemplate>,
): AppSpecDataModel[] {
  return specModels(spec).map((m) => {
    const r = record(m);
    return {
      name: String(r.name ?? ""),
      fields: record(r.fields) as Record<string, string>,
    };
  });
}

/** Whether *user* may edit *spec*: their own, or a catalog spec for staff. */
export function canEditSpec(
  spec: AppRequirementTemplate,
  isStaff: boolean,
): boolean {
  return spec.is_default ? isStaff : true;
}
