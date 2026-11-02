// Rating scale helpers: the grid of values, local checks mirroring the backend
// rules (integers, 1 ≤ max − min ≤ 100, step divides the range, per-value labels
// on the grid) and a few starting presets.

import type { Scale } from "$lib/api/types/human-eval";

export const SCALE_MAX_RANGE = 100;

export function scaleStep(scale: Scale): number {
  return scale.step && scale.step > 0 ? scale.step : 1;
}

/** min, min + step, … max — or [] when the range/step are not valid (labels are not checked). */
export function scaleValues(scale: Scale): number[] {
  if (scaleIssues({ ...scale, labels: undefined }).length > 0) return [];
  const step = scaleStep(scale);
  const out: number[] = [];
  for (let v = scale.min; v <= scale.max; v += step) out.push(v);
  return out;
}

/** Problems the backend would also reject, phrased for the editor. */
export function scaleIssues(scale: Scale): string[] {
  const issues: string[] = [];
  const { min, max } = scale;
  const step = scale.step ?? 1;
  if (!Number.isInteger(min) || !Number.isInteger(max)) {
    issues.push("Minimum and maximum must be whole numbers.");
    return issues;
  }
  if (max <= min) {
    issues.push("Maximum must be greater than minimum.");
    return issues;
  }
  if (max - min > SCALE_MAX_RANGE) {
    issues.push(`The range may span at most ${SCALE_MAX_RANGE} points.`);
  }
  if (!Number.isInteger(step) || step < 1) {
    issues.push("Step must be a whole number of at least 1.");
  } else if ((max - min) % step !== 0) {
    issues.push(`Step ${step} must divide the range ${max - min}.`);
  }
  if (issues.length === 0 && scale.labels) {
    const bad = Object.keys(scale.labels).filter((key) => {
      const n = Number(key);
      return (
        !Number.isInteger(n) || n < min || n > max || (n - min) % step !== 0
      );
    });
    if (bad.length) {
      issues.push(`Value labels outside the scale: ${bad.join(", ")}.`);
    }
  }
  return issues;
}

/** Number of points on the scale (0 when invalid). */
export function scalePoints(scale: Scale): number {
  return scaleValues(scale).length;
}

/** Drop blank labels and labels for values no longer on the grid. */
export function pruneScaleLabels(
  scale: Scale,
): Record<string, string> | undefined {
  if (!scale.labels) return undefined;
  const valid = new Set(scaleValues(scale).map(String));
  const out: Record<string, string> = {};
  for (const [key, text] of Object.entries(scale.labels)) {
    if (valid.has(key) && text.trim() !== "") out[key] = text;
  }
  return Object.keys(out).length ? out : undefined;
}

/**
 * Per-value labels for `scale`'s current grid, picked from `remembered` (every label typed
 * while the editor is open). Bounds are typed one keystroke at a time and pass through
 * invalid or smaller grids (Max 7 → 10 is 7 → 1 → 10; 0–10 → 0–100 passes 0–1), so labels
 * are never thrown away on a bound change: while the range or step is invalid all of them
 * are kept, and on a valid grid the ones on it are used — those off it come back when the
 * grid does.
 */
export function labelsForGrid(
  scale: Scale,
  remembered: Record<string, string> | undefined,
): Record<string, string> | undefined {
  if (!remembered) return undefined;
  if (scaleIssues({ ...scale, labels: undefined }).length > 0) {
    const kept = Object.fromEntries(
      Object.entries(remembered).filter(([, text]) => text.trim() !== ""),
    );
    return Object.keys(kept).length ? kept : undefined;
  }
  return pruneScaleLabels({ ...scale, labels: remembered });
}

/** `remembered` with the scale's current labels on top (the newest text wins). */
export function rememberLabels(
  remembered: Record<string, string> | undefined,
  current: Record<string, string> | undefined,
): Record<string, string> {
  return { ...(remembered ?? {}), ...(current ?? {}) };
}

/** "1–7" or "0–100 (step 5)". */
export function scaleSummary(scale: Scale): string {
  const step = scaleStep(scale);
  const range = `${scale.min}–${scale.max}`;
  return step === 1 ? range : `${range} (step ${step})`;
}

export interface ScalePreset {
  key: string;
  label: string;
  scale: Scale;
}

export const SCALE_PRESETS: ScalePreset[] = [
  {
    key: "agree7",
    label: "1–7 disagree → agree",
    scale: {
      min: 1,
      max: 7,
      step: 1,
      min_label: "Strongly disagree",
      max_label: "Strongly agree",
    },
  },
  {
    key: "agree5",
    label: "1–5 disagree → agree",
    scale: {
      min: 1,
      max: 5,
      step: 1,
      min_label: "Strongly disagree",
      max_label: "Strongly agree",
    },
  },
  {
    key: "quality7",
    label: "1–7 very poor → excellent",
    scale: {
      min: 1,
      max: 7,
      step: 1,
      min_label: "Very poor",
      max_label: "Excellent",
    },
  },
  {
    key: "ten",
    label: "0–10",
    scale: { min: 0, max: 10, step: 1, min_label: "Worst", max_label: "Best" },
  },
  {
    key: "hundred",
    label: "0–100 step 5",
    scale: {
      min: 0,
      max: 100,
      step: 5,
      min_label: "Very low",
      max_label: "Very high",
    },
  },
];

export function cloneScale(scale: Scale): Scale {
  return {
    ...scale,
    ...(scale.labels ? { labels: { ...scale.labels } } : {}),
  };
}
