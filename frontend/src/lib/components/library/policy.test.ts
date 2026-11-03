import { describe, expect, it } from "vitest";
import type {
  KnobsOut,
  LibraryItemOut,
  ResourcePolicyContent,
} from "$lib/api/v3/types";
import knobsFixture from "$lib/api/v3/__fixtures__/knobs.json";
import policies from "$lib/api/v3/__fixtures__/library-resource-policies.json";
import {
  KEY_LIMITS_GROUP,
  POLICY_GROUPS,
  baseTier,
  classBadge,
  closestTier,
  fingerprintChanges,
  formatKnobValue,
  getKnob,
  groupKnobs,
  groupOf,
  inputKind,
  knobLabel,
  knobState,
  overrides,
  policyHints,
  resetKnob,
  setKnob,
  summaryLine,
} from "./policy";

const KNOBS = knobsFixture as unknown as KnobsOut;
const META = KNOBS.knobs;
const meta = (key: string) => META.find((m) => m.key === key)!;
const frugal = () => structuredClone(KNOBS.tiers.frugal);

describe("ResourcePolicyEditor grouping", () => {
  it("puts every knob in exactly one group and the key limits first", () => {
    const groups = groupKnobs(META);
    const all = [...groups.values()].flat().map((m) => m.key);
    expect(all.sort()).toEqual(META.map((m) => m.key).sort());
    expect(
      groups
        .get(KEY_LIMITS_GROUP.id)!
        .map((m) => m.key)
        .sort(),
    ).toEqual([...KNOBS.key_limits].sort());
    for (const group of POLICY_GROUPS)
      expect(groups.get(group.id)!.length, group.id).toBeGreaterThan(0);
  });

  it("files knobs under the spec's accordion groups", () => {
    expect(groupOf(meta("call.max_input_tokens"))).toBe("tokens");
    expect(groupOf(meta("routing.max_price"))).toBe("routing");
    expect(groupOf(meta("caching.ttl"))).toBe("routing");
    expect(groupOf(meta("run.max_workspace_bytes"))).toBe("sandbox");
    expect(groupOf(meta("network_policy"))).toBe("sandbox");
    expect(groupOf(meta("pilot.gates.min_build_rate"))).toBe("stop");
    expect(groupOf(meta("study.stop_rules"))).toBe("stop");
    expect(groupOf(meta("study.max_runs"))).toBe("study");
    expect(groupOf(meta("study.max_cost_usd"))).toBe("key");
  });

  it("has an input for every knob (only the stop rules use a structured editor)", () => {
    const kinds = new Map(META.map((m) => [m.key, inputKind(m)]));
    expect(
      [...kinds.entries()].filter(([, k]) => k === "json").map(([key]) => key),
    ).toEqual(["study.stop_rules"]);
    expect(kinds.get("reasoning")).toBe("reasoning");
    expect(kinds.get("sandbox.memory")).toBe("text");
    expect(kinds.get("run.max_workspace_bytes")).toBe("bytes");
    expect(kinds.get("retries.retry_on")).toBe("list");
    expect(kinds.get("study.alert_at")).toBe("fractions");
  });
});

describe("ResourcePolicyEditor inherited/override logic", () => {
  it("marks every knob inherited when the value equals its base tier", () => {
    const value = frugal();
    expect(overrides(value, KNOBS.tiers.frugal, META)).toEqual([]);
    expect(
      META.every(
        (m) => knobState(value, KNOBS.tiers.frugal, m.key) === "inherited",
      ),
    ).toBe(true);
  });

  it("marks an edited knob as an override and resets it to the base", () => {
    const edited = setKnob(frugal(), "run.max_cost_usd", 0.5);
    expect(knobState(edited, KNOBS.tiers.frugal, "run.max_cost_usd")).toBe(
      "override",
    );
    expect(overrides(edited, KNOBS.tiers.frugal, META)).toEqual([
      "run.max_cost_usd",
    ]);
    const reset = resetKnob(edited, KNOBS.tiers.frugal, "run.max_cost_usd");
    expect(overrides(reset, KNOBS.tiers.frugal, META)).toEqual([]);
    expect(edited.run.max_cost_usd).toBe(0.5);
    expect(KNOBS.tiers.frugal.run.max_cost_usd).toBe(0.3);
  });

  it("treats reasoning as one knob and leaves reasoning.exclude alone", () => {
    const value = setKnob(frugal(), "reasoning", {
      mode: "max_tokens",
      effort: null,
      max_tokens: 2048,
    });
    expect(value.reasoning).toEqual({
      mode: "max_tokens",
      effort: null,
      max_tokens: 2048,
      exclude: false,
    });
    expect(getKnob(value, "reasoning")).toEqual({
      mode: "max_tokens",
      effort: null,
      max_tokens: 2048,
    });
    expect(overrides(value, KNOBS.tiers.frugal, META)).toEqual(["reasoning"]);
  });

  it("compares against no base as all inherited", () => {
    expect(overrides(frugal(), null, META)).toEqual([]);
    expect(knobState(frugal(), undefined, "run.max_cost_usd")).toBe(
      "inherited",
    );
  });

  it("picks the base tier: the policy's own, else the closest preset", () => {
    expect(baseTier("standard", frugal(), KNOBS.tiers, META)).toBe("standard");
    const custom = setKnob(
      structuredClone(KNOBS.tiers.generous),
      "run.max_turns",
      150,
    );
    expect(baseTier("custom", custom, KNOBS.tiers, META)).toBe("generous");
    expect(closestTier(KNOBS.tiers.standard, KNOBS.tiers, META)).toBe(
      "standard",
    );
  });
});

describe("ResourcePolicyEditor class badges and fingerprint", () => {
  it("labels C/R knobs as affecting the treatment, S as study design, O as operational", () => {
    expect(classBadge(meta("context.compaction")).label).toBe(
      "affects treatment",
    );
    expect(classBadge(meta("run.max_turns")).label).toBe("affects treatment");
    expect(classBadge(meta("pilot.enabled")).label).toBe("study design");
    expect(classBadge(meta("concurrency.study_max_parallel")).label).toBe(
      "operational",
    );
    expect(classBadge(meta("context.compaction")).help).toMatch(/Class C/);
  });

  it("makes run.max_cost_usd operational under the tokens budget basis", () => {
    expect(classBadge(meta("run.max_cost_usd"), "usd").cls).toBe("R");
    const tokens = classBadge(meta("run.max_cost_usd"), "tokens");
    expect(tokens.cls).toBe("O");
    expect(tokens.label).toBe("operational");
    expect(classBadge(meta("run.max_cost_usd")).help).toMatch(
      /budget basis "tokens"/,
    );
  });

  it("warns about a new fingerprint only for C/R changes", () => {
    const base = KNOBS.tiers.frugal;
    expect(
      fingerprintChanges(
        setKnob(frugal(), "concurrency.study_max_parallel", 6),
        base,
        META,
      ),
    ).toEqual([]);
    expect(
      fingerprintChanges(setKnob(frugal(), "pilot.repeats", 2), base, META),
    ).toEqual([]);
    expect(
      fingerprintChanges(
        setKnob(frugal(), "routing.provider_sort", "none"),
        base,
        META,
      ),
    ).toEqual(["routing.provider_sort"]);
    const cost = setKnob(frugal(), "run.max_cost_usd", 1);
    expect(fingerprintChanges(cost, base, META, "usd")).toEqual([
      "run.max_cost_usd",
    ]);
    expect(fingerprintChanges(cost, base, META, "tokens")).toEqual([]);
    expect(fingerprintChanges(cost, null, META)).toEqual([]);
  });
});

describe("Budget preset summaries", () => {
  it("matches the backend summary line for the tier presets", () => {
    const [row] =
      policies.items as unknown as LibraryItemOut<"resource_policy">[];
    expect(
      summaryLine(row.content, {
        name: "Frugal",
        version: 1,
        tier: "frugal",
        base: KNOBS.tiers.frugal,
        knobs: META,
      }),
    ).toBe(
      "Frugal v1 · $0.30/run · auto study cap · 2 parallel · effort low · no overrides",
    );
    const generous = KNOBS.tiers.generous;
    expect(
      summaryLine(generous, {
        tier: "generous",
        version: 1,
        base: generous,
        knobs: META,
      }),
    ).toBe(
      "Generous v1 · $10.00/run · auto study cap · 8 parallel · effort high · no pilot · no overrides",
    );
    const custom = setKnob(
      setKnob(frugal(), "study.max_cost_usd", 25),
      "run.max_turns",
      10,
    );
    expect(
      summaryLine(custom, {
        name: "Mine",
        tier: "custom",
        base: KNOBS.tiers.frugal,
        knobs: META,
      }),
    ).toBe(
      "Mine · $0.30/run · $25.00 study cap · 2 parallel · effort low · 2 overrides",
    );
  });

  it("counts a custom preset's overrides against the closest tier when no base is given", () => {
    const custom = setKnob(
      setKnob(frugal(), "study.max_cost_usd", 25),
      "run.max_turns",
      10,
    );
    expect(closestTier(custom, KNOBS.tiers, META)).toBe("frugal");
    for (const base of [null, undefined]) {
      expect(
        summaryLine(custom, {
          name: "Mine",
          version: 1,
          tier: "custom",
          base,
          tiers: KNOBS.tiers,
          knobs: META,
        }),
      ).toBe(
        "Mine v1 · $0.30/run · $25.00 study cap · 2 parallel · effort low · 2 overrides",
      );
    }
    // A tier-tagged preset without a base is compared against its own tier.
    expect(
      summaryLine(KNOBS.tiers.generous, {
        tier: "generous",
        tiers: KNOBS.tiers,
        knobs: META,
      }),
    ).toMatch(/no overrides$/);
  });

  it("formats values with their units", () => {
    expect(formatKnobValue(meta("run.max_cost_usd"), 0.3)).toBe("$0.30");
    expect(formatKnobValue(meta("run.max_total_tokens"), 3_000_000)).toBe(
      "3M tokens",
    );
    expect(
      formatKnobValue(meta("run.max_workspace_bytes"), 200 * 1024 * 1024),
    ).toBe("200 MB");
    expect(formatKnobValue(meta("time.wall_clock_s"), 1200)).toBe("20 min");
    expect(formatKnobValue(meta("sandbox.nano_cpus"), 2e9)).toBe("2 CPU");
    expect(formatKnobValue(meta("routing.max_price"), "model×1.25")).toBe(
      "model×1.25",
    );
    expect(
      formatKnobValue(meta("reasoning"), {
        mode: "effort",
        effort: "low",
        max_tokens: null,
      }),
    ).toBe("effort low");
    expect(formatKnobValue(meta("run.max_uncached_input_tokens"), null)).toBe(
      "unset",
    );
    expect(knobLabel("run.max_cost_usd")).toBe("Run cap");
    expect(knobLabel("pilot.gates.min_build_rate")).toBe("Min build rate");
  });

  it("finds no lint hint on the presets and mirrors the blocking rules", () => {
    for (const tier of ["frugal", "standard", "generous"] as const)
      expect(policyHints(KNOBS.tiers[tier]), tier).toEqual([]);
    const bad: ResourcePolicyContent = setKnob(frugal(), "reasoning", {
      mode: "effort",
      effort: "low",
      max_tokens: 100,
    });
    expect(policyHints(bad).map((h) => h.key)).toEqual(["reasoning"]);
    const tight = setKnob(frugal(), "call.max_input_tokens", 60_000);
    expect(policyHints(tight).map((h) => h.key)).toEqual([
      "call.max_input_tokens",
    ]);
  });
});
