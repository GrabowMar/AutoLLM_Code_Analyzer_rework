import { describe, expect, it } from "vitest";
import {
  LOCATION_EXAMPLES,
  LOCATION_PLACEHOLDER,
  draftErrors,
  draftToInput,
  draftToPatch,
  emptyDraft,
  imageFileIssue,
  locationHref,
  locationIssue,
  uploadName,
} from "./observations";

describe("locations", () => {
  // C27: the placeholder must only show values the field accepts.
  it("shows only examples that pass validation", () => {
    expect(LOCATION_EXAMPLES.length).toBeGreaterThan(0);
    for (const example of LOCATION_EXAMPLES)
      expect(locationIssue(example)).toBeNull();
    expect(LOCATION_PLACEHOLDER).not.toMatch(/\b(GET|POST|PUT|DELETE)\b/);
    expect(locationIssue("GET /api/items/3")).not.toBeNull();
  });

  it("accepts relative paths and http(s) URLs", () => {
    expect(locationIssue("")).toBeNull();
    expect(locationIssue("/admin/users")).toBeNull();
    expect(locationIssue("/search?q=1")).toBeNull();
    expect(locationIssue("https://llm-1.apps.example/x")).toBeNull();
    expect(locationIssue("http://localhost:3000/")).toBeNull();
  });

  it("rejects scripts, other schemes and protocol-relative URLs", () => {
    expect(locationIssue("javascript:alert(1)")).not.toBeNull();
    expect(locationIssue("data:text/html,hi")).not.toBeNull();
    expect(locationIssue("//evil.example")).not.toBeNull();
    expect(locationIssue("admin/users")).not.toBeNull();
    expect(locationIssue("/a b")).not.toBeNull();
    expect(locationIssue(`/${"a".repeat(500)}`)).not.toBeNull();
  });

  it("links only absolute http(s) locations", () => {
    expect(locationHref("https://x.example/a")).toBe("https://x.example/a");
    expect(locationHref("/admin")).toBeNull();
    expect(locationHref("javascript:alert(1)")).toBeNull();
    expect(locationHref("")).toBeNull();
  });
});

describe("drafts", () => {
  it("needs a title and checks field rules", () => {
    const draft = emptyDraft("vulnerability", "single", "sec");
    expect(draftErrors(draft)).toEqual({ title: "Give it a short title." });
    expect(
      draftErrors({
        ...draft,
        title: "XSS",
        location: "javascript:x",
        cwe: "XSS-1",
      }),
    ).toEqual({
      location: expect.any(String),
      cwe: "Use a CWE id such as CWE-79.",
    });
    expect(draftErrors({ ...draft, title: "XSS", cwe: "79" })).toEqual({});
  });

  it("builds trimmed payloads with kind-specific fields", () => {
    const vuln = {
      ...emptyDraft("vulnerability", "single", "sec"),
      title: "  Stored XSS ",
      cwe: "79",
      checklist_item_id: "xss",
      severity: "high" as const,
    };
    expect(draftToInput(vuln)).toMatchObject({
      slot: "single",
      kind: "vulnerability",
      title: "Stored XSS",
      cwe: "CWE-79",
      checklist_item_id: "xss",
      severity: "high",
    });
    const bug = {
      ...emptyDraft("bug", "B", "bugs"),
      title: "Crash",
      cwe: "79",
      checklist_item_id: "x",
    };
    expect(draftToInput(bug)).toMatchObject({
      cwe: "",
      checklist_item_id: "",
      slot: "B",
    });
    expect(draftToPatch(bug)).not.toHaveProperty("slot");
  });
});

describe("screenshots", () => {
  it("accepts small images only", () => {
    expect(
      imageFileIssue({ type: "image/png", size: 1000, name: "a.png" }),
    ).toBeNull();
    expect(imageFileIssue({ type: "image/webp", size: 1000 })).toBeNull();
    expect(
      imageFileIssue({ type: "image/svg+xml", size: 10, name: "a.svg" }),
    ).toMatch(/not a PNG/);
    expect(
      imageFileIssue({ type: "application/pdf", size: 10, name: "a.pdf" }),
    ).toMatch(/not a PNG/);
    expect(
      imageFileIssue({
        type: "image/png",
        size: 6 * 1024 * 1024,
        name: "big.png",
      }),
    ).toMatch(/5 MiB/);
    expect(
      imageFileIssue({ type: "image/png", size: 0, name: "e.png" }),
    ).toMatch(/empty/);
  });

  it("names pasted screenshots", () => {
    expect(uploadName({ name: "image.png", type: "image/png" }, 1)).toBe(
      "screenshot-2.png",
    );
    expect(uploadName({ name: "", type: "image/jpeg" })).toBe(
      "screenshot-1.jpg",
    );
    expect(uploadName({ name: "login.webp", type: "image/webp" })).toBe(
      "login.webp",
    );
  });
});
