import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

// Retired route: keep old links (and their query) working.
export const load: PageLoad = ({ url }) => {
  redirect(301, `/sample-generator${url.search || "?mode=agent"}`);
};
