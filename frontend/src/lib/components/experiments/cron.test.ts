import { describe, expect, it } from "vitest";
import {
  CRON_PRESETS,
  describeCron,
  looksLikeCron,
  presetForCron,
} from "./cron";

describe("cron presets", () => {
  it("recognises presets regardless of whitespace", () => {
    expect(presetForCron("0  2 * * *")).toBe("nightly");
    expect(presetForCron(" 0 2 * * 1 ")).toBe("weekly");
    expect(presetForCron("*/5 * * * *")).toBe("custom");
    expect(CRON_PRESETS.map((p) => p.label)).toEqual([
      "Nightly 02:00",
      "Weekly Mon 02:00",
    ]);
  });

  it("describes common shapes", () => {
    expect(describeCron("0 2 * * *")).toBe("Every day at 02:00");
    expect(describeCron("30 9 * * 1-5")).toBe("Weekdays at 09:30");
    expect(describeCron("0 2 * * 1")).toBe("Mondays at 02:00");
    expect(describeCron("0 2 * * 7")).toBe("Sundays at 02:00");
    expect(describeCron("15 3 1 * *")).toBe("Monthly on day 1 at 03:15");
    expect(describeCron("0 */6 * * *")).toBe("Every 6 hours at :00");
    expect(describeCron("5 * * * *")).toBe("Every hour at :05");
    expect(describeCron("0 2 * 1 *")).toBe("0 2 * 1 *");
    expect(describeCron("nonsense")).toBe("nonsense");
  });

  it("checks the five-field shape", () => {
    expect(looksLikeCron("0 2 * * *")).toBe(true);
    expect(looksLikeCron("0 2 * *")).toBe(false);
    expect(looksLikeCron("0 2 * * ; rm")).toBe(false);
  });
});
