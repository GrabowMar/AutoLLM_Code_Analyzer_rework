/**
 * Small, dependency-free scale helpers for the hand-drawn SVG charts
 * (box plots, forest plots, proportions, scatter). LayerChart wrappers use
 * their own d3 scales; these exist for marks LayerChart has no primitive
 * for (whiskers, CIs, NA rows) where exact control matters for research
 * figures.
 */

/** A linear map from `domain` to `range` (both `[a, b]`). */
export function linearScale(
  domain: [number, number],
  range: [number, number],
): (value: number) => number {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0;
  if (span === 0) return () => (r0 + r1) / 2;
  return (value) => r0 + ((value - d0) / span) * (r1 - r0);
}

function niceStep(span: number, count: number): number {
  const raw = span / Math.max(count, 1);
  const power = Math.pow(10, Math.floor(Math.log10(raw)));
  const fraction = raw / power;
  const nice = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
  return nice * power;
}

/**
 * Round the domain outwards to "nice" values and list the ticks
 * (`count` is a target, not a guarantee). A zero-width domain is padded.
 */
export function niceTicks(
  min: number,
  max: number,
  count = 5,
): { min: number; max: number; ticks: number[] } {
  let lo = Math.min(min, max);
  let hi = Math.max(min, max);
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) {
    return { min: 0, max: 1, ticks: [0, 0.5, 1] };
  }
  if (lo === hi) {
    const pad = lo === 0 ? 1 : Math.abs(lo) * 0.5;
    lo -= pad;
    hi += pad;
    if (min >= 0 && lo < 0) lo = 0;
  }
  const step = niceStep(hi - lo, count);
  const niceMin = Math.floor(lo / step) * step;
  const niceMax = Math.ceil(hi / step) * step;
  const ticks: number[] = [];
  // Integer stepping avoids float drift (0.30000000000000004).
  const n = Math.round((niceMax - niceMin) / step);
  for (let i = 0; i <= n; i++) {
    ticks.push(Number((niceMin + i * step).toPrecision(12)));
  }
  return { min: niceMin, max: niceMax, ticks };
}

/** Compact tick label: 1200 → "1.2k", 0.25 → "0.25", 3 → "3". */
export function formatTick(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${Number((value / 1_000_000).toFixed(1))}M`;
  if (abs >= 10_000) return `${Number((value / 1000).toFixed(0))}k`;
  if (abs >= 1000) return `${Number((value / 1000).toFixed(1))}k`;
  if (abs === 0) return "0";
  if (abs < 0.01) return value.toExponential(0);
  return String(Number(value.toPrecision(3)));
}

/** Deterministic jitter in [-1, 1] for the i-th point (no Math.random: stable across renders). */
export function jitter(i: number): number {
  const x = Math.sin((i + 1) * 12.9898) * 43758.5453;
  return (x - Math.floor(x)) * 2 - 1;
}
