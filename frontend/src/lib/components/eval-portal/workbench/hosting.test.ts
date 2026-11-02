import { describe, expect, it } from "vitest";
import {
  IFRAME_SANDBOX,
  canRequestStart,
  frameDecision,
  hostingSentence,
  idleHosting,
  safeHttpUrl,
  shouldAutoEnsure,
  shouldPollHosting,
  slotsToPoll,
} from "./hosting";

const PORTAL = "https://lab.example.org";

describe("frameDecision", () => {
  it("frames https apps on another origin", () => {
    expect(frameDecision("https://llm-ab12.apps.example.org/", PORTAL)).toEqual(
      {
        canFrame: true,
        canOpen: true,
        url: "https://llm-ab12.apps.example.org/",
        reason: "",
      },
    );
  });

  it("only opens plain http or same-origin apps in a new tab", () => {
    const http = frameDecision("http://llm-ab12.apps.example.org/", PORTAL);
    expect(http.canFrame).toBe(false);
    expect(http.canOpen).toBe(true);
    expect(http.reason).toMatch(/plain HTTP/);
    const same = frameDecision(
      "https://lab.example.org/apps/llm-ab12/",
      PORTAL,
    );
    expect(same.canFrame).toBe(false);
    expect(same.canOpen).toBe(true);
  });

  it("refuses anything that is not an absolute http(s) URL", () => {
    for (const bad of [
      "javascript:alert(1)",
      "data:text/html,x",
      "/apps/x",
      "",
      null,
      undefined,
    ]) {
      const decision = frameDecision(bad, PORTAL);
      expect(decision.canFrame).toBe(false);
      expect(decision.canOpen).toBe(false);
      expect(decision.url).toBeNull();
    }
  });

  it("keeps top navigation out of the sandbox", () => {
    expect(IFRAME_SANDBOX).not.toMatch(/top-navigation/);
    expect(IFRAME_SANDBOX.split(" ")).toEqual([
      "allow-scripts",
      "allow-same-origin",
      "allow-forms",
      "allow-popups",
      "allow-popups-to-escape-sandbox",
      "allow-modals",
      "allow-downloads",
    ]);
  });
});

describe("hosting state", () => {
  it("polls until running, failed or unavailable", () => {
    expect(shouldPollHosting("queued")).toBe(true);
    expect(shouldPollHosting("building")).toBe(true);
    expect(shouldPollHosting("idle")).toBe(true);
    expect(shouldPollHosting("running")).toBe(false);
    expect(shouldPollHosting("failed")).toBe(false);
    expect(shouldPollHosting("unavailable")).toBe(false);
  });

  // C06/C25: the repeating poll never (re)starts an idle app by itself.
  it("auto-ensures only transitional states", () => {
    for (const s of ["queued", "building", "starting", "stopping"] as const) {
      expect(shouldAutoEnsure(s)).toBe(true);
    }
    for (const s of [
      "idle",
      "running",
      "failed",
      "unavailable",
      null,
      undefined,
    ] as const) {
      expect(shouldAutoEnsure(s)).toBe(false);
    }
    // ...while the one-shot ensure on first open still starts an idle app.
    expect(shouldPollHosting("idle")).toBe(true);
  });

  it("polls nothing while the tab is hidden", () => {
    const states = {
      A: { ...idleHosting(), state: "building" as const },
      B: idleHosting(),
    };
    expect(slotsToPoll(["A", "B"], states, true)).toEqual(["A"]);
    expect(slotsToPoll(["A", "B"], states, false)).toEqual([]);
    expect(
      slotsToPoll(
        ["A", "B"],
        { B: { ...idleHosting(), state: "stopping" } },
        true,
      ),
    ).toEqual(["B"]);
  });

  it("offers a start only when idle or failed", () => {
    expect(canRequestStart("idle")).toBe(true);
    expect(canRequestStart("failed")).toBe(true);
    expect(canRequestStart("queued")).toBe(false);
  });

  it("prefers the server's message", () => {
    expect(
      hostingSentence({
        ...idleHosting(),
        state: "queued",
        message: "2nd in line",
      }),
    ).toBe("2nd in line");
    expect(
      hostingSentence({
        ...idleHosting(),
        state: "failed",
        error: "build failed",
      }),
    ).toBe("build failed");
    expect(hostingSentence(idleHosting())).toBe("The app is not running.");
  });

  it("accepts only http(s) URLs", () => {
    expect(safeHttpUrl("https://x.example/a")).toBe("https://x.example/a");
    expect(safeHttpUrl("javascript:void(0)")).toBeNull();
    expect(safeHttpUrl("not a url")).toBeNull();
  });
});
