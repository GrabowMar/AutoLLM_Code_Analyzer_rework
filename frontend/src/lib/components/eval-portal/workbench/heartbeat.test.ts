import { describe, expect, it } from "vitest";
import {
  MAX_SPAN_MS,
  VisibleTimeMeter,
  heartbeatPayload,
  viewingSlots,
} from "./heartbeat";

describe("VisibleTimeMeter", () => {
  it("reports visible time since the last beat", () => {
    const meter = new VisibleTimeMeter(0, true);
    expect(meter.take(30_000)).toBe(30);
    expect(meter.take(60_000)).toBe(30);
  });

  it("does not count hidden time", () => {
    const meter = new VisibleTimeMeter(0, true);
    meter.setVisible(false, 10_000);
    meter.setVisible(true, 25_000);
    expect(meter.take(30_000)).toBe(15);
    meter.setVisible(false, 30_000);
    expect(meter.take(60_000)).toBe(0);
  });

  it("starts hidden when the tab opens in the background", () => {
    const meter = new VisibleTimeMeter(0, false);
    expect(meter.take(30_000)).toBe(0);
    meter.setVisible(true, 30_000);
    expect(meter.take(40_500)).toBe(10);
  });

  it("carries fractions to the next beat", () => {
    const meter = new VisibleTimeMeter(0, true);
    expect(meter.take(1_500)).toBe(1);
    expect(meter.take(3_000)).toBe(2);
  });

  it("caps a single stretch so a sleeping device adds no time", () => {
    const meter = new VisibleTimeMeter(0, true);
    expect(meter.take(3_600_000)).toBe(MAX_SPAN_MS / 1000);
    expect(meter.take(3_630_000)).toBe(30);
  });

  it("caps a beat at the server limit and keeps a bounded backlog", () => {
    const meter = new VisibleTimeMeter(0, true);
    meter.restore(50);
    expect(meter.take(30_000)).toBe(60);
    expect(meter.take(30_000)).toBe(20);
    expect(meter.take(30_000)).toBe(0);
  });

  it("restores seconds of a failed beat", () => {
    const meter = new VisibleTimeMeter(0, true);
    const sent = meter.take(30_000);
    meter.restore(sent);
    expect(meter.take(60_000)).toBe(60);
    meter.restore(-5);
    meter.restore(0);
    expect(meter.pendingMs).toBe(0);
  });
});

describe("heartbeat payload", () => {
  it("records the current visibility and slots", () => {
    const meter = new VisibleTimeMeter(0, true);
    const beat = heartbeatPayload(meter, 30_000, false, ["B", "A", "B"]);
    expect(beat).toEqual({
      active_seconds: 30,
      visible: false,
      viewing: ["A", "B"],
    });
    const next = heartbeatPayload(meter, 60_000, false, []);
    expect(next).toEqual({ active_seconds: 0, visible: false, viewing: [] });
  });

  it("orders and deduplicates viewed slots", () => {
    expect(viewingSlots(["single"], new Set(["single" as const]))).toEqual([
      "single",
    ]);
    expect(viewingSlots(["B"], ["A"])).toEqual(["A", "B"]);
    expect(viewingSlots()).toEqual([]);
  });
});
