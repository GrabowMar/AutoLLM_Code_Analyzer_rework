// Studies are called Experiments in the UI: an old study link opens the same record (query kept, e.g. `?tab=`).
import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

export const load: PageLoad = ({ params, url }) => {
  redirect(308, `/experiments/${encodeURIComponent(params.id)}${url.search}`);
};
