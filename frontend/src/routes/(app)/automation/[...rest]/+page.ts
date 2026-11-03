import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

// Retired v2 automation pages (create, pipeline, run): every old link lands on the studies list.
export const load: PageLoad = () => {
  redirect(308, "/studies");
};
