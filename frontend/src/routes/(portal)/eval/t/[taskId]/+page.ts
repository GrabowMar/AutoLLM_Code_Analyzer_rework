import type { PageLoad } from "./$types";

export const load: PageLoad = ({ params }) => ({ taskId: params.taskId });
