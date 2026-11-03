/**
 * slotPicker store (spec 10.7): the current SlotSet, its tree / endpoints / components, the open file, the
 * part selection, the target form and a preview cache. One instance per `/slots/new` page; switching between
 * frozen parents calls `open(setId)` again (each set keeps its own cached files and previews).
 *
 * Previews run on demand and automatically 400 ms after a selection or form change; a superseded request is
 * aborted and identical bodies are served from the cache. A selection or form change drops the shown preview
 * at once (it described another body), and every preview remembers the body it was made for: Save is enabled
 * only while that body is the one that would be saved (`previewIsCurrent`), so a hole, leak or blocking issue
 * shown is always the one of the target saved.
 */
import { formatApiError, isAbortError } from "$lib/api/core";
import {
  createSlotTarget,
  deleteSlotTarget,
  getSlotSet,
  getSlotSetFile,
  getSlotSetTree,
  listSlotSetComponents,
  listSlotSetEndpoints,
  matchSlotTarget,
  previewSlotTarget,
  revalidateSlotTarget,
} from "$lib/api/v3/slots";
import type {
  ComponentEntry,
  EndpointEntry,
  SlotFileOut,
  SlotMatchOut,
  SlotSetDetailOut,
  SlotTargetOut,
  SlotTargetIn,
  SlotTargetPreviewOut,
  SlotTreeEntry,
} from "$lib/api/v3/types";
import {
  emptyTargetForm,
  previewKey,
  selectionLines,
  staleGuard,
  toSlotTargetCreateIn,
  toSlotTargetIn,
  type PartSelection,
  type TargetFormState,
} from "./selection";

const PREVIEW_DEBOUNCE_MS = 400;

export class SlotPicker {
  setId = $state<string | null>(null);
  set = $state<SlotSetDetailOut | null>(null);
  tree = $state<SlotTreeEntry[]>([]);
  endpoints = $state<EndpointEntry[]>([]);
  components = $state<ComponentEntry[]>([]);
  loading = $state(false);
  error = $state("");

  path = $state<string | null>(null);
  file = $state<SlotFileOut | null>(null);
  fileLoading = $state(false);
  fileError = $state("");

  selection = $state<PartSelection | null>(null);
  form = $state<TargetFormState>(emptyTargetForm());

  preview = $state<SlotTargetPreviewOut | null>(null);
  /** The save key (`#saveKey`) of the body `preview` was made for. */
  previewFor = $state<string | null>(null);
  /**
   * The latest preview of the current selection, kept across form edits only to label the form's defaults
   * (default hole mode, policy, automatic links) while the next preview loads; never used to gate Save.
   */
  lastPreview = $state<SlotTargetPreviewOut | null>(null);
  #filesVersion = $state(0);
  previewLoading = $state(false);
  previewError = $state("");
  saving = $state(false);
  saveError = $state("");

  #files = new Map<string, SlotFileOut>();
  #previews = new Map<string, SlotTargetPreviewOut>();
  #previewAbort: AbortController | null = null;
  #previewTimer: ReturnType<typeof setTimeout> | null = null;
  #loadAbort: AbortController | null = null;

  /** The key of the target body that Save would send now, or null without a selection. */
  get currentKey(): string | null {
    void this.#filesVersion;
    if (!this.setId || !this.selection) return null;
    return this.#saveKey(
      this.setId,
      toSlotTargetIn(this.selection, this.form, this.#fileText(this.selection)),
    );
  }

  /** True while the shown preview was made for exactly the body Save would send. */
  get previewIsCurrent(): boolean {
    return (
      this.preview !== null &&
      this.previewFor !== null &&
      this.previewFor === this.currentKey
    );
  }

  /** The cost model only prices the preview; it is not part of the saved target. */
  #saveKey(setId: string, body: SlotTargetIn): string {
    return previewKey(setId, { ...body, cost_model: undefined });
  }

  #dropPreview(): void {
    this.#previewAbort?.abort();
    this.#previewAbort = null;
    this.preview = null;
    this.previewFor = null;
    this.previewLoading = false;
  }

  get targets(): SlotTargetOut[] {
    return this.set?.targets ?? [];
  }

  /** Lines of the selection in the open file (for the code pane highlight). */
  get selectedLines(): { start_line: number; end_line: number } | null {
    if (!this.selection || !this.file || this.selection.path !== this.file.path)
      return null;
    return selectionLines(this.selection, this.file.symbols);
  }

  async open(setId: string): Promise<void> {
    if (this.setId === setId && this.set) return;
    this.#loadAbort?.abort();
    const controller = new AbortController();
    this.#loadAbort = controller;
    if (this.setId !== setId) {
      this.#files.clear();
      this.path = null;
      this.file = null;
      this.clearSelection();
    }
    this.setId = setId;
    this.loading = true;
    this.error = "";
    try {
      const signal = controller.signal;
      const [set, tree, endpoints, components] = await Promise.all([
        getSlotSet(setId, { signal }),
        getSlotSetTree(setId, { signal }),
        listSlotSetEndpoints(setId, { signal }),
        listSlotSetComponents(setId, { signal }),
      ]);
      if (this.setId !== setId) return;
      this.set = set;
      this.tree = tree;
      this.endpoints = endpoints;
      this.components = components;
      if (!this.path) {
        const first =
          tree.find(
            (t) => t.parsed && !t.is_test && !t.excluded && t.symbols > 0,
          ) ?? tree[0];
        if (first) void this.openFile(first.path);
      }
    } catch (e) {
      if (!isAbortError(e))
        this.error = formatApiError(e, "Could not load the frozen parent.");
    } finally {
      if (this.setId === setId) this.loading = false;
    }
  }

  async refreshSet(): Promise<void> {
    if (!this.setId) return;
    try {
      this.set = await getSlotSet(this.setId);
    } catch (e) {
      this.error = formatApiError(e, "Could not reload the targets.");
    }
  }

  async openFile(path: string): Promise<void> {
    const setId = this.setId;
    if (!setId) return;
    this.path = path;
    this.fileError = "";
    const cached = this.#files.get(path);
    if (cached) {
      this.file = cached;
      return;
    }
    this.fileLoading = true;
    try {
      const file = await getSlotSetFile(setId, path);
      this.#files.set(path, file);
      this.#filesVersion += 1;
      if (this.setId === setId && this.path === path) this.file = file;
      // A region's body gains character offsets once its file text is known: preview that body.
      if (this.setId === setId && this.selection?.path === path)
        this.schedulePreview();
    } catch (e) {
      if (this.path === path) {
        this.file = null;
        this.fileError = formatApiError(e, "Could not load the file.");
      }
    } finally {
      if (this.path === path) this.fileLoading = false;
    }
  }

  /** Select a part; opens its file when it lives elsewhere and schedules a preview. */
  select(selection: PartSelection | null): void {
    this.selection = selection;
    this.#dropPreview();
    this.lastPreview = null;
    this.previewError = "";
    this.saveError = "";
    if (selection && selection.path && selection.path !== this.path)
      void this.openFile(selection.path);
    this.schedulePreview();
  }

  clearSelection(): void {
    this.selection = null;
    this.form = emptyTargetForm();
    this.#dropPreview();
    this.lastPreview = null;
    this.previewError = "";
    this.saveError = "";
  }

  updateForm(patch: Partial<TargetFormState>): void {
    this.form = { ...this.form, ...patch };
    this.#dropPreview();
    this.previewError = "";
    this.schedulePreview();
  }

  /** Reset one form field to "default (can be varied in the study)". */
  resetField(field: keyof TargetFormState): void {
    this.updateForm({
      [field]: field === "label" ? "" : null,
    } as Partial<TargetFormState>);
  }

  schedulePreview(): void {
    if (this.#previewTimer) clearTimeout(this.#previewTimer);
    this.#previewTimer = null;
    if (!this.selection) return;
    this.#previewTimer = setTimeout(
      () => void this.runPreview(),
      PREVIEW_DEBOUNCE_MS,
    );
  }

  #fileText(selection: PartSelection): string | undefined {
    return this.#files.get(selection.path)?.text;
  }

  async runPreview(): Promise<void> {
    const setId = this.setId;
    const selection = this.selection;
    if (!setId || !selection) return;
    const body = toSlotTargetIn(
      selection,
      this.form,
      this.#fileText(selection),
    );
    const key = previewKey(setId, body);
    const saveKey = this.#saveKey(setId, body);
    const cached = this.#previews.get(key);
    // Either way a request still in flight is for another body: it must not land after this one.
    this.#previewAbort?.abort();
    this.#previewAbort = null;
    if (cached) {
      this.preview = cached;
      this.previewFor = saveKey;
      this.lastPreview = cached;
      this.previewLoading = false;
      this.previewError = "";
      return;
    }
    const controller = new AbortController();
    this.#previewAbort = controller;
    this.previewLoading = true;
    this.previewError = "";
    try {
      const guard = await staleGuard(
        selection,
        this.#files.get(selection.path),
      );
      const preview = await previewSlotTarget(
        setId,
        { ...body, ...guard },
        { signal: controller.signal },
      );
      this.#previews.set(key, preview);
      if (this.#previewAbort === controller) {
        this.preview = preview;
        this.previewFor = saveKey;
        this.lastPreview = preview;
      }
    } catch (e) {
      if (isAbortError(e)) return;
      if (this.#previewAbort === controller) {
        this.preview = null;
        this.previewFor = null;
        this.previewError = formatApiError(e, "Could not preview this part.");
      }
    } finally {
      if (this.#previewAbort === controller) this.previewLoading = false;
    }
  }

  /** Save the selected part as a target (validation is queued server-side). */
  async save(): Promise<SlotTargetOut | null> {
    const setId = this.setId;
    const selection = this.selection;
    if (!setId || !selection) return null;
    if (!this.previewIsCurrent) {
      this.saveError = "Preview this part as it is now before saving it.";
      return null;
    }
    if (this.preview?.issues.some((i) => i.severity === "error")) {
      this.saveError = "The preview has blocking issues.";
      return null;
    }
    this.saving = true;
    this.saveError = "";
    try {
      const guard = await staleGuard(
        selection,
        this.#files.get(selection.path),
      );
      const body = {
        ...toSlotTargetCreateIn(
          selection,
          this.form,
          this.#fileText(selection),
        ),
        ...guard,
      };
      const target = await createSlotTarget(setId, body);
      await this.refreshSet();
      return target;
    } catch (e) {
      this.saveError = formatApiError(e, "Could not save the target.");
      return null;
    } finally {
      this.saving = false;
    }
  }

  async match(
    targetId: string,
    slotSets: string[] | "same_task" = "same_task",
  ): Promise<SlotMatchOut> {
    const out = await matchSlotTarget(targetId, slotSets);
    await this.refreshSet();
    return out;
  }

  async revalidate(targetId: string): Promise<void> {
    await revalidateSlotTarget(targetId);
    await this.refreshSet();
  }

  async remove(targetId: string): Promise<void> {
    await deleteSlotTarget(targetId);
    await this.refreshSet();
  }

  destroy(): void {
    this.#previewAbort?.abort();
    this.#loadAbort?.abort();
    if (this.#previewTimer) clearTimeout(this.#previewTimer);
  }
}
