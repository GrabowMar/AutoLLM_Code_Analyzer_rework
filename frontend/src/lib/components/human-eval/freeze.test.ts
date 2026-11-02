import { describe, expect, it } from "vitest";
import type { TaskDesign, TaskSettings } from "$lib/api/types/human-eval";
import { emptyDesign, newBlock, newCriterion } from "./design/factories";
import {
  EDITABLE_AFTER_FREEZE,
  changedFrozenKeys,
  describeFreezeLosses,
  editableAfterFreeze,
  editableAfterFreezeText,
  editsLostOnFreeze,
  qualityLockText,
  rebaseOnFreeze,
  settingLocked,
  speedingExcluded,
  withFrozenSettingsFrom,
} from "./freeze";
import { taskActionConfirm } from "./format";

function design(speeding: boolean): TaskDesign {
  const d = emptyDesign(null);
  d.blocks = [newBlock("rating", [])];
  d.analysis.exclusion_rules.speeding = speeding;
  return d;
}

function fallbackSettings(): TaskSettings {
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

function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T;
}

describe("editable after freeze (mirrors design.editable_after_freeze)", () => {
  it("locks quality only while the analysis plan excludes speeders", () => {
    expect(speedingExcluded(design(false))).toBe(false);
    expect(speedingExcluded(design(true))).toBe(true);
    expect(speedingExcluded(null)).toBe(false);
    expect(editableAfterFreeze(design(false))).toBe(EDITABLE_AFTER_FREEZE);
    const strict = editableAfterFreeze(design(true));
    expect(strict.has("quality.min_seconds_per_item")).toBe(false);
    expect(strict.has("access.allow_accounts")).toBe(true);
    expect(strict.size).toBe(EDITABLE_AFTER_FREEZE.size - 1);
  });

  it("decides which settings fields are locked", () => {
    const strict = editableAfterFreeze(design(true));
    const key = "quality.min_seconds_per_item";
    expect(
      settingLocked(key, { frozen: false, readOnly: false, editable: strict }),
    ).toBe(false);
    expect(settingLocked(key, { frozen: true, readOnly: false })).toBe(false);
    expect(
      settingLocked(key, { frozen: true, readOnly: false, editable: strict }),
    ).toBe(true);
    expect(
      settingLocked("access.allow_accounts", {
        frozen: true,
        readOnly: false,
        editable: strict,
      }),
    ).toBe(false);
    expect(
      settingLocked("blinding.show_spec", { frozen: true, readOnly: false }),
    ).toBe(true);
    expect(
      settingLocked("access.allow_accounts", { frozen: false, readOnly: true }),
    ).toBe(true);
  });

  it("names what stays editable without promising quality under a speeding exclusion", () => {
    expect(editableAfterFreezeText(design(false))).toBe(
      "access, hosting limits, quality and allocation limits",
    );
    expect(editableAfterFreezeText(design(true))).toBe(
      "access, hosting limits and allocation limits",
    );
    expect(qualityLockText(design(false))).toBe("");
    expect(qualityLockText(design(true))).toMatch(/excludes speeders/);
  });

  it("finds changed frozen settings leaves", () => {
    const a = fallbackSettings();
    const b = clone(a);
    b.blinding.show_spec = !a.blinding.show_spec;
    b.access.allow_accounts = !a.access.allow_accounts;
    b.quality.min_seconds_per_item = 30;
    expect(changedFrozenKeys(a, b, EDITABLE_AFTER_FREEZE)).toEqual([
      "blinding.show_spec",
    ]);
    expect(changedFrozenKeys(a, b, editableAfterFreeze(design(true)))).toEqual([
      "blinding.show_spec",
      "quality.min_seconds_per_item",
    ]);
  });

  it("copies only the frozen leaves from the source", () => {
    const target = fallbackSettings();
    target.access.allow_accounts = true;
    target.blinding.show_spec = false;
    const source = fallbackSettings();
    const out = withFrozenSettingsFrom(target, source, EDITABLE_AFTER_FREEZE);
    expect(out.blinding.show_spec).toBe(source.blinding.show_spec);
    expect(out.access.allow_accounts).toBe(true);
    expect(target.blinding.show_spec).toBe(false); // input untouched
  });
});

describe("unsaved edits when the task opens (C12)", () => {
  type Draft = {
    title: string;
    mode: "single" | "pairwise";
    design: TaskDesign;
    settings: TaskSettings;
  };

  function saved(): Draft {
    return {
      title: "Study",
      mode: "single",
      design: design(false),
      settings: fallbackSettings(),
    };
  }

  it("reports design, mode and frozen-setting edits an open would discard", () => {
    const base = saved();
    const draft = clone(base);
    expect(editsLostOnFreeze(draft, base, EDITABLE_AFTER_FREEZE)).toEqual([]);
    draft.title = "Renamed"; // stays editable: not lost
    draft.settings.access.allow_accounts = !base.settings.access.allow_accounts; // editable: not lost
    expect(editsLostOnFreeze(draft, base, EDITABLE_AFTER_FREEZE)).toEqual([]);
    const rating = draft.design.blocks[0];
    if (rating.type !== "rating") throw new Error("rating block expected");
    rating.criteria.push(newCriterion("Security", rating.criteria));
    draft.mode = "pairwise";
    draft.settings.blinding.show_spec = !base.settings.blinding.show_spec;
    const lost = editsLostOnFreeze(draft, base, EDITABLE_AFTER_FREEZE);
    expect(lost).toEqual(["mode", "design", "settings.blinding.show_spec"]);
    expect(describeFreezeLosses(lost)).toBe(
      "the design, the mode and 1 frozen setting (blinding.show_spec)",
    );
    expect(describeFreezeLosses(["design"])).toBe("the design");
    expect(describeFreezeLosses([])).toBe("");
  });

  it("shows the frozen design after open and keeps only the edits an opened task accepts", () => {
    const base = saved();
    const fresh = clone(base); // what the server froze: the saved task
    const draft = clone(base);
    const rating = draft.design.blocks[0];
    if (rating.type !== "rating") throw new Error("rating block expected");
    rating.criteria.push(newCriterion("Security", rating.criteria));
    draft.title = "Renamed";
    draft.settings.blinding.show_spec = !base.settings.blinding.show_spec;
    draft.settings.access.allow_accounts = !base.settings.access.allow_accounts;

    const out = rebaseOnFreeze(draft, base, fresh, EDITABLE_AFTER_FREEZE);
    expect(out.dropped).toEqual(["design", "settings.blinding.show_spec"]);
    // The read-only editor now shows what was frozen, not the unsaved criterion.
    expect(out.draft.design).toEqual(fresh.design);
    const shown = out.draft.design.blocks[0];
    expect(
      shown.type === "rating" && shown.criteria.map((c) => c.label),
    ).not.toContain("Security");
    expect(out.draft.settings.blinding.show_spec).toBe(
      base.settings.blinding.show_spec,
    );
    // Still-editable edits survive and still differ from the baseline (so Save sends them) ...
    expect(out.draft.title).toBe("Renamed");
    expect(out.draft.settings.access.allow_accounts).toBe(
      !base.settings.access.allow_accounts,
    );
    // ... and nothing frozen differs any more, so the save cannot 409 or silently drop work.
    expect(
      editsLostOnFreeze(out.draft, out.base, EDITABLE_AFTER_FREEZE),
    ).toEqual([]);
    expect(
      changedFrozenKeys(
        out.base.settings,
        out.draft.settings,
        EDITABLE_AFTER_FREEZE,
      ),
    ).toEqual([]);
    expect(JSON.stringify(out.draft.design)).toBe(
      JSON.stringify(out.base.design),
    );
  });

  it("treats a quality edit as lost when the frozen plan excludes speeders", () => {
    const base = { ...saved(), design: design(true) };
    const draft = clone(base);
    draft.settings.quality.min_seconds_per_item = 5;
    expect(
      editsLostOnFreeze(draft, base, editableAfterFreeze(base.design)),
    ).toEqual(["settings.quality.min_seconds_per_item"]);
    expect(editsLostOnFreeze(draft, base, EDITABLE_AFTER_FREEZE)).toEqual([]);
  });
});

describe("open confirmation", () => {
  const draftTask = (speeding: boolean) => ({
    status: "draft" as const,
    frozen_at: null,
    design: design(speeding),
  });

  it("warns that unsaved Design-tab edits are discarded by opening", () => {
    const copy = taskActionConfirm("open", draftTask(false), {
      unsavedFreezeEdits: "the design",
    });
    expect(copy?.title).toMatch(/unsaved/);
    expect(copy?.description).toMatch(/unsaved changes to the design/);
    expect(copy?.label).toBe("Open with the saved design");
    // A frozen (paused / closed) task has nothing left to freeze.
    expect(
      taskActionConfirm(
        "open",
        { status: "closed", frozen_at: "x" },
        { unsavedFreezeEdits: "the design" },
      )?.label,
    ).toBe("Open task");
  });

  it("says quality stays editable only without a speeding exclusion", () => {
    expect(taskActionConfirm("open", draftTask(false))?.description).toMatch(
      /quality and allocation limits stay editable/,
    );
    const strict =
      taskActionConfirm("open", draftTask(true))?.description ?? "";
    expect(strict).toMatch(
      /access, hosting limits and allocation limits stay editable/,
    );
    expect(strict).toMatch(/excludes speeders/);
    expect(taskActionConfirm("open", draftTask(true))?.label).toBe(
      "Open and freeze",
    );
  });
});
