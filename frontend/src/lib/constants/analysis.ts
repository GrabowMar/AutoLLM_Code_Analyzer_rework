import { analysisStatusColors, severityColors } from '$lib/constants/colors';

/** Status → Tailwind class mappings for analysis tasks and results */
export const statusColors = analysisStatusColors;

export { severityColors };

/** Analyzer type labels */
export const analyzerTypeLabels: Record<string, string> = {
  static: "Static Analysis",
  dynamic: "Dynamic Analysis",
  performance: "Performance",
  ai: "AI Review",
};

/** Severity sort order (lower = more severe) */
export const severityOrder: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  info: 4,
};
