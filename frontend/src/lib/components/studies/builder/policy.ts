/**
 * Budget-preset helpers of the builder. Library entities are immutable per (slug, version): an edit in
 * the builder never changes a preset in place. "Save as preset" (and the quick fixes "raise cap to $X" /
 * "raise call cap") create a new user-owned preset — or version+1 of the user's own preset — and the
 * design then pins its ref. Budget values never enter a prompt (they only reach the gateway and the
 * scheduler), so changing them does not change `prompt_hash`; class C/R knobs do change the treatment.
 */
import { createLibraryItem, createLibraryVersion } from "$lib/api/v3/library";
import type { LibraryItemOut, ResourcePolicyContent } from "$lib/api/v3/types";
import { parseRef } from "$lib/api/v3/types";
import {
  getKnob,
  invalidateLibrary,
  setKnob,
  slugify,
} from "$lib/components/library";

/** Dotted knob path → value patches applied on top of `content` (the `set_knob` quick fix). */
export function patchPolicy(
  content: ResourcePolicyContent,
  values: Record<string, unknown>,
): ResourcePolicyContent {
  let next = content;
  for (const [key, value] of Object.entries(values))
    next = setKnob(next, key, value as never);
  return next;
}

/** The `set_study_cap` quick fix: a fixed study cap of `cap` USD. */
export function withStudyCap(
  content: ResourcePolicyContent,
  cap: number,
): ResourcePolicyContent {
  return setKnob(content, "study.max_cost_usd", Math.round(cap * 100) / 100);
}

export function sameContent(
  a: ResourcePolicyContent | null | undefined,
  b: ResourcePolicyContent | null | undefined,
): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

/** Key numbers of a tier card: run cap, study cap, parallel runs, reasoning effort. */
export function keyNumbers(
  content: ResourcePolicyContent,
): { label: string; value: string }[] {
  const cap = content.study.max_cost_usd;
  const effort = getKnob(content, "reasoning.effort");
  return [
    { label: "per run", value: `$${content.run.max_cost_usd.toFixed(2)}` },
    {
      label: "study cap",
      value: cap === "auto" ? "auto" : `$${Number(cap).toFixed(2)}`,
    },
    {
      label: "parallel",
      value: String(content.concurrency.study_max_parallel),
    },
    {
      label: "reasoning",
      value:
        content.reasoning.mode === "effort"
          ? String(effort ?? "—")
          : content.reasoning.mode.replace(/_/g, " "),
    },
    {
      label: "pilot",
      value: content.pilot.enabled ? `${content.pilot.repeats}×` : "off",
    },
  ];
}

function suffix(): string {
  return Math.random().toString(36).slice(2, 8);
}

/**
 * Save edited content as a preset the design can pin. The user's own preset (`base`, not a system row)
 * gets version+1 (of its latest version `opts.latestVersion` when the design pins an older one, so the
 * save is not refused as `stale_base`); anything else becomes a new private preset named after `name` (or the base).
 */
export async function savePolicy(
  base: LibraryItemOut<"resource_policy"> | null,
  content: ResourcePolicyContent,
  opts: {
    name?: string;
    ownUserId?: number | null;
    latestVersion?: number | null;
  } = {},
): Promise<LibraryItemOut<"resource_policy">> {
  const own =
    base &&
    !base.is_system &&
    opts.ownUserId != null &&
    base.created_by?.id === opts.ownUserId;
  let saved: LibraryItemOut<"resource_policy">;
  if (own && base) {
    saved = await createLibraryVersion("resource_policy", base.slug, {
      base_version: Math.max(base.version, opts.latestVersion ?? 0),
      content,
    });
  } else {
    const name = (
      opts.name || (base ? `${base.name} (edited)` : "Custom budget")
    ).slice(0, 120);
    const slug = `${slugify(name).slice(0, 40) || "budget"}-${suffix()}`;
    saved = await createLibraryItem("resource_policy", {
      slug,
      name,
      description: base
        ? `Edited from ${base.pinned_ref} in the Study builder.`
        : "Created in the Study builder.",
      content,
    });
  }
  invalidateLibrary("resource_policy");
  return saved;
}

/** The slug of a ref (`frugal@1#…` → `frugal`). */
export function refSlug(ref: string): string {
  return parseRef(ref)?.slug ?? ref.split("@", 1)[0];
}

/**
 * The row a design's preset ref pins, from rows already at hand (the latest-per-slug cache plus versions
 * fetched on demand). A pinned `slug@v[#hash]` resolves only to that exact version (presets are immutable
 * per version, so the latest row of the slug is NOT a stand-in); only an unpinned ref (a bare slug) falls
 * back to the latest row. `undefined` means the pinned version is not loaded yet.
 */
export function findPinnedPolicy(
  ref: string,
  rows: Iterable<LibraryItemOut<"resource_policy">>,
): LibraryItemOut<"resource_policy"> | undefined {
  const parsed = parseRef(ref);
  const all = [...rows];
  if (!parsed) return all.find((r) => r.slug === refSlug(ref));
  return all.find(
    (r) => r.slug === parsed.slug && r.version === parsed.version,
  );
}

/**
 * The `study_cap` a launch sends. `null` (the server then applies the pinned preset's own
 * `study.max_cost_usd`) unless the researcher changed the cap from what the pinned preset says: a different
 * fixed amount is sent as that number, and "Auto" over a preset with a fixed cap is sent as `"auto"` (the
 * frozen plan's estimate cap). A typed cap therefore only overrides the pinned preset on purpose.
 */
export function launchStudyCap(
  policyCap: number | "auto" | null | undefined,
  mode: "auto" | "fixed",
  cap: number | null,
): number | "auto" | null {
  const presetAuto = policyCap == null || policyCap === "auto";
  if (mode === "auto") return presetAuto ? null : "auto";
  if (cap === null || !Number.isFinite(cap)) return null;
  if (!presetAuto && Math.abs(Number(policyCap) - cap) < 0.005) return null;
  return cap;
}
