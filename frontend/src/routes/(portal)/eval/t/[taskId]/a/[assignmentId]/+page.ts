// Participant workbench: client-rendered (cookie / account sessions, live
// heartbeats and an app iframe); the page loads its data in the browser.
import type { PageLoad } from "./$types";

export const ssr = false;

export const load: PageLoad = ({ params }) => ({
  taskId: params.taskId,
  assignmentId: params.assignmentId,
});
