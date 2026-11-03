// The v2 sample generator is gone (spec 2.2): an agentic sample is a study now (one condition, one repeat
// is fine). `?model=<id>` pre-fills the model factor; `?mode=` is ignored (agent mode = app studies).
import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

export const load: PageLoad = ({ url }) => {
  const model = url.searchParams.get("model");
  redirect(
    308,
    model ? `/studies/new?model=${encodeURIComponent(model)}` : "/studies/new",
  );
};
