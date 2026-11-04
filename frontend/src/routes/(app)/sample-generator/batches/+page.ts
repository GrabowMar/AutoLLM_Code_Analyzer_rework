// v2 batches were merged into exploratory experiments.
import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

export const load: PageLoad = () => {
  redirect(308, "/experiments?kind=exploratory");
};
