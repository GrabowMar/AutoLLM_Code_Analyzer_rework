import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SlotTargetIn, SlotTargetPreviewOut } from "$lib/api/v3/types";

type Pending = {
  body: SlotTargetIn;
  signal: AbortSignal | undefined;
  resolve: (p: SlotTargetPreviewOut) => void;
  reject: (e: unknown) => void;
};
const pending: Pending[] = [];
const created: SlotTargetIn[] = [];

vi.mock("$lib/api/v3/slots", () => ({
  previewSlotTarget: vi.fn(
    (_setId: string, body: SlotTargetIn, opts?: { signal?: AbortSignal }) =>
      new Promise<SlotTargetPreviewOut>((resolve, reject) => {
        const entry = { body, signal: opts?.signal, resolve, reject };
        opts?.signal?.addEventListener("abort", () =>
          reject(new DOMException("aborted", "AbortError")),
        );
        pending.push(entry);
      }),
  ),
  createSlotTarget: vi.fn(async (_setId: string, body: SlotTargetIn) => {
    created.push(body);
    return { id: "t1" };
  }),
  getSlotSet: vi.fn(async () => ({ id: "set1", targets: [] })),
  getSlotSetFile: vi.fn(),
  getSlotSetTree: vi.fn(async () => []),
  listSlotSetComponents: vi.fn(async () => []),
  listSlotSetEndpoints: vi.fn(async () => []),
  matchSlotTarget: vi.fn(),
  revalidateSlotTarget: vi.fn(),
  deleteSlotTarget: vi.fn(),
}));

const { SlotPicker } = await import("./picker.svelte");

function previewOf(
  holeMode: string,
  issues: SlotTargetPreviewOut["issues"] = [],
): SlotTargetPreviewOut {
  return {
    default_hole_mode: holeMode,
    issues,
  } as unknown as SlotTargetPreviewOut;
}

async function flush(): Promise<void> {
  for (let i = 0; i < 5; i++) await Promise.resolve();
}

function picker() {
  const p = new SlotPicker();
  p.setId = "set1";
  p.set = { id: "set1", targets: [] } as never;
  return p;
}

const SEL = {
  kind: "symbol" as const,
  path: "app.py",
  symbol_id: "app.py::create_user",
};
const OTHER = {
  kind: "symbol" as const,
  path: "app.py",
  symbol_id: "app.py::delete_user",
};

describe("slot picker: Save acts only on the preview of the current body", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    pending.length = 0;
    created.length = 0;
  });
  afterEach(() => vi.useRealTimers());

  it("drops the preview and aborts the request in flight on a form change", async () => {
    const p = picker();
    p.select(SEL);
    await vi.advanceTimersByTimeAsync(400);
    expect(pending).toHaveLength(1);
    p.updateForm({ hole_mode: "body" as never });
    expect(pending[0].signal?.aborted).toBe(true);
    // A late answer for the old body never becomes the preview.
    pending[0].resolve(
      previewOf("signature", [
        { severity: "error", code: "slot_leak_verbatim" } as never,
      ]),
    );
    await flush();
    expect(p.preview).toBeNull();
    expect(p.previewIsCurrent).toBe(false);
    expect(await p.save()).toBeNull();
    expect(created).toHaveLength(0);

    await vi.advanceTimersByTimeAsync(400);
    expect(pending).toHaveLength(2);
    expect(pending[1].body.default_hole_mode).toBe("body");
    pending[1].resolve(previewOf("body"));
    await flush();
    expect(p.previewIsCurrent).toBe(true);
    expect(await p.save()).not.toBeNull();
    expect(created[0].default_hole_mode).toBe("body");
  });

  it("aborts the in-flight request on a new selection", async () => {
    const p = picker();
    p.select(SEL);
    await vi.advanceTimersByTimeAsync(400);
    p.select(OTHER);
    expect(pending[0].signal?.aborted).toBe(true);
    pending[0].resolve(previewOf("signature"));
    await flush();
    expect(p.preview).toBeNull();
  });

  it("a cached preview is not overwritten by a slower earlier request", async () => {
    const p = picker();
    p.select(SEL);
    await vi.advanceTimersByTimeAsync(400);
    pending[0].resolve(previewOf("signature"));
    await flush();
    expect(p.previewIsCurrent).toBe(true);

    // Edit the form: a request for the new body goes out ...
    p.updateForm({ hole_mode: "body" as never });
    await vi.advanceTimersByTimeAsync(400);
    expect(pending).toHaveLength(2);
    // ... then the researcher reverts it: the cache answers and the slow request is aborted.
    p.updateForm({ hole_mode: null });
    await p.runPreview();
    expect(pending[1].signal?.aborted).toBe(true);
    pending[1].resolve(previewOf("body"));
    await flush();
    expect(p.preview?.default_hole_mode).toBe("signature");
    expect(p.previewIsCurrent).toBe(true);
  });

  it("blocks saving on error issues even when the preview is current", async () => {
    const p = picker();
    p.select(SEL);
    await vi.advanceTimersByTimeAsync(400);
    pending[0].resolve(
      previewOf("signature", [
        { severity: "error", code: "slot_leak_verbatim" } as never,
      ]),
    );
    await flush();
    expect(p.previewIsCurrent).toBe(true);
    expect(await p.save()).toBeNull();
    expect(created).toHaveLength(0);
  });

  it("changing only the preview's cost model does not invalidate Save", async () => {
    const p = picker();
    p.select(SEL);
    await vi.advanceTimersByTimeAsync(400);
    pending[0].resolve(previewOf("signature"));
    await flush();
    p.form = { ...p.form, cost_model: "some/model" } as never;
    expect(p.previewIsCurrent).toBe(true);
  });
});
