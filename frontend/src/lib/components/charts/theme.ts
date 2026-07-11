/**
 * Chart color assignments.
 *
 * Categorical series wear the validated `--chart-1..5` slots (see app.css —
 * both palettes pass the dataviz six checks against their surfaces). Status
 * and severity are NOT categorical: they wear fixed, reserved hues so the
 * same state always looks the same everywhere.
 */

/** Categorical slot for the nth series (1-based, max 5 — never cycle hues). */
export function seriesColor(n: number): string {
  const slot = Math.min(Math.max(n, 1), 5);
  return `var(--chart-${slot})`;
}

/**
 * Severity hues — hue-matched to the badge classes in
 * `$lib/constants/colors.ts` (severityColors). Keep in sync with that map.
 */
export const severityChartColors: Record<string, string> = {
  critical: "#ef4444", // red-500
  high: "#f97316", // orange-500
  medium: "#f59e0b", // amber-500
  low: "#3b82f6", // blue-500
  info: "#94a3b8", // slate-400
};

/** Run-status series colors (completed/failed trend lines). */
export const statusSeriesColors = {
  completed: "var(--success)",
  failed: "var(--destructive)",
} as const;
