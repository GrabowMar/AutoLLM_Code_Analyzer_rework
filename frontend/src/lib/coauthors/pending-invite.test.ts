import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearInvite,
  inviteFromHash,
  isFramed,
  pendingInvite,
  rememberInvite,
} from "./pending-invite";

const TOKEN = `coi_${"a".repeat(41)}-_`;
const KEY = "coauthor.pendingInvite";
const DAY = 24 * 60 * 60 * 1000;

let store: Map<string, string>;

beforeEach(() => {
  store = new Map();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("pending invite (F01)", () => {
  it("round-trips a token", () => {
    rememberInvite(TOKEN);
    expect(pendingInvite()).toBe(TOKEN);
    clearInvite();
    expect(pendingInvite()).toBeNull();
    expect(store.has(KEY)).toBe(false);
  });

  it("never stores a malformed value", () => {
    rememberInvite("coi_short");
    rememberInvite(`${TOKEN}x`);
    expect(store.has(KEY)).toBe(false);
  });

  it("drops malformed stored values", () => {
    for (const bad of [
      "not json",
      "null",
      JSON.stringify({ t: "coi_short", at: Date.now() }),
      JSON.stringify({ t: TOKEN }),
      JSON.stringify({ t: TOKEN, at: "yesterday" }),
      JSON.stringify([TOKEN]),
    ]) {
      store.set(KEY, bad);
      expect(pendingInvite()).toBeNull();
      expect(store.has(KEY)).toBe(false);
    }
  });

  it("drops a value older than 24 hours, or from the future", () => {
    const now = 1_800_000_000_000;
    rememberInvite(TOKEN, now - DAY + 1000);
    expect(pendingInvite(now)).toBe(TOKEN);
    rememberInvite(TOKEN, now - DAY);
    expect(pendingInvite(now)).toBeNull();
    expect(store.has(KEY)).toBe(false);
    rememberInvite(TOKEN, now + 60_000);
    expect(pendingInvite(now)).toBeNull();
  });

  it("survives storage that throws", () => {
    const boom = () => {
      throw new DOMException("blocked", "SecurityError");
    };
    vi.stubGlobal("localStorage", {
      getItem: boom,
      setItem: boom,
      removeItem: boom,
    });
    expect(() => rememberInvite(TOKEN)).not.toThrow();
    expect(pendingInvite()).toBeNull();
    expect(() => clearInvite()).not.toThrow();
  });

  it("survives a missing localStorage", () => {
    vi.stubGlobal("localStorage", undefined);
    expect(() => rememberInvite(TOKEN)).not.toThrow();
    expect(pendingInvite()).toBeNull();
  });
});

describe("inviteFromHash", () => {
  it("accepts #coi_… and a bare token", () => {
    expect(inviteFromHash(`#${TOKEN}`)).toBe(TOKEN);
    expect(inviteFromHash(TOKEN)).toBe(TOKEN);
  });

  it("rejects everything else", () => {
    for (const hash of [
      "",
      "#",
      "#coauthors",
      `#${TOKEN}x`,
      `#${TOKEN.slice(0, -1)}`,
      `#${TOKEN.replace("coi_", "cox_")}`,
      `#${TOKEN.slice(0, -1)}=`,
      `#${encodeURIComponent(TOKEN)}%20`,
      `##${TOKEN}`,
    ]) {
      expect(inviteFromHash(hash)).toBeNull();
    }
  });
});

describe("isFramed", () => {
  it("is false for a top-level page", () => {
    const self = {};
    vi.stubGlobal("window", Object.assign(self, { top: self, self }));
    expect(isFramed()).toBe(false);
  });

  it("is true inside a frame", () => {
    const self = {};
    vi.stubGlobal("window", Object.assign(self, { top: {}, self }));
    expect(isFramed()).toBe(true);
  });

  it("is true when the top window cannot be read", () => {
    const self = {};
    Object.defineProperty(self, "top", {
      get() {
        throw new DOMException("cross-origin", "SecurityError");
      },
    });
    vi.stubGlobal("window", Object.assign(self, { self }));
    expect(isFramed()).toBe(true);
  });
});
