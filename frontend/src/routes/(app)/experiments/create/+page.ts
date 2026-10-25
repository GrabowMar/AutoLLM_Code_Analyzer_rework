import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

// Renamed route: keep old links (and their query) working.
export const load: PageLoad = ({ url }) => {
  redirect(308, `/experiments/new${url.search}`);
};
