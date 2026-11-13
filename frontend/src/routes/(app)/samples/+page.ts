import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

// Samples are listed under Applications (agentic app runs) and inside each study's Data section.
export const load: PageLoad = ({ url }) => {
  redirect(307, `/applications${url.search}`);
};
