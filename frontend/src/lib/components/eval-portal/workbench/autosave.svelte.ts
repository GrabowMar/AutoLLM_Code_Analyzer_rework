// Per-block autosave for the workbench: every change is debounced (800 ms)
// per block, saves of one block never overlap (the newest value wins), and
// `flush()` writes everything pending right away and waits until it is stored
// (before submit, when the tab is hidden or the participant navigates away).

export type SaveState = "idle" | "pending" | "saving" | "saved" | "error";

export const AUTOSAVE_DELAY_MS = 800;

export interface AutosaveDeps {
  setTimeout: (fn: () => void, ms: number) => unknown;
  clearTimeout: (handle: unknown) => void;
  now: () => number;
}

const defaultDeps: AutosaveDeps = {
  setTimeout: (fn, ms) => globalThis.setTimeout(fn, ms),
  clearTimeout: (handle) =>
    globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>),
  now: () => Date.now(),
};

export interface AutosaveOptions {
  delayMs?: number;
  deps?: Partial<AutosaveDeps>;
  /** Called when a save fails (e.g. to switch to read-only on 409). */
  onError?: (key: string, error: unknown) => void;
  /** Called after a successful save with the server's response. */
  onSaved?: (key: string, result: unknown) => void;
}

interface Slot<T> {
  timer: unknown;
  /** Latest value not yet handed to `save`. */
  pending: { value: T } | null;
  inFlight: Promise<void> | null;
}

const SAVE_STATE_RANK: Record<SaveState, number> = {
  error: 4,
  saving: 3,
  pending: 2,
  saved: 1,
  idle: 0,
};

/** The worst of several savers' states (error > saving > pending > saved > idle). */
export function worstSaveState(...states: SaveState[]): SaveState {
  return states.reduce<SaveState>(
    (worst, s) => (SAVE_STATE_RANK[s] > SAVE_STATE_RANK[worst] ? s : worst),
    "idle",
  );
}

// Re-read after an await (TypeScript keeps the pre-await narrowing).
function inFlightOf(slot: {
  inFlight: Promise<void> | null;
}): Promise<void> | null {
  return slot.inFlight;
}

export class BlockAutosave<T> {
  /** Save state per key (block id). */
  states = $state<Record<string, SaveState>>({});
  /** Last save error per key (the thrown API error body). */
  errors = $state.raw<Record<string, unknown>>({});
  /** Time (ms) of the newest successful save. */
  lastSavedAt = $state<number | null>(null);

  #save: (key: string, value: T) => Promise<unknown>;
  #delay: number;
  #deps: AutosaveDeps;
  #onError?: AutosaveOptions["onError"];
  #onSaved?: AutosaveOptions["onSaved"];
  #slots = new Map<string, Slot<T>>();
  #stopped = false;

  constructor(
    save: (key: string, value: T) => Promise<unknown>,
    options: AutosaveOptions = {},
  ) {
    this.#save = save;
    this.#delay = options.delayMs ?? AUTOSAVE_DELAY_MS;
    this.#deps = { ...defaultDeps, ...options.deps };
    this.#onError = options.onError;
    this.#onSaved = options.onSaved;
  }

  #slot(key: string): Slot<T> {
    let slot = this.#slots.get(key);
    if (!slot) {
      slot = { timer: null, pending: null, inFlight: null };
      this.#slots.set(key, slot);
    }
    return slot;
  }

  /** Queue `value` for `key`; it is saved after the debounce delay. */
  schedule(key: string, value: T): void {
    if (this.#stopped) return;
    const slot = this.#slot(key);
    slot.pending = { value };
    if (slot.timer !== null) this.#deps.clearTimeout(slot.timer);
    slot.timer = this.#deps.setTimeout(() => {
      slot.timer = null;
      void this.#run(key);
    }, this.#delay);
    if (!slot.inFlight) this.states[key] = "pending";
  }

  async #run(key: string): Promise<void> {
    const slot = this.#slot(key);
    if (slot.inFlight) {
      // A save of this block is running: chain behind it, newest value wins.
      await slot.inFlight;
      // Another waiter may already have started the next save.
      if (inFlightOf(slot)) return this.#run(key);
    }
    if (!slot.pending) return;
    const { value } = slot.pending;
    slot.pending = null;
    this.states[key] = "saving";
    const job = (async () => {
      try {
        const result = await this.#save(key, value);
        if (slot.pending === null) this.states[key] = "saved";
        if (key in this.errors) {
          const next = { ...this.errors };
          delete next[key];
          this.errors = next;
        }
        this.lastSavedAt = this.#deps.now();
        this.#onSaved?.(key, result);
      } catch (error) {
        // Keep the unsaved value so retry() / the next flush sends it again.
        if (slot.pending === null) slot.pending = { value };
        this.states[key] = "error";
        this.errors = { ...this.errors, [key]: error };
        this.#onError?.(key, error);
      }
    })();
    slot.inFlight = job;
    try {
      await job;
    } finally {
      slot.inFlight = null;
    }
    if (slot.pending && slot.timer === null && this.#stateOf(key) !== "error") {
      // Changes arrived while saving and their timer already fired.
      await this.#run(key);
    }
  }

  #stateOf(key: string): SaveState | undefined {
    return this.states[key];
  }

  /**
   * Save every pending value now. Resolves only once no save of any block is
   * running or waiting (a save chained behind the one in flight included), so
   * the newest values are stored when it returns; true when nothing failed.
   */
  async flush(): Promise<boolean> {
    const keys = [...this.#slots.keys()];
    await Promise.all(keys.map((key) => this.#drain(key)));
    return keys.every((key) => this.states[key] !== "error");
  }

  async #drain(key: string): Promise<void> {
    const slot = this.#slot(key);
    if (slot.timer !== null) {
      this.#deps.clearTimeout(slot.timer);
      slot.timer = null;
    }
    // A failed save keeps its value pending; send it once more, not in a loop.
    let attempted = false;
    for (;;) {
      const running = inFlightOf(slot);
      if (running) {
        // When it settles, #run may start the next save of this block at once.
        await running;
        continue;
      }
      if (!slot.pending || attempted || this.#stopped) return;
      attempted = true;
      await this.#run(key);
    }
  }

  /** Re-send the unsaved value of a block whose save failed. */
  retry(key: string): Promise<boolean> {
    const slot = this.#slot(key);
    if (slot.pending) this.states[key] = "pending";
    return this.flush();
  }

  /** Something is waiting or being written. */
  get busy(): boolean {
    for (const [key, slot] of this.#slots) {
      if (slot.pending || slot.inFlight || slot.timer !== null) return true;
      if (this.states[key] === "saving" || this.states[key] === "pending")
        return true;
    }
    return false;
  }

  /** The worst state over all blocks (error > saving > pending > saved > idle). */
  get overall(): SaveState {
    return worstSaveState(...Object.values(this.states));
  }

  /** Keys whose last save failed. */
  get failedKeys(): string[] {
    return Object.entries(this.states)
      .filter(([, state]) => state === "error")
      .map(([key]) => key);
  }

  /** Drop timers and refuse new work (e.g. when the assignment became read-only). */
  stop(): void {
    this.#stopped = true;
    for (const slot of this.#slots.values()) {
      if (slot.timer !== null) this.#deps.clearTimeout(slot.timer);
      slot.timer = null;
      slot.pending = null;
    }
  }
}
