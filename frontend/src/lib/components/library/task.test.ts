import { describe, expect, it } from "vitest";
import type { LibraryItemOut, TaskSpecContent } from "$lib/api/v3/types";
import crudTodo from "$lib/api/v3/__fixtures__/task-crud_todo_list.json";
import notes from "$lib/api/v3/__fixtures__/task-notes_markdown_render.json";
import {
  draftFromTask,
  emptyTaskContent,
  localTaskIssues,
  newCheckText,
  newEndpoint,
  newProbeText,
  taskFromDraft,
} from "./task";

const FIXTURES = [crudTodo, notes] as unknown as LibraryItemOut<"task">[];

describe("TaskEditor serialization", () => {
  it.each(FIXTURES.map((f) => [f.slug, f] as const))(
    "round-trips the %s contract fixture exactly",
    (_slug, item) => {
      const { content, errors } = taskFromDraft(draftFromTask(item.content));
      expect(errors).toEqual({});
      expect(content).toEqual(item.content);
    },
  );

  it("is stable over repeated round trips", () => {
    const once = taskFromDraft(draftFromTask(FIXTURES[0].content)).content;
    const twice = taskFromDraft(draftFromTask(once)).content;
    expect(twice).toEqual(once);
  });

  it("holds JSON schemas as pretty text and status lists as comma text", () => {
    const content = structuredClone(FIXTURES[0].content);
    content.endpoints[0].request_schema = {
      type: "object",
      required: ["title"],
      properties: { title: { type: "string" } },
    };
    content.endpoints[0].errors = [400, 409];
    const draft = draftFromTask(content);
    expect(draft.endpoints[0].request_schema_text).toContain(
      '\n  "type": "object"',
    );
    expect(draft.endpoints[0].errors_text).toBe("400, 409");
    expect(taskFromDraft(draft).content).toEqual(content);
  });

  it("reports unparsable text per path instead of corrupting the content", () => {
    const draft = draftFromTask(FIXTURES[0].content);
    draft.endpoints[0].request_schema_text = "{ not json";
    draft.endpoints[1].errors_text = "400, teapot";
    draft.acceptance.push("[]");
    draft.seed_data_text = '{"Todo": {"title": "x"}}';
    const { errors } = taskFromDraft(draft);
    expect(errors["endpoints.0.request_schema"]).toMatch(/Invalid JSON/);
    expect(errors["endpoints.1.errors"]).toMatch(/teapot/);
    expect(errors[`acceptance.${draft.acceptance.length - 1}`]).toMatch(
      /JSON object/,
    );
    expect(errors.seed_data).toMatch(/list of rows/);
  });

  it("maps blank optional text to null and bare defaults to strings", () => {
    const draft = draftFromTask(FIXTURES[0].content);
    const field = draft.entities[0].fields[0];
    field.default_text = "medium";
    field.enum_text = "";
    field.ref = "  ";
    const out = taskFromDraft(draft).content.entities[0].fields[0];
    expect(out.default).toBe("medium");
    expect(out.enum).toBeNull();
    expect(out.ref).toBeNull();
    field.default_text = "5";
    expect(taskFromDraft(draft).content.entities[0].fields[0].default).toBe(5);
  });

  it("drops empty prose variants and requires at least one", () => {
    const draft = draftFromTask(FIXTURES[0].content);
    draft.prose.minimal = "  ";
    const { content } = taskFromDraft(draft);
    expect(Object.keys(content.prose).sort()).toEqual([
      "exhaustive",
      "standard",
    ]);
    draft.prose = { minimal: "", standard: "", exhaustive: "" };
    expect(taskFromDraft(draft).errors.prose).toBeTruthy();
  });

  it("keeps the auth flow's register alias and clears it when disabled", () => {
    const content: TaskSpecContent = {
      ...emptyTaskContent(),
      title: "t",
      summary: "s",
      category: "c",
      prose: { standard: "x" },
      auth_flow: {
        register: "auth.register",
        login: "auth.login",
        logout: null,
        credential: "cookie",
        token_json_path: null,
        csrf: "header_from_endpoint",
        csrf_endpoint: "auth.csrf",
      },
    };
    expect(taskFromDraft(draftFromTask(content)).content.auth_flow).toEqual(
      content.auth_flow,
    );
    const draft = draftFromTask(content);
    draft.auth_flow.enabled = false;
    expect(taskFromDraft(draft).content.auth_flow).toBeNull();
  });

  it("new rows serialize into valid-looking content", () => {
    const draft = draftFromTask({
      ...emptyTaskContent(),
      title: "t",
      summary: "s",
      category: "c",
      prose: { standard: "x" },
    });
    draft.endpoints.push({
      ...newEndpoint(),
      id: "items.list",
      path: "/api/items",
    });
    draft.acceptance.push(newCheckText("items.list"));
    draft.probes.push(newProbeText("items.list"));
    const { content, errors } = taskFromDraft(draft);
    expect(errors).toEqual({});
    expect(content.acceptance[0].endpoint).toBe("items.list");
    expect(content.probes[0].kind).toBe("sqli_boolean");
    expect(localTaskIssues(content)).toEqual([]);
  });

  it("flags cross-reference problems locally", () => {
    const content = structuredClone(FIXTURES[0].content);
    expect(localTaskIssues(content)).toEqual([]);
    content.endpoints[1].id = content.endpoints[0].id;
    content.ui.push({
      id: "ui.ghost",
      text: "x",
      endpoints: ["ghost.endpoint"],
    });
    const owner = content.endpoints.findIndex((e) => e.auth === "owner");
    content.endpoints[owner].owner_entity = null;
    const paths = localTaskIssues(content).map((i) => i.path);
    expect(paths).toContain("endpoints.1.id");
    expect(paths).toContain(`ui.${content.ui.length - 1}.endpoints`);
    expect(paths).toContain(`endpoints.${owner}.owner_entity`);
  });
});
