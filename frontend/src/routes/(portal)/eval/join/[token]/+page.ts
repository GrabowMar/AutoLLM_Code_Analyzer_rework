import type { PageLoad } from "./$types";

export const load: PageLoad = ({ params }) => ({ token: params.token });
