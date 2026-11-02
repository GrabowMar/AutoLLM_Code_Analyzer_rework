import { describe, expect, it } from "vitest";
import {
  auxiliaryCallsNote,
  categoryLabel,
  effectiveSteps,
  effectiveToolCalls,
  fmtBool,
  fmtInt,
  fmtOffset,
  fmtPct,
  fmtSeconds,
  fmtTokensShort,
  lineGeometry,
  niceCeil,
  prettyArgs,
  readTelemetry,
  sortedCounts,
} from "./format";

describe("formatters", () => {
  it("never turns a missing value into 0", () => {
    expect(fmtInt(null)).toBe("—");
    expect(fmtPct(undefined)).toBe("—");
    expect(fmtSeconds(Number.NaN)).toBe("—");
    expect(fmtBool(null)).toBe("—");
    expect(fmtTokensShort(undefined)).toBe("—");
  });

  it("formats durations and offsets", () => {
    expect(fmtSeconds(4.25)).toBe("4.3 s");
    expect(fmtSeconds(45.2)).toBe("45 s");
    expect(fmtSeconds(817)).toBe("13m 37s");
    expect(fmtSeconds(7500)).toBe("2h 05m");
    expect(fmtOffset(75.2)).toBe("+1:15");
  });

  it("formats shares and token counts", () => {
    expect(fmtPct(0.9714)).toBe("97%");
    expect(fmtPct(0.5544, 1)).toBe("55.4%");
    expect(fmtTokensShort(46873)).toBe("47k");
    expect(fmtTokensShort(8006)).toBe("8.0k");
    expect(fmtTokensShort(1_250_000)).toBe("1.3M");
  });

  it("labels categories, falling back to the key", () => {
    expect(categoryLabel("probe_http")).toBe("Probe HTTP");
    expect(categoryLabel("brand_new")).toBe("brand new");
  });

  it("sorts counts largest first and drops zeros", () => {
    expect(sortedCounts({ a: 1, b: 5, c: 0 })).toEqual([
      ["b", 5],
      ["a", 1],
    ]);
    expect(sortedCounts(null)).toEqual([]);
  });
});

describe("effective counts", () => {
  it("prefers the harness count", () => {
    expect(effectiveToolCalls(110, 110)).toEqual({
      value: 110,
      source: "harness",
      note: null,
    });
    expect(effectiveToolCalls(32, 30).note).toBe("gateway log: 30");
  });

  it("falls back to the gateway when the harness report is missing", () => {
    const shown = effectiveToolCalls(0, 111);
    expect(shown.value).toBe(111);
    expect(shown.source).toBe("gateway");
    expect(shown.note).toContain("harness report missing");
    expect(effectiveSteps(0, 98).value).toBe(98);
  });

  it("keeps a real zero and an unknown apart", () => {
    expect(effectiveToolCalls(0, 0).value).toBe(0);
    expect(effectiveToolCalls(undefined, undefined).value).toBeNull();
  });

  it("explains auxiliary calls", () => {
    expect(auxiliaryCallsNote({ main: 1, summarizer: 2 }, 3)).toBe(
      "2 of 3 LLM calls were auxiliary (2 summarizer), not agent turns",
    );
    expect(auxiliaryCallsNote({ main: 100 }, 100)).toBeNull();
  });
});

describe("lineGeometry", () => {
  it("scales the series to a nice axis top", () => {
    const g = lineGeometry([8000, 20000, 46873], 200, 100, {
      left: 0,
      right: 0,
      top: 0,
      bottom: 0,
    });
    expect(g.max).toBe(46873);
    expect(g.yTicks.at(-1)?.value).toBe(50000);
    expect(g.points[0].x).toBe(0);
    expect(g.points[2].x).toBe(200);
    expect(g.path.startsWith("M0.0,")).toBe(true);
  });

  it("handles empty and single-point series", () => {
    expect(lineGeometry([], 100, 50).path).toBe("");
    expect(lineGeometry([5], 100, 50).points).toHaveLength(1);
    expect(niceCeil(0)).toBe(0);
    expect(niceCeil(3)).toBe(5);
  });
});

describe("readTelemetry", () => {
  it("reads contract C1 defensively", () => {
    const view = readTelemetry({
      duration_s: 2.3,
      provider: "Novita",
      upstream_retries: 1,
      tokens: { prompt: 10, completion: 2, cached: 8, reasoning: null },
      openrouter: { status: "ok", latency_ms: 420, decode_tps: 55.5 },
    });
    expect(view?.ttft_ms).toBe(420);
    expect(view?.decode_tps).toBe(55.5);
    expect(view?.cached).toBe(8);
    expect(view?.reasoning).toBeNull();
    expect(view?.retries).toBe(1);
  });

  it("returns null for empty or malformed telemetry", () => {
    expect(readTelemetry({})).toBeNull();
    expect(readTelemetry(null)).toBeNull();
    expect(readTelemetry("x")).toBeNull();
    expect(readTelemetry({ tokens: "bad" })).toBeNull();
  });
});

describe("prettyArgs", () => {
  it("pretty-prints JSON strings and keeps broken ones", () => {
    expect(prettyArgs('{"command":"ls"}')).toBe('{\n  "command": "ls"\n}');
    expect(prettyArgs('{"command":"ls')).toBe('{"command":"ls');
  });
});
