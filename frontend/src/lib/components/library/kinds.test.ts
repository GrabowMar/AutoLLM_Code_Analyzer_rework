import { describe, expect, it } from "vitest";
import {
  LIBRARY_API_KINDS,
  LIBRARY_KIND_API,
  parseRef,
  shortRef,
} from "$lib/api/v3/types";
import {
  ALL_KINDS,
  KIND_GROUPS,
  KIND_INFO,
  canEdit,
  kindInfo,
  libraryHref,
} from "./kinds";
import { isValidSlug, slugify } from "./slugify";

describe("library kinds", () => {
  it("covers every API kind exactly once in the nav groups", () => {
    expect([...ALL_KINDS].sort()).toEqual(
      Object.values(LIBRARY_API_KINDS).sort(),
    );
    expect(new Set(KIND_GROUPS.flatMap((g) => g.kinds)).size).toBe(
      ALL_KINDS.length,
    );
    for (const kind of ALL_KINDS)
      expect(LIBRARY_API_KINDS[KIND_INFO[kind].api]).toBe(kind);
  });

  it("calls a resource policy a Budget preset and keeps starters and suites read-only", () => {
    expect(kindInfo("resource-policies").label).toBe("Budget preset");
    expect(KIND_INFO.starter.editing).toBe("readonly");
    expect(KIND_INFO.eval_suite.editing).toBe("readonly");
  });

  it("builds Library links from either kind name", () => {
    expect(libraryHref("prompt_set")).toBe("/library/prompt-sets");
    expect(libraryHref("prompt-sets", "app-default", 2)).toBe(
      "/library/prompt-sets/app-default?v=2",
    );
    expect(LIBRARY_KIND_API.sampling).toBe("sampling-presets");
  });

  it("lets owners and staff edit, never others", () => {
    const own = { is_system: false, created_by: { id: 7, username: "a" } };
    expect(canEdit(own, { id: 7, is_staff: false })).toBe(true);
    expect(canEdit(own, { id: 8, is_staff: false })).toBe(false);
    expect(
      canEdit(
        { is_system: true, created_by: null },
        { id: 8, is_staff: false },
      ),
    ).toBe(false);
    expect(
      canEdit({ is_system: true, created_by: null }, { id: 8, is_staff: true }),
    ).toBe(true);
    expect(canEdit(own, null)).toBe(false);
  });

  it("parses pinned refs and slugs", () => {
    expect(parseRef("crud_todo_list@2#0123456789ab")).toEqual({
      slug: "crud_todo_list",
      version: 2,
      hash12: "0123456789ab",
    });
    expect(parseRef("crud_todo_list@0")).toBeNull();
    expect(shortRef("x@3#0123456789ab")).toBe("x@3");
    expect(slugify("  Todo List (copy) ")).toBe("todo-list-copy");
    expect(isValidSlug("todo_list-2")).toBe(true);
    expect(isValidSlug("-bad")).toBe(false);
  });
});
