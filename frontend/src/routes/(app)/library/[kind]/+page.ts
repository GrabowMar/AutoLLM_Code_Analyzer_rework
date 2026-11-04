import { error } from "@sveltejs/kit";
import { isLibraryApiKind } from "$lib/api/v3/types";
import type { PageLoad } from "./$types";

/** `/library/{kind}`: `kind` is the API name (`tasks`, `prompt-sets`, …); anything else is a 404. */
export const load: PageLoad = ({ params }) => {
  if (!isLibraryApiKind(params.kind))
    error(404, `No library kind "${params.kind}"`);
  return { kind: params.kind };
};
