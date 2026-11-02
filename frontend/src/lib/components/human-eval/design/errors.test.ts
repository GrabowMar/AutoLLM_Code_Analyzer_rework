import { describe, expect, it } from "vitest";
import type { TaskDesign } from "$lib/api/types/human-eval";
import {
  classifyPath,
  describeDesignPath,
  messageAt,
  messagesAt,
  messagesUnder,
  splitErrors,
  splitPath,
  under,
} from "./errors";

describe("splitPath / classifyPath", () => {
  it("accepts dotted and bracket indices", () => {
    expect(splitPath("blocks[0].criteria[1].label")).toEqual([
      "blocks",
      "0",
      "criteria",
      "1",
      "label",
    ]);
    expect(splitPath("blocks.0.criteria.1.label")).toEqual([
      "blocks",
      "0",
      "criteria",
      "1",
      "label",
    ]);
  });

  it("strips the design / settings / body scope and infers the area", () => {
    expect(classifyPath("design.blocks.0.title")).toEqual({
      area: "design",
      segments: ["blocks", "0", "title"],
    });
    expect(classifyPath("body.settings.hosting.reset")).toEqual({
      area: "settings",
      segments: ["hosting", "reset"],
    });
    expect(classifyPath("consent.text")).toEqual({
      area: "design",
      segments: ["consent", "text"],
    });
    expect(classifyPath("hosting.isolation").area).toBe("settings");
    expect(classifyPath("mode")).toEqual({
      area: "general",
      segments: ["mode"],
    });
    expect(classifyPath("").area).toBe("general");
  });

  it("drops pydantic's discriminator tag after a block index", () => {
    expect(
      classifyPath("design.blocks.2.rating.criteria.0.scale.max").segments,
    ).toEqual(["blocks", "2", "criteria", "0", "scale", "max"]);
  });
});

describe("splitErrors / under / messages", () => {
  const errors = splitErrors([
    { path: "design.blocks.0.title", message: "Title is required" },
    {
      path: "blocks.0.criteria.1.scale",
      message: "Step must divide the range",
    },
    {
      path: "blocks.rating_2.criteria.usability.label",
      message: "Label too long",
    },
    {
      path: "settings.hosting.reset",
      message: "per_assignment requires exclusive isolation",
    },
    { path: "mode", message: "Unknown mode" },
  ]);

  it("splits by area", () => {
    expect(errors.design).toHaveLength(3);
    expect(errors.settings).toEqual([
      {
        path: ["hosting", "reset"],
        message: "per_assignment requires exclusive isolation",
      },
    ]);
    expect(errors.general).toEqual([
      { path: ["mode"], message: "Unknown mode" },
    ]);
  });

  it("scopes by index or id", () => {
    const block0 = under(errors.design, "blocks", [0, "rating_1"]);
    expect(messageAt(block0, "title")).toBe("Title is required");
    expect(
      messagesAt(under(block0, "criteria", [1, "speed"]), "scale"),
    ).toEqual(["Step must divide the range"]);

    const block1 = under(errors.design, "blocks", [1, "rating_2"]);
    expect(
      messageAt(under(block1, "criteria", [0, "usability"]), "label"),
    ).toBe("Label too long");
  });

  it("returns empty strings for fields without errors", () => {
    expect(messageAt(errors.design, "consent", "text")).toBe("");
  });

  it("lists nested messages with their remaining path", () => {
    expect(messagesUnder(errors.design, "blocks", 0)).toEqual([
      "title: Title is required",
      "criteria.1.scale: Step must divide the range",
    ]);
  });
});

describe("describeDesignPath", () => {
  const design: TaskDesign = {
    version: 1,
    blocks: [
      {
        id: "rating",
        type: "rating",
        title: "Ratings",
        criteria: [
          {
            id: "overall",
            label: "Overall",
            scale: { min: 1, max: 7 },
            required: true,
            allow_na: false,
          },
          {
            id: "ease",
            label: "Ease of use",
            scale: { min: 1, max: 7 },
            required: true,
            allow_na: false,
          },
        ],
        comment: "off",
      },
    ],
    analysis: {
      primary: null,
      planned_ratings_per_item: null,
      planned_participants: null,
      exclusion_rules: {
        speeding: false,
        attention_failed: false,
        straightlining: false,
        app_not_viewed: false,
      },
      unevaluable_policy: "exclude",
    },
    consent: { enabled: false, text: "" },
    pre_questions: [
      { id: "exp", kind: "text", label: "Experience", required: false },
    ],
    post_questions: [],
  };

  it("names blocks and list entries", () => {
    expect(
      describeDesignPath(
        ["blocks", "0", "criteria", "1", "scale", "max"],
        design,
      ),
    ).toBe("Block 1 “Ratings” › criterion 2 “Ease of use” › scale › max");
    expect(
      describeDesignPath(
        ["blocks", "rating", "criteria", "overall", "label"],
        design,
      ),
    ).toBe("Block 1 “Ratings” › criterion 1 “Overall” › label");
    expect(describeDesignPath(["pre_questions", "0", "label"], design)).toBe(
      "Pre-question 1 “Experience” › label",
    );
  });

  it("degrades gracefully for unknown entries", () => {
    expect(describeDesignPath(["blocks", "9", "title"], design)).toBe(
      "Block 10 › title",
    );
    expect(describeDesignPath([], design)).toBe("Design");
    expect(describeDesignPath(["analysis", "primary"], null)).toBe(
      "Analysis › primary",
    );
  });
});
