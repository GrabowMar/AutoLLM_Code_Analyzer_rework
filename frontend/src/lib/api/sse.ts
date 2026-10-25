/**
 * SSE subscription utility for realtime task progress.
 *
 * Usage:
 *   const cleanup = subscribe(['generation:42', 'dashboard'], (e) => {
 *     console.log(e.type, e.data);
 *   }, { onState: (s) => (live = s === 'open') });
 *   // later…
 *   cleanup();
 *
 * The connection reconnects with exponential backoff (1 s → 30 s). `onState`
 * reports "open" once the stream is connected, "reconnecting" while it waits
 * to retry (use this to start a polling fallback), and "closed" after cleanup.
 */

import { REWORK_EVENT_TYPES } from "./types/events";

export interface SseEvent {
  type: string;
  data: Record<string, unknown>;
}

export type SseState = "open" | "reconnecting" | "closed";

export interface SubscribeOptions {
  onState?: (state: SseState) => void;
}

const BASE_BACKOFF_MS = 1_000;
const MAX_BACKOFF_MS = 30_000;

/** Named `event:` types the stream listener registers (legacy + rework, §F.6). */
export const SSE_EVENT_NAMES: readonly string[] = [
  ...new Set(["status", "progress", "result", "update", ...REWORK_EVENT_TYPES]),
];

/**
 * Subscribe to one or more SSE channels.
 *
 * @param channels  Channel strings, e.g. `['generation:1', 'analysis:2']`
 * @param onEvent   Callback invoked for each server-sent event
 * @param options   `onState(state)` connection-state callback
 * @returns         A cleanup function that closes the connection
 */
export function subscribe(
  channels: string[],
  onEvent: (e: SseEvent) => void,
  options: SubscribeOptions = {},
): () => void {
  const { onState } = options;
  let es: EventSource | null = null;
  let closed = false;
  let backoff = BASE_BACKOFF_MS;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let state: SseState | null = null;

  function setState(next: SseState) {
    if (state === next) return;
    state = next;
    try {
      onState?.(next);
    } catch {
      // a consumer's callback must not break the stream
    }
  }

  function deliver(type: string, raw: string) {
    let data: Record<string, unknown>;
    try {
      data = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return; // ignore malformed message
    }
    backoff = BASE_BACKOFF_MS; // reset on success
    setState("open");
    onEvent({ type, data });
  }

  function connect() {
    if (closed) return;

    const url = `/api/realtime/stream?channels=${encodeURIComponent(channels.join(","))}`;
    const source = new EventSource(url, { withCredentials: true });
    es = source;

    source.onopen = () => {
      backoff = BASE_BACKOFF_MS;
      setState("open");
    };

    source.addEventListener("message", (ev: MessageEvent) => {
      let type = "update";
      try {
        const parsed = JSON.parse(ev.data) as Record<string, unknown>;
        if (typeof parsed["type"] === "string") type = parsed["type"];
      } catch {
        return;
      }
      deliver(type, ev.data);
    });

    // Named event types (e.g. "event: status", "event: tick")
    for (const eventType of SSE_EVENT_NAMES) {
      source.addEventListener(eventType, (ev: MessageEvent) => {
        deliver(eventType, ev.data);
      });
    }

    source.onerror = () => {
      source.close();
      if (es === source) es = null;
      if (closed) return;
      setState("reconnecting");
      // Exponential backoff reconnect
      retryTimer = setTimeout(() => {
        retryTimer = null;
        backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
        connect();
      }, backoff);
    };
  }

  connect();

  return () => {
    if (closed) return;
    closed = true;
    if (retryTimer !== null) {
      clearTimeout(retryTimer);
      retryTimer = null;
    }
    es?.close();
    es = null;
    setState("closed");
  };
}
