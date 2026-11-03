/**
 * Client mirror of the backend factor expansion (`backend/generation/study/expand.py:expand_grid`) and the
 * plan's cell counts, so the builder can show "conditions × units × repeats = cells" before the server
 * resolves. The server stays authoritative (the PlanRail replaces these numbers with `PlanOut`); the two
 * implementations are pinned to the same fixtures (`../__fixtures__/expansion.json`, checked by
 * `backend/generation/tests/wp15_frontend/test_expansion_fixture.py` and `expansion.test.ts`).
 *
 * Rules (spec 9.4):
 * - conditions are combinations of one level per factor, in design order (itertools.product order);
 * - `crossed` factors are fully crossed, `fixed` contribute their single level;
 * - a `fractional` grid has exactly one `fraction` factor whose level is generated from two-level crossed
 *   factors by `"D=ABC"` (`"D=-ABC"` = the complementary half); letters name the crossed factors in design
 *   order; level index 0 is the −1 coded level; a word shorter than 2 letters (resolution < III) → `aliasing`;
 * - `grid.exclude` removes combinations (→ `unbalanced_grid`); compatibility pruning is server-side only.
 */
import type { Design, FactorSpec } from "$lib/api/v3/types";

export class ExpansionError extends Error {
  readonly path: string;
  constructor(message: string, path: string) {
    super(message);
    this.path = path;
    this.name = "ExpansionError";
  }
}

export interface Expansion {
  conditions: Record<string, string>[];
  fullSize: number;
  excluded: number;
  fractional: boolean;
  /** Issue codes the server would report for the grid itself (`aliasing`, `unbalanced_grid`). */
  issues: string[];
  unbalanced: boolean;
}

const GENERATOR_RE = /^\s*([A-Z])\s*=\s*(-?)\s*([A-Z]+)\s*$/;
const MIN_RESOLUTION = 3;

export function parseGenerator(generator: string): {
  left: string;
  negated: boolean;
  word: string;
} {
  const m = GENERATOR_RE.exec(generator);
  if (!m) {
    throw new ExpansionError(
      `generator '${generator}' is not of the form 'D=ABC' (or 'D=-ABC')`,
      "grid.fraction.generator",
    );
  }
  const [, left, minus, word] = m;
  if (word.includes(left) || new Set(word).size !== word.length) {
    throw new ExpansionError(
      `generator '${generator}': the word repeats a letter or contains its left side`,
      "grid.fraction.generator",
    );
  }
  return { left, negated: minus === "-", word };
}

function product(lists: string[][]): string[][] {
  let out: string[][] = [[]];
  for (const list of lists) {
    const next: string[][] = [];
    for (const prefix of out)
      for (const value of list) next.push([...prefix, value]);
    out = next;
  }
  return out;
}

function fractionRule(design: Pick<Design, "factors" | "grid">): {
  key: string;
  levelOf: (combo: Record<string, string>) => string;
  issues: string[];
} {
  const generator = design.grid.fraction?.generator;
  if (!generator)
    throw new ExpansionError(
      "a fractional grid needs grid.fraction.generator (e.g. 'D=ABC')",
      "grid.fraction",
    );
  const { left, negated, word } = parseGenerator(generator);
  const crossed = design.factors.filter((f) => f.role === "crossed");
  const letters = new Map<string, FactorSpec>(
    crossed.map((f, i) => [String.fromCharCode(65 + i), f]),
  );
  const fractions = design.factors.filter((f) => f.role === "fraction");
  if (fractions.length !== 1) {
    throw new ExpansionError(
      "a fractional grid has exactly one factor with role 'fraction'",
      "factors",
    );
  }
  const fraction = fractions[0];
  if (letters.has(left)) {
    throw new ExpansionError(
      `generator ${left}=... names a crossed factor on its left side`,
      "grid.fraction.generator",
    );
  }
  if (fraction.levels.length !== 2) {
    throw new ExpansionError(
      `the fraction factor '${fraction.key}' must have exactly two levels`,
      "grid.fraction",
    );
  }
  for (const letter of word) {
    const factor = letters.get(letter);
    if (!factor)
      throw new ExpansionError(
        `generator letter ${letter} names no crossed factor`,
        "grid.fraction.generator",
      );
    if (factor.levels.length !== 2) {
      throw new ExpansionError(
        `generator letter ${letter} (${factor.key}) must name a two-level crossed factor`,
        "grid.fraction.generator",
      );
    }
  }
  const issues = word.length + 1 < MIN_RESOLUTION ? ["aliasing"] : [];
  const levelOf = (combo: Record<string, string>): string => {
    let sign = negated ? -1 : 1;
    for (const letter of word) {
      const factor = letters.get(letter)!;
      sign *= combo[factor.key] === factor.levels[1] ? 1 : -1;
    }
    return sign > 0 ? fraction.levels[1] : fraction.levels[0];
  };
  return { key: fraction.key, levelOf, issues };
}

function isExcluded(
  combo: Record<string, string>,
  exclusions: Record<string, string>[],
): boolean {
  return exclusions.some((ex) =>
    Object.entries(ex).every(([key, level]) => combo[key] === level),
  );
}

/** The conditions of a design, as the server's `expand_grid` computes them (no compatibility pruning). */
export function expandGrid(
  design: Pick<Design, "factors" | "grid">,
): Expansion {
  const factors = design.factors;
  for (const [i, f] of factors.entries()) {
    if (f.levels.length === 0)
      throw new ExpansionError(
        `factor '${f.key}' has no levels`,
        `factors[${i}]`,
      );
    if (f.role === "fixed" && f.levels.length !== 1) {
      throw new ExpansionError(
        `fixed factor '${f.key}' must have exactly one level, not ${f.levels.length}`,
        `factors[${i}]`,
      );
    }
  }
  const issues: string[] = [];
  let fractionKey: string | null = null;
  let levelOf: ((combo: Record<string, string>) => string) | null = null;
  if (design.grid.type === "fractional") {
    const rule = fractionRule(design);
    fractionKey = rule.key;
    levelOf = rule.levelOf;
    issues.push(...rule.issues);
  } else if (factors.some((f) => f.role === "fraction")) {
    throw new ExpansionError(
      "factors with role 'fraction' need grid.type 'fractional'",
      "factors",
    );
  }
  const gridFactors = factors.filter((f) => f.key !== fractionKey);
  const combos = product(gridFactors.map((f) => f.levels)).map((values) => {
    const combo: Record<string, string> = {};
    gridFactors.forEach((f, i) => (combo[f.key] = values[i]));
    if (fractionKey && levelOf) combo[fractionKey] = levelOf(combo);
    const ordered: Record<string, string> = {};
    for (const f of factors) ordered[f.key] = combo[f.key];
    return ordered;
  });
  const exclusions = design.grid.exclude ?? [];
  const conditions = combos.filter((c) => !isExcluded(c, exclusions));
  const excluded = combos.length - conditions.length;
  if (excluded > 0) issues.push("unbalanced_grid");
  if (conditions.length === 0) {
    throw new ExpansionError(
      "the grid has no condition left after exclusions and pruning",
      "grid",
    );
  }
  return {
    conditions,
    fullSize: combos.length,
    excluded,
    fractional: fractionKey !== null,
    issues,
    unbalanced: fractionKey !== null || excluded > 0,
  };
}

export interface PilotShape {
  enabled: boolean;
  repeats: number;
  units: "all" | number;
}

export interface CellCounts {
  conditions: number;
  units: number;
  repeats: number;
  /** Main-stage cells: conditions × units × repeats. */
  main: number;
  /** Pilot cells: conditions × pilot units × min(pilot repeats, repeats) (0 without a pilot). */
  pilot: number;
  total: number;
  /** Blocks: one per (stage, repeat, unit). */
  blocks: number;
}

/** Cell and block counts of a plan (`study/plan.py:_stages` + `_build_cells`, no pruning). */
export function countCells(
  nConditions: number,
  nUnits: number,
  repeats: number,
  pilot?: PilotShape | null,
): CellCounts {
  const main = nConditions * nUnits * repeats;
  let pilotCells = 0;
  let pilotBlocks = 0;
  if (pilot?.enabled) {
    const units =
      pilot.units === "all"
        ? nUnits
        : Math.min(nUnits, Math.max(0, Math.floor(pilot.units)));
    const reps = Math.min(Math.max(0, pilot.repeats), repeats);
    pilotCells = nConditions * units * reps;
    pilotBlocks = units * reps;
  }
  return {
    conditions: nConditions,
    units: nUnits,
    repeats,
    main,
    pilot: pilotCells,
    total: main + pilotCells,
    blocks: nUnits * repeats + pilotBlocks,
  };
}

/** Number of units a design lists (tasks in app mode; targets, or slot sets when no target is listed). */
export function unitCount(design: Pick<Design, "mode" | "units">): number {
  if (design.mode === "app") return design.units.tasks.length;
  return design.units.slot_targets.length;
}
