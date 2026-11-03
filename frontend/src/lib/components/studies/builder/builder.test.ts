import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Design, PlanOut, StudyOut } from "$lib/api/v3/types";
import {
  AUTOSAVE_MS,
  BuilderState,
  PLAN_DEBOUNCE_MS,
  type BuilderDeps,
} from "./builder.svelte";
import { defaultDesign, setLevels } from "./design";

const ID = "9f1c0c4e-0000-4000-8000-000000000001";

function readyDesign(): Design {
  let d = defaultDesign("app", "exploratory", { base: 1, order: 2 });
  d = setLevels(d, "model", ["m1", "m2"]);
  d = setLevels(d, "stack_profile", ["flask-react@1"]);
  return { ...d, units: { ...d.units, tasks: ["todo@1"] } };
}

function hashOf(design: Design): string {
  return `h-${JSON.stringify(design).length}-${design.repeats}-${design.factors.map((f) => f.levels.join("+")).join("|")}`;
}

function study(design: Design, over: Partial<StudyOut> = {}): StudyOut {
  return {
    id: ID,
    name: "S",
    description: "",
    hypothesis: "",
    status: "draft",
    design,
    design_hash: hashOf(design),
    ...over,
  } as StudyOut;
}

function plan(design: Design, over: Partial<PlanOut> = {}): PlanOut {
  return {
    plan_hash: `p-${hashOf(design)}`,
    design_hash: hashOf(design),
    persisted: false,
    resolver_version: 1,
    n_conditions: 2,
    n_units: 1,
    n_cells: 10,
    n_cells_by_stage: { main: 10 },
    n_blocks: 5,
    unbalanced: false,
    estimate: null,
    issues: [],
    conditions: [],
    units: [],
    cells_sample: [],
    ...over,
  };
}

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function fakeDeps() {
  let saved: Design | null = readyDesign();
  const deps = {
    createStudy: vi.fn(async (body: { design: Design }) => {
      saved = body.design;
      return study(body.design);
    }),
    updateStudy: vi.fn(
      async (_id: string, body: { design?: Design; name?: string }) => {
        if (body.design) saved = body.design;
        return study(saved!, { name: body.name ?? "S" });
      },
    ),
    resolveStudy: vi.fn(
      async (
        _id: string,
        _body?: unknown,
        _options?: { signal?: AbortSignal },
      ) => plan(saved!),
    ),
    freezePlan: vi.fn(async () => plan(saved!, { persisted: true })),
    launchStudy: vi.fn(async () => ({
      status: "running" as const,
      created_runs: 10,
      stage: "main" as const,
      resolved_cap: 3,
    })),
  };
  return deps;
}

async function settle() {
  // Let chained promises (save → resolve) run between timer steps.
  for (let i = 0; i < 10; i++) await Promise.resolve();
}

describe("BuilderState", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("creates the draft once the design is complete, then resolves with persist=false", async () => {
    const deps = fakeDeps();
    const onCreated = vi.fn();
    const b = new BuilderState(
      defaultDesign("app", "exploratory", { base: 1, order: 2 }),
      { name: "My study" },
      {
        deps: deps as unknown as BuilderDeps,
        onCreated,
      },
    );
    b.setDesign(setLevels(b.design, "model", ["m1"]));
    await vi.advanceTimersByTimeAsync(PLAN_DEBOUNCE_MS);
    expect(deps.createStudy).not.toHaveBeenCalled(); // incomplete: no tasks, no stack
    expect(b.issues.map((i) => i.code)).toContain("no_tasks");

    b.setDesign(readyDesign());
    await vi.advanceTimersByTimeAsync(PLAN_DEBOUNCE_MS);
    await settle();
    expect(deps.createStudy).toHaveBeenCalledTimes(1);
    expect(deps.createStudy.mock.calls[0][0]).toMatchObject({
      name: "My study",
    });
    expect(onCreated).toHaveBeenCalledTimes(1);
    expect(b.studyId).toBe(ID);
    expect(deps.resolveStudy).toHaveBeenCalledTimes(1);
    expect(deps.resolveStudy.mock.calls[0][1]).toMatchObject({
      persist: false,
      drop_incompatible: false,
    });
    expect(b.plan?.n_cells).toBe(10);
    expect(b.planFresh).toBe(true);
  });

  it("debounces design edits: one save and one resolve per pause", async () => {
    const deps = fakeDeps();
    const b = new BuilderState(
      readyDesign(),
      {},
      { deps: deps as unknown as BuilderDeps },
    );
    b.attach(study(readyDesign()));
    await vi.advanceTimersByTimeAsync(0);
    await settle();
    deps.resolveStudy.mockClear();
    for (const repeats of [6, 7, 8]) {
      b.setDesign({ ...b.design, repeats });
      await vi.advanceTimersByTimeAsync(PLAN_DEBOUNCE_MS - 100);
    }
    expect(deps.updateStudy).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(100);
    await settle();
    expect(deps.updateStudy).toHaveBeenCalledTimes(1);
    expect(deps.updateStudy.mock.calls[0][1].design?.repeats).toBe(8);
    expect(deps.resolveStudy).toHaveBeenCalledTimes(1);
  });

  it("aborts a superseded resolve and never shows the older plan", async () => {
    const deps = fakeDeps();
    const first = deferred<PlanOut>();
    const signals: AbortSignal[] = [];
    deps.resolveStudy.mockImplementationOnce(
      async (
        _id: string,
        _body: unknown,
        options?: { signal?: AbortSignal },
      ) => {
        signals.push(options!.signal!);
        return first.promise;
      },
    );
    const b = new BuilderState(
      readyDesign(),
      {},
      { deps: deps as unknown as BuilderDeps },
    );
    b.attach(study(readyDesign()));
    await vi.advanceTimersByTimeAsync(0);
    await settle();
    expect(b.planLoading).toBe(true);

    const newer = { ...b.design, repeats: 9 };
    b.setDesign(newer);
    expect(signals[0].aborted).toBe(true);
    first.resolve(plan(readyDesign(), { n_cells: 999 })); // the old answer arrives late
    await vi.advanceTimersByTimeAsync(PLAN_DEBOUNCE_MS);
    await settle();
    expect(b.plan?.n_cells).toBe(10);
    expect(b.plan?.design_hash).toBe(hashOf(newer));
  });

  it("autosaves text edits after 2 s without resolving again", async () => {
    const deps = fakeDeps();
    const b = new BuilderState(
      readyDesign(),
      {},
      { deps: deps as unknown as BuilderDeps },
    );
    b.attach(study(readyDesign()));
    await vi.advanceTimersByTimeAsync(0);
    await settle();
    deps.resolveStudy.mockClear();
    b.setMeta({ name: "Rename" });
    await vi.advanceTimersByTimeAsync(AUTOSAVE_MS - 1);
    expect(deps.updateStudy).not.toHaveBeenCalled();
    expect(b.pending).toBe(true);
    await vi.advanceTimersByTimeAsync(1);
    await settle();
    expect(deps.updateStudy).toHaveBeenCalledTimes(1);
    expect(deps.updateStudy.mock.calls[0][1]).toMatchObject({ name: "Rename" });
    expect(deps.updateStudy.mock.calls[0][1].design).toBeUndefined();
    expect(b.pending).toBe(false);
  });

  it("a text edit does not postpone a design save that is due sooner", async () => {
    const deps = fakeDeps();
    const b = new BuilderState(
      readyDesign(),
      {},
      { deps: deps as unknown as BuilderDeps },
    );
    b.attach(study(readyDesign()));
    await vi.advanceTimersByTimeAsync(0);
    await settle();
    b.setDesign({ ...b.design, repeats: 4 });
    b.setMeta({ description: "x" });
    await vi.advanceTimersByTimeAsync(PLAN_DEBOUNCE_MS);
    await settle();
    expect(deps.updateStudy).toHaveBeenCalledTimes(1);
    expect(deps.updateStudy.mock.calls[0][1]).toMatchObject({
      description: "x",
      design: { repeats: 4 },
    });
  });

  it("shows a refused save's issues and does not resolve", async () => {
    const deps = fakeDeps();
    deps.updateStudy.mockRejectedValueOnce({
      detail: "invalid design",
      code: "invalid_design",
      issues: [
        {
          code: "prose_variant_in_unit",
          severity: "error",
          message: "move it",
          cells: [],
          path: null,
          data: {},
          fix: [],
        },
      ],
    });
    const b = new BuilderState(
      readyDesign(),
      {},
      { deps: deps as unknown as BuilderDeps },
    );
    b.attach(study(readyDesign()));
    await vi.advanceTimersByTimeAsync(0);
    await settle();
    deps.resolveStudy.mockClear();
    b.setDesign({ ...b.design, repeats: 3 });
    await vi.advanceTimersByTimeAsync(PLAN_DEBOUNCE_MS);
    await settle();
    expect(b.saveError).toContain("invalid design");
    expect(b.issues.map((i) => i.code)).toEqual(["prose_variant_in_unit"]);
    expect(deps.resolveStudy).not.toHaveBeenCalled();
    expect(b.canFreeze).toBe(false);
  });

  it("Start needs a frozen plan of the saved design; any edit makes it stale", async () => {
    const deps = fakeDeps();
    const b = new BuilderState(
      readyDesign(),
      {},
      { deps: deps as unknown as BuilderDeps },
    );
    b.attach(study(readyDesign()));
    await vi.advanceTimersByTimeAsync(0);
    await settle();
    expect(b.canFreeze).toBe(true);
    expect(b.canStart).toBe(false);
    await b.freeze();
    expect(deps.freezePlan).toHaveBeenCalledWith(ID, false);
    expect(b.canStart).toBe(true);

    b.setDesign({ ...b.design, repeats: 2 });
    expect(b.canStart).toBe(false);
    await vi.advanceTimersByTimeAsync(PLAN_DEBOUNCE_MS);
    await settle();
    expect(b.planFresh).toBe(true);
    expect(b.canStart).toBe(false); // the frozen plan is of the old design

    await b.freeze();
    const out = await b.launch({ confirm_usd: 5, study_cap: "auto" });
    expect(deps.launchStudy).toHaveBeenCalledWith(ID, {
      confirm_usd: 5,
      study_cap: "auto",
      plan_hash: b.frozen!.plan_hash,
    });
    expect(out.created_runs).toBe(10);
  });

  it("error issues in the plan block Freeze", async () => {
    const deps = fakeDeps();
    deps.resolveStudy.mockImplementation(async () =>
      plan(readyDesign(), {
        issues: [
          {
            code: "starter_missing",
            severity: "error",
            message: "no full starter",
            cells: [],
            path: null,
            data: {},
            fix: [],
          },
        ],
      }),
    );
    const b = new BuilderState(
      readyDesign(),
      {},
      { deps: deps as unknown as BuilderDeps },
    );
    b.attach(study(readyDesign()));
    await vi.advanceTimersByTimeAsync(0);
    await settle();
    expect(b.issues.map((i) => i.code)).toEqual(["starter_missing"]);
    expect(b.canFreeze).toBe(false);
    expect(await b.freeze()).toBeNull();
    expect(deps.freezePlan).not.toHaveBeenCalled();
  });

  it("drop-incompatible re-resolves at once with the flag", async () => {
    const deps = fakeDeps();
    const b = new BuilderState(
      readyDesign(),
      {},
      { deps: deps as unknown as BuilderDeps },
    );
    b.attach(study(readyDesign()));
    await vi.advanceTimersByTimeAsync(0);
    await settle();
    b.setDropIncompatible(true);
    await vi.advanceTimersByTimeAsync(0);
    await settle();
    expect(deps.resolveStudy).toHaveBeenLastCalledWith(
      ID,
      expect.objectContaining({ drop_incompatible: true }),
      expect.anything(),
    );
  });

  it("destroy stops pending work", async () => {
    const deps = fakeDeps();
    const b = new BuilderState(
      readyDesign(),
      {},
      { deps: deps as unknown as BuilderDeps },
    );
    b.attach(study(readyDesign()));
    b.destroy();
    await vi.advanceTimersByTimeAsync(PLAN_DEBOUNCE_MS * 4);
    expect(deps.resolveStudy).not.toHaveBeenCalled();
  });
});
