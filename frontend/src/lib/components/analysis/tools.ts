/**
 * The analyzer tool catalog (names, icons, modes, install state) for analysis
 * pages, loaded once per page load. `loadTools(true)` refetches (after the user
 * installed tools in another tab).
 */
import { getToolCatalog, type AnalyzerTool } from "$lib/api/analyzers";

let cache: Promise<AnalyzerTool[]> | null = null;

export function loadTools(force = false): Promise<AnalyzerTool[]> {
  if (!cache || force) {
    cache = getToolCatalog().catch((error) => {
      cache = null;
      throw error;
    });
  }
  return cache;
}

export function toolIndex(tools: AnalyzerTool[]): Map<string, AnalyzerTool> {
  return new Map(tools.map((t) => [t.slug, t]));
}

/** Category display order and labels (unknown categories follow, never hidden). */
export const CATEGORY_ORDER = [
  "security",
  "dependencies",
  "secrets",
  "lint",
  "performance",
  "ai",
];

export const CATEGORY_LABELS: Record<string, string> = {
  security: "Security",
  dependencies: "Dependencies",
  secrets: "Secrets",
  lint: "Lint & quality",
  performance: "Performance & complexity",
  ai: "AI review",
};

export function categoryLabel(category: string): string {
  return (
    CATEGORY_LABELS[category] ??
    category.charAt(0).toUpperCase() + category.slice(1)
  );
}

export function groupByCategory<T extends { category: string }>(
  items: T[],
): { category: string; items: T[] }[] {
  const extra = [...new Set(items.map((t) => t.category))].filter(
    (c) => !CATEGORY_ORDER.includes(c),
  );
  return [...CATEGORY_ORDER, ...extra]
    .map((category) => ({
      category,
      items: items.filter((t) => t.category === category),
    }))
    .filter((g) => g.items.length > 0);
}
