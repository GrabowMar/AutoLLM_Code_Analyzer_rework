import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

// The Slots nav entry opens the picker.
export const load: PageLoad = ({ url }) => {
  redirect(307, `/slots/new${url.search}`);
};
