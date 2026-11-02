import { describe, expect, it } from "vitest";
import type {
  Block,
  HumanEvalEnvironment,
  PairwiseBlock,
  Question,
  RatingBlock,
  SecurityBlock,
} from "$lib/api/types/human-eval";
import {
  allowedBlockTypes,
  applyQuestionKind,
  blockAllowed,
  disallowedBlocks,
  duplicateBlock,
  emptyDesign,
  newBlock,
  newCriterion,
  newOption,
  newQuestion,
  normalizeDesign,
} from "./factories";
import { isValidId } from "./ids";
import { insertedAfter, moved, removedAt } from "./lists";
import { primaryIsValid, withAddedBlocks } from "./plan";
import {
  pruneScaleLabels,
  scaleIssues,
  scaleSummary,
  scaleValues,
} from "./scale";

const env = {
  security_checklist_default: [
    {
      id: "idor",
      label: "Broken access control",
      help: "Change ids in URLs",
      category: "A01:2021",
    },
  ],
  default_design: {
    version: 1,
    blocks: [],
    analysis: {
      // What the backend sends: default_design("single") pre-registers its own rating block.
      primary: { block_id: "ratings", metric: "composite" },
      planned_ratings_per_item: 3,
      planned_participants: null,
      exclusion_rules: {
        speeding: true,
        attention_failed: true,
        straightlining: false,
        app_not_viewed: false,
      },
      unevaluable_policy: "exclude",
    },
    consent: { enabled: true, text: "Consent text" },
    pre_questions: [],
    post_questions: [],
  },
} as unknown as HumanEvalEnvironment;

describe("block catalogue", () => {
  it("limits block types by mode", () => {
    expect(allowedBlockTypes("pairwise")).toEqual(["bugs", "pairwise"]);
    expect(allowedBlockTypes("single")).not.toContain("pairwise");
    expect(blockAllowed("single", "rating")).toBe(true);
    expect(blockAllowed("pairwise", "rating")).toBe(false);
  });

  it("finds blocks a mode switch made invalid", () => {
    const blocks: Block[] = [
      newBlock("rating", []),
      newBlock("bugs", ["rating"]),
    ];
    expect(disallowedBlocks("pairwise", blocks).map((b) => b.type)).toEqual([
      "rating",
    ]);
  });
});

describe("newBlock", () => {
  it("creates every block type with a valid, unique id", () => {
    const taken: string[] = [];
    for (const type of [
      "rating",
      "requirements",
      "security",
      "scenarios",
      "questionnaire",
      "bugs",
      "pairwise",
    ] as const) {
      const block = newBlock(type, taken, env);
      expect(block.type).toBe(type);
      expect(isValidId(block.id)).toBe(true);
      expect(taken).not.toContain(block.id);
      taken.push(block.id);
    }
  });

  it("suffixes a second block of the same type", () => {
    const first = newBlock("rating", []);
    const second = newBlock("rating", [first.id]);
    expect(first.id).toBe("ratings");
    expect(second.id).toBe("ratings_2");
  });

  it("seeds the security checklist from the environment (copied, not shared)", () => {
    const block = newBlock("security", [], env) as SecurityBlock;
    expect(block.checklist).toEqual(env.security_checklist_default);
    expect(block.checklist[0]).not.toBe(env.security_checklist_default[0]);
    expect(block.finding_review.enabled).toBe(false);
    expect((newBlock("security", [], null) as SecurityBlock).checklist).toEqual(
      [],
    );
  });

  it("starts rating and pairwise blocks with criteria", () => {
    const rating = newBlock("rating", []) as RatingBlock;
    expect(rating.criteria.map((c) => c.id)).toEqual(["overall_quality"]);
    const pairwise = newBlock("pairwise", []) as PairwiseBlock;
    expect(pairwise.criteria.map((c) => c.id)).toEqual([
      "usability",
      "functionality",
    ]);
    expect(pairwise.overall).toBe(true);
  });
});

describe("duplicateBlock", () => {
  it("deep-copies with a new id and keeps entry ids", () => {
    const original = newBlock("rating", []) as RatingBlock;
    const copy = duplicateBlock(original, [original.id]) as RatingBlock;
    expect(copy.id).toBe("ratings_2");
    expect(copy.title).toBe("Ratings (copy)");
    expect(copy.criteria.map((c) => c.id)).toEqual(
      original.criteria.map((c) => c.id),
    );
    copy.criteria[0].label = "Changed";
    expect(original.criteria[0].label).toBe("Overall quality");
  });
});

describe("entries", () => {
  it("derives criterion ids from labels, unique within the block", () => {
    const first = newCriterion("Usability", []);
    const second = newCriterion("Usability", [first]);
    expect([first.id, second.id]).toEqual(["usability", "usability_2"]);
    expect(first.scale).toEqual({
      min: 1,
      max: 7,
      step: 1,
      min_label: "Very poor",
      max_label: "Excellent",
    });
  });

  it("creates questions with the fields their kind needs", () => {
    const single = newQuestion("single", "Role", []);
    expect(single.options?.map((o) => o.value)).toEqual([
      "option_1",
      "option_2",
    ]);
    const attention = newQuestion("attention", "Pick blue", [single]);
    expect(attention.required).toBe(true);
    expect(attention.expected).toBe("option_1");
    const scale = newQuestion("scale", "Confidence", []);
    expect(scale.scale).toEqual({
      min: 1,
      max: 5,
      step: 1,
      min_label: "",
      max_label: "",
    });
    const number = newQuestion("number", "Years", []);
    expect(number).toMatchObject({ min: null, max: null });
    expect(newQuestion("text", "Comments", []).required).toBe(false);
  });

  it("switches kinds without leaking fields", () => {
    const q: Question = { ...newQuestion("attention", "Check", []) };
    const asText = applyQuestionKind(q, "text");
    expect(asText.options).toBeUndefined();
    expect(asText.expected).toBeUndefined();
    const back = applyQuestionKind({ ...q, expected: "missing" }, "attention");
    expect(back.expected).toBe(back.options?.[0].value);
  });

  it("derives option values from labels", () => {
    const a = newOption("Yes, often", []);
    expect(a.value).toBe("yes_often");
    expect(newOption("Yes, often", [a]).value).toBe("yes_often_2");
  });
});

describe("designs", () => {
  it("builds an empty design from the environment defaults", () => {
    const d = emptyDesign(env);
    expect(d.blocks).toEqual([]);
    expect(d.consent).toEqual({ enabled: true, text: "Consent text" });
    expect(d.analysis.planned_ratings_per_item).toBe(3);
    expect(d.analysis.exclusion_rules.speeding).toBe(true);
    expect(emptyDesign(null).analysis.primary).toBeNull();
  });

  it("drops the environment's primary, which points at a block the empty design lacks", () => {
    const d = emptyDesign(env);
    expect(d.analysis.primary).toBeNull();
    expect(primaryIsValid(d.analysis, d.blocks)).toBe(true);
    // The environment's own copy is untouched.
    expect(env.default_design.analysis.primary).toEqual({
      block_id: "ratings",
      metric: "composite",
    });
  });

  it("never lets a new block adopt a primary that was dangling before the add", () => {
    // A restored draft can still carry the stale primary; a new rating block gets id "ratings".
    const stale = {
      ...emptyDesign(null),
      analysis: {
        ...emptyDesign(null).analysis,
        primary: { block_id: "ratings", metric: "composite" },
      },
    };
    const rating = newBlock("rating", []);
    expect(rating.id).toBe("ratings");
    const next = withAddedBlocks(stale, [...stale.blocks, rating]);
    expect(next.blocks.map((b) => b.id)).toEqual(["ratings"]);
    expect(next.analysis.primary).toBeNull();
    // A primary that was valid before the add is kept as is.
    const valid = { blocks: [rating], analysis: { ...stale.analysis } };
    const kept = withAddedBlocks(valid, [
      rating,
      newBlock("bugs", ["ratings"]),
    ]);
    expect(kept.analysis).toBe(valid.analysis);
  });

  it("fills missing fields of partial designs", () => {
    const d = normalizeDesign({ blocks: [newBlock("bugs", [])] } as never);
    expect(d.version).toBe(1);
    expect(d.pre_questions).toEqual([]);
    expect(d.analysis.unevaluable_policy).toBe("exclude");
    expect(d.analysis.exclusion_rules.app_not_viewed).toBe(false);
    expect(normalizeDesign(null).blocks).toEqual([]);
  });
});

describe("lists", () => {
  it("moves, removes and inserts without mutating", () => {
    const list = ["a", "b", "c"];
    expect(moved(list, 0, 1)).toEqual(["b", "a", "c"]);
    expect(moved(list, 2, 1)).toEqual(["a", "b", "c"]);
    expect(moved(list, 0, -1)).toEqual(["a", "b", "c"]);
    expect(removedAt(list, 1)).toEqual(["a", "c"]);
    expect(insertedAfter(list, 0, "x")).toEqual(["a", "x", "b", "c"]);
    expect(insertedAfter(list, 9, "x")).toEqual(["a", "b", "c", "x"]);
    expect(list).toEqual(["a", "b", "c"]);
  });
});

describe("scales", () => {
  it("lists values on the step grid", () => {
    expect(scaleValues({ min: 1, max: 5 })).toEqual([1, 2, 3, 4, 5]);
    expect(scaleValues({ min: 0, max: 100, step: 25 })).toEqual([
      0, 25, 50, 75, 100,
    ]);
    expect(scaleSummary({ min: 0, max: 100, step: 5 })).toBe("0–100 (step 5)");
  });

  it("reports the backend's scale rules", () => {
    expect(scaleIssues({ min: 1, max: 7 })).toEqual([]);
    expect(scaleIssues({ min: 5, max: 5 })).toEqual([
      "Maximum must be greater than minimum.",
    ]);
    expect(scaleIssues({ min: 0, max: 10, step: 3 })).toEqual([
      "Step 3 must divide the range 10.",
    ]);
    expect(scaleIssues({ min: 0, max: 200 })).toEqual([
      "The range may span at most 100 points.",
    ]);
    expect(scaleIssues({ min: 1.5, max: 7 })).toEqual([
      "Minimum and maximum must be whole numbers.",
    ]);
    expect(
      scaleIssues({ min: 0, max: 10, step: 5, labels: { "3": "x" } }),
    ).toEqual(["Value labels outside the scale: 3."]);
    expect(scaleValues({ min: 0, max: 10, step: 3 })).toEqual([]);
  });

  it("prunes blank and off-grid labels", () => {
    expect(
      pruneScaleLabels({
        min: 1,
        max: 5,
        labels: { "1": "Low", "3": " ", "9": "Nope" },
      }),
    ).toEqual({ "1": "Low" });
    expect(
      pruneScaleLabels({ min: 1, max: 5, labels: { "3": "" } }),
    ).toBeUndefined();
  });
});
