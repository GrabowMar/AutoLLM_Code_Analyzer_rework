// WP-17 gate (spec 12.3): nothing may import the v2 clients `lib/api/generation.ts` and
// `lib/api/experiments.ts`; wave 3 deletes them. Files that other work packages delete or
// re-point in wave 2 (the v2 generator/experiments/templates screens, the `client.ts` barrel)
// are listed in PENDING; the list must be empty before the deletion.
import { describe, expect, it } from "vitest";

const SOURCES = import.meta.glob("/src/**/*.{ts,svelte}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const LEGACY = new Set([
  "/src/lib/api/generation.ts",
  "/src/lib/api/experiments.ts",
]);

/** Owned by other WPs; deleted or re-pointed by them (CHANGES "WP-17 → ..."). */
const PENDING_PREFIXES = [
  "/src/lib/components/experiments/", // WP-15 removes it
  "/src/lib/components/generator/", // WP-15 removes it
  "/src/lib/components/templates/", // WP-14 removes it
  "/src/routes/(app)/experiments/", // WP-15 replaces it with a redirect
  "/src/routes/(app)/sample-generator/", // WP-15 replaces it with a redirect
  "/src/routes/(app)/templates/", // WP-14 replaces it with a redirect
];
const PENDING_FILES = new Set([
  "/src/lib/api/client.ts", // `export * from "./generation"`: lead (CHANGES)
  "/src/lib/api/types/experiments.ts", // v2 wire types of experiments.ts: lead (CHANGES)
]);

/** WP-17's files: never allowed to import the v2 clients. */
const WP17_FILES = [
  "/src/lib/components/human-eval/AppPoolPicker.svelte",
  "/src/lib/components/human-eval/detail/DesignTab.svelte",
  "/src/lib/components/human-eval/detail/AppsTab.svelte",
  "/src/lib/components/human-eval/detail/ResultsTab.svelte",
  "/src/routes/(app)/human-eval/new/+page.svelte",
  "/src/routes/(app)/human-eval/new/wizard.ts",
  "/src/lib/components/analysis/AppPicker.svelte",
  "/src/lib/components/analysis/RunOverview.svelte",
  "/src/routes/(app)/analysis/create/+page.svelte",
  "/src/routes/(app)/analysis/[id]/+page.svelte",
  "/src/routes/(app)/analysis/[id]/setup.ts",
  "/src/lib/components/models/ModelApplicationsSection.svelte",
  "/src/lib/components/models/ModelUsageSection.svelte",
  "/src/lib/components/models/ModelBehaviourPanel.svelte",
  "/src/routes/(app)/reports/create/+page.svelte",
  "/src/lib/constants/colors.ts",
  "/src/lib/api/telemetry.ts",
  "/src/lib/api/job-insights.ts",
  "/src/lib/api/trajectory.ts",
  "/src/lib/api/variability.ts",
  "/src/lib/api/execution.ts",
  "/src/lib/api/reports.ts",
  "/src/lib/api/human-eval.ts",
  "/src/lib/api/runs.ts",
  "/src/lib/api/types/events.ts",
  "/src/lib/api/types/execution.ts",
  "/src/lib/api/types/human-eval.ts",
];

const SPECIFIER = /(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s+)["']([^"']+)["']/g;

function dirname(path: string): string {
  return path.slice(0, path.lastIndexOf("/"));
}

/** `/src/...` path of an import specifier, or null for a package import. */
export function resolveSpecifier(file: string, spec: string): string | null {
  let base: string;
  if (spec.startsWith("$lib/")) base = `/src/lib/${spec.slice(5)}`;
  else if (spec.startsWith(".")) {
    const parts = dirname(file).split("/");
    for (const seg of spec.split("/")) {
      if (seg === "..") parts.pop();
      else if (seg !== ".") parts.push(seg);
    }
    base = parts.join("/");
  } else return null;
  return base.replace(/\.(ts|js)$/, "");
}

/** The legacy modules *source* (a file at *file*) imports or re-exports. */
export function legacyImports(file: string, source: string): string[] {
  const hits = new Set<string>();
  for (const match of source.matchAll(SPECIFIER)) {
    const target = resolveSpecifier(file, match[1]);
    if (target && LEGACY.has(`${target}.ts`)) hits.add(`${target}.ts`);
  }
  return [...hits];
}

function pending(file: string): boolean {
  return (
    PENDING_FILES.has(file) || PENDING_PREFIXES.some((p) => file.startsWith(p))
  );
}

describe("legacy client imports", () => {
  it("resolves aliases and relative specifiers", () => {
    expect(
      legacyImports(
        "/src/lib/x.ts",
        'import { a } from "$lib/api/generation";',
      ),
    ).toEqual(["/src/lib/api/generation.ts"]);
    expect(
      legacyImports("/src/lib/api/client.ts", 'export * from "./experiments";'),
    ).toEqual(["/src/lib/api/experiments.ts"]);
    expect(
      legacyImports(
        "/src/lib/api/v3/x.ts",
        "const m = await import('../generation.ts');",
      ),
    ).toEqual(["/src/lib/api/generation.ts"]);
    expect(
      legacyImports(
        "/src/lib/api/types/x.ts",
        'import type { A } from "./experiments";',
      ),
    ).toEqual([]);
    expect(
      legacyImports(
        "/src/lib/x.ts",
        'import { a } from "$lib/api/v3/studies";',
      ),
    ).toEqual([]);
  });

  it("finds no WP-17 file importing the v2 clients", () => {
    const missing = WP17_FILES.filter((f) => !(f in SOURCES));
    expect(missing).toEqual([]);
    const offenders = WP17_FILES.filter(
      (f) => legacyImports(f, SOURCES[f]).length > 0,
    );
    expect(offenders).toEqual([]);
  });

  it("finds no other importer outside the files pending deletion or re-pointing", () => {
    const offenders = Object.entries(SOURCES)
      .filter(
        ([file]) =>
          !LEGACY.has(file) && !file.endsWith("/legacy-imports.test.ts"),
      )
      .filter(
        ([file, source]) =>
          legacyImports(file, source).length > 0 && !pending(file),
      )
      .map(([file]) => file)
      .sort();
    expect(offenders).toEqual([]);
  });
});
