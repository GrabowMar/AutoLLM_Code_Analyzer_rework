// One-line summaries a folded composer step shows instead of its fields.
import type { LLMParams, RunPreset } from "$lib/api/generation";
import { cleanLLMParams } from "../llm-params";

export function presetLabel(
  preset: RunPreset | null,
  fallback: string,
): string {
  return preset ? `${preset.name} v${preset.version}` : fallback;
}

export function overridesLabel(n: number): string {
  return n === 0 ? "no overrides" : `${n} override${n === 1 ? "" : "s"}`;
}

export function llmOverrideCount(params: LLMParams): number {
  return Object.keys(cleanLLMParams(params)).length;
}
