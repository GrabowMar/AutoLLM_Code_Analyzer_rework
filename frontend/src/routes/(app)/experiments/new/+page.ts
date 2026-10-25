import type { PageLoad } from "./$types";

export const ssr = false;

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * URL contract: `?from=<id>` clones an experiment's design, `?edit=<id>`
 * edits a draft in place, `?models=<pk,pk,…>` preselects models (from an
 * endpoint page), `?from_batch=<uuid>` starts from a batch's grid.
 */
export const load: PageLoad = ({ url }) => {
  const param = (name: string) => url.searchParams.get(name)?.trim() || null;
  const uuid = (name: string) => {
    const value = param(name);
    return value && UUID_RE.test(value) ? value : null;
  };
  const models = (param("models") ?? "")
    .split(",")
    .map((s) => Number(s.trim()))
    .filter((n) => Number.isInteger(n) && n > 0);
  return {
    fromId: uuid("from"),
    editId: uuid("edit"),
    fromBatchId: uuid("from_batch"),
    modelIds: [...new Set(models)],
  };
};
