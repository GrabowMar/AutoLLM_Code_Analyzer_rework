// Any deeper study path (e.g. /experiments/<id>/cells/<cell>) → the same path under /experiments (query kept).
import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

export const load: PageLoad = ({ params, url }) => {
  const rest = params.rest
    .split("/")
    .filter(Boolean)
    .map(encodeURIComponent)
    .join("/");
  redirect(308, `/experiments/${rest}${url.search}`);
};
