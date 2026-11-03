import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

// Retired v2 route: an agentic sample is a study now (spec 2.2); `?model=` pre-fills the model factor.
export const load: PageLoad = ({ url }) => {
  const model = url.searchParams.get("model");
  redirect(
    301,
    model ? `/studies/new?model=${encodeURIComponent(model)}` : "/studies/new",
  );
};
