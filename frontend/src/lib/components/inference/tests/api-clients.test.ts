import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createEndpoint,
  deleteEndpoint,
  getEndpointMetrics,
  isEndpointInUseError,
  listEndpointModels,
  listEndpoints,
  revokeToken,
  rotateToken,
  runProbe,
  testEndpoint,
  updateEndpoint,
  updateEndpointModel,
} from "$lib/api/inference";
import {
  getQueue,
  memoryRuleError,
  updateExecutorSettings,
} from "$lib/api/execution";
import { getModels } from "$lib/api/models";

type Call = { url: string; init: RequestInit };

let calls: Call[];
let responses: Array<{ status: number; body: unknown }>;

function respond(status: number, body: unknown) {
  responses.push({ status, body });
}

beforeEach(() => {
  calls = [];
  responses = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      calls.push({ url, init });
      const next = responses.shift() ?? { status: 200, body: {} };
      return new Response(JSON.stringify(next.body), {
        status: next.status,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("inference client", () => {
  it("lists endpoints with and without the system row", async () => {
    respond(200, []);
    respond(200, []);
    await listEndpoints();
    await listEndpoints({ includeSystem: true });
    expect(calls[0].url).toBe("/api/inference/endpoints/?include_system=false");
    expect(calls[1].url).toBe("/api/inference/endpoints/?include_system=true");
  });

  it("creates, patches and deletes with the right verbs and bodies", async () => {
    respond(200, {
      endpoint: { id: 3 },
      token: { id: 1, prefix: "p", secret_once: "s" },
    });
    const created = await createEndpoint({
      name: "Box",
      kind: "ollama",
      connection: "relay",
      max_concurrency: 2,
    });
    expect(created.token?.secret_once).toBe("s");
    expect(calls[0].init.method).toBe("POST");
    expect(JSON.parse(String(calls[0].init.body))).toMatchObject({
      name: "Box",
      max_concurrency: 2,
    });

    respond(200, { id: 3 });
    await updateEndpoint(3, { api_key: "" });
    expect(calls[1].url).toBe("/api/inference/endpoints/3/");
    expect(calls[1].init.method).toBe("PATCH");
    expect(JSON.parse(String(calls[1].init.body))).toEqual({ api_key: "" });

    respond(200, { success: true });
    await deleteEndpoint(3);
    expect(calls[2].init.method).toBe("DELETE");
  });

  it("surfaces a 409 in_use delete", async () => {
    respond(409, { code: "in_use", detail: "used by runs" });
    let caught: unknown;
    try {
      await deleteEndpoint(4);
    } catch (e) {
      caught = e;
    }
    expect(isEndpointInUseError(caught)).toBe(true);
    expect(isEndpointInUseError({ detail: "x" })).toBe(false);
    expect(isEndpointInUseError(null)).toBe(false);
  });

  it("scopes model, token and probe calls to the endpoint", async () => {
    respond(200, []);
    await listEndpointModels(5, { includeHidden: true });
    expect(calls[0].url).toBe(
      "/api/inference/endpoints/5/models/?include_hidden=true",
    );

    respond(200, {});
    await updateEndpointModel(5, 42, { num_ctx_override: null });
    expect(calls[1].url).toBe("/api/inference/endpoints/5/models/42/");
    expect(JSON.parse(String(calls[1].init.body))).toEqual({
      num_ctx_override: null,
    });

    respond(200, {
      new: { id: 2, prefix: "q", secret_once: "t" },
      old_expires_at: null,
    });
    await rotateToken(5, 9, 60);
    expect(calls[2].url).toBe("/api/inference/endpoints/5/tokens/9/rotate/");
    expect(JSON.parse(String(calls[2].init.body))).toEqual({
      grace_minutes: 60,
    });

    respond(200, { revoked_at: "x", sessions_ended: 1, requests_requeued: 2 });
    await revokeToken(5, 9);
    expect(calls[3].url).toBe("/api/inference/endpoints/5/tokens/9/revoke/");
    expect(calls[3].init.method).toBe("POST");

    respond(200, { request_id: "r1" });
    await testEndpoint(5);
    expect(JSON.parse(String(calls[4].init.body))).toEqual({});
    respond(200, { request_id: "r2" });
    await testEndpoint(5, 42);
    expect(JSON.parse(String(calls[5].init.body))).toEqual({ model_id: 42 });

    respond(200, { points: [] });
    await getEndpointMetrics(5, 30);
    expect(calls[6].url).toBe("/api/inference/endpoints/5/metrics/?minutes=30");
  });

  it("runProbe polls until the request is final", async () => {
    respond(200, { request_id: "abc" });
    respond(200, { status: "queued", latency_ms: null });
    respond(200, { status: "leased", latency_ms: null });
    respond(200, { status: "done", latency_ms: 120, sample: "hi" });
    const result = await runProbe(5, null, { intervalMs: 1 });
    expect(result).toMatchObject({ status: "done", latency_ms: 120 });
    expect(calls.map((c) => c.url)).toEqual([
      "/api/inference/endpoints/5/test/",
      "/api/inference/endpoints/5/test/abc/",
      "/api/inference/endpoints/5/test/abc/",
      "/api/inference/endpoints/5/test/abc/",
    ]);
  });

  it("runProbe stops on abort", async () => {
    respond(200, { request_id: "abc" });
    respond(200, { status: "queued", latency_ms: null });
    const controller = new AbortController();
    const pending = runProbe(5, null, {
      intervalMs: 10_000,
      signal: controller.signal,
    });
    await new Promise((r) => setTimeout(r, 5));
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("execution client", () => {
  it("builds the queue query", async () => {
    respond(200, { items: [], others_ahead: {} });
    await getQueue({ experiment_id: "e1", endpoint: "gpu1", limit: 50 });
    expect(calls[0].url).toBe(
      "/api/execution/queue/?experiment_id=e1&endpoint=gpu1&limit=50",
    );
  });

  it("returns the memory-rule body of a rejected PATCH", async () => {
    respond(400, {
      detail: "too many",
      arithmetic: "3 × 3g > 4g",
      mem_limit: "4g",
      required: "9g",
    });
    let caught: unknown;
    try {
      await updateExecutorSettings({ sandbox_slots: 3 });
    } catch (e) {
      caught = e;
    }
    expect(calls[0].init.method).toBe("PATCH");
    expect(memoryRuleError(caught)?.arithmetic).toBe("3 × 3g > 4g");
    expect(memoryRuleError({ detail: "other" })).toBeNull();
  });
});

describe("models client", () => {
  it("passes the endpoint filters", async () => {
    respond(200, { items: [], total: 0, page: 1, per_page: 50, pages: 1 });
    await getModels({ endpoint: "local", include_unavailable: true });
    expect(calls[0].url).toBe(
      "/api/models/?endpoint=local&include_unavailable=1",
    );
  });
});
