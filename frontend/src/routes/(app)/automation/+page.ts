import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

// Automation is schedules and post-runs on each study (Studies → a study → Operations); the v2 hub is retired.
export const load: PageLoad = () => {
  redirect(308, "/studies");
};
