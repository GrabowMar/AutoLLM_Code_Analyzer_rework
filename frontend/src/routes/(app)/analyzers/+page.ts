import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

// Retired route: tools are baked into the analyzer image now (no per-user
// workspace or installs); keep old links working.
export const load: PageLoad = ({ url }) => {
  redirect(301, `/analysis/tools${url.search}`);
};
