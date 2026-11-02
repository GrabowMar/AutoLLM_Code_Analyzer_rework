// Pure helpers for participant questions (task pre/post questionnaires and
// custom questionnaire blocks): scale values, completeness and the same
// client-side checks the server applies on submit (backend services/responses.py
// validate_question_answers). The server stays the authority; these only let the
// form say what is missing before a round trip.
import type {
  Question,
  QuestionAnswer,
  Scale,
} from "$lib/api/types/human-eval";

/** Server limit for text / textarea answers. */
export const TEXT_ANSWER_MAX = 5000;

/** Step of a scale (default 1; anything that is not a positive integer counts as 1). */
export function scaleStep(scale: Scale): number {
  const step = scale.step ?? 1;
  return Number.isInteger(step) && step >= 1 ? step : 1;
}

/** Every selectable value of a scale: min, min + step, … max. */
export function scaleValues(scale: Scale): number[] {
  const out: number[] = [];
  if (
    !Number.isInteger(scale.min) ||
    !Number.isInteger(scale.max) ||
    scale.max < scale.min
  ) {
    return out;
  }
  const step = scaleStep(scale);
  for (let v = scale.min; v <= scale.max; v += step) out.push(v);
  return out;
}

/** True when `value` is an integer on the scale's step grid. */
export function isOnScale(scale: Scale, value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value >= scale.min &&
    value <= scale.max &&
    (value - scale.min) % scaleStep(scale) === 0
  );
}

/** Text describing one scale point: its own label, else the min/max anchor. */
export function scalePointLabel(scale: Scale, value: number): string {
  const own = scale.labels?.[String(value)];
  if (own) return own;
  if (value === scale.min && scale.min_label) return scale.min_label;
  if (value === scale.max && scale.max_label) return scale.max_label;
  return "";
}

/** Option values of a choice question (single, multi, attention). */
export function optionValues(question: Question): string[] {
  return (question.options ?? []).map((o) => o.value);
}

/** Whether an answer holds anything (blank text and empty selections do not). */
export function isProvided(value: QuestionAnswer | undefined): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value === "string") return value.trim() !== "";
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "number") return Number.isFinite(value);
  return false;
}

function rangeText(
  min: number | null | undefined,
  max: number | null | undefined,
): string {
  if (min != null && max != null) return `a number from ${min} to ${max}`;
  if (min != null) return `a number of at least ${min}`;
  if (max != null) return `a number of at most ${max}`;
  return "a number";
}

/**
 * Problem with one answer, or "" when it is acceptable. An unanswered optional
 * question is acceptable; an unanswered required one is not.
 */
export function questionError(
  question: Question,
  value: QuestionAnswer | undefined,
): string {
  if (!isProvided(value)) {
    return question.required ? "This question is required." : "";
  }
  switch (question.kind) {
    case "text":
    case "textarea":
      if (typeof value !== "string") return "Enter text.";
      if (value.length > TEXT_ANSWER_MAX) {
        return `Keep the answer under ${TEXT_ANSWER_MAX} characters.`;
      }
      return "";
    case "single":
    case "attention":
      if (
        typeof value !== "string" ||
        !optionValues(question).includes(value)
      ) {
        return "Choose one of the options.";
      }
      return "";
    case "multi": {
      const allowed = optionValues(question);
      if (
        !Array.isArray(value) ||
        value.some((v) => typeof v !== "string" || !allowed.includes(v))
      ) {
        return "Choose from the listed options.";
      }
      return "";
    }
    case "scale": {
      if (!question.scale) {
        return typeof value === "number" && Number.isInteger(value)
          ? ""
          : "Choose a value.";
      }
      if (!isOnScale(question.scale, value)) {
        return `Choose a value from ${question.scale.min} to ${question.scale.max}.`;
      }
      return "";
    }
    case "number": {
      if (typeof value !== "number" || !Number.isFinite(value)) {
        return `Enter ${rangeText(question.min, question.max)}.`;
      }
      if (
        (question.min != null && value < question.min) ||
        (question.max != null && value > question.max)
      ) {
        return `Enter ${rangeText(question.min, question.max)}.`;
      }
      return "";
    }
    default:
      return "";
  }
}

/** A question counts as answered when it holds a value the server will accept. */
export function isAnswered(
  question: Question,
  value: QuestionAnswer | undefined,
): boolean {
  return isProvided(value) && questionError(question, value) === "";
}

/** Question id → problem, for every question that would fail on submit. */
export function validateQuestions(
  questions: Question[],
  answers: Record<string, QuestionAnswer | undefined>,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const q of questions) {
    const message = questionError(q, answers[q.id]);
    if (message) out[q.id] = message;
  }
  return out;
}

/** True when every required question is answered and no answer is invalid. */
export function questionsComplete(
  questions: Question[],
  answers: Record<string, QuestionAnswer | undefined>,
): boolean {
  return Object.keys(validateQuestions(questions, answers)).length === 0;
}

export interface AnswerTally {
  total: number;
  answered: number;
  required: number;
  requiredAnswered: number;
}

/** Counts for a "3 of 5 answered" line. */
export function answerTally(
  questions: Question[],
  answers: Record<string, QuestionAnswer | undefined>,
): AnswerTally {
  const tally: AnswerTally = {
    total: questions.length,
    answered: 0,
    required: 0,
    requiredAnswered: 0,
  };
  for (const q of questions) {
    const done = isAnswered(q, answers[q.id]);
    if (done) tally.answered += 1;
    if (q.required) {
      tally.required += 1;
      if (done) tally.requiredAnswered += 1;
    }
  }
  return tally;
}

/**
 * The payload to save: only the questions' own ids, blank text as null,
 * selections limited to strings. Unanswered questions are sent as null so a
 * cleared answer replaces the stored one.
 */
export function cleanAnswers(
  questions: Question[],
  answers: Record<string, QuestionAnswer | undefined>,
): Record<string, QuestionAnswer> {
  const out: Record<string, QuestionAnswer> = {};
  for (const q of questions) {
    const v = answers[q.id];
    if (v === undefined || v === null) {
      out[q.id] = null;
    } else if (typeof v === "string") {
      out[q.id] = v.trim() === "" ? null : v;
    } else if (Array.isArray(v)) {
      const picked = v.filter((x): x is string => typeof x === "string");
      out[q.id] = picked.length ? picked : null;
    } else if (typeof v === "number") {
      out[q.id] = Number.isFinite(v) ? v : null;
    } else {
      out[q.id] = null;
    }
  }
  return out;
}

/** Add or remove one option of a multi-choice answer, keeping the options' order. */
export function toggleOption(
  question: Question,
  current: QuestionAnswer | undefined,
  value: string,
  on: boolean,
): string[] {
  const selected = new Set(
    Array.isArray(current) ? current.filter((v) => typeof v === "string") : [],
  );
  if (on) selected.add(value);
  else selected.delete(value);
  const order = optionValues(question);
  const known = order.filter((v) => selected.has(v));
  const unknown = [...selected].filter((v) => !order.includes(v));
  return [...known, ...unknown];
}

export interface MappedErrors {
  /** question id → first server message for it */
  byQuestion: Record<string, string>;
  /** messages that name no question of this form */
  other: string[];
}

/**
 * Map a structured server error (`errors: [{path, message}]`) onto questions.
 * Paths name the question as their last matching segment, e.g. `answers.age`,
 * `pre_answers.age` or just `age`.
 */
export function mapQuestionErrors(
  errors: { path?: string; message?: string }[] | null | undefined,
  questions: Question[],
): MappedErrors {
  const ids = new Set(questions.map((q) => q.id));
  const mapped: MappedErrors = { byQuestion: {}, other: [] };
  for (const err of errors ?? []) {
    const message = typeof err?.message === "string" ? err.message : "";
    if (!message) continue;
    const segments = String(err.path ?? "")
      .split(/[.[\]]+/)
      .filter(Boolean);
    const id = [...segments].reverse().find((s) => ids.has(s));
    if (id) {
      if (!(id in mapped.byQuestion)) mapped.byQuestion[id] = message;
    } else {
      mapped.other.push(message);
    }
  }
  return mapped;
}

/** Structured `errors` array of a thrown API error body, or []. */
export function errorList(error: unknown): { path: string; message: string }[] {
  if (!error || typeof error !== "object") return [];
  const list = (error as { errors?: unknown }).errors;
  if (!Array.isArray(list)) return [];
  return list
    .filter((e): e is Record<string, unknown> => !!e && typeof e === "object")
    .map((e) => ({
      path: typeof e.path === "string" ? e.path : "",
      message: typeof e.message === "string" ? e.message : "",
    }))
    .filter((e) => e.message !== "");
}
