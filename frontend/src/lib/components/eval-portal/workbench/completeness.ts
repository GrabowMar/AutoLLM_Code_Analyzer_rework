// Client-side completeness of workbench blocks. Mirrors the server's strict
// submit validation (spec §5.8 and R1) so the block stepper can show check
// marks and a list of what is still missing. The server stays authoritative:
// Submit always asks it, and its `incomplete` errors are shown per block.
import type {
  Block,
  BlockAnswers,
  BlockContext,
  BugsAnswers,
  BugsBlock,
  FindingVerdictValue,
  Observation,
  PairwiseAnswers,
  PairwiseBlock,
  Question,
  QuestionAnswer,
  QuestionnaireAnswers,
  QuestionnaireBlock,
  RatingAnswers,
  RatingBlock,
  RequirementItem,
  RequirementsAnswers,
  RequirementsBlock,
  Scale,
  ScenariosAnswers,
  ScenariosBlock,
  SecurityAnswers,
  SecurityBlock,
} from "$lib/api/types/human-eval";
import { isValidPairwiseValue } from "./pairwise";

export type Slot = "A" | "B" | "single";

/** Longest free-text answer the server accepts. */
export const MAX_TEXT_ANSWER = 5000;

const REQUIREMENT_VERDICTS = new Set([
  "met",
  "partial",
  "not_met",
  "blocked",
  "cannot_test",
]);
const CHECKLIST_OUTCOMES = new Set([
  "vulnerable",
  "not_vulnerable",
  "not_tested",
]);
const SCENARIO_OUTCOMES = new Set([
  "success",
  "partial",
  "failure",
  "not_attempted",
]);
const FINDING_VERDICTS = new Set<FindingVerdictValue>(["tp", "fp", "unsure"]);

export interface BlockStatus {
  complete: boolean;
  /** Human-readable list of what is still missing (empty when complete). */
  issues: string[];
  /** Answered units (criteria, items, scenarios, questions, …) for a progress hint. */
  done: number;
  total: number;
}

export interface CompletenessInput {
  block: Block;
  answers: BlockAnswers | undefined;
  context: BlockContext | undefined;
  /** App slots of the assignment in display order. */
  slots: Slot[];
  /** Blinded label per slot, for messages ("App 07"). */
  slotLabels?: Partial<Record<string, string>>;
  observations: Observation[];
  findingVerdicts: Record<
    string,
    { verdict: FindingVerdictValue; note: string }
  >;
}

function isBlank(value: unknown): boolean {
  return typeof value !== "string" || value.trim().length === 0;
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

function record<T>(value: unknown): Record<string, T> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, T>)
    : {};
}

/** Values a scale offers: min, min+step, … max. */
export function scaleValues(scale: Scale): number[] {
  const step = scale.step && scale.step > 0 ? scale.step : 1;
  const values: number[] = [];
  for (let v = scale.min; v <= scale.max; v += step) values.push(v);
  return values;
}

/** True when `value` is an integer inside the scale and on its step grid. */
export function onScale(value: unknown, scale: Scale): value is number {
  if (typeof value !== "number" || !Number.isInteger(value)) return false;
  if (value < scale.min || value > scale.max) return false;
  const step = scale.step && scale.step > 0 ? scale.step : 1;
  return (value - scale.min) % step === 0;
}

/** True when a question has no answer at all ("" / [] / null count as none). */
export function isUnanswered(value: QuestionAnswer | undefined): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === "string") return value.trim().length === 0;
  if (Array.isArray(value)) return value.length === 0;
  return false;
}

/**
 * Why a question's answer is not acceptable, or null when it is. Optional
 * questions may stay unanswered, but an answer they do have must be valid.
 */
export function questionIssue(
  question: Question,
  value: QuestionAnswer | undefined,
): string | null {
  if (isUnanswered(value)) {
    return question.required ? "Answer this question" : null;
  }
  const options = new Set((question.options ?? []).map((o) => o.value));
  switch (question.kind) {
    case "text":
    case "textarea":
      if (typeof value !== "string") return "Enter text";
      if (value.length > MAX_TEXT_ANSWER) {
        return `Keep it under ${MAX_TEXT_ANSWER} characters`;
      }
      return null;
    case "single":
    case "attention":
      return typeof value === "string" && options.has(value)
        ? null
        : "Pick one of the options";
    case "multi":
      if (!Array.isArray(value)) return "Pick from the options";
      return value.every((v) => typeof v === "string" && options.has(v))
        ? null
        : "Pick from the options";
    case "scale":
      if (!question.scale) return null;
      return onScale(value, question.scale)
        ? null
        : `Pick a value from ${question.scale.min} to ${question.scale.max}`;
    case "number": {
      if (typeof value !== "number" || !Number.isFinite(value)) {
        return "Enter a number";
      }
      if (
        question.min !== null &&
        question.min !== undefined &&
        value < question.min
      ) {
        return `Enter at least ${question.min}`;
      }
      if (
        question.max !== null &&
        question.max !== undefined &&
        value > question.max
      ) {
        return `Enter at most ${question.max}`;
      }
      return null;
    }
    default:
      return null;
  }
}

/** Questions a questionnaire block asks: server-fixed for standard instruments. */
export function questionnaireQuestions(
  block: QuestionnaireBlock,
  context: BlockContext | undefined,
): Question[] {
  if (block.instrument === "custom") return block.questions ?? [];
  return context?.questions ?? block.questions ?? [];
}

/** Requirement items of the block across slots (deduplicated by id, first slot wins). */
export function requirementItems(
  context: BlockContext | undefined,
  slots: Slot[],
): RequirementItem[] {
  const bySlot = context?.requirements ?? {};
  const keys = [
    ...slots,
    ...Object.keys(bySlot).filter((k) => !slots.includes(k as Slot)),
  ];
  const seen = new Set<string>();
  const items: RequirementItem[] = [];
  for (const slot of keys) {
    for (const item of bySlot[slot] ?? []) {
      if (seen.has(item.id)) continue;
      seen.add(item.id);
      items.push(item);
    }
  }
  return items;
}

/** Ids of every finding the block asks the participant to review (all slots). */
export function reviewFindingIds(context: BlockContext | undefined): string[] {
  const ids: string[] = [];
  for (const list of Object.values(context?.findings ?? {})) {
    for (const finding of list ?? []) ids.push(finding.id);
  }
  return ids;
}

/** Does this requirement verdict need a note under the block's note rule? */
export function requirementNoteRequired(
  block: RequirementsBlock,
  verdict: string | undefined,
): boolean {
  return block.note === "required_unless_met" && !!verdict && verdict !== "met";
}

function ratingStatus(
  block: RatingBlock,
  raw: BlockAnswers | undefined,
): BlockStatus {
  const answers = record<unknown>(raw) as Partial<RatingAnswers>;
  const criteria = record<number | "na">(answers.criteria);
  const issues: string[] = [];
  let done = 0;
  for (const c of block.criteria) {
    const value = criteria[c.id];
    const valid = (value === "na" && c.allow_na) || onScale(value, c.scale);
    if (value !== undefined && valid) done += 1;
    if (value === undefined) {
      if (c.required) issues.push(`Rate “${c.label}”`);
    } else if (!valid) {
      issues.push(`“${c.label}” has a value outside the scale`);
    }
  }
  let total = block.criteria.length;
  if (block.comment === "required") {
    total += 1;
    if (isBlank(answers.comment)) issues.push("Write a comment");
    else done += 1;
  }
  return { complete: issues.length === 0, issues, done, total };
}

function requirementsStatus(
  block: RequirementsBlock,
  raw: BlockAnswers | undefined,
  context: BlockContext | undefined,
  slots: Slot[],
): BlockStatus {
  const answers = record<unknown>(raw) as Partial<RequirementsAnswers>;
  const given = record<{ verdict?: string; note?: string }>(answers.items);
  const items = requirementItems(context, slots);
  let missing = 0;
  let missingNotes = 0;
  let done = 0;
  for (const item of items) {
    const entry = given[item.id];
    const verdict = entry?.verdict;
    if (!verdict || !REQUIREMENT_VERDICTS.has(verdict)) {
      missing += 1;
      continue;
    }
    if (requirementNoteRequired(block, verdict) && isBlank(entry?.note)) {
      missingNotes += 1;
      continue;
    }
    done += 1;
  }
  const issues: string[] = [];
  if (missing)
    issues.push(`${plural(missing, "requirement")} without a verdict`);
  if (missingNotes)
    issues.push(
      `${plural(missingNotes, "verdict")} still need${missingNotes === 1 ? "s" : ""} a note`,
    );
  return { complete: issues.length === 0, issues, done, total: items.length };
}

function securityStatus(
  block: SecurityBlock,
  raw: BlockAnswers | undefined,
  context: BlockContext | undefined,
  verdicts: CompletenessInput["findingVerdicts"],
): BlockStatus {
  const answers = record<unknown>(raw) as Partial<SecurityAnswers>;
  const checklist = record<{ outcome?: string }>(answers.checklist);
  let missingItems = 0;
  for (const item of block.checklist) {
    const outcome = checklist[item.id]?.outcome;
    if (!outcome || !CHECKLIST_OUTCOMES.has(outcome)) missingItems += 1;
  }
  const findingIds = block.finding_review?.enabled
    ? reviewFindingIds(context)
    : [];
  let missingFindings = 0;
  for (const id of findingIds) {
    const verdict = verdicts[id]?.verdict;
    if (!verdict || !FINDING_VERDICTS.has(verdict)) missingFindings += 1;
  }
  const issues: string[] = [];
  if (missingItems)
    issues.push(`${plural(missingItems, "checklist item")} without an outcome`);
  if (missingFindings)
    issues.push(`${plural(missingFindings, "finding")} not reviewed`);
  const total = block.checklist.length + findingIds.length;
  return {
    complete: issues.length === 0,
    issues,
    done: total - missingItems - missingFindings,
    total,
  };
}

function scenariosStatus(
  block: ScenariosBlock,
  raw: BlockAnswers | undefined,
): BlockStatus {
  const answers = record<unknown>(raw) as Partial<ScenariosAnswers>;
  const given = record<{ outcome?: string; ease?: unknown; seconds?: unknown }>(
    answers.scenarios,
  );
  const issues: string[] = [];
  let done = 0;
  for (const scenario of block.scenarios) {
    const entry = given[scenario.id];
    const outcome = entry?.outcome;
    if (!outcome || !SCENARIO_OUTCOMES.has(outcome)) {
      issues.push(`Record the outcome of “${scenario.title}”`);
      continue;
    }
    const seconds = entry?.seconds;
    if (
      seconds !== undefined &&
      seconds !== null &&
      !(typeof seconds === "number" && seconds >= 0)
    ) {
      issues.push(`“${scenario.title}” has an invalid time`);
      continue;
    }
    if (block.ease_question && outcome !== "not_attempted") {
      const ease = entry?.ease;
      if (
        !(
          typeof ease === "number" &&
          Number.isInteger(ease) &&
          ease >= 1 &&
          ease <= 7
        )
      ) {
        issues.push(`Rate how easy “${scenario.title}” was`);
        continue;
      }
    }
    done += 1;
  }
  return {
    complete: issues.length === 0,
    issues,
    done,
    total: block.scenarios.length,
  };
}

function questionnaireStatus(
  block: QuestionnaireBlock,
  raw: BlockAnswers | undefined,
  context: BlockContext | undefined,
): BlockStatus {
  const answers = record<QuestionAnswer>(
    (record<unknown>(raw) as Partial<QuestionnaireAnswers>).answers,
  );
  const questions = questionnaireQuestions(block, context);
  let unanswered = 0;
  let invalid = 0;
  let done = 0;
  for (const q of questions) {
    const value = answers[q.id];
    const issue = questionIssue(q, value);
    if (!issue) {
      if (!isUnanswered(value)) done += 1;
      else if (!q.required) done += 1;
      continue;
    }
    if (isUnanswered(value)) unanswered += 1;
    else invalid += 1;
  }
  const issues: string[] = [];
  if (unanswered)
    issues.push(`${plural(unanswered, "required question")} unanswered`);
  if (invalid) issues.push(`${plural(invalid, "answer")} to fix`);
  return {
    complete: issues.length === 0,
    issues,
    done,
    total: questions.length,
  };
}

/** Bug reports of the assignment per slot (any bugs block). */
export function bugCounts(observations: Observation[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const o of observations) {
    if (o.kind !== "bug") continue;
    counts[o.app_slot] = (counts[o.app_slot] ?? 0) + 1;
  }
  return counts;
}

function bugsStatus(
  block: BugsBlock,
  raw: BlockAnswers | undefined,
  slots: Slot[],
  labels: Partial<Record<string, string>>,
  observations: Observation[],
): BlockStatus {
  const answers = record<unknown>(raw) as Partial<BugsAnswers>;
  const noneFound = record<boolean>(answers.none_found);
  const counts = bugCounts(observations);
  const issues: string[] = [];
  let done = 0;
  const min = Math.max(0, block.min_reports ?? 0);
  for (const slot of slots) {
    const n = counts[slot] ?? 0;
    const name =
      slots.length > 1 ? ` for ${labels[slot] || `app ${slot}`}` : "";
    if (min > 0) {
      if (n >= min) done += 1;
      else issues.push(`Report ${plural(min - n, "more bug")}${name}`);
    } else if (n >= 1 || noneFound[slot] === true) {
      done += 1;
    } else {
      issues.push(`Report a bug or confirm you found none${name}`);
    }
  }
  return { complete: issues.length === 0, issues, done, total: slots.length };
}

function pairwiseStatus(
  block: PairwiseBlock,
  raw: BlockAnswers | undefined,
): BlockStatus {
  const answers = record<unknown>(raw) as Partial<PairwiseAnswers>;
  const criteria = record<unknown>(answers.criteria);
  const issues: string[] = [];
  let done = 0;
  let total = block.criteria.length;
  for (const c of block.criteria) {
    if (isValidPairwiseValue(criteria[c.id], block.scale)) done += 1;
    else issues.push(`Compare the apps on “${c.label}”`);
  }
  if (block.overall) {
    total += 1;
    if (isValidPairwiseValue(answers.overall, block.scale)) done += 1;
    else issues.push("Give your overall preference");
  }
  if (block.rationale === "required") {
    total += 1;
    if (isBlank(answers.rationale)) issues.push("Explain your choice");
    else done += 1;
  }
  return { complete: issues.length === 0, issues, done, total };
}

/** Completeness of one block, mirroring the server's submit validation. */
export function blockCompleteness(input: CompletenessInput): BlockStatus {
  const { block, answers, context, slots, observations, findingVerdicts } =
    input;
  switch (block.type) {
    case "rating":
      return ratingStatus(block, answers);
    case "requirements":
      return requirementsStatus(block, answers, context, slots);
    case "security":
      return securityStatus(block, answers, context, findingVerdicts);
    case "scenarios":
      return scenariosStatus(block, answers);
    case "questionnaire":
      return questionnaireStatus(block, answers, context);
    case "bugs":
      return bugsStatus(
        block,
        answers,
        slots,
        input.slotLabels ?? {},
        observations,
      );
    case "pairwise":
      return pairwiseStatus(block, answers);
    default:
      return { complete: true, issues: [], done: 0, total: 0 };
  }
}

/** Starting answers for a block that has no saved response yet. */
export function emptyAnswers(block: Block): BlockAnswers {
  switch (block.type) {
    case "rating":
      return { criteria: {} };
    case "requirements":
      return { items: {} };
    case "security":
      return { checklist: {} };
    case "scenarios":
      return { scenarios: {} };
    case "questionnaire":
      return { answers: {} };
    case "bugs":
      return { none_found: {} };
    case "pairwise":
      return { criteria: {} };
    default:
      return {} as BlockAnswers;
  }
}

/**
 * Group a server `incomplete` error list (`[{path: "<block_id>.<field>", message}]`)
 * by block. Paths that name no known block land in `general`.
 */
export function errorsByBlock(
  errors: { path: string; message: string }[] | undefined,
  blockIds: string[],
): { byBlock: Record<string, string[]>; general: string[] } {
  const known = new Set(blockIds);
  const byBlock: Record<string, string[]> = {};
  const general: string[] = [];
  for (const error of errors ?? []) {
    const path = typeof error?.path === "string" ? error.path : "";
    const message = typeof error?.message === "string" ? error.message : "";
    if (!message) continue;
    const head = path.split(".")[0] ?? "";
    if (known.has(head)) {
      (byBlock[head] ??= []).push(message);
    } else {
      general.push(path ? `${path}: ${message}` : message);
    }
  }
  return { byBlock, general };
}
