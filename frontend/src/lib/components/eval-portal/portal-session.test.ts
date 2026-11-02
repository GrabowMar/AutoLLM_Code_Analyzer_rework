import { describe, expect, it, vi } from "vitest";
import type { PortalMe } from "$lib/api/types/human-eval";
import {
  PortalSession,
  anonymousMe,
  type PortalSessionDeps,
} from "./portal-session.svelte";

const participantMe: PortalMe = {
  kind: "participant",
  participant: { task_id: "t1", label: "P007" },
  user: null,
  participations: [
    {
      task_id: "t1",
      task_title: "Study",
      task_status: "open",
      participant_label: "P007",
      participant_status: "active",
      kind: "invite",
      submitted: 2,
      target: 5,
    },
  ],
};

const accountMe: PortalMe = {
  kind: "account",
  participant: null,
  user: { email: "e@example.org", name: "", is_evaluator: true },
  participations: [],
};

function httpError(status: number) {
  const error = { detail: "x" };
  Object.defineProperty(error, "httpStatus", { value: status });
  return error;
}

function deps(over: Partial<PortalSessionDeps> = {}): PortalSessionDeps {
  return {
    fetchMe: vi.fn(async () => participantMe),
    logoutPortal: vi.fn(async () => {}),
    logoutAccount: vi.fn(async () => {}),
    accountSignedIn: vi.fn(() => false),
    ...over,
  };
}

describe("PortalSession", () => {
  it("loads me/ and exposes the identities", async () => {
    const session = new PortalSession(deps());
    expect(session.me).toBeNull();
    await session.refresh();
    expect(session.me).toEqual(participantMe);
    expect(session.hasParticipantSession).toBe(true);
    expect(session.isAccount).toBe(false);
    expect(session.loading).toBe(false);
  });

  it("keeps the newest answer when refreshes overlap", async () => {
    let resolveFirst: (me: PortalMe) => void = () => {};
    const fetchMe = vi
      .fn<() => Promise<PortalMe>>()
      .mockImplementationOnce(
        () => new Promise((resolve) => (resolveFirst = resolve)),
      )
      .mockImplementationOnce(async () => accountMe);
    const session = new PortalSession(deps({ fetchMe }));
    const first = session.refresh();
    await session.refresh();
    resolveFirst(participantMe);
    await first;
    expect(session.me).toEqual(accountMe);
  });

  it("reports a failed refresh without dropping the last answer", async () => {
    const fetchMe = vi
      .fn<() => Promise<PortalMe>>()
      .mockResolvedValueOnce(participantMe)
      .mockRejectedValueOnce(httpError(500));
    const session = new PortalSession(deps({ fetchMe }));
    await session.refresh();
    expect(await session.refresh()).toBeNull();
    expect(session.me).toEqual(participantMe);
    expect(session.error).not.toBe("");
  });

  it("signs a code session out of the portal only", async () => {
    const d = deps();
    const session = new PortalSession(d);
    await session.refresh();
    await session.signOut();
    expect(d.logoutPortal).toHaveBeenCalledTimes(1);
    expect(d.logoutAccount).not.toHaveBeenCalled();
    expect(session.me).toEqual(anonymousMe());
  });

  it("signs an account out of the portal and of allauth", async () => {
    const d = deps({ fetchMe: vi.fn(async () => accountMe) });
    const session = new PortalSession(d);
    await session.refresh();
    await session.signOut();
    expect(d.logoutPortal).toHaveBeenCalledTimes(1);
    expect(d.logoutAccount).toHaveBeenCalledTimes(1);
    expect(session.isAccount).toBe(false);
  });

  it("also ends the account session when only the auth store knows about it", async () => {
    const d = deps({ accountSignedIn: vi.fn(() => true) });
    const session = new PortalSession(d);
    await session.signOut();
    expect(d.logoutAccount).toHaveBeenCalledTimes(1);
  });

  it("ignores a 401 from portal logout but re-throws other failures after clearing", async () => {
    const quiet = new PortalSession(
      deps({ logoutPortal: vi.fn(async () => Promise.reject(httpError(401))) }),
    );
    await expect(quiet.signOut()).resolves.toBeUndefined();

    const failing = new PortalSession(
      deps({ logoutPortal: vi.fn(async () => Promise.reject(httpError(500))) }),
    );
    await failing.refresh();
    await expect(failing.signOut()).rejects.toBeTruthy();
    expect(failing.me).toEqual(anonymousMe());
  });

  // C26: sign out / Leave must not leave console history or headers for the next person.
  it("clears the data kept in this browser on sign-out, even when logout fails", async () => {
    const ok = deps({ clearLocalData: vi.fn() });
    await new PortalSession(ok).signOut();
    expect(ok.clearLocalData).toHaveBeenCalledTimes(1);

    const failing = deps({
      clearLocalData: vi.fn(),
      logoutPortal: vi.fn(async () => Promise.reject(httpError(500))),
    });
    await expect(new PortalSession(failing).signOut()).rejects.toBeTruthy();
    expect(failing.clearLocalData).toHaveBeenCalledTimes(1);
  });

  it("forgets a withdrawn task locally before reloading", async () => {
    const fetchMe = vi
      .fn<() => Promise<PortalMe>>()
      .mockResolvedValueOnce(participantMe)
      .mockRejectedValueOnce(httpError(500));
    const session = new PortalSession(deps({ fetchMe }));
    await session.refresh();
    await session.forgetTask("t1");
    expect(session.me?.participant).toBeNull();
    expect(session.me?.participations).toEqual([]);
  });
});
