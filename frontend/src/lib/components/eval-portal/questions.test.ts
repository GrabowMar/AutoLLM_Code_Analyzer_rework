import { describe, expect, it } from "vitest";
import type { Question } from "$lib/api/types/human-eval";
import {
  TEXT_ANSWER_MAX,
  answerTally,
  cleanAnswers,
  errorList,
  isAnswered,
  isOnScale,
  isProvided,
  mapQuestionErrors,
  questionError,
  questionsComplete,
  scalePointLabel,
  scaleValues,
  toggleOption,
  validateQuestions,
} from "./questions";

const q = (
  over: Partial<Question> & Pick<Question, "id" | "kind">,
): Question => ({
  label: over.id,
  required: true,
  ...over,
});

const options = [
  { value: "a", label: "A" },
  { value: "b", label: "B" },
  { value: "c", label: "C" },
];

describe("scaleValues / isOnScale", () => {
  it("lists every point of a step-1 scale", () => {
    expect(scaleValues({ min: 1, max: 5 })).toEqual([1, 2, 3, 4, 5]);
  });

  it("honours the step (NASA-TLX 0..100 step 5 has 21 points)", () => {
    const values = scaleValues({ min: 0, max: 100, step: 5 });
    expect(values).toHaveLength(21);
    expect(values[0]).toBe(0);
    expect(values[20]).toBe(100);
    expect(values[1]).toBe(5);
  });

  it("treats a missing or bad step as 1 and an inverted scale as empty", () => {
    expect(scaleValues({ min: 0, max: 2, step: 0 })).toEqual([0, 1, 2]);
    expect(scaleValues({ min: 0, max: 2, step: 1.5 })).toEqual([0, 1, 2]);
    expect(scaleValues({ min: 3, max: 1 })).toEqual([]);
  });

  it("accepts only integers on the step grid", () => {
    const tlx = { min: 0, max: 100, step: 5 };
    expect(isOnScale(tlx, 35)).toBe(true);
    expect(isOnScale(tlx, 36)).toBe(false);
    expect(isOnScale(tlx, 105)).toBe(false);
    expect(isOnScale(tlx, "35")).toBe(false);
    expect(isOnScale({ min: 1, max: 7 }, 2.5)).toBe(false);
  });

  it("labels points with their own label, else the anchors", () => {
    const scale = {
      min: 1,
      max: 5,
      min_label: "Low",
      max_label: "High",
      labels: { "3": "Neutral" },
    };
    expect(scalePointLabel(scale, 1)).toBe("Low");
    expect(scalePointLabel(scale, 3)).toBe("Neutral");
    expect(scalePointLabel(scale, 4)).toBe("");
    expect(scalePointLabel(scale, 5)).toBe("High");
  });
});

describe("isProvided", () => {
  it("does not count blank text, empty selections or null", () => {
    expect(isProvided(null)).toBe(false);
    expect(isProvided(undefined)).toBe(false);
    expect(isProvided("   ")).toBe(false);
    expect(isProvided([])).toBe(false);
    expect(isProvided(Number.NaN)).toBe(false);
    expect(isProvided(0)).toBe(true);
    expect(isProvided("x")).toBe(true);
    expect(isProvided(["a"])).toBe(true);
  });
});

describe("questionError", () => {
  it("requires required questions and lets optional ones stay empty", () => {
    expect(questionError(q({ id: "t", kind: "text" }), "")).toMatch(/required/);
    expect(
      questionError(q({ id: "t", kind: "text", required: false }), null),
    ).toBe("");
  });

  it("limits text length", () => {
    const text = q({ id: "t", kind: "textarea" });
    expect(questionError(text, "ok")).toBe("");
    expect(questionError(text, "x".repeat(TEXT_ANSWER_MAX + 1))).toMatch(
      /5000/,
    );
  });

  it("checks single, attention and multi answers against the options", () => {
    expect(questionError(q({ id: "s", kind: "single", options }), "b")).toBe(
      "",
    );
    expect(
      questionError(q({ id: "s", kind: "single", options }), "z"),
    ).not.toBe("");
    expect(
      questionError(q({ id: "att", kind: "attention", options }), "c"),
    ).toBe("");
    expect(
      questionError(q({ id: "m", kind: "multi", options }), ["a", "c"]),
    ).toBe("");
    expect(
      questionError(q({ id: "m", kind: "multi", options }), ["a", "x"]),
    ).not.toBe("");
    expect(questionError(q({ id: "m", kind: "multi", options }), [])).toMatch(
      /required/,
    );
  });

  it("checks scale answers against the step grid", () => {
    const tlx = q({
      id: "tlx",
      kind: "scale",
      scale: { min: 0, max: 100, step: 5 },
    });
    expect(questionError(tlx, 50)).toBe("");
    expect(questionError(tlx, 52)).toMatch(/0 to 100/);
  });

  it("checks number answers against min/max", () => {
    const age = q({ id: "age", kind: "number", min: 18, max: 99 });
    expect(questionError(age, 30)).toBe("");
    expect(questionError(age, 17)).toMatch(/18 to 99/);
    expect(questionError(q({ id: "n", kind: "number", min: 0 }), -1)).toMatch(
      /at least 0/,
    );
    expect(questionError(q({ id: "n", kind: "number" }), "12")).not.toBe("");
  });
});

describe("completeness", () => {
  const questions: Question[] = [
    q({ id: "exp", kind: "scale", scale: { min: 1, max: 5 } }),
    q({ id: "langs", kind: "multi", options, required: false }),
    q({ id: "note", kind: "textarea", required: false }),
  ];

  it("is complete when every required question is validly answered", () => {
    expect(questionsComplete(questions, {})).toBe(false);
    expect(questionsComplete(questions, { exp: 3 })).toBe(true);
    expect(questionsComplete(questions, { exp: 3, langs: ["zz"] })).toBe(false);
    expect(validateQuestions(questions, { exp: 9 })).toEqual({
      exp: expect.any(String),
    });
  });

  it("counts answered and required questions", () => {
    expect(answerTally(questions, { exp: 2, note: "hi" })).toEqual({
      total: 3,
      answered: 2,
      required: 1,
      requiredAnswered: 1,
    });
    expect(isAnswered(questions[0], 2)).toBe(true);
    expect(isAnswered(questions[0], null)).toBe(false);
  });
});

describe("cleanAnswers", () => {
  it("keeps only known ids and sends cleared answers as null", () => {
    const questions: Question[] = [
      q({ id: "a", kind: "text" }),
      q({ id: "b", kind: "multi", options }),
      q({ id: "c", kind: "number" }),
      q({ id: "d", kind: "single", options }),
    ];
    expect(
      cleanAnswers(questions, { a: "  ", b: [], c: 4, extra: "drop me" }),
    ).toEqual({ a: null, b: null, c: 4, d: null });
    expect(cleanAnswers(questions, { a: "text", b: ["a"], d: "b" })).toEqual({
      a: "text",
      b: ["a"],
      c: null,
      d: "b",
    });
  });
});

describe("toggleOption", () => {
  it("adds and removes options in the options' order", () => {
    const multi = q({ id: "m", kind: "multi", options });
    expect(toggleOption(multi, null, "c", true)).toEqual(["c"]);
    expect(toggleOption(multi, ["c"], "a", true)).toEqual(["a", "c"]);
    expect(toggleOption(multi, ["a", "c"], "a", false)).toEqual(["c"]);
    expect(toggleOption(multi, ["a"], "a", true)).toEqual(["a"]);
  });
});

describe("server errors", () => {
  const questions: Question[] = [
    q({ id: "age", kind: "number" }),
    q({ id: "role", kind: "text" }),
  ];

  it("maps error paths to question ids", () => {
    const mapped = mapQuestionErrors(
      [
        { path: "answers.age", message: "Too young" },
        { path: "post_answers.role", message: "Required" },
        { path: "role", message: "Second message ignored" },
        { path: "", message: "Something else" },
        { path: "answers.unknown", message: "Not ours" },
      ],
      questions,
    );
    expect(mapped.byQuestion).toEqual({ age: "Too young", role: "Required" });
    expect(mapped.other).toEqual(["Something else", "Not ours"]);
  });

  it("reads the structured errors array of a thrown body", () => {
    expect(
      errorList({
        detail: "Invalid",
        code: "invalid_answers",
        errors: [{ path: "a", message: "m" }, { path: "b" }],
      }),
    ).toEqual([{ path: "a", message: "m" }]);
    expect(errorList({ detail: "x" })).toEqual([]);
    expect(errorList(null)).toEqual([]);
  });
});
