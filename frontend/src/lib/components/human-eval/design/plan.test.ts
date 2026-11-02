import { describe, expect, it } from "vitest";
import type { Block, QuestionnaireBlock } from "$lib/api/types/human-eval";
import { emptyDesign, newBlock } from "./factories";
import {
  blockMetrics,
  metricOptions,
  metricValue,
  parseMetricValue,
  plannedSampleField,
  planSummary,
  primaryIsValid,
  reconcilePlan,
  renameBlockInPlan,
} from "./plan";

describe("metric values", () => {
  it("round-trips block id and metric", () => {
    expect(parseMetricValue(metricValue("ratings", "composite"))).toEqual({
      block_id: "ratings",
      metric: "composite",
    });
    expect(parseMetricValue("")).toBeNull();
    expect(parseMetricValue("ratings::")).toBeNull();
  });
});

describe("blockMetrics", () => {
  it("offers the documented metric per block type", () => {
    const security = newBlock("security", []);
    const blocks: Block[] = [
      newBlock("rating", []),
      newBlock("requirements", []),
      { ...security, checklist: [{ id: "idor", label: "IDOR" }] } as Block,
      newBlock("scenarios", []),
      newBlock("bugs", []),
      newBlock("pairwise", []),
    ];
    const metrics = blocks.map((b) => blockMetrics(b).map((m) => m.metric));
    expect(metrics).toEqual([
      ["composite", "overall_quality"],
      ["compliance"],
      ["vulnerable_share"],
      ["success_rate"],
      ["bugs_per_rater"],
      ["overall", "usability", "functionality"],
    ]);
  });

  it("offers vulnerable share only for security blocks with a checklist", () => {
    const security = newBlock("security", []);
    expect(blockMetrics(security)).toEqual([]);
    const withChecklist = {
      ...security,
      checklist: [{ id: "idor", label: "IDOR" }],
    } as Block;
    expect(blockMetrics(withChecklist).map((m) => m.metric)).toEqual([
      "vulnerable_share",
    ]);
  });

  it("offers a score only for standard questionnaires and overall only when asked", () => {
    const q = newBlock("questionnaire", []) as QuestionnaireBlock;
    expect(blockMetrics(q).map((m) => m.metric)).toEqual(["score"]);
    expect(blockMetrics({ ...q, instrument: "custom" })).toEqual([]);
    const p = newBlock("pairwise", []);
    expect(
      blockMetrics({ ...p, overall: false } as Block).map((m) => m.metric),
    ).toEqual(["usability", "functionality"]);
  });
});

describe("pairwise primary wording", () => {
  it("names the tested statistic (preference share), not Bradley–Terry strength", () => {
    const options = blockMetrics(newBlock("pairwise", []));
    expect(options.map((o) => o.label)).toEqual([
      "Comparison — overall preference share",
      "Comparison — Usability preference share",
      "Comparison — Functionality preference share",
    ]);
    for (const o of options) {
      expect(o.description).toMatch(
        /^Each app's preference share on .+: \(wins \+ ½ ties\) \/ comparisons, compared between conditions\./,
      );
      expect(o.description).toMatch(
        /Bradley–Terry strengths are reported descriptively only/,
      );
    }
  });
});

describe("plan consistency", () => {
  const rating = newBlock("rating", []);
  const plan = {
    ...emptyDesign(null).analysis,
    primary: { block_id: rating.id, metric: "composite" },
  };

  it("keeps a valid primary and clears a dangling one", () => {
    expect(primaryIsValid(plan, [rating])).toBe(true);
    expect(reconcilePlan(plan, [rating])).toBe(plan);
    expect(reconcilePlan(plan, []).primary).toBeNull();
    expect(primaryIsValid({ ...plan, primary: null }, [])).toBe(true);
  });

  it("follows a block id rename", () => {
    expect(renameBlockInPlan(plan, rating.id, "renamed").primary).toEqual({
      block_id: "renamed",
      metric: "composite",
    });
    expect(renameBlockInPlan(plan, "other", "renamed")).toBe(plan);
  });

  it("lists every option across blocks", () => {
    expect(
      metricOptions([rating, newBlock("bugs", [rating.id])]).map(
        (o) => o.value,
      ),
    ).toEqual([
      "ratings::composite",
      "ratings::overall_quality",
      "bugs::bugs_per_rater",
    ]);
  });
});

describe("planned sample", () => {
  it("maps the allocation strategy to its planned-sample field", () => {
    expect(plannedSampleField("balanced")).toBe("planned_ratings_per_item");
    expect(plannedSampleField("all")).toBe("planned_participants");
    expect(plannedSampleField(undefined)).toBeNull();
  });

  it("summarises the plan", () => {
    const design = emptyDesign(null);
    const rating = newBlock("rating", []);
    design.blocks.push(rating);
    design.analysis.primary = { block_id: rating.id, metric: "composite" };
    design.analysis.planned_ratings_per_item = 3;
    design.analysis.exclusion_rules.speeding = true;
    expect(planSummary(design, "balanced")).toEqual([
      "Primary: Ratings — composite score",
      "Planned 3 ratings per item",
      "Excludes: speeding",
      "Unevaluable apps excluded (complete case)",
    ]);
  });
});
