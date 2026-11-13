// Studies are called Experiments in the UI: old links land on the experiments list (query kept).
import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

export const load: PageLoad = ({ url }) => {
  redirect(308, `/experiments${url.search}`);
};
