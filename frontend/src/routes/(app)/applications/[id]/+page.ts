import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

// v3: a generation run is a Sample (spec 10.1); the old application page redirects for one release.
export const load: PageLoad = ({ params, url }) => {
  redirect(308, `/samples/${encodeURIComponent(params.id)}${url.search}`);
};
