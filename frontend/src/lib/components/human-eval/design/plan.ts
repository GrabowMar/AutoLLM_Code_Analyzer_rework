// Analysis plan helpers: which primary metrics each block offers (mirrors the
// AnalysisPlan doc in types/human-eval.ts) and keeping the plan consistent
// when blocks change.

import type {
  AllocationStrategy,
  AnalysisPlan,
  Block,
  TaskDesign,
} from "$lib/api/types/human-eval";

export interface MetricOption {
  block_id: string;
  metric: string;
  /** Option value for a single select: "<block_id>::<metric>". */
  value: string;
  label: string;
  description: string;
}

const SEP = "::";

export function metricValue(blockId: string, metric: string): string {
  return `${blockId}${SEP}${metric}`;
}

export function parseMetricValue(
  value: string,
): { block_id: string; metric: string } | null {
  const i = value.indexOf(SEP);
  if (i <= 0) return null;
  const block_id = value.slice(0, i);
  const metric = value.slice(i + SEP.length);
  return metric ? { block_id, metric } : null;
}

const PAIRWISE_SHARE_HELP = (source: string) =>
  `Each app's preference share on ${source}: (wins + ½ ties) / comparisons, compared between conditions. Bradley–Terry strengths are reported descriptively only.`;

/** Metrics one block can contribute as the pre-registered primary outcome. */
export function blockMetrics(block: Block): MetricOption[] {
  const opt = (
    metric: string,
    label: string,
    description: string,
  ): MetricOption => ({
    block_id: block.id,
    metric,
    value: metricValue(block.id, metric),
    label: `${block.title || block.id} — ${label}`,
    description,
  });
  switch (block.type) {
    case "rating":
      return [
        opt(
          "composite",
          "composite score",
          "Mean of all criteria, each rescaled to 0–1",
        ),
        ...block.criteria.map((c) =>
          opt(c.id, c.label || c.id, "One criterion's mean rating"),
        ),
      ];
    case "requirements":
      return [
        opt(
          "compliance",
          "compliance",
          "Met = 1, partial = ½, not met / blocked = 0",
        ),
      ];
    case "security":
      // Needs a checklist (the backend offers no metric otherwise).
      return block.checklist.length
        ? [
            opt(
              "vulnerable_share",
              "vulnerable share",
              "Share of checklist items judged vulnerable",
            ),
          ]
        : [];
    case "scenarios":
      return [
        opt(
          "success_rate",
          "success rate",
          "Share of attempted scenarios completed",
        ),
      ];
    case "questionnaire":
      return block.instrument === "custom"
        ? []
        : [
            opt(
              "score",
              "instrument score",
              "Standard 0–100 score of the instrument",
            ),
          ];
    case "bugs":
      return [
        opt(
          "bugs_per_rater",
          "bugs per rater",
          "Bug reports per submitted assignment",
        ),
      ];
    case "pairwise":
      // The confirmatory contrast compares each app's preference share between conditions
      // (analysis.py); Bradley–Terry strengths are only reported descriptively.
      return [
        ...(block.overall
          ? [
              opt(
                "overall",
                "overall preference share",
                PAIRWISE_SHARE_HELP("the overall question"),
              ),
            ]
          : []),
        ...block.criteria.map((c) =>
          opt(
            c.id,
            `${c.label || c.id} preference share`,
            PAIRWISE_SHARE_HELP("one criterion"),
          ),
        ),
      ];
  }
}

export function metricOptions(blocks: readonly Block[]): MetricOption[] {
  return blocks.flatMap((b) => blockMetrics(b));
}

/** The plan's primary still points at an existing block and metric. */
export function primaryIsValid(
  plan: AnalysisPlan,
  blocks: readonly Block[],
): boolean {
  if (!plan.primary) return true;
  const value = metricValue(plan.primary.block_id, plan.primary.metric);
  return metricOptions(blocks).some((o) => o.value === value);
}

/** A copy of the plan whose primary is cleared when it no longer exists (block deleted, criterion removed). */
export function reconcilePlan(
  plan: AnalysisPlan,
  blocks: readonly Block[],
): AnalysisPlan {
  return primaryIsValid(plan, blocks) ? plan : { ...plan, primary: null };
}

/**
 * Blocks and plan after adding blocks (`nextBlocks` = the current blocks plus the new ones).
 * A primary that already pointed at no block (a restored draft's, say) is cleared first: a
 * new block can reuse that id (a new rating block is always "ratings"), and the stale
 * primary would then pre-register an outcome nobody picked.
 */
export function withAddedBlocks(
  design: Pick<TaskDesign, "blocks" | "analysis">,
  nextBlocks: Block[],
): Pick<TaskDesign, "blocks" | "analysis"> {
  return {
    blocks: nextBlocks,
    analysis: reconcilePlan(design.analysis, design.blocks),
  };
}

/** Rewrite the plan's primary after a block id change (duplicate keeps the original's id). */
export function renameBlockInPlan(
  plan: AnalysisPlan,
  oldId: string,
  newId: string,
): AnalysisPlan {
  if (plan.primary?.block_id !== oldId) return plan;
  return { ...plan, primary: { ...plan.primary, block_id: newId } };
}

/** Which planned-sample field applies under an allocation strategy. */
export function plannedSampleField(
  strategy: AllocationStrategy | null | undefined,
): "planned_ratings_per_item" | "planned_participants" | null {
  if (strategy === "balanced") return "planned_ratings_per_item";
  if (strategy === "all") return "planned_participants";
  return null;
}

/** Short summary of a plan for review screens. */
export function planSummary(
  design: TaskDesign,
  strategy?: AllocationStrategy | null,
): string[] {
  const plan = design.analysis;
  const lines: string[] = [];
  if (plan.primary) {
    const option = metricOptions(design.blocks).find(
      (o) =>
        o.value === metricValue(plan.primary!.block_id, plan.primary!.metric),
    );
    lines.push(
      `Primary: ${option?.label ?? `${plan.primary.block_id} / ${plan.primary.metric}`}`,
    );
  } else {
    lines.push("No primary metric (results are exploratory)");
  }
  const field = plannedSampleField(strategy);
  if (
    field === "planned_ratings_per_item" ||
    (!field && plan.planned_ratings_per_item != null)
  ) {
    lines.push(
      plan.planned_ratings_per_item != null
        ? `Planned ${plan.planned_ratings_per_item} ratings per item`
        : "No planned ratings per item",
    );
  }
  if (
    field === "planned_participants" ||
    (!field && plan.planned_participants != null)
  ) {
    lines.push(
      plan.planned_participants != null
        ? `Planned ${plan.planned_participants} participants`
        : "No planned participant count",
    );
  }
  const rules = Object.entries(plan.exclusion_rules)
    .filter(([, on]) => on)
    .map(
      ([k]) =>
        EXCLUSION_RULE_LABELS[k as keyof AnalysisPlan["exclusion_rules"]] ?? k,
    );
  lines.push(
    rules.length ? `Excludes: ${rules.join(", ")}` : "No rule-based exclusions",
  );
  lines.push(
    plan.unevaluable_policy === "floor"
      ? "Unevaluable apps count at the floor"
      : "Unevaluable apps excluded (complete case)",
  );
  return lines;
}

export const EXCLUSION_RULE_LABELS: Record<
  keyof AnalysisPlan["exclusion_rules"],
  string
> = {
  speeding: "speeding",
  attention_failed: "failed attention checks",
  straightlining: "straight-lining",
  app_not_viewed: "app never viewed",
};
