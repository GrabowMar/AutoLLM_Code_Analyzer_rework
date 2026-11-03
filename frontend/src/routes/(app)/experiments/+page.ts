// v2 Experiments became v3 Studies (spec 10.1): old links land on the studies list.
import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

export const load: PageLoad = ({ url }) => {
  redirect(308, `/studies${url.search}`);
};
