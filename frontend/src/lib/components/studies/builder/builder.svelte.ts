/**
 * BuilderState — the Study builder's draft and plan lifecycle (spec 10.2, 10.7).
 *
 * - The draft lives server-side as a Study with `status=draft` (no sessionStorage): the first save that
 *   the server can accept creates it (`POST /studies/`), later saves `PATCH` it. Design edits are saved
 *   500 ms after the last change (the plan needs the saved design: `resolve` reads it), text edits
 *   (name, description, hypothesis) 2 s after the last keystroke.
 * - After every saved design change the plan rail's plan is re-resolved with `persist=false`; a newer
 *   change aborts the request in flight and stale answers are dropped, so the rail never shows a plan
 *   of an older design.
 * - Freezing (the first half of "Launch experiment" on Review) persists the plan; launch is allowed only while the frozen plan is the plan of the
 *   saved design (`plan_hash` and `design_hash` match) and it has no error.
 *
 * Every API call is injectable (`BuilderDeps`) so debounce, abort and autosave are unit-tested with fake
 * timers.
 */
import { apiErrorStatus, formatApiError, isAbortError } from "$lib/api/core";
import * as api from "$lib/api/v3/studies";
import type {
  Design,
  ErrorOut,
  LaunchIn,
  LaunchOut,
  PlanOut,
  StudyCreateIn,
  StudyOut,
  StudyPatchIn,
} from "$lib/api/v3/types";
import { canCreate, localChecks } from "./design";
import { fromIssues, hasErrors, type SummaryIssue } from "./issues";

export const PLAN_DEBOUNCE_MS = 500;
export const AUTOSAVE_MS = 2000;
export const SAMPLE_CELLS = 50;

export interface BuilderDeps {
  createStudy: (
    body: StudyCreateIn,
    options?: { signal?: AbortSignal },
  ) => Promise<StudyOut>;
  updateStudy: (
    id: string,
    body: StudyPatchIn,
    options?: { signal?: AbortSignal },
  ) => Promise<StudyOut>;
  resolveStudy: typeof api.resolveStudy;
  freezePlan: typeof api.freezePlan;
  launchStudy: (id: string, body: LaunchIn) => Promise<LaunchOut>;
}

const defaultDeps: BuilderDeps = {
  createStudy: api.createStudy,
  updateStudy: api.updateStudy,
  resolveStudy: api.resolveStudy,
  freezePlan: api.freezePlan,
  launchStudy: api.launchStudy,
};

export interface BuilderMeta {
  name: string;
  description: string;
  hypothesis: string;
}

export interface BuilderOptions {
  /** Called once when the first save created the draft (the page puts `?draft=<id>` in the URL). */
  onCreated?: (study: StudyOut) => void;
  deps?: Partial<BuilderDeps>;
}

function issuesOf(error: unknown): SummaryIssue[] {
  const body = error as Partial<ErrorOut> | null;
  if (body && Array.isArray(body.issues) && body.issues.length > 0)
    return fromIssues(body.issues);
  return [];
}

export class BuilderState {
  studyId = $state<string | null>(null);
  study = $state.raw<StudyOut | null>(null);
  design = $state.raw<Design>(null as unknown as Design);
  meta = $state<BuilderMeta>({ name: "", description: "", hypothesis: "" });
  dropIncompatible = $state(false);

  saving = $state(false);
  saveError = $state("");
  /** Server issues of a refused save (400 with `issues`). */
  saveIssues = $state.raw<SummaryIssue[]>([]);
  lastSavedAt = $state<number | null>(null);

  plan = $state.raw<PlanOut | null>(null);
  planLoading = $state(false);
  planError = $state("");
  frozen = $state.raw<PlanOut | null>(null);
  freezing = $state(false);
  launching = $state(false);

  /** Edits not yet saved (or saving). */
  pending = $state(false);
  /**
   * A saved draft whose latest edit removed a required choice (no model, task, stack…): the server would
   * refuse it, so the edit is held locally (shown as a to-do, never as a failed save) until the design can
   * be saved again.
   */
  held = $state(false);

  private deps: BuilderDeps;
  private options: BuilderOptions;
  private savedDesign = "";
  private savedMeta = "";
  private timer: ReturnType<typeof setTimeout> | null = null;
  private dueAt = Infinity;
  private running: Promise<void> | null = null;
  private again = false;
  private planController: AbortController | null = null;
  private version = 0;
  private destroyed = false;

  constructor(
    design: Design,
    meta: Partial<BuilderMeta> = {},
    options: BuilderOptions = {},
  ) {
    this.design = design;
    this.meta = {
      name: meta.name ?? "",
      description: meta.description ?? "",
      hypothesis: meta.hypothesis ?? "",
    };
    this.options = options;
    this.deps = { ...defaultDeps, ...options.deps };
  }

  /** Resume a saved draft (or a clone): its design is the saved state; the plan is resolved at once. */
  attach(study: StudyOut): void {
    this.studyId = study.id;
    this.study = study;
    this.design = study.design;
    this.meta = {
      name: study.name,
      description: study.description,
      hypothesis: study.hypothesis,
    };
    this.savedDesign = JSON.stringify(study.design);
    this.savedMeta = JSON.stringify(this.meta);
    this.schedule(0);
  }

  /** Client checks while nothing is saved yet, else the server's plan issues (plus a refused save's). */
  get issues(): SummaryIssue[] {
    const local = localChecks(this.design).map((c) => ({ ...c }));
    if (!this.studyId || this.held) return local;
    if (this.saveIssues.length > 0) return this.saveIssues;
    const fromPlan = fromIssues(this.plan?.issues);
    return fromPlan.length > 0 ? fromPlan : this.plan ? [] : local;
  }

  get dirty(): boolean {
    return (
      JSON.stringify(this.design) !== this.savedDesign ||
      JSON.stringify(this.meta) !== this.savedMeta
    );
  }

  /** The rail's plan is the plan of the saved design and nothing is waiting to be saved. */
  get planFresh(): boolean {
    return (
      this.plan !== null &&
      this.study !== null &&
      this.plan.design_hash === this.study.design_hash &&
      !this.pending &&
      !this.planLoading &&
      !this.dirty
    );
  }

  get canFreeze(): boolean {
    return (
      this.planFresh &&
      !hasErrors(fromIssues(this.plan?.issues)) &&
      !this.saveError
    );
  }

  /** Start needs a frozen plan that is still the plan of the saved design, with no error issue. */
  get canStart(): boolean {
    const frozen = this.frozen;
    return (
      frozen !== null &&
      this.planFresh &&
      frozen.plan_hash === this.plan?.plan_hash &&
      frozen.design_hash === this.study?.design_hash &&
      !hasErrors(fromIssues(frozen.issues))
    );
  }

  setDesign(next: Design): void {
    this.design = next;
    this.version++;
    this.planController?.abort();
    this.schedule(PLAN_DEBOUNCE_MS);
  }

  setMeta(patch: Partial<BuilderMeta>): void {
    this.meta = { ...this.meta, ...patch };
    this.schedule(AUTOSAVE_MS);
  }

  setDropIncompatible(on: boolean): void {
    this.dropIncompatible = on;
    this.version++;
    this.planController?.abort();
    this.schedule(0, true);
  }

  /** Save now (and re-resolve); resolves once the draft and plan are current. */
  async flush(): Promise<void> {
    this.clearTimer();
    await this.sync();
  }

  /** Re-resolve the saved design (e.g. after a library change elsewhere). */
  refreshPlan(): void {
    this.version++;
    this.schedule(0, true);
  }

  async freeze(): Promise<PlanOut | null> {
    await this.flush();
    const id = this.studyId;
    if (!id || !this.canFreeze) return null;
    this.freezing = true;
    try {
      const plan = await this.deps.freezePlan(id, this.dropIncompatible);
      this.frozen = plan;
      this.plan = plan;
      return plan;
    } catch (e) {
      this.planError = formatApiError(e, "Could not freeze the plan.");
      return null;
    } finally {
      this.freezing = false;
    }
  }

  async launch(body: Omit<LaunchIn, "plan_hash">): Promise<LaunchOut> {
    const id = this.studyId;
    const frozen = this.frozen;
    if (!id || !frozen || !this.canStart)
      throw {
        detail: "Freeze a current plan first.",
        code: "plan_stale",
      } satisfies ErrorOut;
    this.launching = true;
    try {
      return await this.deps.launchStudy(id, {
        ...body,
        plan_hash: frozen.plan_hash,
      });
    } finally {
      this.launching = false;
    }
  }

  destroy(): void {
    this.destroyed = true;
    this.clearTimer();
    this.planController?.abort();
  }

  // ── internals ─────────────────────────────────────────────────────────

  private forceResolve = false;

  private clearTimer(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    this.dueAt = Infinity;
  }

  private schedule(delay: number, resolveOnly = false): void {
    if (this.destroyed) return;
    if (resolveOnly) this.forceResolve = true;
    this.pending = true;
    const due = Date.now() + delay;
    // A text edit never postpones a save that is already due sooner; a design edit restarts the window.
    if (this.timer !== null && delay === AUTOSAVE_MS && this.dueAt <= due)
      return;
    this.clearTimer();
    this.dueAt = due;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.dueAt = Infinity;
      void this.sync();
    }, delay);
  }

  private sync(): Promise<void> {
    if (this.running) {
      this.again = true;
      return this.running;
    }
    this.running = (async () => {
      try {
        do {
          this.again = false;
          await this.syncOnce();
        } while (this.again && !this.destroyed);
      } finally {
        this.running = null;
        if (this.timer === null) this.pending = false;
      }
    })();
    return this.running;
  }

  private async syncOnce(): Promise<void> {
    if (this.destroyed) return;
    const design = this.design;
    const meta = { ...this.meta };
    const designJson = JSON.stringify(design);
    const metaJson = JSON.stringify(meta);
    const changed =
      designJson !== this.savedDesign || metaJson !== this.savedMeta;
    // Back to a saveable design (e.g. Undo restored the tasks): nothing is held any more.
    if (this.held && (designJson === this.savedDesign || canCreate(design)))
      this.held = false;
    let id = this.studyId;
    if (!id) {
      if (!canCreate(design)) return; // client checks are shown; nothing to save yet
      this.saving = true;
      try {
        const study = await this.deps.createStudy({
          name: meta.name.trim() || "Untitled experiment",
          description: meta.description,
          hypothesis: meta.hypothesis,
          design,
        });
        id = study.id;
        this.studyId = id;
        this.accept(study, designJson, metaJson);
        this.options.onCreated?.(study);
      } catch (e) {
        this.refused(e);
        return;
      } finally {
        this.saving = false;
      }
    } else if (changed) {
      if (!canCreate(design)) {
        this.held = true; // the client checks say what is missing; nothing is sent
        return;
      }
      this.held = false;
      this.saving = true;
      try {
        const patch: StudyPatchIn = {};
        if (designJson !== this.savedDesign) patch.design = design;
        if (metaJson !== this.savedMeta) {
          patch.name = meta.name.trim() || "Untitled experiment";
          patch.description = meta.description;
          patch.hypothesis = meta.hypothesis;
        }
        const study = await this.deps.updateStudy(id, patch);
        this.accept(study, designJson, metaJson);
      } catch (e) {
        this.refused(e);
        return;
      } finally {
        this.saving = false;
      }
    } else if (
      !this.forceResolve &&
      this.plan &&
      this.plan.design_hash === this.study?.design_hash &&
      !this.planError
    ) {
      return; // nothing changed since the last plan
    }
    this.forceResolve = false;
    if (designJson !== JSON.stringify(this.design)) return; // a newer edit is queued; resolve after it
    await this.resolve(id);
  }

  private accept(study: StudyOut, designJson: string, metaJson: string): void {
    this.study = study;
    this.savedDesign = designJson;
    this.savedMeta = metaJson;
    this.saveError = "";
    this.saveIssues = [];
    this.lastSavedAt = Date.now();
  }

  private refused(e: unknown): void {
    const status = apiErrorStatus(e);
    this.saveError = formatApiError(
      e,
      status === 409
        ? "The experiment is locked."
        : "Could not save the draft.",
    );
    this.saveIssues = issuesOf(e);
  }

  private async resolve(id: string): Promise<void> {
    this.planController?.abort();
    const controller = new AbortController();
    this.planController = controller;
    const version = this.version;
    this.planLoading = true;
    try {
      const plan = await this.deps.resolveStudy(
        id,
        {
          persist: false,
          drop_incompatible: this.dropIncompatible,
          sample_cells: SAMPLE_CELLS,
        },
        { signal: controller.signal },
      );
      if (
        controller.signal.aborted ||
        version !== this.version ||
        this.destroyed
      )
        return;
      this.plan = plan;
      this.planError = "";
    } catch (e) {
      if (
        isAbortError(e) ||
        controller.signal.aborted ||
        version !== this.version
      )
        return;
      this.planError = formatApiError(e, "Could not resolve the plan.");
      const issues = issuesOf(e);
      if (issues.length > 0) this.saveIssues = issues;
    } finally {
      if (this.planController === controller) {
        this.planController = null;
        this.planLoading = false;
      }
    }
  }
}
