// Pairwise (A/B) preference values as the participant sees them. Values are
// stored exactly as displayed ("A" = the app shown as A); the server maps
// sides back to apps with the assignment's `swapped` flag, so the workbench
// never reorders the apps it receives.
import type { PairwiseScale, PairwiseValue } from "$lib/api/types/human-eval";

export interface PairwiseOption {
  value: PairwiseValue;
  /** String form used by radio groups ("A", "tie", "B", "-2" … "2"). */
  key: string;
  /** Full sentence for screen readers and the summary. */
  label: string;
  /** Compact button text. */
  short: string;
  /** Which side the option leans to. */
  side: "A" | "B" | "tie";
}

const AB_TIE: PairwiseOption[] = [
  { value: "A", key: "A", label: "A is better", short: "A", side: "A" },
  {
    value: "tie",
    key: "tie",
    label: "About the same",
    short: "Tie",
    side: "tie",
  },
  { value: "B", key: "B", label: "B is better", short: "B", side: "B" },
];

const FIVE_POINT: PairwiseOption[] = [
  { value: -2, key: "-2", label: "A is much better", short: "A++", side: "A" },
  {
    value: -1,
    key: "-1",
    label: "A is slightly better",
    short: "A+",
    side: "A",
  },
  { value: 0, key: "0", label: "About the same", short: "=", side: "tie" },
  { value: 1, key: "1", label: "B is slightly better", short: "B+", side: "B" },
  { value: 2, key: "2", label: "B is much better", short: "B++", side: "B" },
];

/** Options in display order, A on the left. */
export function pairwiseOptions(scale: PairwiseScale): PairwiseOption[] {
  return scale === "five_point" ? FIVE_POINT : AB_TIE;
}

/** True when `value` is an allowed answer on this scale. */
export function isValidPairwiseValue(
  value: unknown,
  scale: PairwiseScale,
): value is PairwiseValue {
  if (scale === "five_point") {
    return (
      typeof value === "number" &&
      Number.isInteger(value) &&
      value >= -2 &&
      value <= 2
    );
  }
  return value === "A" || value === "B" || value === "tie";
}

/** Radio-group key of a stored value ("" when unanswered or invalid). */
export function pairwiseKey(
  value: PairwiseValue | null | undefined,
  scale: PairwiseScale,
): string {
  if (!isValidPairwiseValue(value, scale)) return "";
  return String(value);
}

/** Stored value for a radio-group key, or undefined for "" / unknown keys. */
export function parsePairwiseKey(
  key: string,
  scale: PairwiseScale,
): PairwiseValue | undefined {
  return pairwiseOptions(scale).find((o) => o.key === key)?.value;
}

/** Readable form of a stored value ("A is slightly better"), "—" when unanswered. */
export function formatPairwiseValue(
  value: PairwiseValue | null | undefined,
  scale: PairwiseScale,
): string {
  const key = pairwiseKey(value, scale);
  return pairwiseOptions(scale).find((o) => o.key === key)?.label ?? "—";
}

/** Side a stored value favours, or null when unanswered. */
export function pairwiseSide(
  value: PairwiseValue | null | undefined,
  scale: PairwiseScale,
): "A" | "B" | "tie" | null {
  const key = pairwiseKey(value, scale);
  return pairwiseOptions(scale).find((o) => o.key === key)?.side ?? null;
}

/** Display letter for an app slot: "A"/"B" in pairwise mode, "" for a single app. */
export function slotLetter(slot: string): string {
  return slot === "A" || slot === "B" ? slot : "";
}
