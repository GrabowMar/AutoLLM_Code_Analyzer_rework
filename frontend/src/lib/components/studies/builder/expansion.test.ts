import { describe, expect, it } from "vitest";
import type { FactorSpec, GridSpec } from "$lib/api/v3/types";
import fixture from "../__fixtures__/expansion-conditions.json";
import wp10 from "../__fixtures__/expansion.json";
import {
  countCells,
  expandGrid,
  ExpansionError,
  parseGenerator,
  unitCount,
} from "./expansion";

interface Case {
  name: string;
  factors: FactorSpec[];
  grid: GridSpec;
  expected?: {
    n_conditions: number;
    full_size: number;
    excluded: number;
    fractional: boolean;
    issues: string[];
    conditions: Record<string, string>[];
  };
  error?: string;
}

const cases = fixture.cases as unknown as Case[];

describe("expandGrid mirrors the backend expansion (shared fixtures)", () => {
  it("has the fixture cases", () => {
    expect(cases.length).toBeGreaterThanOrEqual(8);
  });

  for (const c of cases) {
    it(c.name, () => {
      if (c.error) {
        expect(() => expandGrid({ factors: c.factors, grid: c.grid })).toThrow(
          ExpansionError,
        );
        expect(() => expandGrid({ factors: c.factors, grid: c.grid })).toThrow(
          c.error,
        );
        return;
      }
      const out = expandGrid({ factors: c.factors, grid: c.grid });
      const want = c.expected!;
      expect(out.conditions).toEqual(want.conditions);
      expect(out.conditions.length).toBe(want.n_conditions);
      expect(out.fullSize).toBe(want.full_size);
      expect(out.excluded).toBe(want.excluded);
      expect(out.fractional).toBe(want.fractional);
      expect([...out.issues].sort()).toEqual([...want.issues].sort());
      expect(out.unbalanced).toBe(want.fractional || want.excluded > 0);
    });
  }
});

interface Wp10Case {
  name: string;
  factors: FactorSpec[];
  grid: Partial<GridSpec>;
  units: number;
  repeats: number;
  expected: {
    conditions: number;
    cells: number;
    blocks: number;
    unbalanced: boolean;
  };
}

describe("WP-10 expansion fixtures (same file as the backend's tests/wp10_study/fixtures)", () => {
  for (const c of wp10.cases as unknown as Wp10Case[]) {
    it(c.name, () => {
      const grid: GridSpec = {
        type: c.grid.type ?? "full",
        fraction: c.grid.fraction ?? null,
        exclude: c.grid.exclude ?? [],
      };
      const out = expandGrid({ factors: c.factors, grid });
      expect(out.conditions.length).toBe(c.expected.conditions);
      expect(out.unbalanced).toBe(c.expected.unbalanced);
      const counts = countCells(out.conditions.length, c.units, c.repeats);
      expect(counts.main).toBe(c.expected.cells);
      expect(counts.blocks).toBe(c.expected.blocks);
    });
  }
});

describe("countCells (shared fixtures)", () => {
  for (const c of fixture.counts) {
    it(c.name, () => {
      const pilot = c.pilot as {
        enabled: boolean;
        repeats: number;
        units: "all" | number;
      };
      expect(
        countCells(c.n_conditions, c.n_units, c.repeats, pilot),
      ).toMatchObject(c.expected);
    });
  }

  it("counts without a pilot", () => {
    expect(countCells(3, 2, 4)).toEqual({
      conditions: 3,
      units: 2,
      repeats: 4,
      main: 24,
      pilot: 0,
      total: 24,
      blocks: 8,
    });
  });
});

describe("parseGenerator", () => {
  it("parses positive and negated generators", () => {
    expect(parseGenerator("D=ABC")).toEqual({
      left: "D",
      negated: false,
      word: "ABC",
    });
    expect(parseGenerator(" D = -AB ")).toEqual({
      left: "D",
      negated: true,
      word: "AB",
    });
  });

  it("rejects malformed and self-referencing generators", () => {
    expect(() => parseGenerator("DABC")).toThrow(ExpansionError);
    expect(() => parseGenerator("A=AB")).toThrow(/repeats a letter/);
    expect(() => parseGenerator("D=AAB")).toThrow(/repeats a letter/);
  });
});

describe("unitCount", () => {
  it("counts tasks in app mode and targets in slot mode", () => {
    const units = {
      tasks: ["a@1", "b@1"],
      slot_sets: ["s"],
      slot_targets: ["t1", "t2", "t3"],
    };
    expect(unitCount({ mode: "app", units })).toBe(2);
    expect(unitCount({ mode: "slot", units })).toBe(3);
  });
});
