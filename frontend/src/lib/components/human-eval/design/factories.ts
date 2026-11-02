// Block and entry factories for the design editor, plus the block catalogue
// (labels, descriptions, which mode allows which block).

import {
  PAIRWISE_BLOCK_TYPES,
  SINGLE_BLOCK_TYPES,
} from "$lib/api/types/human-eval";
import type {
  AnalysisPlan,
  Block,
  BlockType,
  BugsBlock,
  ConsentConfig,
  Criterion,
  CustomRequirement,
  FindingReviewConfig,
  HumanEvalEnvironment,
  PairwiseBlock,
  PairwiseCriterion,
  Question,
  QuestionKind,
  QuestionOption,
  QuestionnaireBlock,
  RatingBlock,
  RequirementSource,
  RequirementsBlock,
  Scale,
  Scenario,
  ScenariosBlock,
  SecurityBlock,
  SecurityChecklistItem,
  TaskDesign,
  TaskMode,
} from "$lib/api/types/human-eval";
import { idFromLabel, uniqueId } from "./ids";

export interface BlockTypeInfo {
  label: string;
  /** Default title of a new block. */
  title: string;
  description: string;
}

export const BLOCK_TYPE_INFO: Record<BlockType, BlockTypeInfo> = {
  rating: {
    label: "Rating rubric",
    title: "Ratings",
    description:
      "Criteria rated on a numeric scale (e.g. usability, visual quality).",
  },
  requirements: {
    label: "Requirements checklist",
    title: "Requirements",
    description:
      "Each requirement from the app spec judged met / partial / not met.",
  },
  security: {
    label: "Security testing",
    title: "Security",
    description:
      "Guided checklist, vulnerability reports, TP/FP review of tool findings, HTTP console.",
  },
  scenarios: {
    label: "Usability scenarios",
    title: "Scenarios",
    description:
      "Tasks to complete in the app, with outcome, optional timer and ease rating.",
  },
  questionnaire: {
    label: "Questionnaire",
    title: "Questionnaire",
    description:
      "A standard instrument (SUS, UMUX-Lite, NASA-TLX) or custom questions.",
  },
  bugs: {
    label: "Bug log",
    title: "Bugs",
    description: "Free bug reports with steps, severity and screenshots.",
  },
  pairwise: {
    label: "Pairwise preference",
    title: "Comparison",
    description: "Which of two apps is better, per criterion and overall.",
  },
};

export const BLOCK_TYPE_ORDER: BlockType[] = [
  "rating",
  "requirements",
  "security",
  "scenarios",
  "questionnaire",
  "bugs",
  "pairwise",
];

export function allowedBlockTypes(mode: TaskMode): BlockType[] {
  const allowed =
    mode === "pairwise" ? PAIRWISE_BLOCK_TYPES : SINGLE_BLOCK_TYPES;
  return BLOCK_TYPE_ORDER.filter((t) => allowed.includes(t));
}

export function blockAllowed(mode: TaskMode, type: BlockType): boolean {
  return allowedBlockTypes(mode).includes(type);
}

export const REQUIREMENT_SOURCES: {
  value: RequirementSource;
  label: string;
}[] = [
  { value: "backend", label: "Backend requirements" },
  { value: "frontend", label: "Frontend requirements" },
  { value: "admin", label: "Admin requirements" },
  { value: "api", label: "API endpoints" },
  { value: "admin_api", label: "Admin API endpoints" },
];

export const QUESTION_KINDS: {
  value: QuestionKind;
  label: string;
  description: string;
}[] = [
  { value: "single", label: "Single choice", description: "Pick one option" },
  {
    value: "multi",
    label: "Multiple choice",
    description: "Pick any number of options",
  },
  {
    value: "scale",
    label: "Scale",
    description: "A number on a labelled scale",
  },
  {
    value: "number",
    label: "Number",
    description: "A number, optionally bounded",
  },
  { value: "text", label: "Short text", description: "One line" },
  { value: "textarea", label: "Long text", description: "A paragraph" },
  {
    value: "attention",
    label: "Attention check",
    description: "One option is the expected answer",
  },
];

/** Finding categories offered for TP/FP sampling (Finding.category values). */
export const FINDING_CATEGORIES: { value: string; label: string }[] = [
  { value: "security", label: "Security" },
  { value: "secrets", label: "Secrets" },
  { value: "quality", label: "Quality" },
  { value: "best_practice", label: "Best practice" },
  { value: "performance", label: "Performance" },
  { value: "dependency", label: "Dependency" },
];

export function defaultScale(): Scale {
  return {
    min: 1,
    max: 7,
    step: 1,
    min_label: "Very poor",
    max_label: "Excellent",
  };
}

function ids<T extends { id: string }>(list: readonly T[]): string[] {
  return list.map((x) => x.id);
}

export function newCriterion(
  label: string,
  existing: readonly Criterion[],
  scale?: Scale,
): Criterion {
  return {
    id: idFromLabel(label, ids(existing), "criterion"),
    label,
    help: "",
    scale: scale
      ? { ...scale, ...(scale.labels ? { labels: { ...scale.labels } } : {}) }
      : defaultScale(),
    required: true,
    allow_na: true,
  };
}

export function newPairwiseCriterion(
  label: string,
  existing: readonly PairwiseCriterion[],
): PairwiseCriterion {
  return {
    id: idFromLabel(label, ids(existing), "criterion"),
    label,
    help: "",
  };
}

export function newOption(
  label: string,
  existing: readonly QuestionOption[],
): QuestionOption {
  return {
    value: idFromLabel(
      label,
      existing.map((o) => o.value),
      "option",
    ),
    label,
  };
}

function optionsFrom(labels: string[]): QuestionOption[] {
  const out: QuestionOption[] = [];
  for (const label of labels) out.push(newOption(label, out));
  return out;
}

export function newQuestion(
  kind: QuestionKind,
  label: string,
  existing: readonly Question[],
): Question {
  const q: Question = {
    id: idFromLabel(label, ids(existing), "question"),
    kind,
    label,
    help: "",
    required: kind !== "textarea" && kind !== "text",
  };
  return applyQuestionKind(q, kind);
}

/** Switch a question's kind, adding/removing the fields that kind needs. */
export function applyQuestionKind(q: Question, kind: QuestionKind): Question {
  const next: Question = {
    id: q.id,
    kind,
    label: q.label,
    help: q.help ?? "",
    required: q.required,
  };
  if (kind === "single" || kind === "multi" || kind === "attention") {
    next.options = q.options?.length
      ? q.options.map((o) => ({ ...o }))
      : optionsFrom(["Option 1", "Option 2"]);
    if (kind === "attention") {
      next.required = true;
      const values = next.options.map((o) => o.value);
      next.expected =
        q.expected && values.includes(q.expected) ? q.expected : values[0];
    }
  }
  if (kind === "scale")
    next.scale = q.scale
      ? { ...q.scale }
      : { min: 1, max: 5, step: 1, min_label: "", max_label: "" };
  if (kind === "number") {
    next.min = q.min ?? null;
    next.max = q.max ?? null;
  }
  return next;
}

export function newScenario(
  title: string,
  existing: readonly Scenario[],
): Scenario {
  return {
    id: idFromLabel(title, ids(existing), "scenario"),
    title,
    description: "",
    success_criteria: "",
  };
}

export function newChecklistItem(
  label: string,
  existing: readonly SecurityChecklistItem[],
): SecurityChecklistItem {
  return {
    id: idFromLabel(label, ids(existing), "check"),
    label,
    help: "",
    category: "",
  };
}

export function newCustomRequirement(
  text: string,
  existing: readonly CustomRequirement[],
): CustomRequirement {
  return { id: idFromLabel(text, ids(existing), "requirement"), text };
}

export function defaultFindingReview(): FindingReviewConfig {
  return {
    enabled: false,
    max_findings: 10,
    categories: ["security", "secrets"],
    include_ai: false,
    include_dependency: false,
    include_dynamic: false,
  };
}

/** A fresh block of `type` with an id unique among `takenIds`. */
export function newBlock(
  type: BlockType,
  takenIds: Iterable<string>,
  environment?: HumanEvalEnvironment | null,
): Block {
  const info = BLOCK_TYPE_INFO[type];
  const id = idFromLabel(info.title, takenIds, type);
  switch (type) {
    case "rating": {
      const block: RatingBlock = {
        id,
        type,
        title: info.title,
        instructions: "",
        criteria: [],
        comment: "optional",
      };
      block.criteria.push(newCriterion("Overall quality", block.criteria));
      return block;
    }
    case "requirements": {
      const block: RequirementsBlock = {
        id,
        type,
        title: info.title,
        instructions: "",
        sources: ["backend", "frontend", "api"],
        custom: [],
        note: "optional",
      };
      return block;
    }
    case "security": {
      const block: SecurityBlock = {
        id,
        type,
        title: info.title,
        instructions: "",
        checklist: (environment?.security_checklist_default ?? []).map((c) => ({
          ...c,
        })),
        reports: true,
        finding_review: defaultFindingReview(),
        http_console: false,
      };
      return block;
    }
    case "scenarios": {
      const block: ScenariosBlock = {
        id,
        type,
        title: info.title,
        instructions: "",
        scenarios: [],
        timed: true,
        ease_question: true,
      };
      return block;
    }
    case "questionnaire": {
      const block: QuestionnaireBlock = {
        id,
        type,
        title: info.title,
        instructions: "",
        instrument: "sus",
        questions: [],
      };
      return block;
    }
    case "bugs": {
      const block: BugsBlock = {
        id,
        type,
        title: info.title,
        instructions: "",
        min_reports: 0,
      };
      return block;
    }
    case "pairwise": {
      const block: PairwiseBlock = {
        id,
        type,
        title: info.title,
        instructions: "",
        criteria: [],
        scale: "ab_tie",
        overall: true,
        rationale: "optional",
      };
      block.criteria.push(newPairwiseCriterion("Usability", block.criteria));
      block.criteria.push(
        newPairwiseCriterion("Functionality", block.criteria),
      );
      return block;
    }
  }
}

/** Deep copy with a new unique block id and a "(copy)" title; entry ids stay (they are block-scoped). */
export function duplicateBlock(
  block: Block,
  takenIds: Iterable<string>,
): Block {
  const copy = clonePlain(block);
  copy.id = uniqueId(block.id, takenIds);
  copy.title = `${block.title} (copy)`.slice(0, 300);
  return copy;
}

export function defaultAnalysisPlan(): AnalysisPlan {
  return {
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
  };
}

export function defaultConsent(): ConsentConfig {
  return { enabled: false, text: "" };
}

/**
 * A design with no blocks (consent and plan defaults from the environment when known).
 * The environment's default design pre-registers a primary on its own rating block
 * ("ratings" / composite); with no blocks that primary would dangle (and silently attach
 * to the next rating block added, whose id is also "ratings"), so it is dropped.
 */
export function emptyDesign(
  environment?: HumanEvalEnvironment | null,
): TaskDesign {
  const base = environment?.default_design;
  return {
    version: 1,
    blocks: [],
    analysis: base?.analysis
      ? { ...clonePlain(base.analysis), primary: null }
      : defaultAnalysisPlan(),
    consent: base?.consent ? { ...base.consent } : defaultConsent(),
    pre_questions: [],
    post_questions: [],
  };
}

/** Fill fields an older or partial design may lack, without touching what is there. */
export function normalizeDesign(
  design: Partial<TaskDesign> | null | undefined,
): TaskDesign {
  const d = design ?? {};
  const plan = d.analysis ?? defaultAnalysisPlan();
  return {
    version: 1,
    blocks: Array.isArray(d.blocks) ? d.blocks : [],
    analysis: {
      ...defaultAnalysisPlan(),
      ...plan,
      exclusion_rules: {
        ...defaultAnalysisPlan().exclusion_rules,
        ...(plan.exclusion_rules ?? {}),
      },
    },
    consent: d.consent ?? defaultConsent(),
    pre_questions: Array.isArray(d.pre_questions) ? d.pre_questions : [],
    post_questions: Array.isArray(d.post_questions) ? d.post_questions : [],
  };
}

/** A deep, non-reactive copy (presets, server payloads, reactive proxies). */
export function clonePlain<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

/** Blocks the mode does not allow (e.g. after switching single → pairwise). */
export function disallowedBlocks(
  mode: TaskMode,
  blocks: readonly Block[],
): Block[] {
  return blocks.filter((b) => !blockAllowed(mode, b.type));
}
