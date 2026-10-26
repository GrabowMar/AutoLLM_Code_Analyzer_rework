import { describe, expect, it } from "vitest";
import type { EndpointSummary, SessionOut } from "$lib/api/types/inference";
import {
  applyEndpointEvent,
  blockedReasonText,
  endpointSlugFromName,
  endpointStatusTone,
  formatAgo,
  formatBytes,
  formatCtx,
  newSessionHosts,
  probeSummary,
  relayServerUrlProblem,
  shortDigest,
  slugError,
} from "../endpoint-display";

function endpoint(overrides: Partial<EndpointSummary> = {}): EndpointSummary {
  return {
    id: 7,
    slug: "gpu-box-1",
    name: "GPU box 1",
    kind: "ollama",
    connection: "relay",
    api_flavor: "native",
    enabled: true,
    is_system: false,
    visibility: "private",
    owner: { id: 1, username: "me" },
    can_edit: true,
    has_api_key: false,
    api_key_prefix: null,
    base_url: null,
    max_concurrency: 2,
    max_active_jobs_effective: { sandbox: 2, llm: 2 },
    max_resident_models: 1,
    request_timeout_s: 900,
    num_ctx_cap: 32768,
    keep_alive: "30m",
    assume_tools: false,
    status: "online",
    live: {
      session_count: 1,
      sessions: [
        {
          id: "s1",
          role: "active",
          hostname: "gpu1",
          relay_version: "1.0.0",
          client_ip: "10.0.0.5",
          capacity: 2,
          last_seen_at: "2026-10-25T10:00:00Z",
        },
      ],
      inflight_requests: 1,
      queued_requests: 0,
      active_jobs: 1,
      loaded: [],
      tokens_per_s_5m: 12.5,
    },
    models: { total: 3, available: 3, tools: 2 },
    last_inventory_at: null,
    last_error: null,
    server_version: "0.5.0",
    ...overrides,
  };
}

function session(overrides: Partial<SessionOut>): SessionOut {
  return {
    id: "x",
    role: "active",
    relay_id: null,
    hostname: "gpu1",
    relay_version: "1.0.0",
    upstream_kind: "ollama",
    upstream_hint: "",
    capacity: 1,
    client_ip: "10.0.0.5",
    token_prefix: "llr_abc",
    model_count: 3,
    inflight: 0,
    started_at: "2026-10-01T00:00:00Z",
    last_seen_at: "2026-10-01T00:00:00Z",
    ended_at: null,
    ended_reason: "",
    ...overrides,
  };
}

describe("endpointSlugFromName", () => {
  it("lowercases, dashes and trims", () => {
    expect(endpointSlugFromName("GPU Box #1 ")).toBe("gpu-box-1");
    expect(endpointSlugFromName("  Žluťoučký kůň ")).toBe("zlutoucky-kun");
  });
  it("caps the length without a trailing dash", () => {
    const slug = endpointSlugFromName("a".repeat(38) + " b c");
    expect(slug.length).toBeLessThanOrEqual(39);
    expect(slug.endsWith("-")).toBe(false);
  });
  it("returns empty when nothing valid is left", () => {
    expect(endpointSlugFromName("!!!")).toBe("");
    expect(endpointSlugFromName("x")).toBe("");
  });
});

describe("slugError", () => {
  it("accepts the backend pattern", () => {
    expect(slugError("gpu-box-1")).toBe("");
    expect(slugError("")).toBe("");
  });
  it("rejects bad slugs", () => {
    expect(slugError("a")).toMatch(/2 characters/);
    expect(slugError("-gpu")).not.toBe("");
    expect(slugError("GPU")).not.toBe("");
    expect(slugError("a".repeat(40))).toMatch(/39/);
  });
});

describe("formatting", () => {
  const now = Date.parse("2026-10-25T12:00:00Z");
  it("formatAgo", () => {
    expect(formatAgo(null, now)).toBe("never");
    expect(formatAgo("2026-10-25T12:00:00Z", now)).toBe("just now");
    expect(formatAgo("2026-10-25T11:59:56Z", now)).toBe("4 s ago");
    expect(formatAgo("2026-10-25T11:57:00Z", now)).toBe("3 min ago");
    expect(formatAgo("2026-10-25T10:00:00Z", now)).toBe("2 h ago");
    expect(formatAgo("2026-10-20T12:00:00Z", now)).toBe("5 d ago");
    expect(formatAgo("garbage", now)).toBe("—");
  });
  it("formatBytes, shortDigest, formatCtx", () => {
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(512 * 1024 ** 2)).toBe("512 MB");
    expect(formatBytes(18.25 * 1024 ** 3)).toBe("18.3 GB");
    expect(shortDigest("sha256:abcdef0123456789")).toBe("abcdef01");
    expect(shortDigest("")).toBe("");
    expect(formatCtx(32768)).toBe("32K");
    expect(formatCtx(4000)).toBe((4000).toLocaleString());
    expect(formatCtx(null)).toBe("—");
  });
  it("blocked reasons and tones", () => {
    expect(blockedReasonText("endpoint_offline")).toBe("Waiting for relay");
    expect(blockedReasonText("something_new")).toBe("something new");
    expect(blockedReasonText(null)).toBe("");
    expect(endpointStatusTone("online")).toBe("success");
    expect(endpointStatusTone("degraded")).toBe("warning");
    expect(endpointStatusTone("offline")).toBe("danger");
    expect(endpointStatusTone("weird")).toBe("neutral");
  });
});

describe("applyEndpointEvent", () => {
  it("applies the owner payload and keeps session details", () => {
    const before = endpoint();
    const after = applyEndpointEvent(before, {
      type: "endpoint",
      status: "degraded",
      sessions: [
        {
          id: "s1",
          role: "active",
          hostname: "gpu1",
          relay_version: "1.0.1",
          client_ip: "10.0.0.5",
        },
        {
          id: "s2",
          role: "standby",
          hostname: "gpu1b",
          relay_version: "1.0.1",
          client_ip: null,
        },
      ],
      inflight: 2,
      queued: 5,
      loaded: [{ name: "qwen:7b", size_vram: 1, cpu_spill: false }],
      last_error: "boom",
    });
    expect(after).not.toBe(before);
    expect(before.live.inflight_requests).toBe(1); // not mutated
    expect(after.status).toBe("degraded");
    expect(after.live.inflight_requests).toBe(2);
    expect(after.live.queued_requests).toBe(5);
    expect(after.live.session_count).toBe(2);
    expect(after.live.sessions?.[0]).toMatchObject({
      id: "s1",
      relay_version: "1.0.1",
      capacity: 2,
      last_seen_at: "2026-10-25T10:00:00Z",
    });
    expect(after.live.sessions?.[1].capacity).toBe(0);
    expect(after.live.loaded).toHaveLength(1);
    expect(after.last_error).toBe("boom");
  });

  it("applies the public payload without losing other fields", () => {
    const before = endpoint();
    const after = applyEndpointEvent(before, {
      type: "endpoint",
      status: "offline",
      session_count: 0,
      inflight: 0,
      queued: 3,
      loaded_count: 2,
    });
    expect(after.status).toBe("offline");
    expect(after.live.session_count).toBe(0);
    expect(after.live.queued_requests).toBe(3);
    expect(after.live.sessions).toEqual(before.live.sessions);
    expect(after.last_error).toBeNull();
  });

  it("normalizes bare-name and duplicate loaded entries", () => {
    const after = applyEndpointEvent(endpoint(), {
      type: "endpoint",
      loaded: [
        "a:1b",
        "b:2b",
        "a:1b",
        { name: "c:3b", size_vram: 5, cpu_spill: true },
        {},
      ],
    });
    expect(after.live.loaded).toEqual([
      { name: "a:1b", size_vram: null, cpu_spill: false },
      { name: "b:2b", size_vram: null, cpu_spill: false },
      { name: "c:3b", size_vram: 5, cpu_spill: true },
    ]);
  });

  it("ignores other event types", () => {
    const before = endpoint();
    expect(
      applyEndpointEvent(before, { type: "session", event: "connected" }),
    ).toBe(before);
  });
});

describe("newSessionHosts", () => {
  it("flags a live session from a host the token never used", () => {
    const flagged = newSessionHosts([
      session({
        id: "a",
        started_at: "2026-10-01T00:00:00Z",
        ended_at: "2026-10-02T00:00:00Z",
      }),
      session({
        id: "b",
        started_at: "2026-10-03T00:00:00Z",
        hostname: "laptop",
        client_ip: "203.0.113.9",
      }),
    ]);
    expect([...flagged].sort()).toEqual(["203.0.113.9", "laptop"]);
  });

  it("does not flag a token's first session or a known host", () => {
    expect(newSessionHosts([session({ id: "a" })]).size).toBe(0);
    const flagged = newSessionHosts([
      session({ id: "a", ended_at: "2026-10-02T00:00:00Z" }),
      session({ id: "b", started_at: "2026-10-05T00:00:00Z" }),
      session({
        id: "c",
        token_prefix: "llr_other",
        hostname: "elsewhere",
        client_ip: "1.2.3.4",
      }),
    ]);
    expect(flagged.size).toBe(0);
  });
});

describe("probeSummary", () => {
  it("reports a reachable endpoint with latency and sample", () => {
    const line = probeSummary({
      status: "done",
      latency_ms: 1234,
      sample: "ok",
    });
    expect(line.tone).toBe("success");
    expect(line.text).toContain("Reachable");
    expect(line.text).toContain("ms");
    expect(line.text).toContain("ok");
  });

  it("treats a request still queued after the poll as no answer yet, not a failure", () => {
    for (const status of ["queued", "leased"]) {
      const line = probeSummary({ status, latency_ms: null });
      expect(line.tone).toBe("warning");
      expect(line.text).toContain(`No answer yet (${status})`);
    }
  });

  it("shows finished failures with their error", () => {
    const line = probeSummary({
      status: "failed",
      latency_ms: null,
      error: "HTTP 404: model not found",
    });
    expect(line.tone).toBe("danger");
    expect(line.text).toBe("Test failed: HTTP 404: model not found");
    expect(probeSummary({ status: "expired", latency_ms: null }).text).toBe(
      "Test expired",
    );
  });
});

describe("relayServerUrlProblem", () => {
  it("accepts a public https origin", () => {
    expect(relayServerUrlProblem("https://dev1.grabowmar.ovh")).toBeNull();
  });
  it("flags internal, plain-http and local URLs", () => {
    expect(relayServerUrlProblem("http://django:8000")).toMatch(/https/);
    expect(relayServerUrlProblem("https://django:8000")).toMatch(/internal/);
    expect(relayServerUrlProblem("http://localhost:8000")).toMatch(
      /only reachable/,
    );
    expect(relayServerUrlProblem("")).not.toBeNull();
    expect(relayServerUrlProblem("not a url")).not.toBeNull();
  });
});
