/**
 * Shared shapes of the results figures: one plotted row of a dot-and-CI
 * figure, and the context every block section gets from ResultsTab.
 */
import type { Ci } from "$lib/api/types/human-eval";

export interface PlotRow {
  key: string;
  label: string;
  /** Muted second line under the label (e.g. "baseline", a component). */
  sub?: string;
  value: number | null;
  ci?: Ci | null;
  /** Sensitivity estimate drawn as a hollow mark (e.g. unevaluable apps at the floor). */
  secondary?: number | null;
  /** Right-hand count column ("6 apps · 18 ratings"). */
  n?: string;
  /** Rank printed in the first column (rankings only). */
  rank?: number | null;
}

export interface ResultsContext {
  /** "Condition" | "Model" | "App" */
  groupNoun: string;
  groupLabel: (key: string) => string;
  /** p-values are withheld while provisional. */
  pWithheld: boolean;
  /** The pre-registered primary outcome. */
  primary: { block_id: string; metric: string } | null;
}
