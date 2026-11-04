// Studies are called Experiments in the UI: the old builder link (e.g. `?model=`) opens the experiment builder.
import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

export const load: PageLoad = ({ url }) => {
  redirect(308, `/experiments/new${url.search}`);
};
