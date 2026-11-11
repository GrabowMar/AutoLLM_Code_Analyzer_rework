import { describe, expect, it } from "vitest";
import type { ConstraintStatus, SelectionSensitivity } from "$lib/api/rankings";
import {
  SORT_KEYS,
  admittedLabel,
  backendSort,
  confirmBody,
  constraintFigure,
  defaultQuotas,
  describeBlocked,
  exclusionLabel,
  firstSortDir,
  fmtDate,
  fmtScore,
  fmtShare,
  frequencyTable,
  picksByTier,
  quotaProblem,
  tierBand,
} from "./mss";
import feasible from "./fixtures/selection-feasible.json";
import type { SelectionPayload } from "$lib/api/rankings";

/** backend/rankings/services/constants.py SORT_KEY_MAP keys (the API's accepted sort_by values). */
const BACKEND_SORT_KEY_MAP = [
  "mss",
  "composite",
  "popularity",
  "price",
  "capability",
  "recency",
  "coverage",
  "context",
  "name",
  "apps",
  "empirical",
];

describe("rankings sort keys", () => {
  it("map every page sort key onto a backend SORT_KEY_MAP key", () => {
    for (const [key, spec] of Object.entries(SORT_KEYS)) {
      expect(BACKEND_SORT_KEY_MAP, key).toContain(spec.backend);
    }
  });

  it("covers every backend sort key from the page", () => {
    const used = new Set(Object.values(SORT_KEYS).map((s) => s.backend));
    for (const k of BACKEND_SORT_KEY_MAP) expect(used.has(k), k).toBe(true);
  });

  it("sends no removed MSS v1 keys", () => {
    const sent = Object.values(SORT_KEYS).map((s) => s.backend);
    for (const old of [
      "adoption",
      "benchmark",
      "cost_efficiency",
      "accessibility",
    ]) {
      expect(sent).not.toContain(old);
    }
  });

  it("flips the direction for the price score (the backend sorts price by run cost)", () => {
    expect(backendSort("price", "desc")).toEqual({
      sort_by: "price",
      sort_dir: "asc",
    });
    expect(backendSort("run_cost", "asc")).toEqual({
      sort_by: "price",
      sort_dir: "asc",
    });
    expect(backendSort("mss", "desc")).toEqual({
      sort_by: "mss",
      sort_dir: "desc",
    });
  });

  it("starts $/run and names ascending, scores descending", () => {
    expect(firstSortDir("run_cost")).toBe("asc");
    expect(firstSortDir("name")).toBe("asc");
    expect(firstSortDir("popularity")).toBe("desc");
  });
});

describe("formatting", () => {
  it("never turns a missing score into 0", () => {
    expect(fmtScore(null)).toBeNull();
    expect(fmtScore(undefined)).toBeNull();
    expect(fmtScore(0)).toBe("0.0");
    expect(fmtScore(0.6123)).toBe("61.2");
  });

  it("formats small market shares", () => {
    expect(fmtShare(0.0173710817)).toBe("1.74%");
    expect(fmtShare(0.00089)).toBe("0.089%");
    expect(fmtShare(null)).toBe("—");
  });

  it("collapses lists of fetch dates", () => {
    expect(fmtDate("2026-11-09T09:22:19+00:00")).toBe("2026-11-09");
    expect(fmtDate(["2026-11-09", "2026-11-08T10:00:00Z", "2026-11-09"])).toBe(
      "2026-11-08 – 2026-11-09",
    );
    expect(fmtDate(null)).toBe("—");
  });

  it("prints tier bands", () => {
    const tiers = {
      bands_usd: {
        budget: [0.01, 0.1224],
        mid: [0.1224, 0.5976],
        frontier: [0.5976, 10],
      },
    } as const;
    expect(tierBand(tiers as never, "budget")).toBe("≤ $0.122/run");
    expect(tierBand(tiers as never, "mid")).toBe("$0.122 – $0.598/run");
    expect(tierBand(tiers as never, "frontier")).toBe("> $0.598/run");
    expect(tierBand({}, "mid")).toBe("");
    // An empty frame: the backend's placeholder bands are not printed.
    const empty = {
      bands_usd: { budget: [0.01, 0.0101], mid: [0, 0], frontier: [0, 10] },
      counts: { budget: 0, mid: 0, frontier: 0 },
    };
    for (const t of ["budget", "mid", "frontier"] as const)
      expect(tierBand(empty as never, t)).toBe("");
  });
});

describe("reasons", () => {
  it("reads exclusion codes with targets", () => {
    expect(exclusionLabel("superseded_by:z-ai/glm-5.3")).toEqual({
      label: "Superseded by",
      target: "z-ai/glm-5.3",
    });
    expect(exclusionLabel("too_old").label).toBe("Older than 12 months");
    // Rankings rows carry the plain E7 code; curated exclusions come from mss_frame.yaml.
    expect(exclusionLabel("superseded")).toEqual({
      label: "Superseded",
      target: null,
    });
    expect(exclusionLabel("curated_exclude").label).toBe("Curated exclusion");
    expect(exclusionLabel("something_new").label).toBe("something new");
  });

  it("reads blocked_by codes", () => {
    expect(
      describeBlocked("floor:Q-quality(capability 0.392 < 0.407)"),
    ).toEqual({
      kind: "floor",
      label: "Quality floor",
      detail: "capability 0.392 < 0.407",
    });
    expect(describeBlocked("budget:B-openrouter")).toMatchObject({
      kind: "budget",
      label: "OpenRouter budget",
    });
    expect(describeBlocked("cap:Q-vendor(xiaomi)")).toMatchObject({
      kind: "cap",
      label: "Per vendor",
      detail: "xiaomi",
    });
    expect(describeBlocked("lookahead:Q-open,Q-claude")).toMatchObject({
      kind: "lookahead",
      detail: "Open weights, Claude",
    });
    expect(describeBlocked("no_paid_route").kind).toBe("route");
  });

  it("reads admitted_by", () => {
    expect(admittedLabel("rank")).toBe("by rank");
    expect(admittedLabel("quota:Q-open")).toBe("quota: Open weights");
  });

  it("prints constraint figures", () => {
    const c = (o: Partial<ConstraintStatus>): ConstraintStatus => ({
      code: "",
      kind: "quota",
      value: null,
      limit: null,
      satisfied: true,
      binding: false,
      ...o,
    });
    expect(
      constraintFigure(
        c({ code: "B-openrouter", kind: "cap", value: 63.23, limit: 85 }),
      ),
    ).toBe("$63.23 ≤ $85.00");
    expect(constraintFigure(c({ code: "Q-vendors", value: 7, limit: 6 }))).toBe(
      "7 ≥ 6",
    );
    expect(
      constraintFigure(
        c({
          code: "Q-tier",
          value: { frontier: 3, mid: 3, budget: 4 },
          limit: { frontier: 3, mid: 3, budget: 4 },
        }),
      ),
    ).toBe("3/3/4");
    expect(constraintFigure(c({ code: "Q-quality", kind: "floor" }))).toBe(
      "met",
    );
  });
});

describe("selection helpers", () => {
  const payload = feasible as unknown as SelectionPayload;

  it("groups picks frontier → mid → budget in tier-rank order", () => {
    const groups = picksByTier(payload.selection?.picks);
    expect(groups.map((g) => g.tier)).toEqual(["frontier", "mid", "budget"]);
    expect(groups.map((g) => g.picks.length)).toEqual([3, 3, 4]);
    for (const g of groups) {
      const ranks = g.picks.map((p) => p.tier_rank);
      expect(ranks).toEqual([...ranks].sort((a, b) => a - b));
    }
  });

  it("builds the frequency table with picks first and S1 classes", () => {
    const rows = frequencyTable(payload.sensitivity);
    const firstNonPick = rows.findIndex((r) => !r.picked);
    expect(rows.slice(0, firstNonPick).every((r) => r.picked)).toBe(true);
    expect(
      rows.find((r) => r.canonical_id === "meta/muse-spark-1.3")?.cls,
    ).toBe("borderline");
    expect(rows.find((r) => r.canonical_id === "z-ai/glm-5.3")).toMatchObject({
      picked: false,
      cls: "contender",
    });
    expect(frequencyTable(null)).toEqual([]);
    const onlyS2: SelectionSensitivity = {
      S2: {
        ...payload.sensitivity!.S2!,
        frequencies: [
          {
            canonical_id: "x",
            frequency: 0.5,
            picked: false,
            class: "contender",
          },
        ],
      },
    };
    expect(frequencyTable(onlyS2)[0]).toMatchObject({
      s1: null,
      s2: 0.5,
      cls: null,
    });
  });

  it("derives default quotas in the 3/3/4 proportions", () => {
    expect(defaultQuotas(10)).toEqual({ frontier: 3, mid: 3, budget: 4 });
    expect(defaultQuotas(12)).toEqual({ frontier: 4, mid: 4, budget: 4 });
    expect(defaultQuotas(11)).toEqual({ frontier: 3, mid: 3, budget: 5 });
  });

  it("rejects quotas that do not sum to N", () => {
    expect(quotaProblem(10, { frontier: 3, mid: 3, budget: 4 })).toBeNull();
    expect(quotaProblem(10, { frontier: 3, mid: 3, budget: 3 })).toMatch(
      /sum to 9/,
    );
    expect(quotaProblem(10, { frontier: null, mid: 3, budget: 4 })).toMatch(
      /Frontier/,
    );
    expect(quotaProblem(null, { frontier: 3, mid: 3, budget: 4 })).toMatch(/N/);
  });
});

describe("confirmBody", () => {
  const nominal = ["a/x", "b/y", "c/z"];

  it("keeps the nominal picks without swaps", () => {
    expect(confirmBody(nominal, {})).toEqual({
      final_ids: nominal,
      overrides: [],
      problems: [],
    });
  });

  it("records a swap with its reason", () => {
    const out = confirmBody(nominal, {
      "b/y": { with: "d/w", reason: " borderline in S1 " },
    });
    expect(out.final_ids).toEqual(["a/x", "d/w", "c/z"]);
    expect(out.overrides).toEqual([
      { replace: "b/y", with: "d/w", reason: "borderline in S1" },
    ]);
    expect(out.problems).toEqual([]);
  });

  it("requires a reason and refuses duplicates", () => {
    expect(
      confirmBody(nominal, { "b/y": { with: "d/w", reason: "" } }).problems[0],
    ).toMatch(/reason/);
    expect(
      confirmBody(nominal, { "b/y": { with: "a/x", reason: "r" } }).problems[0],
    ).toMatch(/twice/);
  });

  it("treats an empty choice as keep", () => {
    expect(
      confirmBody(nominal, { "b/y": { with: "", reason: "" } }).overrides,
    ).toEqual([]);
  });
});
