/**
 * Launch-time helpers of the builder: whether the post-run analysis configured in Measurement must be
 * attached (it used to need a separate "Attach" click, and was silently dropped without it), compared by
 * the server's normalized shape (`study/automation.py:normalize_post_run_config`) so a resumed draft
 * never gets the same post-run twice.
 */
import type { PostRunOut } from "$lib/api/v3/types";

export interface PostRunConfigLike {
  tool_slugs?: unknown;
  only_passing?: unknown;
  report?: unknown;
}

/** Stable key of an analysis post-run config (tool order and duplicates do not matter). */
export function postRunKey(
  config: PostRunConfigLike | null | undefined,
): string {
  const tools = Array.isArray(config?.tool_slugs)
    ? [
        ...new Set(
          config.tool_slugs.filter(
            (t): t is string => typeof t === "string" && !!t,
          ),
        ),
      ].sort()
    : [];
  return JSON.stringify({
    tools,
    only_passing: !!config?.only_passing,
    report: config?.report === undefined ? true : !!config.report,
  });
}

/** Attach at launch: something to run (analyzers or the report) and no identical completed-trigger post-run yet. */
export function shouldAttachPostRun(
  config: PostRunConfigLike,
  existing: readonly PostRunOut[],
): boolean {
  const tools = Array.isArray(config.tool_slugs) ? config.tool_slugs.length : 0;
  if (tools === 0 && !config.report) return false;
  const key = postRunKey(config);
  return !existing.some(
    (p) =>
      p.kind === "analysis" &&
      p.trigger === "completed" &&
      postRunKey(p.config) === key,
  );
}
