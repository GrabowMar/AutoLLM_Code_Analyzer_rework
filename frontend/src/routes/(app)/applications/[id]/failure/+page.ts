import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

// v3: failures are explained on the sample's Overview (outcome taxonomy, termination, attempts).
export const load: PageLoad = ({ params }) => {
  redirect(308, `/samples/${encodeURIComponent(params.id)}`);
};
