import { describe, expect, it } from "vitest";
import { resolveStudyTab, tabsFor } from "./tabs";

describe("study tabs", () => {
  it("numbers the shown tabs without gaps", () => {
    expect(tabsFor("app").map((t) => `${t.number} ${t.label}`)).toEqual([
      "1 Abstract",
      "2 Method",
      "3 Results",
      "4 Data",
      "5 Operations",
    ]);
    expect(tabsFor("slot").map((t) => t.value)).toContain("patterns");
  });

  it("keeps the old names as aliases", () => {
    expect(resolveStudyTab("design", "app")).toBe("method");
    expect(resolveStudyTab("plan", "app")).toBe("method");
    expect(resolveStudyTab("monitor", "app")).toBe("operations");
    expect(resolveStudyTab("budget", "slot")).toBe("operations");
    expect(resolveStudyTab("overview", "app")).toBe("summary");
  });

  it("rejects unknown tabs and Patterns on app studies", () => {
    expect(resolveStudyTab("nope", "app")).toBeNull();
    expect(resolveStudyTab("patterns", "app")).toBeNull();
    expect(resolveStudyTab("patterns", "slot")).toBe("patterns");
    expect(resolveStudyTab(null)).toBeNull();
  });
});
