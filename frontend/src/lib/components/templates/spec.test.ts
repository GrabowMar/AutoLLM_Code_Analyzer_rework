import { describe, expect, it } from "vitest";
import type { AppRequirementTemplate } from "$lib/api/generation";
import {
  draftToJson,
  draftToPayload,
  emptyEndpoint,
  emptyModel,
  jsonToDraft,
  specDataModels,
  specIssues,
  specSlugFromName,
  specToDraft,
} from "$lib/components/templates/spec";

const SPEC = {
  name: "Book Library",
  slug: "crud_book_library",
  category: "CRUD",
  difficulty: "standard",
  description: "Track your book collection",
  backend_requirements: ["Book model", "CRUD endpoints"],
  frontend_requirements: ["Book list"],
  admin_requirements: [],
  api_endpoints: [
    {
      method: "GET",
      path: "/api/books",
      description: "List all books",
      request: null,
      response: { items: [{ id: 1 }], total: 1 },
    },
  ],
  admin_api_endpoints: [],
  data_model: { name: "Book", fields: { id: "integer", title: "string" } },
} as unknown as AppRequirementTemplate;

const errors = (issues: ReturnType<typeof specIssues>) =>
  issues.filter((i) => i.level === "error").map((i) => i.message);

describe("specToDraft / draftToPayload", () => {
  it("round-trips the whole graded contract, not only the requirements", () => {
    const payload = draftToPayload(specToDraft(SPEC));
    expect(payload.api_endpoints).toEqual(SPEC.api_endpoints);
    expect(payload.data_model).toEqual(SPEC.data_model);
    expect(payload.difficulty).toBe("standard");
    expect(payload.category).toBe("CRUD");
    expect(payload.backend_requirements).toEqual(SPEC.backend_requirements);
  });

  it("a copy clears the slug and renames", () => {
    const draft = specToDraft(SPEC, true);
    expect(draft.slug).toBe("");
    expect(draft.name).toBe("Book Library (copy)");
    expect(draft.endpoints).toHaveLength(1);
  });

  it("drops blank requirement lines and an empty data model", () => {
    const draft = specToDraft(null);
    draft.name = "X";
    draft.backend = "a\n\n  b  \n";
    draft.models = [emptyModel()];
    const payload = draftToPayload(draft);
    expect(payload.backend_requirements).toEqual(["a", "b"]);
    // A new spec is v3: models go in data_models, and an untouched model row is dropped.
    expect(payload.spec_schema_version).toBe(3);
    expect(payload.data_models).toEqual([]);
    expect(payload.data_model).toBeUndefined();
  });

  it("a legacy spec keeps its single data_model shape", () => {
    const payload = draftToPayload(specToDraft(SPEC));
    expect(payload.spec_schema_version).toBe(1);
    expect(payload.data_models).toBeUndefined();
    // Adding a second model switches to the list, even on a legacy spec.
    const draft = specToDraft(SPEC);
    draft.models.push({
      ...emptyModel(),
      name: "Review",
      fields: [{ key: -1, name: "id", type: "integer" }],
    });
    expect(draftToPayload(draft).data_models?.map((m) => m.name)).toEqual([
      "Book",
      "Review",
    ]);
  });
});

describe("specIssues", () => {
  it("accepts a seeded-style spec without errors or warnings", () => {
    expect(specIssues(specToDraft(SPEC), { isNew: false })).toEqual([]);
  });

  it("checks slug shape and collisions only for new specs", () => {
    const draft = specToDraft(SPEC);
    expect(
      errors(
        specIssues(draft, { isNew: true, takenSlugs: ["crud_book_library"] }),
      ),
    ).toContain("Slug already in use");
    draft.slug = "Bad-Slug";
    expect(errors(specIssues(draft, { isNew: true })).join()).toMatch(
      /snake_case/,
    );
    expect(errors(specIssues(draft, { isNew: false }))).toEqual([]);
  });

  it("mirrors the schema's endpoint rules", () => {
    const draft = specToDraft(SPEC);
    const bad = emptyEndpoint("POST");
    bad.path = "/books";
    bad.request = "{not json";
    draft.endpoints.push(bad);
    const msgs = errors(specIssues(draft, { isNew: false }));
    expect(msgs).toEqual([
      "Endpoint 2: path must start with /api/",
      "Endpoint 2: description is required",
      "Endpoint 2: request is not valid JSON",
    ]);
  });

  it("warns that a spec without endpoints cannot be graded", () => {
    const draft = specToDraft(SPEC);
    draft.endpoints = [];
    const issues = specIssues(draft, { isNew: false });
    expect(errors(issues)).toEqual([]);
    expect(issues.some((i) => /cannot be graded/.test(i.message))).toBe(true);
  });

  it("requires a model name once fields exist, and field types", () => {
    const draft = specToDraft(SPEC);
    draft.models[0].name = "";
    draft.models[0].fields[0].type = "";
    expect(errors(specIssues(draft, { isNew: false }))).toEqual([
      "Data model field id: type is required",
      "Data model: name the model",
    ]);
  });
});

describe("JSON mode", () => {
  it("round-trips through draftToJson / jsonToDraft", () => {
    const again = draftToPayload(jsonToDraft(draftToJson(specToDraft(SPEC))));
    expect(again).toEqual(draftToPayload(specToDraft(SPEC)));
  });

  it("refuses non-objects and bad difficulty", () => {
    expect(() => jsonToDraft("[]")).toThrow(/JSON object/);
    expect(() => jsonToDraft("{")).toThrow(/Not valid JSON/);
    expect(() => jsonToDraft('{"difficulty":"hard"}')).toThrow(/difficulty/);
  });
});

describe("specSlugFromName", () => {
  it("makes snake_case starting with a letter", () => {
    expect(specSlugFromName("Chat Messenger 2")).toBe("chat_messenger_2");
    expect(specSlugFromName("  3D Viewer!")).toBe("d_viewer");
  });
});

const V3_SPEC = {
  ...SPEC,
  spec_schema_version: 3,
  backend_requirements: [
    "Access control: anyone may read books; require auth for creating books",
  ],
  api_endpoints: [
    {
      method: "GET",
      path: "/api/books",
      description: "List all books",
      auth: "public",
      query: { query: "Text matched against title and author" },
      request: null,
      response: { items: [{ id: 1 }], total: 1 },
    },
    {
      method: "POST",
      path: "/api/books",
      description: "Create a book",
      auth: "user",
      success_status: 201,
      request: { title: "string" },
      response: { id: 1 },
      notes: "kept as is",
    },
    {
      method: "GET",
      path: "/api/health",
      description: "Health check",
      auth: "public",
      request: null,
      response: { status: "healthy" },
    },
  ],
  data_model: { name: "Book", fields: { id: "integer", title: "string" } },
  data_models: [
    { name: "Book", fields: { id: "integer", title: "string" } },
    {
      name: "Review",
      description: "A rating",
      fields: { id: "integer", rating: "integer (1-5)" },
    },
  ],
} as unknown as AppRequirementTemplate;

describe("schema v3", () => {
  it("round-trips data_models and endpoint auth/query/success_status", () => {
    const payload = draftToPayload(specToDraft(V3_SPEC));
    expect(payload.data_models).toEqual(V3_SPEC.data_models);
    expect(payload.data_model).toBeUndefined();
    expect(payload.api_endpoints).toEqual(V3_SPEC.api_endpoints);
    expect(specIssues(specToDraft(V3_SPEC), { isNew: false })).toEqual([]);
  });

  it("loads a legacy data_model as a one-model list", () => {
    const draft = specToDraft(SPEC);
    expect(draft.models.map((m) => m.name)).toEqual(["Book"]);
    expect(specDataModels(SPEC)).toEqual([SPEC.data_model]);
    expect(specDataModels(V3_SPEC).map((m) => m.name)).toEqual([
      "Book",
      "Review",
    ]);
  });

  it("a new spec starts on v3 with the health endpoint", () => {
    const draft = specToDraft(null);
    expect(draft.schemaVersion).toBe(3);
    expect(draft.endpoints.map((e) => `${e.method} ${e.path}`)).toEqual([
      "GET /api/health",
    ]);
  });

  it("mirrors the v3 conventions", () => {
    const draft = specToDraft(V3_SPEC);
    draft.backend = "Books are public";
    draft.endpoints = draft.endpoints.filter((e) => e.path !== "/api/health");
    draft.endpoints[0].auth = "";
    draft.endpoints[1].successStatus = "500";
    draft.endpoints[1].query = "no description";
    const dup = emptyEndpoint("POST", "user");
    dup.path = "/api/books/";
    dup.description = "Again";
    draft.endpoints.push(dup);
    const admin = emptyEndpoint("GET", "user");
    admin.path = "/api/overview";
    admin.description = "Admin overview";
    draft.adminEndpoints = [admin];
    draft.models[1].name = "Book";
    expect(errors(specIssues(draft, { isNew: false }))).toEqual([
      "Model Book is listed twice",
      "Endpoint 1: choose who may call it (public, user or admin)",
      'Endpoint 2: write each query parameter as "name: what it does"',
      "Endpoint 2: success status must be 200-399",
      "Endpoint 3: POST /api/books/ is already endpoint 2",
      "Admin endpoint 1: admin paths start with /api/admin/",
      "Admin endpoint 1: admin endpoints are admin-only",
      "Add GET /api/health (public): the evaluation polls it",
      'The first backend requirement is "Access control: …" (who may read and write what)',
    ]);
    // The same draft as a legacy spec: only the schema rules.
    draft.schemaVersion = 2;
    expect(errors(specIssues(draft, { isNew: false }))).toEqual([
      "Model Book is listed twice",
      'Endpoint 2: write each query parameter as "name: what it does"',
      "Endpoint 2: success status must be 200-399",
    ]);
  });

  it("JSON mode keeps the v3 shape and checks the schema version", () => {
    const text = draftToJson(specToDraft(V3_SPEC));
    const doc = JSON.parse(text);
    expect(doc.spec_schema_version).toBe(3);
    expect(doc.data_models).toHaveLength(2);
    expect(doc.data_model).toBeUndefined();
    expect(draftToPayload(jsonToDraft(text))).toEqual(
      draftToPayload(specToDraft(V3_SPEC)),
    );
    expect(() => jsonToDraft('{"spec_schema_version": 9}')).toThrow(
      /spec_schema_version/,
    );
    expect(() => jsonToDraft('{"data_models": {}}')).toThrow(/list/);
  });
});
