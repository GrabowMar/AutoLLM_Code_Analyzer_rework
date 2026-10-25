import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

// Retired automation pages (create, pipeline, run): every old link lands on
// the experiments list.
export const load: PageLoad = () => {
  redirect(308, "/experiments");
};
