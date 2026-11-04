/**
 * TaskEditor state ↔ `TaskSpecContent` (schema 4).
 *
 * The editor works on a `TaskDraft`: the content with every free-form JSON part held as text (request and
 * response schemas, field/query defaults, seed data, each acceptance check and probe) and list fields as
 * comma text, so a half-typed value never corrupts the content. `draftFromTask` and `taskFromDraft` are
 * exact inverses on normalized content (the round trip is tested against the contract fixtures);
 * `taskFromDraft` returns per-path errors for text that does not parse, and the server's lint stays the
 * authority on everything else (`localTaskIssues` only mirrors the cheap cross-reference checks).
 */
import type {
  AuthFlow,
  CheckSpec,
  Endpoint,
  EndpointAuth,
  Entity,
  EntityField,
  FieldType,
  HttpMethod,
  ProbeSpec,
  ProseVariant,
  QueryParam,
  QuerySemantics,
  TaskSpecContent,
  UiRequirement,
} from "$lib/api/v3/types";
import { PROSE_VARIANTS } from "$lib/api/v3/types";

export interface FieldDraft {
  name: string;
  type: FieldType;
  required: boolean;
  unique: boolean;
  max_length: number | null;
  min: number | null;
  max: number | null;
  /** Comma-separated values; "" = no enum (null). */
  enum_text: string;
  ref: string;
  /** JSON (bare words are taken as strings); "" = null. */
  default_text: string;
  secret: boolean;
}

export interface EntityDraft {
  name: string;
  fields: FieldDraft[];
  owned_by_user: boolean;
  soft_delete: boolean;
}

export interface QueryDraft {
  name: string;
  type: FieldType;
  required: boolean;
  enum_text: string;
  default_text: string;
  max: number | null;
  semantics: QuerySemantics;
}

export interface EndpointDraft {
  id: string;
  method: HttpMethod;
  path: string;
  auth: EndpointAuth;
  owner_entity: string;
  success_status: number;
  query: QueryDraft[];
  request_schema_text: string;
  response_schema_text: string;
  content_type: Endpoint["content_type"];
  /** "400, 409". */
  errors_text: string;
  tags_text: string;
  realtime: Endpoint["realtime"];
}

export interface AuthFlowDraft {
  enabled: boolean;
  register: string;
  login: string;
  logout: string;
  credential: AuthFlow["credential"];
  token_json_path: string;
  csrf: AuthFlow["csrf"];
  csrf_endpoint: string;
}

export interface TaskDraft {
  title: string;
  summary: string;
  category: string;
  has_frontend: boolean;
  features: string[];
  surfaces: string[];
  entities: EntityDraft[];
  endpoints: EndpointDraft[];
  ui: UiRequirement[];
  auth_flow: AuthFlowDraft;
  seed_data_text: string;
  /** One JSON document per hand-written check / probe. */
  acceptance: string[];
  probes: string[];
  prose: Record<ProseVariant, string>;
}

export type TaskErrors = Record<string, string>;

// ── helpers ──────────────────────────────────────────────────────────────

const pretty = (value: unknown) => JSON.stringify(value, null, 2);

function listText(
  values: readonly (string | number)[] | null | undefined,
): string {
  return (values ?? []).join(", ");
}

function splitList(text: string): string[] {
  return text
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function jsonText(value: unknown): string {
  return value === null || value === undefined ? "" : JSON.stringify(value);
}

/** "" → null; JSON when it parses; otherwise the bare text as a string ("medium"). */
function parseLoose(text: string): unknown {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return trimmed;
  }
}

function parseObject(
  text: string,
  path: string,
  errors: TaskErrors,
): Record<string, unknown> | null {
  if (text.trim() === "") return null;
  try {
    const value = JSON.parse(text);
    if (value === null) return null;
    if (typeof value !== "object" || Array.isArray(value)) {
      errors[path] = "Must be a JSON object.";
      return null;
    }
    return value as Record<string, unknown>;
  } catch (e) {
    errors[path] = `Invalid JSON: ${(e as Error).message}`;
    return null;
  }
}

const orNull = (text: string): string | null =>
  text.trim() === "" ? null : text.trim();

// ── content → draft ──────────────────────────────────────────────────────

function fieldDraft(f: EntityField): FieldDraft {
  return {
    name: f.name,
    type: f.type,
    required: f.required,
    unique: f.unique,
    max_length: f.max_length,
    min: f.min,
    max: f.max,
    enum_text: f.enum === null ? "" : listText(f.enum),
    ref: f.ref ?? "",
    default_text: jsonText(f.default),
    secret: f.secret,
  };
}

function queryDraft(q: QueryParam): QueryDraft {
  return {
    name: q.name,
    type: q.type,
    required: q.required,
    enum_text: q.enum === null ? "" : listText(q.enum),
    default_text: jsonText(q.default),
    max: q.max,
    semantics: q.semantics,
  };
}

function endpointDraft(e: Endpoint): EndpointDraft {
  return {
    id: e.id,
    method: e.method,
    path: e.path,
    auth: e.auth,
    owner_entity: e.owner_entity ?? "",
    success_status: e.success_status,
    query: e.query.map(queryDraft),
    request_schema_text:
      e.request_schema === null ? "" : pretty(e.request_schema),
    response_schema_text:
      e.response_schema === null ? "" : pretty(e.response_schema),
    content_type: e.content_type,
    errors_text: listText(e.errors),
    tags_text: listText(e.tags),
    realtime: e.realtime,
  };
}

export function emptyAuthFlow(): AuthFlowDraft {
  return {
    enabled: false,
    register: "",
    login: "",
    logout: "",
    credential: "bearer",
    token_json_path: "",
    csrf: "none",
    csrf_endpoint: "",
  };
}

export function draftFromTask(content: TaskSpecContent): TaskDraft {
  const flow = content.auth_flow;
  return {
    title: content.title,
    summary: content.summary,
    category: content.category,
    has_frontend: content.has_frontend,
    features: [...content.features],
    surfaces: [...content.surfaces],
    entities: content.entities.map((e) => ({
      name: e.name,
      fields: e.fields.map(fieldDraft),
      owned_by_user: e.owned_by_user,
      soft_delete: e.soft_delete,
    })),
    endpoints: content.endpoints.map(endpointDraft),
    ui: content.ui.map((u) => ({
      id: u.id,
      text: u.text,
      endpoints: [...u.endpoints],
    })),
    auth_flow: flow
      ? {
          enabled: true,
          register: flow.register ?? "",
          login: flow.login,
          logout: flow.logout ?? "",
          credential: flow.credential,
          token_json_path: flow.token_json_path ?? "",
          csrf: flow.csrf,
          csrf_endpoint: flow.csrf_endpoint ?? "",
        }
      : emptyAuthFlow(),
    seed_data_text: Object.keys(content.seed_data ?? {}).length
      ? pretty(content.seed_data)
      : "",
    acceptance: content.acceptance.map((c) => pretty(c)),
    probes: content.probes.map((p) => pretty(p)),
    prose: {
      minimal: content.prose.minimal ?? "",
      standard: content.prose.standard ?? "",
      exhaustive: content.prose.exhaustive ?? "",
    },
  };
}

// ── draft → content ──────────────────────────────────────────────────────

function fieldFromDraft(f: FieldDraft): EntityField {
  return {
    name: f.name.trim(),
    type: f.type,
    required: f.required,
    unique: f.unique,
    max_length: f.max_length,
    min: f.min,
    max: f.max,
    enum: f.enum_text.trim() === "" ? null : splitList(f.enum_text),
    ref: orNull(f.ref),
    default: parseLoose(f.default_text),
    secret: f.secret,
  };
}

function queryFromDraft(q: QueryDraft): QueryParam {
  return {
    name: q.name.trim(),
    type: q.type,
    required: q.required,
    enum: q.enum_text.trim() === "" ? null : splitList(q.enum_text),
    default: parseLoose(q.default_text),
    max: q.max,
    semantics: q.semantics,
  };
}

function parseStatuses(
  text: string,
  path: string,
  errors: TaskErrors,
): number[] {
  const out: number[] = [];
  for (const part of splitList(text)) {
    const n = Number(part);
    if (!Number.isInteger(n) || n < 100 || n > 599) {
      errors[path] = `"${part}" is not an HTTP status.`;
      continue;
    }
    out.push(n);
  }
  return out;
}

function endpointFromDraft(
  e: EndpointDraft,
  i: number,
  errors: TaskErrors,
): Endpoint {
  return {
    id: e.id.trim(),
    method: e.method,
    path: e.path.trim(),
    auth: e.auth,
    owner_entity: orNull(e.owner_entity),
    success_status: e.success_status,
    query: e.query.map(queryFromDraft),
    request_schema: parseObject(
      e.request_schema_text,
      `endpoints.${i}.request_schema`,
      errors,
    ),
    response_schema: parseObject(
      e.response_schema_text,
      `endpoints.${i}.response_schema`,
      errors,
    ),
    content_type: e.content_type,
    errors: parseStatuses(e.errors_text, `endpoints.${i}.errors`, errors),
    tags: splitList(e.tags_text),
    realtime: e.realtime,
  };
}

function parseItems<T>(texts: string[], path: string, errors: TaskErrors): T[] {
  const out: T[] = [];
  texts.forEach((text, i) => {
    const value = parseObject(text, `${path}.${i}`, errors);
    if (value) out.push(value as T);
    else if (!errors[`${path}.${i}`])
      errors[`${path}.${i}`] = "Empty item: remove it or fill it in.";
  });
  return out;
}

function authFlowFromDraft(a: AuthFlowDraft): AuthFlow | null {
  if (!a.enabled) return null;
  return {
    register: orNull(a.register),
    login: a.login.trim(),
    logout: orNull(a.logout),
    credential: a.credential,
    token_json_path: orNull(a.token_json_path),
    csrf: a.csrf,
    csrf_endpoint: orNull(a.csrf_endpoint),
  };
}

export function taskFromDraft(draft: TaskDraft): {
  content: TaskSpecContent;
  errors: TaskErrors;
} {
  const errors: TaskErrors = {};
  let seed: Record<string, Record<string, unknown>[]> = {};
  if (draft.seed_data_text.trim() !== "") {
    const parsed = parseObject(draft.seed_data_text, "seed_data", errors);
    if (parsed) {
      const bad = Object.entries(parsed).find(
        ([, rows]) => !Array.isArray(rows),
      );
      if (bad) errors.seed_data = `seed_data.${bad[0]} must be a list of rows.`;
      else seed = parsed as Record<string, Record<string, unknown>[]>;
    }
  }
  const prose: Partial<Record<ProseVariant, string>> = {};
  for (const variant of PROSE_VARIANTS) {
    if (draft.prose[variant].trim() !== "")
      prose[variant] = draft.prose[variant];
  }
  if (Object.keys(prose).length === 0)
    errors.prose = "Write at least one prose variant.";
  const content: TaskSpecContent = {
    title: draft.title.trim(),
    summary: draft.summary.trim(),
    category: draft.category.trim(),
    entities: draft.entities.map(
      (e): Entity => ({
        name: e.name.trim(),
        fields: e.fields.map(fieldFromDraft),
        owned_by_user: e.owned_by_user,
        soft_delete: e.soft_delete,
      }),
    ),
    endpoints: draft.endpoints.map((e, i) => endpointFromDraft(e, i, errors)),
    ui: draft.ui.map((u) => ({
      id: u.id.trim(),
      text: u.text,
      endpoints: [...u.endpoints],
    })),
    has_frontend: draft.has_frontend,
    features: [...draft.features],
    surfaces: [...draft.surfaces],
    auth_flow: authFlowFromDraft(draft.auth_flow),
    seed_data: seed,
    acceptance: parseItems<CheckSpec>(draft.acceptance, "acceptance", errors),
    probes: parseItems<ProbeSpec>(draft.probes, "probes", errors),
    prose,
  };
  if (!content.title) errors.title = "A title is required.";
  if (!content.summary) errors.summary = "A summary is required.";
  if (!content.category) errors.category = "A category is required.";
  return { content, errors };
}

// ── new rows ─────────────────────────────────────────────────────────────

export function newField(): FieldDraft {
  return {
    name: "",
    type: "string",
    required: false,
    unique: false,
    max_length: null,
    min: null,
    max: null,
    enum_text: "",
    ref: "",
    default_text: "",
    secret: false,
  };
}

export function newEntity(): EntityDraft {
  return {
    name: "",
    fields: [newField()],
    owned_by_user: false,
    soft_delete: false,
  };
}

export function newQuery(): QueryDraft {
  return {
    name: "",
    type: "string",
    required: false,
    enum_text: "",
    default_text: "",
    max: null,
    semantics: "filter",
  };
}

export function newEndpoint(): EndpointDraft {
  return {
    id: "",
    method: "GET",
    path: "/api/",
    auth: "user",
    owner_entity: "",
    success_status: 200,
    query: [],
    request_schema_text: "",
    response_schema_text: "",
    content_type: "json",
    errors_text: "",
    tags_text: "",
    realtime: "none",
  };
}

export function newCheckText(endpointId = ""): string {
  return pretty({
    id: endpointId ? `${endpointId}.extra` : "check.id",
    category: "functional",
    endpoint: endpointId || null,
    actor: "user_a",
    setup: [],
    request: {
      method: "GET",
      path: "/api/",
      query: {},
      json: null,
      form: null,
      files: null,
    },
    expect: {
      status: 200,
      json_schema: null,
      json_contains: null,
      header_contains: null,
      max_ms: null,
    },
    depends_on: [],
    restart_before: false,
    stated_in: [],
  });
}

export function newProbeText(endpointId = ""): string {
  return pretty({
    id: endpointId ? `${endpointId}.probe` : "probe.id",
    cwe: "cwe-89",
    kind: "sqli_boolean",
    endpoint: endpointId || null,
    params: {},
    requires_checks: null,
    positive_control: null,
  });
}

export function emptyTaskContent(): TaskSpecContent {
  return {
    title: "",
    summary: "",
    category: "",
    entities: [],
    endpoints: [],
    ui: [],
    has_frontend: true,
    features: [],
    surfaces: [],
    auth_flow: null,
    seed_data: {},
    acceptance: [],
    probes: [],
    prose: { standard: "" },
  };
}

// ── local checks (the server lint is authoritative) ──────────────────────

export interface LocalIssue {
  path: string;
  message: string;
}

/** Cheap cross-reference checks shown while editing. */
export function localTaskIssues(content: TaskSpecContent): LocalIssue[] {
  const issues: LocalIssue[] = [];
  const ids = new Map<string, number>();
  const entities = new Set(content.entities.map((e) => e.name));
  content.endpoints.forEach((e, i) => {
    if (!e.id)
      issues.push({
        path: `endpoints.${i}.id`,
        message: "Every endpoint needs a stable id.",
      });
    else if (ids.has(e.id))
      issues.push({
        path: `endpoints.${i}.id`,
        message: `Duplicate endpoint id "${e.id}".`,
      });
    ids.set(e.id, i);
    if (!e.path.startsWith("/"))
      issues.push({
        path: `endpoints.${i}.path`,
        message: "Paths start with /.",
      });
    if (e.auth === "owner" && !e.owner_entity) {
      issues.push({
        path: `endpoints.${i}.owner_entity`,
        message: "Owner-scoped endpoints name the owned entity.",
      });
    }
    if (e.owner_entity && !entities.has(e.owner_entity)) {
      issues.push({
        path: `endpoints.${i}.owner_entity`,
        message: `No entity "${e.owner_entity}".`,
      });
    }
  });
  content.ui.forEach((u, i) => {
    for (const ref of u.endpoints) {
      if (!ids.has(ref))
        issues.push({
          path: `ui.${i}.endpoints`,
          message: `Unknown endpoint id "${ref}".`,
        });
    }
  });
  const flow = content.auth_flow;
  if (flow) {
    for (const [key, ref] of [
      ["register", flow.register],
      ["login", flow.login],
      ["logout", flow.logout],
      ["csrf_endpoint", flow.csrf_endpoint],
    ] as const) {
      if (ref && !ids.has(ref))
        issues.push({
          path: `auth_flow.${key}`,
          message: `Unknown endpoint id "${ref}".`,
        });
    }
  }
  content.acceptance.forEach((c, i) => {
    if (c.endpoint && !ids.has(c.endpoint)) {
      issues.push({
        path: `acceptance.${i}`,
        message: `Check ${c.id}: unknown endpoint id "${c.endpoint}".`,
      });
    }
  });
  content.probes.forEach((p, i) => {
    if (p.endpoint && !ids.has(p.endpoint)) {
      issues.push({
        path: `probes.${i}`,
        message: `Probe ${p.id}: unknown endpoint id "${p.endpoint}".`,
      });
    }
  });
  return issues;
}
