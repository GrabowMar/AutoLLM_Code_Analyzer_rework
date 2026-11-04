// The v2 sample generator is gone (spec 2.2): one app is generated on the "Generate app" page (a quick,
// one-cell study). `?model=<id>` pre-fills the model; `?mode=` is ignored.
import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

export const load: PageLoad = ({ url }) => {
  const model = url.searchParams.get("model");
  redirect(
    308,
    model ? `/generate?model=${encodeURIComponent(model)}` : "/generate",
  );
};
