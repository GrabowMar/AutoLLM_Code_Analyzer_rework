import { describe, expect, it } from "vitest";
import type { HumanEvalEnvironment, Preset } from "$lib/api/types/human-eval";
import { newBlock } from "$lib/components/human-eval/design/factories";
import {
  applyPreset,
  applyScratch,
  createInput,
  designFingerprint,
  DRAFT_KEY,
  draftNotes,
  emptyDraft,
  fallbackSettings,
  hasContent,
  parseDraft,
} from "./wizard";

const settings = fallbackSettings();

const preset: Preset = {
  slug: "security-assessment",
  name: "Security assessment",
  description: "Guided security testing",
  mode: "single",
  design: {
    version: 1,
    blocks: [newBlock("security", [])],
    analysis: {
      primary: null,
      planned_ratings_per_item: 3,
      planned_participants: null,
      exclusion_rules: {
        speeding: true,
        attention_failed: false,
        straightlining: false,
        app_not_viewed: true,
      },
      unevaluable_policy: "exclude",
    },
    consent: { enabled: true, text: "Consent" },
    pre_questions: [],
    post_questions: [],
  },
  settings: {
    ...settings,
    hosting: {
      ...settings.hosting,
      isolation: "exclusive",
      reset: "per_assignment",
    },
  },
};

describe("starting points", () => {
  it("applies a preset's mode, design and settings but keeps typed fields", () => {
    const draft = { ...emptyDraft(null), title: "My study", runIds: ["r1"] };
    const next = applyPreset(draft, preset);
    expect(next.start).toBe("security-assessment");
    expect(next.title).toBe("My study");
    expect(next.runIds).toEqual(["r1"]);
    expect(next.design.blocks).toHaveLength(1);
    expect(next.design).not.toBe(preset.design);
    expect(next.settings.hosting.reset).toBe("per_assignment");
    next.design.blocks[0].title = "Changed";
    expect(preset.design.blocks[0].title).toBe("Security");
  });

  it("starts from scratch with no blocks and the environment's defaults", () => {
    const env = {
      default_settings: { ...settings, quality: { min_seconds_per_item: 5 } },
      default_design: null,
    } as unknown as HumanEvalEnvironment;
    const next = applyScratch(applyPreset(emptyDraft(null), preset), env);
    expect(next.start).toBe("");
    expect(next.design.blocks).toEqual([]);
    expect(next.settings.quality.min_seconds_per_item).toBe(5);
  });

  it("starts without a primary outcome although the environment's default design has one", () => {
    // The backend's default_design("single") pre-registers ratings/composite for its own rating
    // block; an empty design has no such block, so the primary would only dangle (and block Create).
    const env = {
      default_settings: settings,
      default_design: {
        ...preset.design,
        blocks: [],
        analysis: {
          ...preset.design.analysis,
          primary: { block_id: "ratings", metric: "composite" },
        },
      },
    } as unknown as HumanEvalEnvironment;
    expect(emptyDraft(env).design.analysis.primary).toBeNull();
    const scratch = applyScratch(applyPreset(emptyDraft(env), preset), env);
    expect(scratch.design.analysis.primary).toBeNull();
    expect(scratch.design.analysis.exclusion_rules.speeding).toBe(true);
  });

  it("fingerprints what a preset replaces", () => {
    const a = applyPreset(emptyDraft(null), preset);
    const b = { ...a, title: "Other title" };
    expect(designFingerprint(a)).toBe(designFingerprint(b));
    expect(designFingerprint({ ...a, mode: "pairwise" })).not.toBe(
      designFingerprint(a),
    );
  });
});

describe("stored drafts", () => {
  it("round-trips through JSON and rejects junk", () => {
    const draft = {
      ...applyPreset(emptyDraft(null), preset),
      title: "T",
      studyId: "study-1",
    };
    expect(parseDraft(JSON.stringify(draft))).toEqual(draft);
    expect(parseDraft(null)).toBeNull();
    expect(parseDraft("not json")).toBeNull();
    expect(
      parseDraft(JSON.stringify({ mode: "triple", design: {}, settings: {} })),
    ).toBeNull();
    expect(
      parseDraft(JSON.stringify({ mode: "single", design: [], settings: {} })),
    ).toBeNull();
  });

  it("never restores a v2 draft's job or experiment ids", () => {
    const v2 = {
      ...emptyDraft(null),
      jobIds: ["j1"],
      experimentId: "exp-1",
    } as Record<string, unknown>;
    delete v2.runIds;
    delete v2.studyId;
    const parsed = parseDraft(JSON.stringify(v2));
    expect(parsed?.runIds).toEqual([]);
    expect(parsed?.studyId).toBeNull();
    expect(parsed).not.toHaveProperty("jobIds");
    expect(parsed).not.toHaveProperty("experimentId");
    expect(DRAFT_KEY).toMatch(/:v3$/);
  });

  it("knows an untouched draft has nothing to restore", () => {
    expect(hasContent(emptyDraft(null))).toBe(false);
    expect(hasContent({ ...emptyDraft(null), title: " x " })).toBe(true);
    expect(hasContent({ ...emptyDraft(null), start: "" })).toBe(true);
    expect(hasContent({ ...emptyDraft(null), studyId: "s" })).toBe(true);
    expect(hasContent({ ...emptyDraft(null), runIds: ["r"] })).toBe(true);
  });
});

describe("createInput", () => {
  it("trims text and only sends the app pool parts that are set", () => {
    const input = createInput({
      ...emptyDraft(null),
      title: "  Study  ",
      description: " d ",
    });
    expect(input.title).toBe("Study");
    expect(input.description).toBe("d");
    expect(input).not.toHaveProperty("study_id");
    expect(input).not.toHaveProperty("run_ids");
    const withApps = createInput({
      ...emptyDraft(null),
      title: "S",
      runIds: ["a"],
      studyId: "s",
    });
    expect(withApps.run_ids).toEqual(["a"]);
    expect(withApps.study_id).toBe("s");
    // The v2 keys are never sent (clean break).
    expect(withApps).not.toHaveProperty("job_ids");
    expect(withApps).not.toHaveProperty("experiment_id");
  });
});

describe("draftNotes", () => {
  it("warns about an empty pool and design", () => {
    const texts = draftNotes(emptyDraft(null)).map((n) => n.text);
    expect(texts.some((t) => t.startsWith("No apps yet"))).toBe(true);
    expect(texts.some((t) => t.startsWith("No evaluation blocks"))).toBe(true);
  });

  it("flags shared app state as a warning only when security testing is designed", () => {
    const security = {
      ...applyPreset(emptyDraft(null), preset),
      runIds: ["a"],
    };
    security.settings = {
      ...security.settings,
      hosting: { ...security.settings.hosting, reset: "none" },
    };
    const note = draftNotes(security).find((n) =>
      n.text.includes("shared app state"),
    );
    expect(note?.level).toBe("warning");

    const rating = { ...emptyDraft(null), runIds: ["a"] };
    rating.design = { ...rating.design, blocks: [newBlock("rating", [])] };
    expect(
      draftNotes(rating).find((n) => n.text.includes("shared app state"))
        ?.level,
    ).toBe("info");
  });

  it("flags pairwise designs without a pairwise block and disallowed blocks", () => {
    const d = { ...emptyDraft(null), mode: "pairwise" as const, runIds: ["a"] };
    d.design = { ...d.design, blocks: [newBlock("rating", [])] };
    const texts = draftNotes(d).map((n) => n.text);
    expect(texts).toContain("Pairwise tasks need a pairwise preference block.");
    expect(
      texts.some((t) => t.includes("not available in pairwise mode")),
    ).toBe(true);
  });

  it("notes a missing primary outcome and planned sample", () => {
    const texts = draftNotes(applyPreset(emptyDraft(null), preset)).map(
      (n) => n.text,
    );
    expect(texts.some((t) => t.startsWith("No primary outcome"))).toBe(true);
    expect(texts.some((t) => t.startsWith("No planned sample"))).toBe(false);
  });
});
