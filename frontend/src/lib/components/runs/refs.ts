/**
 * Library links for pinned refs in manifests and slot records: `slug@v#hash12` → `/library/<kind>/<slug>?v=<v>`
 * (WP-14's routes; `?v=` opens that exact, read-only version).
 */
import { parseRef, type LibraryApiKind } from "$lib/api/v3/types";

/** Manifest factor keys (and a few manifest sections) → library URL kind. */
export const FACTOR_LIBRARY_KIND: Record<string, LibraryApiKind> = {
  harness_preset: "harness-presets",
  stack_profile: "stacks",
  stack: "stacks",
  starter: "starters",
  style: "styles",
  guidance: "guidance",
  prompt_set: "prompt-sets",
  sampling_preset: "sampling-presets",
  sampling: "sampling-presets",
  resource_policy: "resource-policies",
  resources: "resource-policies",
  task: "tasks",
  eval_suite: "eval-suites",
  detector: "detectors",
  prompt: "prompts",
  template_ref: "prompts",
};

/** The library page of a pinned ref, or null when the text is not a ref or the kind is unknown. */
export function libraryHref(
  kind: string,
  ref: string | null | undefined,
): string | null {
  const apiKind = FACTOR_LIBRARY_KIND[kind];
  const parsed = parseRef(ref);
  if (!apiKind || !parsed) return null;
  return `/library/${apiKind}/${encodeURIComponent(parsed.slug)}?v=${parsed.version}`;
}

/** A JSON path (`["factors", "style", "ref"]`) → the library kind its ref belongs to, if any. */
export function kindForPath(path: (string | number)[]): string | null {
  for (let i = path.length - 1; i >= 0; i--) {
    const key = String(path[i]);
    if (key === "ref" || key === "preset_ref" || key === "template_ref") {
      if (key === "template_ref") return "prompt";
      const owner = path[i - 1];
      if (owner === "harness" && key === "preset_ref") return "harness_preset";
      if (owner === "eval_suite") return "eval_suite";
      if (owner !== undefined) return String(owner);
    }
  }
  return null;
}
