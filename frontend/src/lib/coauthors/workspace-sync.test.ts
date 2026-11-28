import { afterEach, beforeEach, expect, it, vi } from "vitest";

/** In-memory BroadcastChannel: a message reaches every other channel of the same name. */
class FakeChannel {
  static all: FakeChannel[] = [];
  listeners = new Set<(event: { data: unknown }) => void>();
  constructor(public name: string) {
    FakeChannel.all.push(this);
  }
  postMessage(data: unknown) {
    for (const other of FakeChannel.all) {
      if (other !== this && other.name === this.name) {
        other.listeners.forEach((listener) => listener({ data }));
      }
    }
  }
  addEventListener(
    _type: string,
    listener: (event: { data: unknown }) => void,
  ) {
    this.listeners.add(listener);
  }
  removeEventListener(
    _type: string,
    listener: (event: { data: unknown }) => void,
  ) {
    this.listeners.delete(listener);
  }
}

let assign: ReturnType<typeof vi.fn>;
let local: Map<string, string>;
let storageListeners: Set<(event: { key: string | null }) => void>;

beforeEach(() => {
  FakeChannel.all = [];
  assign = vi.fn();
  local = new Map();
  storageListeners = new Set();
  vi.stubGlobal("BroadcastChannel", FakeChannel);
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => local.get(k) ?? null,
    setItem: (k: string, v: string) => void local.set(k, v),
    removeItem: (k: string) => void local.delete(k),
  });
  vi.stubGlobal("window", {
    location: { assign, pathname: "/experiments", search: "?page=2" },
    addEventListener: (
      _t: string,
      l: (event: { key: string | null }) => void,
    ) => storageListeners.add(l),
    removeEventListener: (
      _t: string,
      l: (event: { key: string | null }) => void,
    ) => storageListeners.delete(l),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

const sync = () => import("./workspace-sync");
/** What another tab of the same browser does when it switches. */
const otherTabSwitches = () =>
  new FakeChannel("coauthor-workspace").postMessage({ type: "changed" });

it("maps the workspace a tab shows to the X-Workspace value", async () => {
  const { setWorkspaceClaim, workspaceClaim } = await sync();
  expect(workspaceClaim()).toBeNull();
  setWorkspaceClaim(12);
  expect(workspaceClaim()).toBe("12");
  setWorkspaceClaim(null);
  expect(workspaceClaim()).toBe("own");
  setWorkspaceClaim(undefined);
  expect(workspaceClaim()).toBeNull();
});

it("a switch in another tab calls the handler once, however it arrives", async () => {
  const { onWorkspaceChange } = await sync();
  const handler = vi.fn();
  onWorkspaceChange(handler);
  otherTabSwitches();
  storageListeners.forEach((l) => l({ key: "coauthor.workspace.changed" }));
  expect(handler).toHaveBeenCalledTimes(1);
});

it("the storage event alone is enough (no BroadcastChannel)", async () => {
  vi.stubGlobal("BroadcastChannel", undefined);
  const { onWorkspaceChange } = await sync();
  const handler = vi.fn();
  onWorkspaceChange(handler);
  storageListeners.forEach((l) => l({ key: "unrelated" }));
  expect(handler).not.toHaveBeenCalled();
  storageListeners.forEach((l) => l({ key: "coauthor.workspace.changed" }));
  expect(handler).toHaveBeenCalledTimes(1);
});

it("unsubscribing stops listening", async () => {
  const { onWorkspaceChange } = await sync();
  const handler = vi.fn();
  onWorkspaceChange(handler)();
  otherTabSwitches();
  expect(handler).not.toHaveBeenCalled();
  expect(storageListeners.size).toBe(0);
});

it("announcing reaches other tabs, not this one", async () => {
  const { announceWorkspaceChange, onWorkspaceChange } = await sync();
  const here = vi.fn();
  onWorkspaceChange(here);
  const elsewhere = vi.fn();
  new FakeChannel("coauthor-workspace").addEventListener("message", elsewhere);
  announceWorkspaceChange();
  expect(elsewhere).toHaveBeenCalledTimes(1);
  expect(here).not.toHaveBeenCalled();
  expect(local.has("coauthor.workspace.changed")).toBe(true);
});

it("announcing never throws, even with blocked storage", async () => {
  vi.stubGlobal("localStorage", {
    setItem: () => {
      throw new Error("blocked");
    },
  });
  const { announceWorkspaceChange } = await sync();
  expect(() => announceWorkspaceChange()).not.toThrow();
});

it("reloadHere loads the current page again", async () => {
  (await sync()).reloadHere();
  expect(assign).toHaveBeenCalledWith("/experiments?page=2");
});
