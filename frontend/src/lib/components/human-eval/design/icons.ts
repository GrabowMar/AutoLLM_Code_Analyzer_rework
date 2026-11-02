// Lucide icon per block type (design editor, block picker, summaries).
import type { Component } from "svelte";
import type { BlockType } from "$lib/api/types/human-eval";
import Star from "@lucide/svelte/icons/star";
import ListChecks from "@lucide/svelte/icons/list-checks";
import ShieldCheck from "@lucide/svelte/icons/shield-check";
import Route from "@lucide/svelte/icons/route";
import ClipboardList from "@lucide/svelte/icons/clipboard-list";
import Bug from "@lucide/svelte/icons/bug";
import GitCompareArrows from "@lucide/svelte/icons/git-compare-arrows";

export const BLOCK_ICONS: Record<BlockType, Component> = {
  rating: Star,
  requirements: ListChecks,
  security: ShieldCheck,
  scenarios: Route,
  questionnaire: ClipboardList,
  bugs: Bug,
  pairwise: GitCompareArrows,
};
