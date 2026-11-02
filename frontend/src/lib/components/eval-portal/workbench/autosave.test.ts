import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  AUTOSAVE_DELAY_MS,
  BlockAutosave,
  worstSaveState,
} from "./autosave.svelte";

function deferred<T = unknown>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("BlockAutosave", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("debounces per block and saves the newest value", async () => {
    const save = vi.fn(async () => ({ ok: true }));
    const autosave = new BlockAutosave<number>(save);
    autosave.schedule("a", 1);
    autosave.schedule("a", 2);
    autosave.schedule("b", 9);
    expect(autosave.states).toEqual({ a: "pending", b: "pending" });
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS - 1);
    expect(save).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(save.mock.calls).toEqual([
      ["a", 2],
      ["b", 9],
    ]);
    expect(autosave.states).toEqual({ a: "saved", b: "saved" });
    expect(autosave.lastSavedAt).not.toBeNull();
    expect(autosave.overall).toBe("saved");
    expect(autosave.busy).toBe(false);
  });

  it("never overlaps saves of one block", async () => {
    const first = deferred();
    const save = vi
      .fn<(key: string, value: number) => Promise<unknown>>()
      .mockImplementationOnce(() => first.promise)
      .mockResolvedValue({});
    const autosave = new BlockAutosave<number>(save);
    autosave.schedule("a", 1);
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS);
    expect(save).toHaveBeenCalledTimes(1);
    autosave.schedule("a", 2);
    autosave.schedule("a", 3);
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS);
    expect(save).toHaveBeenCalledTimes(1);
    expect(autosave.states.a).toBe("saving");
    first.resolve({});
    await vi.advanceTimersByTimeAsync(0);
    expect(save.mock.calls).toEqual([
      ["a", 1],
      ["a", 3],
    ]);
    expect(autosave.states.a).toBe("saved");
  });

  it("keeps a failed value for retry and reports the error", async () => {
    const onError = vi.fn();
    const save = vi
      .fn<(key: string, value: string) => Promise<unknown>>()
      .mockRejectedValueOnce({ detail: "offline" })
      .mockResolvedValue({});
    const autosave = new BlockAutosave<string>(save, { onError });
    autosave.schedule("a", "x");
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS);
    expect(autosave.states.a).toBe("error");
    expect(autosave.failedKeys).toEqual(["a"]);
    expect(autosave.errors.a).toEqual({ detail: "offline" });
    expect(onError).toHaveBeenCalledWith("a", { detail: "offline" });
    expect(await autosave.retry("a")).toBe(true);
    expect(save.mock.calls).toEqual([
      ["a", "x"],
      ["a", "x"],
    ]);
    expect(autosave.states.a).toBe("saved");
    expect(autosave.errors).toEqual({});
  });

  it("flushes pending values immediately", async () => {
    const save = vi.fn(async () => ({}));
    const autosave = new BlockAutosave<number>(save);
    autosave.schedule("a", 1);
    autosave.schedule("b", 2);
    expect(autosave.busy).toBe(true);
    expect(await autosave.flush()).toBe(true);
    expect(save).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS * 2);
    expect(save).toHaveBeenCalledTimes(2);
  });

  it("flush reports failure", async () => {
    const save = vi.fn(async () => {
      throw { detail: "nope" };
    });
    const autosave = new BlockAutosave<number>(save);
    autosave.schedule("a", 1);
    expect(await autosave.flush()).toBe(false);
    expect(autosave.overall).toBe("error");
  });

  it("stops accepting work", async () => {
    const save = vi.fn(async () => ({}));
    const autosave = new BlockAutosave<number>(save);
    autosave.schedule("a", 1);
    autosave.stop();
    autosave.schedule("a", 2);
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS * 2);
    expect(save).not.toHaveBeenCalled();
  });

  // C05: a flush during an in-flight save must also wait for the save of the
  // newer value that starts when the first one settles.
  it("flush waits for the newer value queued behind an in-flight save", async () => {
    const first = deferred();
    const second = deferred();
    const save = vi
      .fn<(key: string, value: number) => Promise<unknown>>()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise)
      .mockResolvedValue({});
    const autosave = new BlockAutosave<number>(save);
    autosave.schedule("a", 1);
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS);
    expect(save).toHaveBeenCalledTimes(1);
    autosave.schedule("a", 2);
    let flushed: boolean | null = null;
    void autosave.flush().then((ok) => (flushed = ok));
    first.resolve({});
    await vi.advanceTimersByTimeAsync(0);
    expect(save.mock.calls).toEqual([
      ["a", 1],
      ["a", 2],
    ]);
    expect(flushed).toBeNull();
    expect(autosave.states.a).toBe("saving");
    second.resolve({});
    await vi.advanceTimersByTimeAsync(0);
    expect(flushed).toBe(true);
    expect(autosave.states.a).toBe("saved");
    expect(autosave.busy).toBe(false);
    expect(save).toHaveBeenCalledTimes(2);
  });

  it("flush waits for a save chained by a timer that fired during the in-flight save", async () => {
    const first = deferred();
    const second = deferred();
    const save = vi
      .fn<(key: string, value: number) => Promise<unknown>>()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise)
      .mockResolvedValue({});
    const autosave = new BlockAutosave<number>(save);
    autosave.schedule("a", 1);
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS);
    autosave.schedule("a", 2);
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS); // the second timer chains behind save #1
    expect(save).toHaveBeenCalledTimes(1);
    let flushed: boolean | null = null;
    void autosave.flush().then((ok) => (flushed = ok));
    first.resolve({});
    await vi.advanceTimersByTimeAsync(0);
    expect(save.mock.calls.map((c) => c[1])).toEqual([1, 2]);
    expect(flushed).toBeNull();
    second.resolve({});
    await vi.advanceTimersByTimeAsync(0);
    expect(flushed).toBe(true);
    expect(save).toHaveBeenCalledTimes(2);
  });

  it("flush retries a failed value once, then reports the failure", async () => {
    const save = vi.fn(async () => {
      throw { detail: "offline" };
    });
    const autosave = new BlockAutosave<number>(save);
    autosave.schedule("a", 1);
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS);
    expect(save).toHaveBeenCalledTimes(1);
    expect(await autosave.flush()).toBe(false);
    expect(save).toHaveBeenCalledTimes(2);
  });

  it("combines savers into the worst state", () => {
    expect(worstSaveState()).toBe("idle");
    expect(worstSaveState("saved", "idle")).toBe("saved");
    expect(worstSaveState("saved", "pending")).toBe("pending");
    expect(worstSaveState("pending", "saving", "saved")).toBe("saving");
    expect(worstSaveState("saving", "error")).toBe("error");
  });

  it("passes the server result to onSaved", async () => {
    const onSaved = vi.fn();
    const autosave = new BlockAutosave<number>(
      async () => ({ updated_at: "t" }),
      { onSaved },
    );
    autosave.schedule("a", 1);
    await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS);
    expect(onSaved).toHaveBeenCalledWith("a", { updated_at: "t" });
  });
});
