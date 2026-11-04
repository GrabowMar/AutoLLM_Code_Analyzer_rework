// Any other v2 experiments path (e.g. /experiments/<id>/<old tab>) → the experiments list.
import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

export const load: PageLoad = () => {
  redirect(308, "/experiments");
};
