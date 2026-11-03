// State helpers for the "new evaluation task" page: the draft being composed,
// starting points (presets / scratch), the browser-session copy of the draft
// and the non-blocking checks shown before creating it.

import type {
  HumanEvalEnvironment,
  Preset,
  TaskCreateInput,
  TaskDesign,
  TaskMode,
  TaskSettings,
} from "$lib/api/types/human-eval";
import {
  clonePlain,
  disallowedBlocks,
  emptyDesign,
  normalizeDesign,
} from "$lib/components/human-eval/design/factories";

export interface WizardDraft {
  /** Preset slug the design started from; "" = from scratch; null = not chosen yet. */
  start: string | null;
  title: string;
  description: string;
  instructions: string;
  mode: TaskMode;
  design: TaskDesign;
  settings: TaskSettings;
  /** GenerationRun ids picked individually. */
  runIds: string[];
  /** The linked study (its current main-stage runs join the pool), or null. */
  studyId: string | null;
}

/** v3 drafts (run and study ids); a stored v2 draft (job / experiment ids) is never read. */
export const DRAFT_KEY = "llmlab:human-eval:new-task:v3";

/**
 * Used only when neither the environment nor a preset could be loaded; the
 * server fills and validates every key again.
 */
export function fallbackSettings(): TaskSettings {
  return {
    blinding: {
      show_spec: true,
      show_source_code: false,
      show_admin_credentials: true,
    },
    allocation: {
      strategy: "balanced",
      ratings_per_item: 3,
      allow_overflow: false,
      max_items_per_participant: null,
      assignment_timeout_minutes: 60,
      allow_skip: true,
    },
    pairing: {
      strategy: "all_pairs",
      k: 2,
      cross_condition_only: true,
      baseline_condition_id: null,
    },
    access: {
      allow_invite_links: true,
      allow_access_codes: true,
      allow_accounts: false,
    },
    hosting: {
      on_demand: true,
      idle_stop_minutes: 30,
      max_running_apps: 5,
      isolation: "shared",
      reset: "none",
    },
    quality: { min_seconds_per_item: 60 },
  };
}

export function defaultSettings(
  environment: HumanEvalEnvironment | null,
): TaskSettings {
  return environment?.default_settings
    ? clonePlain(environment.default_settings)
    : fallbackSettings();
}

export function emptyDraft(
  environment: HumanEvalEnvironment | null,
): WizardDraft {
  return {
    start: null,
    title: "",
    description: "",
    instructions: "",
    mode: "single",
    design: emptyDesign(environment),
    settings: defaultSettings(environment),
    runIds: [],
    studyId: null,
  };
}

/** Take a preset's mode, design and settings; keep what the researcher typed and picked. */
export function applyPreset(draft: WizardDraft, preset: Preset): WizardDraft {
  return {
    ...draft,
    start: preset.slug,
    mode: preset.mode,
    design: normalizeDesign(clonePlain(preset.design)),
    settings: clonePlain(preset.settings),
  };
}

/** An empty design (consent and plan defaults kept) and the default settings. */
export function applyScratch(
  draft: WizardDraft,
  environment: HumanEvalEnvironment | null,
): WizardDraft {
  return {
    ...draft,
    start: "",
    design: emptyDesign(environment),
    settings: defaultSettings(environment),
  };
}

/** Canonical JSON of the parts a preset replaces (to tell whether the researcher edited them). */
export function designFingerprint(
  draft: Pick<WizardDraft, "mode" | "design" | "settings">,
): string {
  return JSON.stringify([draft.mode, draft.design, draft.settings]);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

/** Parse a stored draft; null when absent or not shaped like one. */
export function parseDraft(raw: string | null): WizardDraft | null {
  if (!raw) return null;
  try {
    const d = JSON.parse(raw) as unknown;
    if (!isRecord(d) || !isRecord(d.design) || !isRecord(d.settings))
      return null;
    if (d.mode !== "single" && d.mode !== "pairwise") return null;
    return {
      start: typeof d.start === "string" ? d.start : null,
      title: typeof d.title === "string" ? d.title : "",
      description: typeof d.description === "string" ? d.description : "",
      instructions: typeof d.instructions === "string" ? d.instructions : "",
      mode: d.mode,
      design: normalizeDesign(d.design as Partial<TaskDesign>),
      settings: d.settings as unknown as TaskSettings,
      runIds: Array.isArray(d.runIds)
        ? d.runIds.filter((x): x is string => typeof x === "string")
        : [],
      studyId: typeof d.studyId === "string" && d.studyId ? d.studyId : null,
    };
  } catch {
    return null;
  }
}

export function loadStoredDraft(): WizardDraft | null {
  try {
    return parseDraft(sessionStorage.getItem(DRAFT_KEY));
  } catch {
    return null;
  }
}

export function storeDraft(draft: WizardDraft): void {
  try {
    sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  } catch {
    // Storage full or blocked: the draft only lives in memory.
  }
}

export function clearStoredDraft(): void {
  try {
    sessionStorage.removeItem(DRAFT_KEY);
  } catch {
    // ignore
  }
}

/** True when the draft holds anything worth restoring. */
export function hasContent(draft: WizardDraft): boolean {
  return (
    draft.start !== null ||
    !!draft.title.trim() ||
    !!draft.description.trim() ||
    !!draft.instructions.trim() ||
    draft.runIds.length > 0 ||
    !!draft.studyId ||
    draft.design.blocks.length > 0
  );
}

export function createInput(draft: WizardDraft): TaskCreateInput {
  const input: TaskCreateInput = {
    title: draft.title.trim(),
    description: draft.description.trim(),
    instructions: draft.instructions,
    mode: draft.mode,
    design: draft.design,
    settings: draft.settings,
  };
  if (draft.studyId) input.study_id = draft.studyId;
  if (draft.runIds.length) input.run_ids = [...draft.runIds];
  return input;
}

export interface DraftNote {
  level: "warning" | "info";
  text: string;
}

/**
 * Checks the researcher should see before creating the draft. None of them
 * blocks saving; the server reports structural errors and the open blockers.
 */
export function draftNotes(draft: WizardDraft): DraftNote[] {
  const notes: DraftNote[] = [];
  const { design, settings, mode } = draft;
  if (draft.runIds.length === 0 && !draft.studyId) {
    notes.push({
      level: "warning",
      text: "No apps yet. Add them now or later on the Apps tab; the task cannot open without apps.",
    });
  }
  if (design.blocks.length === 0) {
    notes.push({
      level: "warning",
      text: "No evaluation blocks. The draft can be saved, but it needs at least one block to open.",
    });
  }
  const disallowed = disallowedBlocks(mode, design.blocks);
  if (disallowed.length) {
    notes.push({
      level: "warning",
      text: `${disallowed.length} block${disallowed.length === 1 ? " is" : "s are"} not available in ${mode === "pairwise" ? "pairwise" : "single-app"} mode.`,
    });
  }
  if (
    mode === "pairwise" &&
    !design.blocks.some((b) => b.type === "pairwise")
  ) {
    notes.push({
      level: "warning",
      text: "Pairwise tasks need a pairwise preference block.",
    });
  }
  const access = settings.access;
  if (
    !access.allow_invite_links &&
    !access.allow_access_codes &&
    !access.allow_accounts
  ) {
    notes.push({
      level: "warning",
      text: "No access method is enabled, so nobody could join.",
    });
  }
  const securityTesting = design.blocks.some((b) => b.type === "security");
  if (settings.hosting.reset !== "per_assignment") {
    notes.push({
      level: securityTesting ? "warning" : "info",
      text: securityTesting
        ? "Security testing with shared app state: later participants may find accounts, data or damage left by earlier ones. Consider one participant at a time with a reset per assignment."
        : "Apps are not reset between participants (shared app state); results will note it.",
    });
  }
  if (!design.analysis.primary) {
    notes.push({
      level: "info",
      text: "No primary outcome: results will be reported as exploratory.",
    });
  }
  const planned =
    settings.allocation.strategy === "balanced"
      ? design.analysis.planned_ratings_per_item
      : design.analysis.planned_participants;
  if (planned == null) {
    notes.push({
      level: "info",
      text: "No planned sample size: results cannot be confirmatory.",
    });
  }
  if (settings.blinding.show_source_code) {
    notes.push({
      level: "info",
      text: "Source code is shown: before opening, the task checks it for model names that would break blinding.",
    });
  }
  return notes;
}
