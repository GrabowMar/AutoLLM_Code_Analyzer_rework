import { describe, expect, it } from "vitest";
import {
  fmtBytes,
  fmtInt,
  fmtSeconds,
  fmtShare,
  fmtUsd,
  humanizeKey,
  isPlainObject,
  llmSecondsOf,
  outputTokensPerLlmSecond,
  scalarText,
  shortHash,
} from "./format";

describe("insight formatters", () => {
  it("never renders a missing value as 0", () => {
    expect(fmtSeconds(null)).toBe("—");
    expect(fmtInt(undefined)).toBe("—");
    expect(fmtBytes(null)).toBe("—");
    expect(fmtUsd(null)).toBe("—");
    expect(fmtShare(1, 0)).toBe("—");
    expect(fmtShare(null, 10)).toBe("—");
    expect(shortHash(undefined)).toBe("—");
    expect(scalarText(null)).toBe("—");
    expect(scalarText("")).toBe("—");
  });

  it("never prints 60 in a seconds or minutes slot", () => {
    expect(fmtSeconds(0.9996)).toBe("1.0s");
    expect(fmtSeconds(59.96)).toBe("1m 0s");
    expect(fmtSeconds(119.6)).toBe("2m 0s");
    expect(fmtSeconds(3599.6)).toBe("1h 0m");
    expect(fmtSeconds(7199)).toBe("2h 0m");
    expect(fmtSeconds(5282.54)).toBe("1h 28m");
  });

  it("formats durations across scales", () => {
    expect(fmtSeconds(0.25)).toBe("250ms");
    expect(fmtSeconds(42.34)).toBe("42.3s");
    expect(fmtSeconds(192)).toBe("3m 12s");
    expect(fmtSeconds(5282)).toBe("1h 28m");
  });

  it("formats bytes, costs, shares and hashes", () => {
    expect(fmtBytes(267_717_744)).toBe("255.3 MB");
    expect(fmtUsd(0.0031)).toBe("$0.00310");
    expect(fmtUsd(0.465)).toBe("$0.465");
    expect(fmtShare(1, 4)).toBe("25%");
    expect(shortHash("sha256:abcdef0123456789")).toBe("abcdef012345");
    expect(humanizeKey("max_ccn_function")).toBe("Max ccn function");
  });

  it("tells plain objects apart", () => {
    expect(isPlainObject({ a: 1 })).toBe(true);
    expect(isPlainObject([1])).toBe(false);
    expect(isPlainObject(null)).toBe(false);
    expect(scalarText(true)).toBe("yes");
    expect(scalarText({ a: 1 })).toBe('{"a":1}');
  });
});

describe("throughput", () => {
  it("is output tokens per LLM-second, null when either side is unknown", () => {
    expect(outputTokensPerLlmSecond(49_110, 845.18)).toBeCloseTo(58.11, 1);
    expect(outputTokensPerLlmSecond(null, 100)).toBeNull();
    expect(outputTokensPerLlmSecond(100, null)).toBeNull();
    expect(outputTokensPerLlmSecond(100, 0)).toBeNull();
  });

  it("reads LLM seconds from the gateway total, then the trajectory split", () => {
    expect(llmSecondsOf({ agent: { llm_seconds: 845.18 } })).toBe(845.18);
    expect(
      llmSecondsOf({ agent: { trajectory: { time: { llm_s: 12.5 } } } }),
    ).toBe(12.5);
    expect(llmSecondsOf({ agent: {} })).toBeNull();
    expect(llmSecondsOf({})).toBeNull();
    expect(llmSecondsOf(null)).toBeNull();
  });
});
