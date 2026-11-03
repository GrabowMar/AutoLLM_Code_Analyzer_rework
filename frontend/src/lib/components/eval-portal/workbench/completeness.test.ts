import { describe, expect, it } from "vitest";
import type {
  Block,
  BlockAnswers,
  BlockContext,
  Observation,
  Question,
} from "$lib/api/types/human-eval";
import {
  blockCompleteness,
  emptyAnswers,
  errorsByBlock,
  onScale,
  questionIssue,
  requirementItems,
  scaleValues,
  type CompletenessInput,
  type Slot,
} from "./completeness";

function input(
  block: Block,
  answers: BlockAnswers | undefined,
  over: Partial<CompletenessInput> = {},
): CompletenessInput {
  return {
    block,
    answers,
    context: undefined,
    slots: ["single"],
    observations: [],
    findingVerdicts: {},
    ...over,
  };
}

function bug(slot: Slot, id = `${slot}-${Math.random()}`): Observation {
  return {
    id,
    assignment_id: "a",
    app_slot: slot,
    block_id: "bugs",
    kind: "bug",
    title: "Broken",
    description: "",
    steps: "",
    location: "",
    category: "",
    severity: "",
    cwe: "",
    checklist_item_id: "",
    attachments: [],
    created_at: "",
    updated_at: "",
  };
}

const rating: Block = {
  id: "quality",
  type: "rating",
  title: "Quality",
  comment: "optional",
  criteria: [
    {
      id: "usability",
      label: "Usability",
      scale: { min: 1, max: 7 },
      required: true,
      allow_na: false,
    },
    {
      id: "looks",
      label: "Looks",
      scale: { min: 1, max: 7 },
      required: false,
      allow_na: true,
    },
    {
      id: "speed",
      label: "Speed",
      scale: { min: 0, max: 100, step: 5 },
      required: true,
      allow_na: true,
    },
  ],
};

describe("scales", () => {
  it("lists values on the step grid", () => {
    expect(scaleValues({ min: 1, max: 5 })).toEqual([1, 2, 3, 4, 5]);
    expect(scaleValues({ min: 0, max: 20, step: 5 })).toEqual([
      0, 5, 10, 15, 20,
    ]);
  });

  it("checks range, integers and step", () => {
    expect(onScale(3, { min: 1, max: 7 })).toBe(true);
    expect(onScale(0, { min: 1, max: 7 })).toBe(false);
    expect(onScale(2.5, { min: 1, max: 7 })).toBe(false);
    expect(onScale(15, { min: 0, max: 100, step: 5 })).toBe(true);
    expect(onScale(12, { min: 0, max: 100, step: 5 })).toBe(false);
    expect(onScale("3", { min: 1, max: 7 })).toBe(false);
  });
});

describe("rating blocks", () => {
  it("needs every required criterion", () => {
    const status = blockCompleteness(
      input(rating, { criteria: { usability: 4 } }),
    );
    expect(status.complete).toBe(false);
    expect(status.issues).toEqual(["Rate “Speed”"]);
    expect(status.done).toBe(1);
    expect(status.total).toBe(3);
  });

  it("accepts na only where allowed and flags off-grid values", () => {
    expect(
      blockCompleteness(
        input(rating, { criteria: { usability: 4, speed: "na" } }),
      ).complete,
    ).toBe(true);
    const bad = blockCompleteness(
      input(rating, { criteria: { usability: "na", speed: 12 } }),
    );
    expect(bad.complete).toBe(false);
    expect(bad.issues).toHaveLength(2);
  });

  it("requires a non-blank comment when the block says so", () => {
    const block = { ...rating, comment: "required" } as Block;
    const answers = { criteria: { usability: 1, speed: 0 }, comment: "   " };
    expect(blockCompleteness(input(block, answers)).issues).toEqual([
      "Write a comment",
    ]);
    expect(
      blockCompleteness(input(block, { ...answers, comment: "fine" })).complete,
    ).toBe(true);
  });

  it("treats missing answers as empty", () => {
    expect(blockCompleteness(input(rating, undefined)).complete).toBe(false);
  });
});

describe("requirements blocks", () => {
  const block: Block = {
    id: "reqs",
    type: "requirements",
    title: "Requirements",
    sources: ["frontend"],
    custom: [],
    note: "required_unless_met",
  };
  const context: BlockContext = {
    requirements: {
      single: [
        { id: "b_1", source: "frontend", text: "Login" },
        { id: "b_2", source: "frontend", text: "Logout" },
        { id: "c_x", source: "custom", text: "Looks right" },
      ],
    },
  };

  it("needs a verdict per item and notes unless met", () => {
    const status = blockCompleteness(
      input(
        block,
        { items: { b_1: { verdict: "met" }, b_2: { verdict: "blocked" } } },
        { context },
      ),
    );
    expect(status.complete).toBe(false);
    expect(status.issues).toEqual([
      "1 requirement without a verdict",
      "1 verdict still needs a note",
    ]);
    expect(status.done).toBe(1);
    expect(status.total).toBe(3);
  });

  it("is complete with every verdict and notes", () => {
    const status = blockCompleteness(
      input(
        block,
        {
          items: {
            b_1: { verdict: "met" },
            b_2: { verdict: "cannot_test", note: "no admin" },
            c_x: { verdict: "partial", note: "colours off" },
          },
        },
        { context },
      ),
    );
    expect(status.complete).toBe(true);
  });

  it("does not ask for notes when the rule is optional", () => {
    const optional = { ...block, note: "optional" } as Block;
    const status = blockCompleteness(
      input(
        optional,
        {
          items: {
            b_1: { verdict: "not_met" },
            b_2: { verdict: "met" },
            c_x: { verdict: "met" },
          },
        },
        { context },
      ),
    );
    expect(status.complete).toBe(true);
  });

  it("deduplicates items across slots", () => {
    const items = requirementItems(
      {
        requirements: {
          A: [{ id: "x", source: "api", text: "GET /" }],
          B: [{ id: "x", source: "api", text: "GET /" }],
        },
      },
      ["A", "B"],
    );
    expect(items.map((i) => i.id)).toEqual(["x"]);
  });
});

describe("security blocks", () => {
  const block: Block = {
    id: "sec",
    type: "security",
    title: "Security",
    checklist: [
      { id: "idor", label: "IDOR" },
      { id: "xss", label: "XSS" },
    ],
    reports: true,
    finding_review: {
      enabled: true,
      max_findings: 5,
      categories: [],
      include_ai: false,
      include_dependency: false,
      include_dynamic: false,
    },
    http_console: true,
  };
  const finding = {
    tool: "semgrep",
    tool_name: "Semgrep",
    tool_severity: "",
    category: "security",
    rule_id: "r",
    title: "t",
    description: "",
    file_path: "app.py",
    line: 1,
    code_snippet: "",
    docs_url: "",
    cwe: "",
    url: "",
    method: "",
    param: "",
    evidence: "",
  };
  const context: BlockContext = {
    findings: {
      single: [
        { ...finding, id: "f1" },
        { ...finding, id: "f2" },
      ],
    },
  };

  it("needs every checklist outcome and every finding verdict", () => {
    const status = blockCompleteness(
      input(
        block,
        { checklist: { idor: { outcome: "vulnerable" } } },
        {
          context,
          findingVerdicts: { f1: { verdict: "tp", note: "" } },
        },
      ),
    );
    expect(status.issues).toEqual([
      "1 checklist item without an outcome",
      "1 finding not reviewed",
    ]);
    expect(status.done).toBe(2);
    expect(status.total).toBe(4);
  });

  it("is complete when everything is judged", () => {
    const status = blockCompleteness(
      input(
        block,
        {
          checklist: {
            idor: { outcome: "not_tested" },
            xss: { outcome: "not_vulnerable" },
          },
        },
        {
          context,
          findingVerdicts: {
            f1: { verdict: "fp", note: "" },
            f2: { verdict: "unsure", note: "" },
          },
        },
      ),
    );
    expect(status.complete).toBe(true);
  });

  it("ignores findings when the review is off", () => {
    const off = {
      ...block,
      finding_review: { ...block.finding_review, enabled: false },
    } as Block;
    const status = blockCompleteness(
      input(
        off,
        {
          checklist: {
            idor: { outcome: "vulnerable" },
            xss: { outcome: "vulnerable" },
          },
        },
        { context },
      ),
    );
    expect(status.complete).toBe(true);
  });
});

describe("scenario blocks", () => {
  const block: Block = {
    id: "ux",
    type: "scenarios",
    title: "Tasks",
    timed: true,
    ease_question: true,
    scenarios: [
      { id: "s1", title: "Sign up", description: "" },
      { id: "s2", title: "Buy", description: "" },
    ],
  };

  it("needs outcomes and ease unless not attempted", () => {
    const status = blockCompleteness(
      input(block, {
        scenarios: {
          s1: { outcome: "success" },
          s2: { outcome: "not_attempted" },
        },
      }),
    );
    expect(status.issues).toEqual(["Rate how easy “Sign up” was"]);
    expect(
      blockCompleteness(
        input(block, {
          scenarios: {
            s1: { outcome: "partial", ease: 5, seconds: 40 },
            s2: { outcome: "not_attempted" },
          },
        }),
      ).complete,
    ).toBe(true);
  });

  it("rejects negative times and out-of-range ease", () => {
    const status = blockCompleteness(
      input(block, {
        scenarios: {
          s1: { outcome: "success", ease: 8 },
          s2: { outcome: "failure", ease: 3, seconds: -1 },
        },
      }),
    );
    expect(status.complete).toBe(false);
    expect(status.issues).toHaveLength(2);
  });
});

describe("questionnaire blocks", () => {
  const questions: Question[] = [
    {
      id: "q1",
      kind: "scale",
      label: "Q1",
      required: true,
      scale: { min: 1, max: 5 },
    },
    {
      id: "q2",
      kind: "multi",
      label: "Q2",
      required: true,
      options: [
        { value: "a", label: "A" },
        { value: "b", label: "B" },
      ],
    },
    { id: "q3", kind: "number", label: "Q3", required: false, min: 0, max: 10 },
    { id: "q4", kind: "text", label: "Q4", required: false },
  ];
  const custom: Block = {
    id: "q",
    type: "questionnaire",
    title: "Q",
    instrument: "custom",
    questions,
  };

  it("checks required answers and valid optional ones", () => {
    const status = blockCompleteness(
      input(custom, { answers: { q1: 3, q2: [], q3: 11 } }),
    );
    expect(status.issues).toEqual([
      "1 required question unanswered",
      "1 answer to fix",
    ]);
    expect(
      blockCompleteness(
        input(custom, { answers: { q1: 3, q2: ["b"], q3: 10, q4: "" } }),
      ).complete,
    ).toBe(true);
  });

  it("uses the server's questions for standard instruments", () => {
    const sus: Block = {
      id: "sus",
      type: "questionnaire",
      title: "SUS",
      instrument: "sus",
      questions: [],
    };
    const context: BlockContext = { questions: [questions[0]] };
    expect(
      blockCompleteness(input(sus, { answers: {} }, { context })).complete,
    ).toBe(false);
    expect(
      blockCompleteness(input(sus, { answers: { q1: 5 } }, { context }))
        .complete,
    ).toBe(true);
  });

  it("validates each question kind", () => {
    const single: Question = {
      id: "s",
      kind: "single",
      label: "S",
      required: true,
      options: [{ value: "x", label: "X" }],
    };
    expect(questionIssue(single, "x")).toBeNull();
    expect(questionIssue(single, "y")).not.toBeNull();
    expect(questionIssue(single, null)).toBe("Answer this question");
    expect(questionIssue({ ...single, kind: "attention" }, "x")).toBeNull();
    expect(
      questionIssue(
        { id: "t", kind: "textarea", label: "T", required: true },
        "x".repeat(5001),
      ),
    ).not.toBeNull();
    expect(
      questionIssue(
        {
          id: "n",
          kind: "number",
          label: "N",
          required: true,
          min: null,
          max: null,
        },
        1e9,
      ),
    ).toBeNull();
    expect(
      questionIssue(
        {
          id: "tlx",
          kind: "scale",
          label: "TLX",
          required: true,
          scale: { min: 0, max: 100, step: 5 },
        },
        55,
      ),
    ).toBeNull();
    expect(
      questionIssue(
        {
          id: "tlx",
          kind: "scale",
          label: "TLX",
          required: true,
          scale: { min: 0, max: 100, step: 5 },
        },
        52,
      ),
    ).not.toBeNull();
  });
});

describe("bug blocks", () => {
  const block: Block = {
    id: "bugs",
    type: "bugs",
    title: "Bugs",
    min_reports: 0,
  };

  it("needs a bug or 'none found' per slot", () => {
    const labels = { A: "App 01", B: "App 02" };
    const status = blockCompleteness(
      input(
        block,
        { none_found: { A: true } },
        { slots: ["A", "B"], slotLabels: labels },
      ),
    );
    expect(status.issues).toEqual([
      "Report a bug or confirm you found none for App 02",
    ]);
    expect(
      blockCompleteness(
        input(
          block,
          { none_found: { A: true } },
          { slots: ["A", "B"], observations: [bug("B")] },
        ),
      ).complete,
    ).toBe(true);
  });

  it("enforces the minimum per slot", () => {
    const min2 = { ...block, min_reports: 2 } as Block;
    const status = blockCompleteness(
      input(
        min2,
        { none_found: { single: true } },
        { observations: [bug("single")] },
      ),
    );
    expect(status.issues).toEqual(["Report 1 more bug"]);
    expect(
      blockCompleteness(
        input(
          min2,
          { none_found: {} },
          { observations: [bug("single"), bug("single")] },
        ),
      ).complete,
    ).toBe(true);
  });

  it("does not count other kinds", () => {
    const note = { ...bug("single"), kind: "note" as const };
    expect(
      blockCompleteness(
        input(block, { none_found: {} }, { observations: [note] }),
      ).complete,
    ).toBe(false);
  });
});

describe("pairwise blocks", () => {
  const block: Block = {
    id: "ab",
    type: "pairwise",
    title: "A vs B",
    criteria: [
      { id: "ux", label: "UX" },
      { id: "fn", label: "Function" },
    ],
    scale: "ab_tie",
    overall: true,
    rationale: "required",
  };

  it("needs every criterion, the overall preference and a rationale", () => {
    const status = blockCompleteness(
      input(block, { criteria: { ux: "A" } }, { slots: ["A", "B"] }),
    );
    expect(status.issues).toEqual([
      "Compare the apps on “Function”",
      "Give your overall preference",
      "Explain your choice",
    ]);
    expect(
      blockCompleteness(
        input(
          block,
          {
            criteria: { ux: "A", fn: "tie" },
            overall: "B",
            rationale: "B is faster",
          },
          { slots: ["A", "B"] },
        ),
      ).complete,
    ).toBe(true);
  });

  it("checks values against the scale", () => {
    const five = {
      ...block,
      scale: "five_point",
      overall: false,
      rationale: "off",
    } as Block;
    expect(
      blockCompleteness(input(five, { criteria: { ux: -2, fn: 0 } })).complete,
    ).toBe(true);
    expect(
      blockCompleteness(input(five, { criteria: { ux: "A", fn: 3 } })).complete,
    ).toBe(false);
  });
});

describe("helpers", () => {
  it("builds empty answers per block type", () => {
    expect(emptyAnswers(rating)).toEqual({ criteria: {} });
    expect(
      emptyAnswers({ id: "b", type: "bugs", title: "", min_reports: 0 }),
    ).toEqual({ none_found: {} });
  });

  it("groups server errors by block", () => {
    const { byBlock, general } = errorsByBlock(
      [
        { path: "quality.criteria.speed", message: "Required" },
        { path: "quality.comment", message: "Write a comment" },
        { path: "bugs", message: "Report a bug" },
        { path: "unknown.x", message: "Odd" },
        { path: "", message: "Something" },
      ],
      ["quality", "bugs"],
    );
    expect(byBlock).toEqual({
      quality: ["Required", "Write a comment"],
      bugs: ["Report a bug"],
    });
    expect(general).toEqual(["unknown.x: Odd", "Something"]);
  });
});
