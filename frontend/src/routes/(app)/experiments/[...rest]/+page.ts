// Any other v2 experiments path (e.g. /experiments/create, /experiments/<id>/<sub>) → the studies list.
import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

export const load: PageLoad = () => {
  redirect(308, "/studies");
};
