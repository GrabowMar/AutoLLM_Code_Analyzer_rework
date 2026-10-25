import { describe, expect, it } from "vitest";
import { toggleSelection } from "$lib/components/generator/ProfilePicker.svelte";

describe("ProfilePicker multiple mode selection", () => {
  it("appends in pick order and removes on a second toggle", () => {
    let ids: number[] = [];
    ids = toggleSelection(ids, 3);
    ids = toggleSelection(ids, 1);
    ids = toggleSelection(ids, 2);
    expect(ids).toEqual([3, 1, 2]);
    ids = toggleSelection(ids, 1);
    expect(ids).toEqual([3, 2]);
  });
  it("never mutates the input", () => {
    const ids = [1, 2];
    const next = toggleSelection(ids, 3);
    expect(ids).toEqual([1, 2]);
    expect(next).not.toBe(ids);
  });
});
