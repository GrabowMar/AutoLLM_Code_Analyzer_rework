/** Labels, descriptions and icons of the platform report types. */
import type { Component } from "svelte";
import Brain from "@lucide/svelte/icons/brain";
import GitCompareArrows from "@lucide/svelte/icons/git-compare-arrows";
import Wrench from "@lucide/svelte/icons/wrench";
import TrendingUp from "@lucide/svelte/icons/trending-up";
import Layers from "@lucide/svelte/icons/layers";
import FlaskConical from "@lucide/svelte/icons/flask-conical";
import Microscope from "@lucide/svelte/icons/microscope";
import type { ReportType } from "$lib/api/reports";

export interface ReportTypeInfo {
  label: string;
  description: string;
  icon: Component;
  /** Report measures code under one analysis plan (supports plan pinning + rules). */
  measured: boolean;
}

export const REPORT_TYPE_INFO: Record<ReportType, ReportTypeInfo> = {
  experiment_report: {
    label: "Experiment report",
    description:
      "Per-condition statistics and pairwise differences for one designed experiment, under one analysis plan.",
    icon: FlaskConical,
    measured: true,
  },
  study_report: {
    label: "Study report",
    description:
      "Paper-ready tables under one analysis plan: outcome funnel, deduplicated issues, correlations, rankings with uncertainty and validity threats. Exports to Markdown and LaTeX.",
    icon: Microscope,
    measured: true,
  },
  model_analysis: {
    label: "Model analysis",
    description:
      "One model's generation results and measured analysis: severity, per-trial statistics, tools, LOC.",
    icon: Brain,
    measured: true,
  },
  template_comparison: {
    label: "Template comparison",
    description:
      "Models side by side on one app template, with rank sensitivity to the severity weights.",
    icon: GitCompareArrows,
    measured: true,
  },
  tool_analysis: {
    label: "Tool analysis",
    description:
      "How each analyzer behaves across runs: outcomes, failure reasons, success rate, findings per scan.",
    icon: Wrench,
    measured: false,
  },
  generation_analytics: {
    label: "Generation analytics",
    description:
      "Generation throughput, success and lines of code over a time window.",
    icon: TrendingUp,
    measured: false,
  },
  comprehensive: {
    label: "Comprehensive",
    description:
      "Platform-wide totals plus generation analytics, tool behaviour and rank sensitivity.",
    icon: Layers,
    measured: false,
  },
};

export function reportTypeInfo(type: string): ReportTypeInfo {
  return (
    REPORT_TYPE_INFO[type as ReportType] ?? {
      label: type.replace(/_/g, " "),
      description: "",
      icon: Layers,
      measured: false,
    }
  );
}
