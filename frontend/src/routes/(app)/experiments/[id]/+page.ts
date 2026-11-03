// v2 experiment pages → the study record (Experiment was renamed Study; old tab names still resolve).
import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

export const load: PageLoad = ({ params, url }) => {
  redirect(308, `/studies/${encodeURIComponent(params.id)}${url.search}`);
};
