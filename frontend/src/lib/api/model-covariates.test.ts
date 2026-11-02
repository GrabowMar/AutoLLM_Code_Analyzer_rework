import { describe, expect, it } from "vitest";
import {
  displayMatch,
  externalView,
  fmtScientific,
  getModelCovariatesCsvUrl,
  rangePercent,
  readEpoch,
  readLmarena,
} from "./model-covariates";

describe("readEpoch", () => {
  it("reads the stored record and keeps missing values null", () => {
    const e = readEpoch({
      epoch: {
        model: "DeepSeek V4.1 Flash",
        parameters: 552e9,
        active_params_parsed: 16e9,
        training_compute_flop: 4.32e24,
        training_dataset_size: "45T",
        confidence: "Likely",
        match: "variant",
        license: "CC-BY 4.0 (Epoch AI)",
      },
    });
    expect(e?.model).toBe("DeepSeek V4.1 Flash");
    expect(e?.parameters).toBe(552e9);
    expect(e?.training_dataset_size).toBeNull();
    expect(e?.publication_date).toBeNull();
    expect(e?.match).toBe("variant");
  });

  it("returns null without a usable record", () => {
    expect(readEpoch(null)).toBeNull();
    expect(readEpoch({})).toBeNull();
    expect(readEpoch({ epoch: { parameters: 1 } })).toBeNull();
    expect(readEpoch({ epoch: "x" })).toBeNull();
  });
});

describe("readLmarena", () => {
  it("orders categories and maps the agent score scale", () => {
    const a = readLmarena({
      lmarena: {
        categories: {
          agent: {
            metric: "score",
            score: 0.019,
            score_lower: 0.016,
            score_upper: 0.022,
            session_count: 266436,
          },
          text_coding: { rating: 1480, vote_count: 900 },
          webdev: {
            rating: 1619.2,
            rating_lower: 1608.2,
            rating_upper: 1630.1,
            vote_count: 3510,
            rank: 29,
            n_ranked: 152,
          },
          broken: "x",
          no_value: { rating: null },
        },
        names: { webdev: "Flash (Max)", text: 3 },
        match: "assumed",
      },
    });
    expect(a?.categories.map((c) => c.key)).toEqual([
      "webdev",
      "text_coding",
      "agent",
    ]);
    const agent = a?.categories.find((c) => c.key === "agent");
    expect(agent?.metric).toBe("score");
    expect(agent?.count).toBe(266436);
    expect(a?.categories[0].count).toBe(3510);
    expect(a?.names).toEqual({ webdev: "Flash (Max)" });
    expect(a?.match).toBe("assumed");
  });

  it("returns null when no category has a value", () => {
    expect(readLmarena({ lmarena: { categories: {} } })).toBeNull();
    expect(readLmarena({ lmarena: {} })).toBeNull();
    expect(readLmarena(undefined)).toBeNull();
  });
});

describe("formatting", () => {
  it("writes scientific notation with superscripts", () => {
    expect(fmtScientific(4.32e24)).toBe("4.32 × 10²⁴");
    expect(fmtScientific(3e24)).toBe("3 × 10²⁴");
    expect(fmtScientific(0.0015)).toBe("1.5 × 10⁻³");
    expect(fmtScientific(null)).toBe("—");
  });

  it("places values on a range and refuses unknown or flat ranges", () => {
    expect(rangePercent(1500, 1000, 2000)).toBe(50);
    expect(rangePercent(2500, 1000, 2000)).toBe(100);
    expect(rangePercent(1500, null, 2000)).toBeNull();
    expect(rangePercent(1500, 1000, 1000)).toBeNull();
  });

  it("builds the CSV download URL", () => {
    expect(getModelCovariatesCsvUrl()).toBe(
      "/api/models/covariates/?format=csv&used_only=true",
    );
    expect(getModelCovariatesCsvUrl(false)).toContain("used_only=false");
  });
});

describe("externalView", () => {
  const twin = {
    epoch: { model: "Flash One", match: "variant" },
    lmarena: {
      categories: { webdev: { rating: 1447 } },
      match: "exact",
    },
  };

  it("prefers the route's own curated records over the twin's", () => {
    const own = {
      epoch: { model: "Flash One 0813", match: "assumed" },
      lmarena: {
        categories: { webdev: { rating: 1582 } },
        match: "assumed",
      },
    };
    const v = externalView(twin, own);
    expect(v.epoch?.model).toBe("Flash One 0813");
    expect(v.arena?.categories[0].value).toBe(1582);
    expect(v.epochInherited).toBe(false);
    expect(v.arenaInherited).toBe(false);
  });

  it("flags records shown from the twin and never shows its match as this route's", () => {
    const v = externalView(twin, { ollama: {} });
    expect(v.arena?.categories[0].value).toBe(1447);
    expect(v.epochInherited).toBe(true);
    expect(v.arenaInherited).toBe(true);
    expect(displayMatch(v.arena?.match ?? null, v.arenaInherited)).toBe(
      "assumed",
    );
    expect(displayMatch("variant", false)).toBe("variant");
    expect(displayMatch(null, true)).toBeNull();
  });

  it("does not flag a catalog model shown from its own metadata", () => {
    const v = externalView(twin, twin);
    expect(v.epochInherited).toBe(false);
    expect(externalView(twin).arenaInherited).toBe(false);
  });
});
