/**
 * App spec editing: a form draft ⇄ the API payload, and the checks of
 * `backend/generation/data/requirements/schema.json` (errors block a save;
 * warnings say what the conformance suite cannot grade).
 */
import type {
  AppRequirementTemplate,
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

export const DIFFICULTIES: AppSpecDifficulty[] = [
  "basic",
  "standard",
  "advanced",
];

const SLUG_RE = /^[a-z][a-z0-9]*(_[a-z0-9]+)*$/;

export interface EndpointDraft {
  /** Stable key for keyed each blocks; never sent. */
  key: number;
  method: HttpMethod;
  path: string;
  description: string;
  /** JSON text; "" means null. */
  request: string;
  response: string;
}

export interface FieldDraft {
  key: number;
  name: string;
  type: string;
}

export interface SpecDraft {
  name: string;
  slug: string;
  category: string;
  difficulty: AppSpecDifficulty;
  description: string;
  /** One requirement per line. */
  backend: string;
  frontend: string;
  admin: string;
  modelName: string;
  fields: FieldDraft[];
  endpoints: EndpointDraft[];
  adminEndpoints: EndpointDraft[];
}

export interface SpecPayload {
  name: string;
  slug: string;
  category: string;
  difficulty: AppSpecDifficulty;
  description: string;
  backend_requirements: string[];
  frontend_requirements: string[];
  admin_requirements: string[];
  api_endpoints: Endpoint[];
  admin_api_endpoints: Endpoint[];
  data_model:
    | { name: string; fields: Record<string, string> }
    | Record<string, never>;
}

export interface Endpoint {
  method: string;
  path: string;
  description: string;
  request?: unknown;
  response?: unknown;
}

export interface SpecIssue {
  /** Form field id the issue belongs to ("" for the whole spec). */
  field: string;
  message: string;
  level: "error" | "warning";
}

let nextKey = 1;
const key = () => nextKey++;

export function emptyEndpoint(method: HttpMethod = "GET"): EndpointDraft {
  return {
    key: key(),
    method,
    path: "/api/",
    description: "",
    request: "",
    response: "",
  };
}

export function emptyField(): FieldDraft {
  return { key: key(), name: "", type: "" };
}

function jsonText(value: unknown): string {
  return value === null || value === undefined
    ? ""
    : JSON.stringify(value, null, 2);
}

function endpointDraft(raw: unknown): EndpointDraft {
  const e = (raw && typeof raw === "object" ? raw : {}) as Record<
    string,
    unknown
  >;
  const method = String(e.method ?? "GET").toUpperCase();
  return {
    key: key(),
    method: (HTTP_METHODS as readonly string[]).includes(method)
      ? (method as HttpMethod)
      : "GET",
    path: String(e.path ?? ""),
    description: String(e.description ?? ""),
    request: jsonText(e.request),
    response: jsonText(e.response),
  };
}

/** A draft of *spec*, or an empty one. `copy` clears the slug and renames. */
export function specToDraft(
  spec: Partial<AppRequirementTemplate> | null,
  copy = false,
): SpecDraft {
  const model = (spec?.data_model ?? {}) as {
    name?: unknown;
    fields?: unknown;
  };
  const fields =
    model.fields && typeof model.fields === "object"
      ? Object.entries(model.fields as Record<string, unknown>).map(
          ([name, type]) => ({ key: key(), name, type: String(type ?? "") }),
        )
      : [];
  return {
    name: copy && spec?.name ? `${spec.name} (copy)` : (spec?.name ?? ""),
    slug: copy ? "" : (spec?.slug ?? ""),
    category: spec?.category ?? "",
    difficulty: spec?.difficulty ?? "",
    description: spec?.description ?? "",
    backend: (spec?.backend_requirements ?? []).join("\n"),
    frontend: (spec?.frontend_requirements ?? []).join("\n"),
    admin: (spec?.admin_requirements ?? []).join("\n"),
    modelName: typeof model.name === "string" ? model.name : "",
    fields,
    endpoints: (spec?.api_endpoints ?? []).map(endpointDraft),
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

function endpointPayload(e: EndpointDraft): Endpoint {
  const request = parseJson(e.request);
  const response = parseJson(e.response);
  return {
    method: e.method,
    path: e.path.trim(),
    description: e.description.trim(),
    request: request.ok ? request.value : null,
    response: response.ok ? response.value : null,
  };
}

/** The API payload for *draft*. Call only when `specIssues` has no errors. */
export function draftToPayload(draft: SpecDraft): SpecPayload {
  const fields = Object.fromEntries(
    draft.fields
      .filter((f) => f.name.trim())
      .map((f) => [f.name.trim(), f.type.trim()]),
  );
  const modelName = draft.modelName.trim();
  return {
    name: draft.name.trim(),
    slug: draft.slug.trim(),
    category: draft.category.trim(),
    difficulty: draft.difficulty,
    description: draft.description.trim(),
    backend_requirements: lines(draft.backend),
    frontend_requirements: lines(draft.frontend),
    admin_requirements: lines(draft.admin),
    api_endpoints: draft.endpoints.map(endpointPayload),
    admin_api_endpoints: draft.adminEndpoints.map(endpointPayload),
    data_model:
      modelName || Object.keys(fields).length
        ? { name: modelName, fields }
        : {},
  };
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

function endpointIssues(
  list: EndpointDraft[],
  prefix: string,
  issues: SpecIssue[],
) {
  const seen = new Set<string>();
  list.forEach((e, i) => {
    const id = `${prefix}-${e.key}`;
    const label = `${prefix === "ep" ? "Endpoint" : "Admin endpoint"} ${i + 1}`;
    if (!/^\/api\//.test(e.path.trim())) {
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
    const sig = `${e.method} ${e.path.trim()}`;
    if (seen.has(sig)) {
      issues.push({
        field: `${id}-path`,
        message: `${label}: ${sig} is listed twice`,
        level: "warning",
      });
    }
    seen.add(sig);
  });
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

  const names = new Set<string>();
  draft.fields.forEach((f, i) => {
    const name = f.name.trim();
    if (!name && f.type.trim()) {
      issues.push({
        field: `field-${f.key}-name`,
        message: `Data model field ${i + 1}: name is required`,
        level: "error",
      });
    }
    if (name && !f.type.trim()) {
      issues.push({
        field: `field-${f.key}-type`,
        message: `Data model field ${name}: type is required`,
        level: "error",
      });
    }
    if (name && names.has(name)) {
      issues.push({
        field: `field-${f.key}-name`,
        message: `Data model field ${name} is listed twice`,
        level: "error",
      });
    }
    if (name) names.add(name);
  });
  if (names.size && !draft.modelName.trim()) {
    issues.push({
      field: "spec-model-name",
      message: "Data model: name the model",
      level: "error",
    });
  }

  endpointIssues(draft.endpoints, "ep", issues);
  endpointIssues(draft.adminEndpoints, "aep", issues);

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
  if (!names.size) {
    issues.push({
      field: "",
      message: "No data model fields",
      level: "warning",
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
  Object.assign(doc, {
    backend_requirements: p.backend_requirements,
    frontend_requirements: p.frontend_requirements,
    admin_requirements: p.admin_requirements,
    api_endpoints: p.api_endpoints,
    admin_api_endpoints: p.admin_api_endpoints,
    data_model: p.data_model,
  });
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
    (typeof o.data_model !== "object" || Array.isArray(o.data_model))
  ) {
    throw new Error("data_model must be an object");
  }
  return specToDraft({
    name: str("name"),
    slug: str("slug"),
    category: str("category"),
    difficulty: difficulty as AppSpecDifficulty,
    description: str("description"),
    backend_requirements: arr("backend_requirements").map(String),
    frontend_requirements: arr("frontend_requirements").map(String),
    admin_requirements: arr("admin_requirements").map(String),
    api_endpoints: arr("api_endpoints"),
    admin_api_endpoints: arr("admin_api_endpoints"),
    data_model: (o.data_model ?? {}) as Record<string, unknown>,
  });
}

/** Whether *user* may edit *spec*: their own, or a catalog spec for staff. */
export function canEditSpec(
  spec: AppRequirementTemplate,
  isStaff: boolean,
): boolean {
  return spec.is_default ? isStaff : true;
}
