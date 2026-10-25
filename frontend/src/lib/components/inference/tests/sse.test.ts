import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  subscribe,
  SSE_EVENT_NAMES,
  type SseEvent,
  type SseState,
} from "$lib/api/sse";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  closed = false;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, ((ev: MessageEvent) => void)[]>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (ev: MessageEvent) => void) {
    const list = this.listeners.get(type) ?? [];
    list.push(fn);
    this.listeners.set(type, list);
  }
  close() {
    this.closed = true;
  }
  emit(type: string, data: unknown) {
    const ev = {
      data: typeof data === "string" ? data : JSON.stringify(data),
    } as MessageEvent;
    for (const fn of this.listeners.get(type) ?? []) fn(ev);
  }
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.useFakeTimers();
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("subscribe", () => {
  it("registers the rework event names", () => {
    for (const name of [
      "job",
      "tick",
      "state",
      "post_run",
      "endpoint",
      "session",
      "capacity",
      "status",
    ]) {
      expect(SSE_EVENT_NAMES).toContain(name);
    }
    expect(new Set(SSE_EVENT_NAMES).size).toBe(SSE_EVENT_NAMES.length);
  });

  it("delivers named and default events and reports state", () => {
    const events: SseEvent[] = [];
    const states: SseState[] = [];
    const stop = subscribe(
      ["experiment:1", "capacity"],
      (e) => events.push(e),
      {
        onState: (s) => states.push(s),
      },
    );
    const es = FakeEventSource.instances[0];
    expect(es.url).toBe(
      "/api/realtime/stream?channels=experiment%3A1%2Ccapacity",
    );

    es.onopen?.();
    es.emit("tick", { type: "tick", eta_seconds: 5 });
    es.emit("message", { type: "job", job_id: "j" });
    es.emit("message", { no_type: true });
    es.emit("capacity", "not json");
    expect(events.map((e) => e.type)).toEqual(["tick", "job", "update"]);
    expect(events[0].data.eta_seconds).toBe(5);
    expect(states).toEqual(["open"]);

    stop();
    expect(es.closed).toBe(true);
    expect(states).toEqual(["open", "closed"]);
    stop(); // idempotent
    expect(states).toEqual(["open", "closed"]);
  });

  it("reconnects with backoff and reports reconnecting", () => {
    const states: SseState[] = [];
    const stop = subscribe(["capacity"], () => {}, {
      onState: (s) => states.push(s),
    });
    const first = FakeEventSource.instances[0];
    first.onerror?.();
    expect(first.closed).toBe(true);
    expect(states).toEqual(["reconnecting"]);
    vi.advanceTimersByTime(999);
    expect(FakeEventSource.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeEventSource.instances).toHaveLength(2);

    // Second failure waits longer (2 s).
    FakeEventSource.instances[1].onerror?.();
    vi.advanceTimersByTime(1_999);
    expect(FakeEventSource.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(FakeEventSource.instances).toHaveLength(3);

    FakeEventSource.instances[2].onopen?.();
    expect(states).toEqual(["reconnecting", "open"]);

    stop();
    FakeEventSource.instances[2].onerror?.();
    vi.advanceTimersByTime(60_000);
    expect(FakeEventSource.instances).toHaveLength(3);
    expect(states.at(-1)).toBe("closed");
  });

  it("works without options (legacy callers)", () => {
    const seen: string[] = [];
    const stop = subscribe(["generation:x"], (e) => seen.push(e.type));
    FakeEventSource.instances[0].emit("status", { status: "running" });
    expect(seen).toEqual(["status"]);
    stop();
  });

  it("survives a throwing onState callback", () => {
    const events: string[] = [];
    const stop = subscribe(["capacity"], (e) => events.push(e.type), {
      onState: () => {
        throw new Error("consumer bug");
      },
    });
    FakeEventSource.instances[0].emit("capacity", { type: "capacity" });
    expect(events).toEqual(["capacity"]);
    stop();
  });
});
