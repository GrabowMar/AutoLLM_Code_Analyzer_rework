import { afterEach, describe, expect, it, vi } from "vitest";
import type { RequestEvent } from "@sveltejs/kit";
import {
  buildProxyHeaders,
  proxyResponseHeaders,
  proxyToApi,
} from "./hooks.server";

function makeEvent(request: Request, clientIp = "10.0.0.7"): RequestEvent {
  return {
    request,
    url: new URL(request.url),
    getClientAddress: () => clientIp,
  } as unknown as RequestEvent;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("hooks proxy headers", () => {
  it("appends the peer address to X-Forwarded-For instead of overwriting it", () => {
    const request = new Request("https://lab.example.org/api/relay/v1/poll", {
      headers: { "X-Forwarded-For": "203.0.113.5" },
    });
    const headers = buildProxyHeaders(
      request,
      new URL(request.url),
      "172.18.0.9",
    );
    expect(headers.get("X-Forwarded-For")).toBe("203.0.113.5, 172.18.0.9");
    expect(headers.get("X-Forwarded-Host")).toBe("lab.example.org");
    expect(headers.get("X-Forwarded-Proto")).toBe("https");
  });

  it("uses only the peer address when no chain was sent", () => {
    const request = new Request("http://localhost:5173/api/models/");
    const headers = buildProxyHeaders(
      request,
      new URL(request.url),
      "127.0.0.1",
    );
    expect(headers.get("X-Forwarded-For")).toBe("127.0.0.1");
  });

  it("strips content-encoding and content-length from upstream responses", () => {
    const upstream = new Headers({
      "content-encoding": "gzip",
      "content-length": "123",
      "content-type": "application/json",
    });
    const headers = proxyResponseHeaders(upstream);
    expect(headers.get("content-encoding")).toBeNull();
    expect(headers.get("content-length")).toBeNull();
    expect(headers.get("content-type")).toBe("application/json");
  });
});

describe("proxyToApi", () => {
  it("forwards the abort signal, the appended XFF and returns stripped headers", async () => {
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => {
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: {
          "content-type": "application/json",
          "content-encoding": "gzip",
          "content-length": "999",
        },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    const request = new Request("https://lab.example.org/api/relay/v1/poll", {
      method: "POST",
      body: JSON.stringify({ inflight: [] }),
      headers: {
        "content-type": "application/json",
        "x-forwarded-for": "198.51.100.1",
      },
      signal: controller.signal,
    });

    const response = await proxyToApi(makeEvent(request));

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/api\/relay\/v1\/poll$/);
    expect(init.signal).toBe(request.signal);
    expect((init.headers as Headers).get("X-Forwarded-For")).toBe(
      "198.51.100.1, 10.0.0.7",
    );
    expect(response.headers.get("content-encoding")).toBeNull();
    expect(response.headers.get("content-length")).toBeNull();
    expect(await response.json()).toEqual({ ok: true });
  });
});

describe("proxyToApi client disconnects", () => {
  it("answers quietly when the client aborted the request", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn(async () => {
      controller.abort();
      throw new DOMException("The operation was aborted.", "AbortError");
    });
    vi.stubGlobal("fetch", fetchMock);
    const request = new Request("https://lab.example.org/api/relay/v1/poll", {
      method: "POST",
      body: "{}",
      signal: controller.signal,
    });

    const response = await proxyToApi(makeEvent(request));

    expect(response.status).toBe(499);
  });

  it("still fails loudly when the API is unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );
    const request = new Request("https://lab.example.org/api/models/");

    await expect(proxyToApi(makeEvent(request))).rejects.toThrow(
      "fetch failed",
    );
  });
});
