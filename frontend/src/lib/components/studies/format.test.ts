import { describe, expect, it } from "vitest";
import {
  accessionId,
  DASH,
  formatPct,
  formatSeconds,
  formatSecondsRange,
  formatTokenCount,
  formatUsd,
  isActive,
  isEditable,
  isTerminal,
  outcomeLabel,
  pauseLabel,
  plural,
  refLabel,
  runTone,
  spendFraction,
} from "./format";

describe("money and fractions", () => {
  it("never prints a missing value as zero", () => {
    expect(formatUsd(null)).toBe(DASH);
    expect(formatUsd(undefined)).toBe(DASH);
    expect(formatUsd(Number.NaN)).toBe(DASH);
    expect(formatPct(null)).toBe(DASH);
    expect(formatTokenCount(null)).toBe(DASH);
    expect(formatSeconds(null)).toBe(DASH);
  });

  it("prints a measured zero and adapts the precision", () => {
    expect(formatUsd(0)).toBe("$0.00");
    expect(formatUsd(0.0042)).toBe("$0.0042");
    expect(formatUsd(0.31)).toBe("$0.31");
    expect(formatUsd(12.4)).toBe("$12.40");
    expect(formatUsd(1240.4)).toBe("$1,240");
    expect(formatUsd(-0.5)).toBe("−$0.50");
  });

  it("formats percentages, tokens and spend fractions", () => {
    expect(formatPct(0.125, 1)).toBe("12.5%");
    expect(formatTokenCount(1_234_567)).toBe("1.2M");
    expect(formatTokenCount(34_400)).toBe("34k");
    expect(formatTokenCount(1500)).toBe("1.5k");
    expect(spendFraction(2, 8)).toBe(0.25);
    expect(spendFraction(2, 0)).toBeNull();
    expect(spendFraction(null, 8)).toBeNull();
  });
});

describe("durations", () => {
  it("picks a readable unit", () => {
    expect(formatSeconds(45)).toBe("45 s");
    expect(formatSeconds(720)).toBe("12 min");
    expect(formatSeconds(3.4 * 3600)).toBe("3.4 h");
    expect(formatSeconds(2.1 * 86400)).toBe("2.1 d");
    expect(formatSecondsRange(7200, 7200)).toBe("2.0 h");
    expect(formatSecondsRange(7200, 14400)).toBe("2.0 h – 4.0 h");
  });
});

describe("labels and states", () => {
  it("labels outcomes, pauses and refs", () => {
    expect(outcomeLabel("build_fail")).toBe("Build failed");
    expect(outcomeLabel("")).toBe(DASH);
    expect(pauseLabel("spend_cap")).toBe("A spend cap blocks new runs");
    expect(pauseLabel("rule:infra_error_rate")).toBe(
      "Stop rule infra error rate",
    );
    expect(refLabel("stack@3#abcdef123456")).toBe("stack@3");
    expect(accessionId("955a5bd5-1111-4000-8000-000000000000")).toBe(
      "STU-955A5BD5",
    );
    expect(plural(1, "cell")).toBe("1 cell");
    expect(plural(1200, "cell")).toBe("1,200 cells");
  });

  it("knows the lifecycle", () => {
    expect(isActive("piloting")).toBe(true);
    expect(isTerminal("stopped")).toBe(true);
    expect(isEditable("planned")).toBe(true);
    expect(isEditable("planned", true)).toBe(false);
    expect(isEditable("running")).toBe(false);
    expect(runTone("infra_error")).toBe("warning");
    expect(runTone("tested")).toBe("success");
    expect(runTone("unknown-thing")).toBe("neutral");
  });
});
