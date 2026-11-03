/**
 * PowerHelper math (spec 10.2 step 4): planning numbers shown next to the repeats field. Pure functions,
 * unit-tested against published values; nothing here is a result, only a planning aid.
 *
 * - App mode: the minimum detectable paired difference in mean pass rate between two conditions, with
 *   units (tasks) as pairs (CRN: same seed per (unit, repeat)).
 * - Slot mode: the Wilson 95% half-width of a prevalence at p = .5 and at a chosen p; the pooled precision
 *   across targets with a between-target variance; and the expected saving of a `ci_width_reached` stop.
 */

/** Inverse standard normal CDF (Acklam's rational approximation, relative error < 1.2e-9). */
export function normalQuantile(p: number): number {
  if (!(p > 0 && p < 1)) {
    if (p === 0) return -Infinity;
    if (p === 1) return Infinity;
    return Number.NaN;
  }
  const a = [
    -3.969683028665376e1, 2.209460984245205e2, -2.759285104469687e2,
    1.38357751867269e2, -3.066479806614716e1, 2.506628277459239,
  ];
  const b = [
    -5.447609879822406e1, 1.615858368580409e2, -1.556989798598866e2,
    6.680131188771972e1, -1.328068155288572e1,
  ];
  const c = [
    -7.784894002430293e-3, -3.223964580411365e-1, -2.400758277161838,
    -2.549732539343734, 4.374664141464968, 2.938163982698783,
  ];
  const d = [
    7.784695709041462e-3, 3.224671290700398e-1, 2.445134137142996,
    3.754408661907416,
  ];
  const low = 0.02425;
  let x: number;
  if (p < low) {
    const q = Math.sqrt(-2 * Math.log(p));
    x =
      (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
      ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  } else if (p <= 1 - low) {
    const q = p - 0.5;
    const r = q * q;
    x =
      ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) *
        q) /
      (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1);
  } else {
    const q = Math.sqrt(-2 * Math.log(1 - p));
    x =
      -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
      ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  }
  return x;
}

export const Z95 = normalQuantile(0.975);

/** Wilson score interval for k successes out of n (95% by default). */
export function wilsonInterval(
  k: number,
  n: number,
  z = Z95,
): { low: number; high: number; center: number } | null {
  if (!(n > 0) || k < 0 || k > n) return null;
  const p = k / n;
  const z2 = z * z;
  const denom = 1 + z2 / n;
  const center = (p + z2 / (2 * n)) / denom;
  const half = (z * Math.sqrt((p * (1 - p)) / n + z2 / (4 * n * n))) / denom;
  return {
    low: Math.max(0, center - half),
    high: Math.min(1, center + half),
    center,
  };
}

/** Wilson half-width at an assumed proportion `p` and sample size `n` (the planning version). */
export function wilsonHalfWidth(p: number, n: number, z = Z95): number {
  if (!(n > 0)) return Number.NaN;
  const z2 = z * z;
  return (z * Math.sqrt((p * (1 - p)) / n + z2 / (4 * n * n))) / (1 + z2 / n);
}

/** Smallest n whose Wilson half-width at `p` is ≤ `target` (capped at `nMax`; null if never reached). */
export function nForHalfWidth(
  p: number,
  target: number,
  nMax = 100_000,
  z = Z95,
): number | null {
  if (!(target > 0)) return null;
  // The half-width decreases monotonically in n for fixed p: binary search.
  if (wilsonHalfWidth(p, nMax, z) > target) return null;
  let lo = 1;
  let hi = nMax;
  while (lo < hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (wilsonHalfWidth(p, mid, z) <= target) hi = mid;
    else lo = mid + 1;
  }
  return lo;
}

export interface PairedPowerInput {
  /** Paired units (tasks). */
  units: number;
  /** Repeats per (condition, unit). */
  repeats: number;
  /** Assumed pass rate (within-unit binomial variance p(1−p)/r per arm). */
  p?: number;
  /** Between-unit standard deviation of the pass rate. */
  sdUnit?: number;
  /** Correlation of the two conditions' unit means (CRN pairing makes it positive). */
  rho?: number;
  alpha?: number;
  power?: number;
}

/**
 * Minimum detectable paired difference in mean pass rate (two-sided `alpha`, `power`):
 * MDE = (z₁₋α/₂ + z_power) · √(Var(dᵢ) / units) with Var(dᵢ) = 2σᵤ²(1 − ρ) + 2p(1 − p)/r.
 * Returns null with fewer than 2 units (no paired inference).
 */
export function pairedDetectableDifference(
  input: PairedPowerInput,
): number | null {
  const {
    units,
    repeats,
    p = 0.5,
    sdUnit = 0.2,
    rho = 0.5,
    alpha = 0.05,
    power = 0.8,
  } = input;
  if (units < 2 || repeats < 1) return null;
  const variance =
    2 * sdUnit * sdUnit * (1 - rho) + (2 * p * (1 - p)) / repeats;
  const z = normalQuantile(1 - alpha / 2) + normalQuantile(power);
  return z * Math.sqrt(variance / units);
}

/**
 * Half-width of the pooled prevalence across `targets` targets with `n` variants each, when targets differ
 * (between-target variance `tau²`): z · √((τ² + p(1 − p)/n) / T).
 */
export function pooledHalfWidth(
  p: number,
  n: number,
  targets: number,
  tau: number,
  z = Z95,
): number | null {
  if (!(targets > 0) || !(n > 0)) return null;
  return z * Math.sqrt((tau * tau + (p * (1 - p)) / n) / targets);
}

/**
 * Expected effect of a `ci_width_reached` stop: the n at which the Wilson half-width at `p` drops below
 * `threshold` (never below `nMin`), and the share of the planned `nMax` variants it saves.
 */
export function precisionStopSaving(
  p: number,
  threshold: number,
  nMin: number,
  nMax: number,
): { stopAt: number; saving: number } | null {
  if (!(nMax > 0)) return null;
  const needed = nForHalfWidth(p, threshold, nMax);
  if (needed === null) return { stopAt: nMax, saving: 0 };
  const stopAt = Math.min(nMax, Math.max(nMin, needed));
  return { stopAt, saving: (nMax - stopAt) / nMax };
}
