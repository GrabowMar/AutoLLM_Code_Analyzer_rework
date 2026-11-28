import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const TOKEN = `coi_${"A".repeat(40)}x-_`;

let fetchMock: ReturnType<typeof vi.fn>;
let assign: ReturnType<typeof vi.fn>;
let session: Map<string, string>;
let posted: unknown[];

beforeEach(() => {
  fetchMock = vi.fn();
  assign = vi.fn();
  session = new Map();
  posted = [];
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("document", { cookie: "csrftoken=csrf-1" });
  vi.stubGlobal("window", {
    location: { assign, href: "", pathname: "/studies/7", search: "?tab=runs" },
  });
  vi.stubGlobal(
    "BroadcastChannel",
    class {
      postMessage(data: unknown) {
        posted.push(data);
      }
      addEventListener() {}
      removeEventListener() {}
    },
  );
  vi.stubGlobal("localStorage", { setItem: () => {} });
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

function respond(body: unknown, status = 200) {
  fetchMock.mockResolvedValueOnce(
    status === 204
      ? new Response(null, { status })
      : Response.json(body, { status }),
  );
}

function call(i = 0) {
  const [url, init] = fetchMock.mock.calls[i] as [string, RequestInit];
  const headers = (init.headers ?? {}) as Record<string, string>;
  return {
    url,
    method: (init.method ?? "GET").toUpperCase(),
    body: init.body ? JSON.parse(String(init.body)) : undefined,
    csrf: headers["X-CSRFToken"],
  };
}

const api = () => import("./coauthors");

describe("requests", () => {
  it("reads the context", async () => {
    respond({ actor: {}, acting_as: null, workspaces: [] });
    await (await api()).getCoauthorContext();
    expect(call()).toMatchObject({
      url: "/api/coauthors/context/",
      method: "GET",
    });
  });

  it("switches workspace, and back to the own account with null", async () => {
    const { switchWorkspace } = await api();
    respond({});
    respond({});
    await switchWorkspace(7);
    await switchWorkspace(null);
    expect(call(0)).toEqual({
      url: "/api/coauthors/workspace/",
      method: "POST",
      body: { coauthorship_id: 7 },
      csrf: "csrf-1",
    });
    expect(call(1).body).toEqual({ coauthorship_id: null });
  });

  it("previews and accepts with the token in the body only (F02)", async () => {
    const { previewInvite, acceptInvite } = await api();
    respond({ state: "valid" });
    respond({ status: "joined", context: {} });
    await previewInvite(TOKEN);
    await acceptInvite(TOKEN);
    expect(call(0)).toEqual({
      url: "/api/coauthors/join/preview/",
      method: "POST",
      body: { token: TOKEN },
      csrf: "csrf-1",
    });
    expect(call(1)).toEqual({
      url: "/api/coauthors/join/accept/",
      method: "POST",
      body: { token: TOKEN },
      csrf: "csrf-1",
    });
    for (const [url, init] of fetchMock.mock.calls as [string, RequestInit][]) {
      expect(url).not.toContain(TOKEN);
      expect(JSON.stringify(init.headers)).not.toContain(TOKEN);
    }
  });

  it("fetches the CSRF cookie before a signed-out preview", async () => {
    vi.stubGlobal("document", { cookie: "" });
    fetchMock.mockImplementationOnce(async () => {
      vi.stubGlobal("document", { cookie: "csrftoken=fresh" });
      return Response.json({});
    });
    respond({ state: "valid" });
    await (await api()).previewInvite(TOKEN);
    expect(call(0).url).toBe("/_allauth/browser/v1/config");
    expect(call(1)).toMatchObject({
      url: "/api/coauthors/join/preview/",
      csrf: "fresh",
    });
  });

  it("leaves a workspace", async () => {
    respond(null, 204);
    await (await api()).leaveWorkspace(3);
    expect(call()).toMatchObject({
      url: "/api/coauthors/memberships/3/",
      method: "DELETE",
      csrf: "csrf-1",
    });
  });

  it("manages invite links", async () => {
    const { listInvites, createInvite, revokeInvite } = await api();
    respond([]);
    respond({ id: 1, token: TOKEN, path: `/join#${TOKEN}` }, 201);
    respond(null, 204);
    await listInvites();
    const created = await createInvite({
      label: "Ana",
      expires_in_days: 7,
      max_uses: 1,
    });
    await revokeInvite(1);
    expect(call(0)).toMatchObject({
      url: "/api/coauthors/invites/",
      method: "GET",
    });
    expect(call(1)).toEqual({
      url: "/api/coauthors/invites/",
      method: "POST",
      body: { label: "Ana", expires_in_days: 7, max_uses: 1 },
      csrf: "csrf-1",
    });
    expect(created.path).toBe(`/join#${TOKEN}`);
    expect(call(2)).toMatchObject({
      url: "/api/coauthors/invites/1/",
      method: "DELETE",
    });
  });

  it("manages co-authors and pages through their activity", async () => {
    const { listMembers, removeMember, getMemberActivity } = await api();
    respond([]);
    respond(null, 204);
    respond([]);
    respond([]);
    await listMembers();
    await removeMember(4);
    await getMemberActivity(4);
    await getMemberActivity(4, { limit: 20, before: 99 });
    expect(call(0).url).toBe("/api/coauthors/members/");
    expect(call(1)).toMatchObject({
      url: "/api/coauthors/members/4/",
      method: "DELETE",
    });
    expect(call(2).url).toBe("/api/coauthors/members/4/activity/?limit=50");
    expect(call(3).url).toBe(
      "/api/coauthors/members/4/activity/?limit=20&before=99",
    );
  });
});

describe("errors", () => {
  it("passes the error body through with its status and code", async () => {
    const { listInvites, coauthorErrorCode } = await api();
    const { apiErrorStatus, formatApiError } = await import("./core");
    const body = {
      detail:
        "This belongs to the account owner's sign-in. Switch to your own account to do this.",
      code: "acting_denied",
      errors: [],
    };
    respond(body, 403);
    const error = await listInvites().catch((e: unknown) => e);
    expect(apiErrorStatus(error)).toBe(403);
    expect(coauthorErrorCode(error)).toBe("acting_denied");
    expect(formatApiError(error)).toBe(body.detail);
    expect(assign).not.toHaveBeenCalled();
  });

  it("reads no code from other shapes", async () => {
    const { coauthorErrorCode } = await api();
    expect(coauthorErrorCode({ detail: [] })).toBe("");
    expect(coauthorErrorCode(null)).toBe("");
    expect(coauthorErrorCode("x")).toBe("");
  });

  it("a 409 workspace_ended flags the reason and goes home", async () => {
    respond(
      {
        detail: "You no longer have access to this workspace.",
        code: "workspace_ended",
        errors: [],
      },
      409,
    );
    const { getCoauthorContext } = await api();
    const error = await getCoauthorContext().catch((e: unknown) => e);
    expect((error as { code: string }).code).toBe("workspace_ended");
    expect(session.get("coauthor.ended")).toBe("1");
    expect(assign).toHaveBeenCalledWith("/");
    expect(posted).toHaveLength(1); // the other tabs follow
  });

  it("requests in flight together navigate once", async () => {
    const ended = {
      detail: "You no longer have access to this workspace.",
      code: "workspace_ended",
      errors: [],
    };
    respond(ended, 409);
    respond(ended, 409);
    const { getCoauthorContext } = await api();
    const { getMe } = await import("./users");
    await Promise.all([
      getCoauthorContext().catch(() => null),
      getMe().catch(() => null),
    ]);
    expect(assign).toHaveBeenCalledTimes(1);
  });

  it("a 409 workspace_changed reloads the page it was on", async () => {
    respond(
      {
        detail:
          "The workspace was switched in another tab. Reload to continue.",
        code: "workspace_changed",
        errors: [],
      },
      409,
    );
    const error = await (await api()).revokeInvite(3).catch((e: unknown) => e);
    expect((error as { code: string }).code).toBe("workspace_changed");
    expect(assign).toHaveBeenCalledWith("/studies/7?tab=runs");
    expect(session.has("coauthor.ended")).toBe(false);
  });

  it("switch, join and leave tell the other tabs", async () => {
    const { switchWorkspace, acceptInvite, leaveWorkspace, listInvites } =
      await api();
    respond({ actor: {}, acting_as: null, workspaces: [] });
    await switchWorkspace(null);
    respond({ status: "joined", coauthorship_id: 4 });
    await acceptInvite(TOKEN);
    respond(null, 204);
    await leaveWorkspace(4);
    respond([]);
    await listInvites();
    expect(posted).toHaveLength(3);
  });

  it("other 409s neither flag nor navigate", async () => {
    respond(
      {
        detail: "This is your own invite link.",
        code: "own_invite",
        errors: [],
      },
      409,
    );
    const error = await (await api())
      .acceptInvite(TOKEN)
      .catch((e: unknown) => e);
    expect((error as { code: string }).code).toBe("own_invite");
    expect(session.has("coauthor.ended")).toBe(false);
    expect(assign).not.toHaveBeenCalled();
  });
});
