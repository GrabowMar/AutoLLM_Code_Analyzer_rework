import { error } from "@sveltejs/kit";
import { isLibraryApiKind } from "$lib/api/v3/types";
import type { PageLoad } from "./$types";

/**
 * `/library/{kind}/{slug}` — URL contract: `?v=<n>` shows that version read-only (default: the latest),
 * `?edit=1` opens the editor on the latest version, `?diff=<a>..<b>` opens the diff of two versions,
 * `?tab=content|versions|usage|oracle`.
 */
export const load: PageLoad = ({ params, url }) => {
  if (!isLibraryApiKind(params.kind))
    error(404, `No library kind "${params.kind}"`);
  const v = Number(url.searchParams.get("v"));
  const diff = /^(\d+)\.\.(\d+)$/.exec(url.searchParams.get("diff") ?? "");
  const tab = url.searchParams.get("tab");
  return {
    kind: params.kind,
    slug: params.slug,
    version: Number.isInteger(v) && v > 0 ? v : null,
    edit: url.searchParams.get("edit") === "1",
    diff: diff
      ? ([Number(diff[1]), Number(diff[2])] as [number, number])
      : null,
    tab:
      tab === "versions" ||
      tab === "usage" ||
      tab === "oracle" ||
      tab === "diff"
        ? tab
        : "content",
  };
};
