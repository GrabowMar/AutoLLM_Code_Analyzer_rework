import { afterEach, beforeEach, expect, it, vi } from "vitest";

const person = (id: number, email: string) => ({
  id,
  email,
  name: "",
  display_name: email,
});
const workspace = {
  coauthorship_id: 5,
  owner: person(1, "owner@example.org"),
  owner_is_staff: true,
  joined_at: "2026-11-19T10:00:00.000Z",
  last_active_at: null,
};

let fetchMock: ReturnType<typeof vi.fn>;
let assign: ReturnType<typeof vi.fn>;
let session: Map<string, string>;
let channels: Array<{ listener?: () => void }>;

beforeEach(() => {
  fetchMock = vi.fn();
  assign = vi.fn();
  channels = [];
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("document", { cookie: "csrftoken=csrf-1" });
  vi.stubGlobal("window", {
    location: { assign, href: "", pathname: "/reports", search: "" },
    addEventListener: () => {},
    removeEventListener: () => {},
  });
  // One channel per tab; posting reaches the other tabs' channels.
  vi.stubGlobal(
    "BroadcastChannel",
    class {
      listener?: () => void;
      constructor() {
        channels.push(this);
      }
      postMessage() {
        channels.filter((c) => c !== this).forEach((c) => c.listener?.());
      }
      addEventListener(_t: string, l: () => void) {
        this.listener = l;
      }
      removeEventListener() {
        this.listener = undefined;
      }
    },
  );
  vi.stubGlobal("localStorage", { setItem: () => {} });
  session = new Map();
  vi.stubGlobal("sessionStorage", {
    getItem: (k: string) => session.get(k) ?? null,
    setItem: (k: string, v: string) => void session.set(k, v),
    removeItem: (k: string) => void session.delete(k),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

const store = async () => (await import("./workspace.svelte")).getWorkspace();

it("maps the context payload", async () => {
  fetchMock.mockResolvedValueOnce(
    Response.json({
      actor: person(2, "member@example.org"),
      acting_as: workspace,
      workspaces: [workspace],
    }),
  );
  const ws = await store();
  expect(ws.loaded).toBe(false);
  await ws.load();
  expect(ws.loaded).toBe(true);
  expect(ws.actor?.email).toBe("member@example.org");
  expect(ws.actingAs?.owner.email).toBe("owner@example.org");
  expect(ws.workspaces).toHaveLength(1);
});

it("shares one request between concurrent loads", async () => {
  fetchMock.mockResolvedValue(
    Response.json({ actor: person(2, "m@x"), acting_as: null, workspaces: [] }),
  );
  const ws = await store();
  await Promise.all([ws.load(), ws.load()]);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("a failed load leaves a safe empty state", async () => {
  fetchMock.mockResolvedValueOnce(
    Response.json({ detail: "boom" }, { status: 500 }),
  );
  const ws = await store();
  await ws.load();
  expect(ws.loaded).toBe(true);
  expect(ws.actingAs).toBeNull();
  expect(ws.workspaces).toEqual([]);
});

it("a network failure leaves a safe empty state", async () => {
  fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
  const ws = await store();
  await ws.load();
  expect(ws.loaded).toBe(true);
  expect(ws.actingAs).toBeNull();
});

it("switchTo posts the choice, then reloads the app", async () => {
  fetchMock.mockResolvedValueOnce(
    Response.json({ actor: person(2, "m@x"), acting_as: null, workspaces: [] }),
  );
  const ws = await store();
  await ws.switchTo(null);
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(url).toBe("/api/coauthors/workspace/");
  expect(init.method).toBe("POST");
  expect(JSON.parse(String(init.body))).toEqual({ coauthorship_id: null });
  expect(assign).toHaveBeenCalledWith("/");
});

it("switchTo does not reload when the switch fails", async () => {
  fetchMock.mockResolvedValueOnce(
    Response.json(
      { detail: "Workspace not found", code: "not_found", errors: [] },
      { status: 404 },
    ),
  );
  const ws = await store();
  await expect(ws.switchTo(9)).rejects.toMatchObject({ code: "not_found" });
  expect(assign).not.toHaveBeenCalled();
});

const contextBody = (
  actingAs: typeof workspace | null,
  actorId = 2,
  workspaces = actingAs ? [workspace] : [],
) =>
  Response.json({
    actor: person(actorId, "member@example.org"),
    acting_as: actingAs,
    workspaces,
  });

async function freshStore() {
  vi.resetModules();
  return store();
}

it("notices that access ended when the tab was in a workspace", async () => {
  fetchMock.mockResolvedValueOnce(contextBody(workspace));
  await (await freshStore()).load();
  // Next page load: the server already ended the selection.
  fetchMock.mockResolvedValueOnce(contextBody(null));
  const ws = await freshStore();
  await ws.load();
  expect(ws.consumeEnded()).toBe(true);
  expect(ws.consumeEnded()).toBe(false);
});

it("does not report an ending after switching to the own account", async () => {
  fetchMock.mockResolvedValueOnce(contextBody(workspace));
  const ws = await freshStore();
  await ws.load();
  fetchMock.mockResolvedValueOnce(contextBody(null));
  await ws.switchTo(null);
  fetchMock.mockResolvedValueOnce(contextBody(null));
  const next = await freshStore();
  await next.load();
  expect(next.consumeEnded()).toBe(false);
});

it("does not report an ending when another tab switched away", async () => {
  fetchMock.mockResolvedValueOnce(contextBody(workspace));
  await (await freshStore()).load();
  // The workspace is still usable; this person just chose their own account elsewhere.
  fetchMock.mockResolvedValueOnce(contextBody(null, 2, [workspace]));
  const ws = await freshStore();
  await ws.load();
  expect(ws.consumeEnded()).toBe(false);
});

it("does not report an ending to a different person in the same tab", async () => {
  fetchMock.mockResolvedValueOnce(contextBody(workspace, 2));
  await (await freshStore()).load();
  fetchMock.mockResolvedValueOnce(contextBody(null, 3));
  const ws = await freshStore();
  await ws.load();
  expect(ws.consumeEnded()).toBe(false);
});

it("consumes the flag apiFetch sets on a 409 workspace_ended", async () => {
  session.set("coauthor.ended", "1");
  const ws = await store();
  expect(ws.consumeEnded()).toBe(true);
  expect(session.has("coauthor.ended")).toBe(false);
});

it("reset forgets the loaded context", async () => {
  fetchMock.mockResolvedValueOnce(contextBody(workspace));
  const ws = await store();
  await ws.load();
  ws.reset();
  expect(ws.loaded).toBe(false);
  expect(ws.actor).toBeNull();
  expect(ws.actingAs).toBeNull();
  expect(ws.workspaces).toEqual([]);
});

const headersOf = (i: number) =>
  ((fetchMock.mock.calls[i] as [string, RequestInit])[1].headers ??
    {}) as Record<string, string>;

it("changing requests say which workspace this tab shows", async () => {
  fetchMock.mockResolvedValueOnce(contextBody(workspace));
  const ws = await freshStore();
  await ws.load();
  const { apiFetch } = await import("$lib/api/core");
  fetchMock.mockResolvedValue(Response.json({}));
  await apiFetch("/reports/", { method: "POST", body: "{}" });
  await apiFetch("/reports/");
  expect(headersOf(1)["X-Workspace"]).toBe("5");
  expect(headersOf(2)["X-Workspace"]).toBeUndefined(); // reads are not checked

  fetchMock.mockResolvedValueOnce(contextBody(null));
  ws.reset();
  await apiFetch("/reports/", { method: "DELETE" });
  expect(headersOf(3)["X-Workspace"]).toBeUndefined(); // unknown: no claim
  fetchMock.mockResolvedValueOnce(contextBody(null));
  await ws.load();
  fetchMock.mockResolvedValue(Response.json({}));
  await apiFetch("/reports/", { method: "PATCH", body: "{}" });
  expect(headersOf(5)["X-Workspace"]).toBe("own");
});

it("a failed load sends no claim", async () => {
  fetchMock.mockResolvedValueOnce(Response.json({}, { status: 500 }));
  const ws = await freshStore();
  await ws.load();
  const { apiFetch } = await import("$lib/api/core");
  fetchMock.mockResolvedValueOnce(Response.json({}));
  await apiFetch("/reports/", { method: "POST", body: "{}" });
  expect(headersOf(1)["X-Workspace"]).toBeUndefined();
});

it("a switch in another tab reloads this one", async () => {
  const ws = await freshStore();
  const stop = ws.watchOtherTabs();
  // Another tab of the same browser switches workspace.
  fetchMock.mockResolvedValueOnce(contextBody(null));
  const { switchWorkspace } = await import("$lib/api/coauthors");
  const otherTab = new (globalThis.BroadcastChannel as unknown as new () => {
    postMessage: () => void;
  })();
  await switchWorkspace(null);
  expect(assign).not.toHaveBeenCalled(); // this module's own channel never hears itself
  otherTab.postMessage();
  expect(assign).toHaveBeenCalledWith("/reports");
  stop();
});
