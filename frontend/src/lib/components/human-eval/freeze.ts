// What a task's first open freezes, mirroring backend/human_eval/services/design.py
// (EDITABLE_AFTER_FREEZE, editable_after_freeze, changed_frozen_keys), and what that
// means for edits the Design tab has not saved yet.

import type {
  TaskDesign,
  TaskMode,
  TaskSettings,
} from "$lib/api/types/human-eval";

/** Settings leaves (`section.key`) that stay editable after the design is frozen (spec §5.1 / R1). */
export const EDITABLE_AFTER_FREEZE: ReadonlySet<string> = new Set([
  "allocation.max_items_per_participant",
  "allocation.assignment_timeout_minutes",
  "allocation.allow_skip",
  "allocation.allow_overflow",
  "access.allow_invite_links",
  "access.allow_access_codes",
  "access.allow_accounts",
  "hosting.on_demand",
  "hosting.idle_stop_minutes",
  "hosting.max_running_apps",
  "quality.min_seconds_per_item",
]);

/** Anything carrying a design's analysis plan (a TaskDesign, a TaskDetail's `design`). */
export type DesignLike =
  | { analysis?: Partial<TaskDesign["analysis"]> | null }
  | null
  | undefined;

/** The pre-registered analysis excludes speeders: the quality threshold then decides which data count. */
export function speedingExcluded(design: DesignLike): boolean {
  return !!design?.analysis?.exclusion_rules?.speeding;
}

/**
 * Settings leaves editable after freeze for this (frozen) design: `quality.*` is frozen too
 * while the analysis plan excludes speeders (backend `editable_after_freeze(design)`).
 */
export function editableAfterFreeze(design: DesignLike): ReadonlySet<string> {
  return editableSettingKeys(speedingExcluded(design));
}

const EDITABLE_WITHOUT_QUALITY: ReadonlySet<string> = new Set(
  [...EDITABLE_AFTER_FREEZE].filter((key) => !key.startsWith("quality.")),
);

/** EDITABLE_AFTER_FREEZE, without `quality.*` when `lockQuality` (a speeding exclusion is pre-registered). */
export function editableSettingKeys(lockQuality: boolean): ReadonlySet<string> {
  return lockQuality ? EDITABLE_WITHOUT_QUALITY : EDITABLE_AFTER_FREEZE;
}

/** "access, hosting limits, quality and allocation limits" — the settings an opened task still accepts, for notices. */
export function editableAfterFreezeText(design: DesignLike): string {
  return speedingExcluded(design)
    ? "access, hosting limits and allocation limits"
    : "access, hosting limits, quality and allocation limits";
}

/** Why the quality threshold is locked after opening (empty when it is not). */
export function qualityLockText(design: DesignLike): string {
  return speedingExcluded(design)
    ? "The minimum seconds per item is locked as well: the analysis plan excludes speeders, so it decides which data count."
    : "";
}

/** A settings field is read-only: nothing is editable, or the design is frozen and the key isn't in `editable`. */
export function settingLocked(
  key: string,
  opts: { frozen: boolean; readOnly: boolean; editable?: ReadonlySet<string> },
): boolean {
  return (
    opts.readOnly ||
    (opts.frozen && !(opts.editable ?? EDITABLE_AFTER_FREEZE).has(key))
  );
}

type Leaves = Record<string, Record<string, unknown>>;

function sections(settings: TaskSettings): Leaves {
  return settings as unknown as Leaves;
}

/** `section.key` leaves of a settings document. */
function leafKeys(...docs: TaskSettings[]): string[] {
  const keys = new Set<string>();
  for (const doc of docs) {
    for (const [section, values] of Object.entries(sections(doc))) {
      if (values && typeof values === "object")
        for (const key of Object.keys(values)) keys.add(`${section}.${key}`);
    }
  }
  return [...keys];
}

function leaf(settings: TaskSettings, path: string): unknown {
  const [section, key] = path.split(".");
  return sections(settings)[section]?.[key];
}

function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Deep copy of JSON data (works on reactive proxies, unlike structuredClone). */
function clone<T>(value: T): T {
  return value === undefined ? value : (JSON.parse(JSON.stringify(value)) as T);
}

/** Frozen settings leaves (not in `editable`) that differ between two documents, sorted. */
export function changedFrozenKeys(
  before: TaskSettings,
  after: TaskSettings,
  editable: ReadonlySet<string>,
): string[] {
  return leafKeys(before, after)
    .filter(
      (path) =>
        !editable.has(path) && !same(leaf(before, path), leaf(after, path)),
    )
    .sort();
}

/** `target` with every frozen leaf (not in `editable`) taken from `source` (deep copies; inputs untouched). */
export function withFrozenSettingsFrom(
  target: TaskSettings,
  source: TaskSettings,
  editable: ReadonlySet<string>,
): TaskSettings {
  const out = clone(target) as unknown as Leaves;
  for (const path of leafKeys(source)) {
    if (editable.has(path)) continue;
    const [section, key] = path.split(".");
    out[section] = {
      ...(out[section] ?? {}),
      [key]: clone(leaf(source, path)),
    };
  }
  return out as unknown as TaskSettings;
}

/** The parts of a Design-tab draft that a freeze locks. */
export interface FreezableDraft {
  mode: TaskMode;
  design: TaskDesign;
  settings: TaskSettings;
}

/**
 * Unsaved edits (draft vs. its baseline) that a freeze would not take: "mode", "design" and
 * every frozen settings leaf as "settings.<section>.<key>". Opening freezes the saved task.
 */
export function editsLostOnFreeze(
  draft: FreezableDraft,
  base: FreezableDraft,
  editable: ReadonlySet<string>,
): string[] {
  const lost: string[] = [];
  if (draft.mode !== base.mode) lost.push("mode");
  if (!same(draft.design, base.design)) lost.push("design");
  for (const path of changedFrozenKeys(base.settings, draft.settings, editable))
    lost.push(`settings.${path}`);
  return lost;
}

/**
 * Draft and baseline after the task froze while the draft held unsaved edits: mode, design
 * and the frozen settings take the server's (frozen) values in both, so the form shows what
 * was frozen and a save no longer carries them (it would 409 or be dropped silently). Edits
 * a frozen task still accepts (title, schedule, editable settings) stay unsaved in the draft.
 */
export function rebaseOnFreeze<D extends FreezableDraft>(
  draft: D,
  base: D,
  fresh: FreezableDraft,
  editable: ReadonlySet<string>,
): { draft: D; base: D; dropped: string[] } {
  const dropped = editsLostOnFreeze(draft, base, editable);
  const frozenParts = (d: D): D => ({
    ...d,
    mode: fresh.mode,
    design: clone(fresh.design),
    settings: withFrozenSettingsFrom(d.settings, fresh.settings, editable),
  });
  return { draft: frozenParts(draft), base: frozenParts(base), dropped };
}

/** "the design, the mode and 2 frozen settings (blinding.show_spec, allocation.strategy)". */
export function describeFreezeLosses(lost: readonly string[]): string {
  const parts: string[] = [];
  if (lost.includes("design")) parts.push("the design");
  if (lost.includes("mode")) parts.push("the mode");
  const settings = lost
    .filter((x) => x.startsWith("settings."))
    .map((x) => x.slice("settings.".length));
  if (settings.length) {
    parts.push(
      `${settings.length === 1 ? "1 frozen setting" : `${settings.length} frozen settings`} (${settings.join(", ")})`,
    );
  }
  if (parts.length <= 1) return parts[0] ?? "";
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}
