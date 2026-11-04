import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

// Retired v2 route: one app is generated on the "Generate app" page (spec 2.2); `?model=` pre-fills the model.
export const load: PageLoad = ({ url }) => {
  const model = url.searchParams.get("model");
  redirect(
    301,
    model ? `/generate?model=${encodeURIComponent(model)}` : "/generate",
  );
};
