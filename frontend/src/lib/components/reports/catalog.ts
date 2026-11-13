/**
 * The report types as the pages present them: label (the backend `Report.Type` label),
 * icon, one-line purpose, group, what the configuration needs, and what the report
 * contains (the tables `renderers.report_tables` builds for that type, in order).
 */
import type { Component } from "svelte";
import Brain from "@lucide/svelte/icons/brain";
import GitCompareArrows from "@lucide/svelte/icons/git-compare-arrows";
import Wrench from "@lucide/svelte/icons/wrench";
import TrendingUp from "@lucide/svelte/icons/trending-up";
import Layers from "@lucide/svelte/icons/layers";
import FlaskConical from "@lucide/svelte/icons/flask-conical";
import Microscope from "@lucide/svelte/icons/microscope";
import { REPORT_TYPE_LABELS, type ReportType } from "$lib/api/reports";

export type ReportGroup = "research" | "platform";

export interface ReportTypeInfo {
  key: ReportType;
  label: string;
  icon: Component;
  group: ReportGroup;
  /** One line: what the report answers. */
  purpose: string;
  /** What must be picked before it can run ("" = nothing). */
  needs: string;
  /** What the report holds, in order. */
  contents: string[];
}

export const REPORT_GROUPS: {
  key: ReportGroup;
  label: string;
  lead: string;
}[] = [
  {
    key: "research",
    label: "Research reports",
    lead: "Paper-ready tables for one experiment or task, with uncertainty.",
  },
  {
    key: "platform",
    label: "Platform rollups",
    lead: "Descriptive aggregates over your runs and analyses.",
  },
];

export const REPORT_TYPES: ReportTypeInfo[] = [
  {
    key: "study_report",
    label: REPORT_TYPE_LABELS.study_report,
    icon: Microscope,
    group: "research",
    purpose:
      "Outcome funnel, deduplicated issues, correlations and rankings with uncertainty, and the threats to validity found in the data.",
    needs: "",
    contents: [
      "Protocol and definitions",
      "Outcome funnel and plan metrics by model",
      "Issues by model, task and tool (deduplicated)",
      "Dependency advisories and manifest findings",
      "Tool overlap and issue types",
      "Code size and complexity",
      "Spearman correlations (exploratory)",
      "TOPSIS ranking with rank intervals",
      "Threats to validity",
    ],
  },
  {
    key: "experiment_report",
    label: REPORT_TYPE_LABELS.experiment_report,
    icon: FlaskConical,
    group: "research",
    purpose:
      "Leaderboard and paired comparisons against the pre-registered baseline condition of one experiment.",
    needs: "an experiment",
    contents: [
      "Study record",
      "Leaderboard (pass rate, 95% CI)",
      "Paired deltas vs the baseline",
      "Code issues per condition",
      "Notes",
    ],
  },
  {
    key: "template_comparison",
    label: REPORT_TYPE_LABELS.template_comparison,
    icon: GitCompareArrows,
    group: "research",
    purpose:
      "Every model on the same library task, one row per model and treatment.",
    needs: "a library task",
    contents: ["Models on the task", "Outcomes, size and findings per model"],
  },
  {
    key: "model_analysis",
    label: REPORT_TYPE_LABELS.model_analysis,
    icon: Brain,
    group: "platform",
    purpose:
      "Generation, code size and findings aggregated for a single model.",
    needs: "a model",
    contents: ["Runs and outcomes", "Lines of code", "Findings by tool"],
  },
  {
    key: "tool_analysis",
    label: REPORT_TYPE_LABELS.tool_analysis,
    icon: Wrench,
    group: "platform",
    purpose: "Findings broken down across analyzers and security tools.",
    needs: "",
    contents: ["Runs per tool", "Findings by level as each tool printed it"],
  },
  {
    key: "generation_analytics",
    label: REPORT_TYPE_LABELS.generation_analytics,
    icon: TrendingUp,
    group: "platform",
    purpose:
      "Throughput, outcomes and code size of your runs over a time window.",
    needs: "",
    contents: [
      "Runs by status and outcome",
      "Runs by kind and model",
      "Lines of code per run",
    ],
  },
  {
    key: "comprehensive",
    label: REPORT_TYPE_LABELS.comprehensive,
    icon: Layers,
    group: "platform",
    purpose:
      "Platform-wide rollup of generation, analysis, tools and ranking sensitivity.",
    needs: "",
    contents: [
      "Platform totals",
      "Generation analytics",
      "Tool analysis",
      "Ranking sensitivity to severity weights",
    ],
  },
];

export const REPORT_TYPE_INFO = Object.fromEntries(
  REPORT_TYPES.map((t) => [t.key, t]),
) as Record<ReportType, ReportTypeInfo>;

/** The export formats of a completed report. */
export const EXPORT_FORMATS = [
  { format: "md", label: "Markdown", ext: ".md" },
  { format: "tex", label: "LaTeX", ext: ".tex" },
  { format: "json", label: "JSON", ext: ".json" },
] as const;
