import { describe, expect, it } from "vitest";
import type { AppRequirementTemplate } from "$lib/api/generation";
import {
  draftToJson,
  draftToPayload,
  emptyEndpoint,
  jsonToDraft,
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
    const payload = draftToPayload(draft);
    expect(payload.backend_requirements).toEqual(["a", "b"]);
    expect(payload.data_model).toEqual({});
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
    draft.modelName = "";
    draft.fields[0].type = "";
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
