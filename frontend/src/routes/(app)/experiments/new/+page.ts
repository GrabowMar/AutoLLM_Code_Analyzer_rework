// v2 "New experiment" → the v3 Study builder.
import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

export const load: PageLoad = () => {
  redirect(308, "/studies/new");
};
