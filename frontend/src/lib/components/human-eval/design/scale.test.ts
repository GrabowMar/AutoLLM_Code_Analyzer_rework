import { describe, expect, it } from "vitest";
import type { Scale } from "$lib/api/types/human-eval";
import { labelsForGrid, rememberLabels } from "./scale";

/** What ScaleEditor.setBound does on every applied keystroke of a bound field. */
function typeBound(
  scale: Scale,
  remembered: Record<string, string>,
  field: "min" | "max" | "step",
  value: number,
) {
  const memory = rememberLabels(remembered, scale.labels);
  const next: Scale = { ...scale, [field]: value };
  const labels = labelsForGrid(next, memory);
  if (labels) next.labels = labels;
  else delete next.labels;
  return { scale: next, remembered: memory };
}

const seven = Object.fromEntries(
  [1, 2, 3, 4, 5, 6, 7].map((v) => [String(v), `L${v}`]),
);

describe("scale labels while bounds are typed", () => {
  it("keeps every label through an invalid intermediate bound (max 7 → 1 → 10)", () => {
    let state = {
      scale: { min: 1, max: 7, step: 1, labels: { ...seven } } as Scale,
      remembered: {} as Record<string, string>,
    };
    state = typeBound(state.scale, state.remembered, "max", 1); // max ≤ min: invalid mid-edit
    expect(state.scale.labels).toEqual(seven);
    state = typeBound(state.scale, state.remembered, "max", 10);
    expect(state.scale.max).toBe(10);
    expect(state.scale.labels).toEqual(seven);
  });

  it("brings labels back after a smaller valid grid on the way (0–10 → 0–1 → 0–10)", () => {
    const eleven = Object.fromEntries(
      Array.from({ length: 11 }, (_, v) => [String(v), `V${v}`]),
    );
    let state = {
      scale: { min: 0, max: 10, step: 1, labels: { ...eleven } } as Scale,
      remembered: {} as Record<string, string>,
    };
    state = typeBound(state.scale, state.remembered, "max", 1);
    expect(state.scale.labels).toEqual({ "0": "V0", "1": "V1" });
    state = typeBound(state.scale, state.remembered, "max", 10);
    expect(state.scale.labels).toEqual(eleven);
  });

  it("drops labels off the final grid and empties when none fit", () => {
    let state = {
      scale: { min: 1, max: 7, step: 1, labels: { ...seven } } as Scale,
      remembered: {} as Record<string, string>,
    };
    state = typeBound(state.scale, state.remembered, "max", 5);
    expect(Object.keys(state.scale.labels ?? {})).toEqual([
      "1",
      "2",
      "3",
      "4",
      "5",
    ]);
    state = typeBound(state.scale, state.remembered, "min", 20);
    // Invalid (max < min): all remembered labels stay on the scale until the grid is valid again.
    expect(state.scale.labels).toEqual(seven);
    state = typeBound(state.scale, state.remembered, "max", 30);
    expect(state.scale.labels).toBeUndefined();
  });

  it("ignores blank labels and keeps the newest text", () => {
    expect(labelsForGrid({ min: 1, max: 1 }, { "1": " ", "2": "x" })).toEqual({
      "2": "x",
    });
    expect(labelsForGrid({ min: 1, max: 5 }, undefined)).toBeUndefined();
    expect(rememberLabels({ "1": "old", "2": "two" }, { "1": "new" })).toEqual({
      "1": "new",
      "2": "two",
    });
    expect(rememberLabels(undefined, undefined)).toEqual({});
  });
});
