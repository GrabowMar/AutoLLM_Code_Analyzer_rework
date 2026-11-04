import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

/**
 * `/templates` (the v2 Generation Library) → `/library`, for one release. Old deep links map to the
 * matching kind: `?tab=specs` → tasks, `stacks` → stacks, `presets&kind=harness|sampling|budget` → the
 * preset kinds, `profiles|blocks` (scaffolding prompts, gone) → prompt templates.
 */
const TABS: Record<string, string> = {
  specs: "tasks",
  app: "tasks",
  stacks: "stacks",
  profiles: "prompts",
  blocks: "prompts",
};
const PRESETS: Record<string, string> = {
  harness: "harness-presets",
  sampling: "sampling-presets",
  budget: "resource-policies",
};

export const load: PageLoad = ({ url }) => {
  const tab = url.searchParams.get("tab") ?? "";
  const kind =
    tab === "presets"
      ? PRESETS[url.searchParams.get("kind") ?? "harness"]
      : TABS[tab];
  redirect(301, kind ? `/library/${kind}` : "/library");
};
