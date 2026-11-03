// v2 batch pages: batches were merged into exploratory studies; their rows were wiped at the cut-over.
import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

export const load: PageLoad = () => {
  redirect(308, "/studies?kind=exploratory");
};
