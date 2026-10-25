import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

// Automation is now Schedules and Post-run on each experiment (Experiments →
// an experiment → Automation tab); the hub and its pipelines are retired.
export const load: PageLoad = () => {
  redirect(308, "/experiments");
};
