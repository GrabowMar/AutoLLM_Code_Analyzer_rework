// Heartbeat accounting for the workbench (spec R1 "Heartbeats/timing").
// A beat goes out every 30 s while the workbench is open, visible or not;
// `active_seconds` carries only the visible-tab time since the last beat and
// `viewing` the slots whose app is on screen or was opened in another tab.
import type { HeartbeatInput } from "$lib/api/types/human-eval";

export type Slot = "A" | "B" | "single";

export const HEARTBEAT_INTERVAL_MS = 30_000;
/** The server adds at most this much active time per beat. */
export const MAX_BEAT_SECONDS = 60;
/**
 * Longest stretch one observation may add. Timers of a visible tab fire on
 * time, so a longer gap means the device slept with the tab "visible".
 */
export const MAX_SPAN_MS = 60_000;

const SLOT_ORDER: Slot[] = ["A", "B", "single"];

/**
 * Accumulates visible-tab time between beats. Pure (the caller passes `now`),
 * so it is unit-tested without timers.
 */
export class VisibleTimeMeter {
  #visible: boolean;
  #mark: number;
  #ms = 0;

  constructor(now: number, visible: boolean) {
    this.#visible = visible;
    this.#mark = now;
  }

  get visible(): boolean {
    return this.#visible;
  }

  /** Milliseconds banked so far (not yet reported). */
  get pendingMs(): number {
    return this.#ms;
  }

  #advance(now: number): void {
    if (this.#visible) {
      const span = Math.max(0, now - this.#mark);
      this.#ms += Math.min(span, MAX_SPAN_MS);
    }
    this.#mark = now;
  }

  /** Record a visibility change at `now`. */
  setVisible(visible: boolean, now: number): void {
    this.#advance(now);
    this.#visible = visible;
  }

  /**
   * Whole seconds of visible time to report now (at most `cap`). Fractions
   * and any excess carry over to the next beat; the backlog never exceeds
   * one beat's cap.
   */
  take(now: number, cap: number = MAX_BEAT_SECONDS): number {
    this.#advance(now);
    const whole = Math.floor(this.#ms / 1000);
    const sent = Math.max(0, Math.min(whole, cap));
    this.#ms -= sent * 1000;
    this.#ms = Math.min(this.#ms, cap * 1000);
    return sent;
  }

  /** Put back seconds a failed beat could not deliver. */
  restore(seconds: number, cap: number = MAX_BEAT_SECONDS): void {
    if (!(seconds > 0)) return;
    this.#ms = Math.min(this.#ms + seconds * 1000, cap * 1000);
  }
}

/** Unique slots in canonical order (A, B, single). */
export function viewingSlots(...groups: Iterable<Slot>[]): Slot[] {
  const seen = new Set<Slot>();
  for (const group of groups) for (const slot of group) seen.add(slot);
  return SLOT_ORDER.filter((slot) => seen.has(slot));
}

/** The beat payload: visible time since the last beat, visibility and viewed slots. */
export function heartbeatPayload(
  meter: VisibleTimeMeter,
  now: number,
  visible: boolean,
  viewing: Slot[],
): HeartbeatInput {
  meter.setVisible(visible, now);
  return {
    active_seconds: meter.take(now),
    visible,
    viewing: viewingSlots(viewing),
  };
}
