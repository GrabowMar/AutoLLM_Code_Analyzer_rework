import { describe, expect, it, vi } from "vitest";
import type {
  HostingInfo,
  HostingState,
  PortalApp,
} from "$lib/api/types/human-eval";
import {
  PLATFORM_FAILURE_ERROR,
  SKIP_REASONS,
  afterSubmitText,
  clearConsoleStorage,
  consoleStorageKey,
  defaultSkipReason,
  errorCode,
  errorItems,
  formatDuration,
  isReadOnlyError,
  orderedApps,
  sendMaybeKeepalive,
  slotTitle,
  workbenchError,
  workbenchHref,
} from "./workbench";

function app(slot: PortalApp["slot"], label: string): PortalApp {
  return {
    slot,
    label,
    hosting: {
      state: "idle",
      url: null,
      progress: null,
      message: "",
      error: "",
    },
    spec: null,
    admin_credentials: null,
    has_source: false,
  };
}

function apiError(status: number, body: Record<string, unknown>) {
  const error = { ...body };
  Object.defineProperty(error, "httpStatus", { value: status });
  return error;
}

describe("apps", () => {
  it("shows A before B whatever order they arrive in", () => {
    expect(
      orderedApps([app("B", "App 01"), app("A", "App 09")]).map((a) => a.slot),
    ).toEqual(["A", "B"]);
    expect(slotTitle(app("A", "App 09"))).toBe("A · App 09");
    expect(slotTitle(app("single", "App 03"))).toBe("App 03");
  });

  it("builds portal paths", () => {
    expect(workbenchHref("t1", "a2")).toBe("/eval/t/t1/a/a2");
  });
});

describe("errors", () => {
  it("reads structured bodies", () => {
    const body = apiError(400, {
      detail: "Incomplete",
      code: "incomplete",
      errors: [{ path: "q.x", message: "Required" }, { path: "y" }, null],
    });
    expect(errorCode(body)).toBe("incomplete");
    expect(errorItems(body)).toEqual([{ path: "q.x", message: "Required" }]);
    expect(errorCode(null)).toBe("");
    expect(errorItems("x")).toEqual([]);
  });

  it("detects read-only refusals", () => {
    expect(
      isReadOnlyError(apiError(409, { code: "read_only", detail: "x" })),
    ).toBe(true);
    expect(
      isReadOnlyError(apiError(409, { code: "quota_exceeded", detail: "x" })),
    ).toBe(false);
  });

  it("explains sessions and CSRF", () => {
    expect(workbenchError(apiError(401, {}), "x")).toMatch(/session has ended/);
    expect(
      workbenchError(apiError(403, { detail: "CSRF check failed" }), "x"),
    ).toMatch(/security cookie/);
    expect(workbenchError(apiError(400, { detail: "Bad slot" }), "x")).toBe(
      "Bad slot",
    );
    expect(workbenchError({}, "Fallback")).toBe("Fallback");
  });
});

describe("text", () => {
  it("keeps app_broken and platform_unavailable apart", () => {
    const values = SKIP_REASONS.map((r) => r.value);
    expect(values).toContain("app_broken");
    expect(values).toContain("platform_unavailable");
    expect(new Set(values).size).toBe(values.length);
  });

  it("files build and start failures under app_broken, not platform_unavailable (R1)", () => {
    const broken = SKIP_REASONS.find((r) => r.value === "app_broken")!;
    const platform = SKIP_REASONS.find(
      (r) => r.value === "platform_unavailable",
    )!;
    expect(broken.description).toMatch(/fails to build or start/);
    expect(platform.description).not.toMatch(/build|start/);
  });

  it("formats durations", () => {
    expect(formatDuration(0)).toBe("0:00");
    expect(formatDuration(65)).toBe("1:05");
    expect(formatDuration(3725)).toBe("1:02:05");
    expect(formatDuration(-3)).toBe("0:00");
  });

  it("explains why no next app came", () => {
    expect(afterSubmitText("done")).toMatch(/every app/);
    expect(afterSubmitText("assigned")).toBe("");
    expect(afterSubmitText(undefined)).toBe("");
  });
});

// C01/C17: an app whose own build or start failed must default to app_broken,
// so it can count as unevaluable; only platform problems default to platform_unavailable.
describe("defaultSkipReason", () => {
  const h = (
    state: HostingState,
    error = "",
  ): Pick<HostingInfo, "state" | "error"> => ({ state, error });

  it("picks app_broken when the app itself failed", () => {
    expect(
      defaultSkipReason(["single"], {
        single: h("failed", "The app could not be started."),
      }),
    ).toBe("app_broken");
    expect(defaultSkipReason(["single"], { single: h("failed") })).toBe(
      "app_broken",
    );
  });

  it("picks platform_unavailable for the queue, unreachable apps or a platform failure", () => {
    expect(defaultSkipReason(["single"], { single: h("queued") })).toBe(
      "platform_unavailable",
    );
    expect(defaultSkipReason(["single"], { single: h("unavailable") })).toBe(
      "platform_unavailable",
    );
    expect(
      defaultSkipReason(["single"], {
        single: h("failed", PLATFORM_FAILURE_ERROR),
      }),
    ).toBe("platform_unavailable");
  });

  it("prefers app_broken when one app of a pair failed by itself", () => {
    expect(
      defaultSkipReason(["A", "B"], {
        A: h("queued"),
        B: h("failed", "The app could not be started."),
      }),
    ).toBe("app_broken");
  });

  it("picks nothing when the apps work or are on their way", () => {
    expect(
      defaultSkipReason(["A", "B"], { A: h("running"), B: h("building") }),
    ).toBe("");
    expect(defaultSkipReason(["single"], {})).toBe("");
  });
});

// C26: console data must not outlive the participant's session in a shared browser.
describe("console storage", () => {
  function fakeStorage(entries: Record<string, string>) {
    const map = new Map(Object.entries(entries));
    return {
      map,
      get length() {
        return map.size;
      },
      key: (i: number) => [...map.keys()][i] ?? null,
      removeItem: (k: string) => void map.delete(k),
    };
  }

  it("keys the remember opt-in per assignment", () => {
    expect(consoleStorageKey("rememberAuth", "a1")).toBe(
      "heval.console.rememberAuth.a1",
    );
    expect(consoleStorageKey("rememberAuth", "a1")).not.toBe(
      consoleStorageKey("rememberAuth", "a2"),
    );
    expect(consoleStorageKey("history", "a1")).toBe("heval.console.history.a1");
  });

  it("removes every console key and nothing else", () => {
    const storage = fakeStorage({
      "heval.console.history.a1": "[]",
      "heval.console.auth.a1": "Bearer x",
      "heval.console.rememberAuth": "1",
      "heval.console.rememberAuth.a1": "1",
      "heval.workbench.split": "58",
      other: "x",
    });
    clearConsoleStorage(storage);
    expect([...storage.map.keys()].sort()).toEqual([
      "heval.workbench.split",
      "other",
    ]);
  });

  it("ignores missing or blocked storage", () => {
    expect(() => clearConsoleStorage(null)).not.toThrow();
    const blocked = {
      get length(): number {
        throw new Error("SecurityError");
      },
      key: () => null,
      removeItem: () => {},
    };
    expect(() => clearConsoleStorage(blocked)).not.toThrow();
  });
});

// C28: flushes while the page is hidden or closing use keepalive.
describe("sendMaybeKeepalive", () => {
  it("uses keepalive only while leaving", async () => {
    const send = vi.fn(async (o: { keepalive?: boolean }) => o);
    expect(await sendMaybeKeepalive(false, send)).toEqual({});
    expect(await sendMaybeKeepalive(true, send)).toEqual({ keepalive: true });
  });

  it("falls back to a plain request when the browser refuses keepalive", async () => {
    const send = vi
      .fn<(o: { keepalive?: boolean }) => Promise<string>>()
      .mockRejectedValueOnce(new TypeError("keepalive quota"))
      .mockResolvedValue("ok");
    expect(await sendMaybeKeepalive(true, send)).toBe("ok");
    expect(send.mock.calls).toEqual([[{ keepalive: true }], [{}]]);
  });

  it("passes server errors through without resending", async () => {
    const error = apiError(409, { code: "read_only" });
    const send = vi.fn(async () => {
      throw error;
    });
    await expect(sendMaybeKeepalive(true, send)).rejects.toBe(error);
    expect(send).toHaveBeenCalledTimes(1);
  });
});
