import { describe, expect, it } from "vitest";
import type { AppSpecView } from "$lib/api/types/human-eval";
import {
  MAX_HEADERS,
  bodyIssue,
  buildHeaders,
  consoleErrorMessage,
  endpointSuggestions,
  headerValue,
  historyEntry,
  parseHeaderBlock,
  parseHeaderLine,
  parseHistory,
  pathIssue,
  prettyBody,
  pushHistory,
  redactHeaders,
  statusTone,
  withDefaultContentType,
} from "./console";

describe("header parsing", () => {
  it("parses Name: value lines", () => {
    expect(parseHeaderLine("Authorization: Bearer abc:def")).toEqual({
      name: "Authorization",
      value: "Bearer abc:def",
    });
    expect(parseHeaderLine("X-Empty:")).toEqual({ name: "X-Empty", value: "" });
    expect(parseHeaderLine("   ")).toBeNull();
    expect(parseHeaderLine(": value")).toBeNull();
    expect(parseHeaderLine("no colon")).toBeNull();
  });

  it("parses a pasted block, skipping junk", () => {
    expect(
      parseHeaderBlock("Accept: application/json\r\n\nbad line\nX-Id: 7\n"),
    ).toEqual([
      { name: "Accept", value: "application/json" },
      { name: "X-Id", value: "7" },
    ]);
  });
});

describe("buildHeaders", () => {
  it("keeps valid rows and ignores empty ones", () => {
    const built = buildHeaders([
      { name: "Accept", value: "application/json" },
      { name: "", value: "" },
      { name: " X-Trace ", value: " 1 " },
    ]);
    expect(built.headers).toEqual({
      Accept: "application/json",
      "X-Trace": "1",
    });
    expect(built.errors).toEqual([]);
  });

  it("rejects invalid names, line breaks, duplicates and nameless values", () => {
    const built = buildHeaders([
      { name: "Bad Name", value: "x" },
      { name: "X-A", value: "one\r\ntwo" },
      { name: "x-dup", value: "1" },
      { name: "X-Dup", value: "2" },
      { name: "", value: "orphan" },
      { name: "X".repeat(65), value: "x" },
    ]);
    expect(built.errors).toHaveLength(5);
    expect(built.headers).toEqual({ "x-dup": "1" });
  });

  it("reports headers the server drops", () => {
    const built = buildHeaders([
      { name: "Host", value: "evil" },
      { name: "Content-Length", value: "3" },
      { name: "Accept", value: "*/*" },
    ]);
    expect(built.dropped).toEqual(["Host", "Content-Length"]);
    expect(built.headers).toEqual({ Accept: "*/*" });
  });

  it("limits the number of headers and value length", () => {
    const rows = Array.from({ length: MAX_HEADERS + 1 }, (_, i) => ({
      name: `X-${i}`,
      value: "v",
    }));
    expect(buildHeaders(rows).errors).toEqual([
      `Send at most ${MAX_HEADERS} headers.`,
    ]);
    expect(
      buildHeaders([{ name: "X", value: "v".repeat(4097) }]).errors,
    ).toHaveLength(1);
  });

  it("looks headers up case-insensitively and adds a JSON content type", () => {
    expect(headerValue({ "Content-Type": "text/html" }, "content-type")).toBe(
      "text/html",
    );
    expect(headerValue(null, "x")).toBeUndefined();
    expect(withDefaultContentType({}, '{"a":1}')).toEqual({
      "Content-Type": "application/json",
    });
    expect(
      withDefaultContentType({ "content-type": "text/plain" }, '{"a":1}'),
    ).toEqual({
      "content-type": "text/plain",
    });
    expect(withDefaultContentType({}, "a=1")).toEqual({});
    expect(withDefaultContentType({}, "")).toEqual({});
  });
});

describe("request checks", () => {
  it("mirrors the server's path rules", () => {
    expect(pathIssue("/api/items?id=1")).toBeNull();
    expect(pathIssue("")).not.toBeNull();
    expect(pathIssue("api/items")).not.toBeNull();
    expect(pathIssue("//evil.example/x")).not.toBeNull();
    expect(pathIssue("/a b")).not.toBeNull();
    expect(pathIssue("/a\tb")).not.toBeNull();
    expect(pathIssue("/a#frag")).not.toBeNull();
    expect(pathIssue(`/${"a".repeat(2048)}`)).not.toBeNull();
  });

  it("measures bodies in UTF-8 bytes", () => {
    expect(bodyIssue("x".repeat(256 * 1024))).toBeNull();
    expect(bodyIssue("é".repeat(128 * 1024 + 1))).not.toBeNull();
  });
});

describe("endpoint suggestions", () => {
  const spec: AppSpecView = {
    name: "Todo",
    description: "",
    backend_requirements: [],
    frontend_requirements: [],
    admin_requirements: [],
    api_endpoints: [
      {
        method: "get",
        path: "/api/todos",
        description: "List todos",
        auth: "",
      },
      {
        method: "POST",
        path: "/api/todos",
        description: "Create a todo",
        auth: "user",
      },
      { method: "GET", path: "/api/todos", description: "dup", auth: "" },
      { method: "TRACE", path: "/api/trace", description: "", auth: "" },
      { method: "GET", path: "no-slash", description: "", auth: "" },
    ],
    admin_api_endpoints: [
      {
        method: "DELETE",
        path: "/api/admin/users/{id}",
        description: "Remove user",
        auth: "admin",
      },
    ],
  };

  it("lists spec endpoints once with normalized methods", () => {
    const all = endpointSuggestions(spec);
    expect(all.map((s) => `${s.method} ${s.path}`)).toEqual([
      "GET /api/todos",
      "POST /api/todos",
      "GET /api/trace",
      "DELETE /api/admin/users/{id}",
    ]);
    expect(all[3].source).toBe("admin_api");
  });

  it("filters by method, path or description", () => {
    expect(endpointSuggestions(spec, "admin").map((s) => s.path)).toEqual([
      "/api/admin/users/{id}",
    ]);
    expect(endpointSuggestions(spec, "create").map((s) => s.method)).toEqual([
      "POST",
    ]);
    expect(endpointSuggestions(null, "x")).toEqual([]);
  });
});

describe("responses and history", () => {
  it("pretty-prints JSON bodies only", () => {
    expect(
      prettyBody('{"a":1}', { "Content-Type": "application/json" }),
    ).toEqual({
      text: '{\n  "a": 1\n}',
      json: true,
    });
    expect(prettyBody("[1,2]", {})).toEqual({
      text: "[\n  1,\n  2\n]",
      json: true,
    });
    expect(prettyBody('{"a":', { "content-type": "application/json" })).toEqual(
      { text: '{"a":', json: false },
    );
    expect(prettyBody("<html>", { "content-type": "text/html" })).toEqual({
      text: "<html>",
      json: false,
    });
  });

  it("tones status codes", () => {
    expect(statusTone(200)).toBe("success");
    expect(statusTone(302)).toBe("info");
    expect(statusTone(404)).toBe("warning");
    expect(statusTone(500)).toBe("danger");
    expect(statusTone(null)).toBe("danger");
  });

  it("keeps credentials out of history unless asked", () => {
    const headers = { Authorization: "Bearer t", Cookie: "s=1", Accept: "*/*" };
    expect(redactHeaders(headers, false)).toEqual({ Accept: "*/*" });
    expect(redactHeaders(headers, true)).toEqual(headers);
    const entry = historyEntry(
      { slot: "single", method: "GET", path: "/x", headers, body: "" },
      { id: "e1", status_code: 200, elapsed_ms: 12, error: "" },
      false,
      new Date("2026-01-01T00:00:00Z"),
    );
    expect(entry.headers).toEqual({ Accept: "*/*" });
    expect(entry.at).toBe("2026-01-01T00:00:00.000Z");
  });

  it("keeps the newest entries first, deduplicated and capped", () => {
    const make = (id: string) =>
      historyEntry(
        {
          slot: "single",
          method: "GET",
          path: `/${id}`,
          headers: {},
          body: "",
        },
        { id, status_code: 200, elapsed_ms: 1, error: "" },
        false,
      );
    let list = pushHistory([], make("1"));
    list = pushHistory(list, make("2"));
    list = pushHistory(list, make("1"));
    expect(list.map((e) => e.id)).toEqual(["1", "2"]);
    expect(pushHistory(list, make("3"), 2).map((e) => e.id)).toEqual([
      "3",
      "1",
    ]);
  });

  it("parses stored history defensively", () => {
    expect(parseHistory(null)).toEqual([]);
    expect(parseHistory("not json")).toEqual([]);
    expect(parseHistory('{"a":1}')).toEqual([]);
    const parsed = parseHistory(
      JSON.stringify([{ id: "1", path: "/x", method: "post" }, { nope: true }]),
    );
    expect(parsed).toHaveLength(1);
    expect(parsed[0].method).toBe("POST");
    expect(parsed[0].headers).toEqual({});
    const dupes = parseHistory(
      JSON.stringify([
        { id: "a", path: "/1", method: "GET" },
        { id: "a", path: "/2", method: "GET" },
        { path: "/no-id", method: "GET" },
      ]),
    );
    expect(dupes.map((e) => e.path)).toEqual(["/1"]);
  });

  it("explains rate limits", () => {
    expect(
      consoleErrorMessage(429, { code: "rate_limited", detail: "x" }),
    ).toMatch(/30 requests per minute/);
    expect(
      consoleErrorMessage(429, { detail: "Too many concurrent requests." }),
    ).toMatch(/Wait for your other requests/);
    expect(
      consoleErrorMessage(409, { code: "read_only", detail: "Read only." }),
    ).toBe("Read only.");
    expect(consoleErrorMessage(null, null)).toBe(
      "The request could not be sent.",
    );
  });
});
