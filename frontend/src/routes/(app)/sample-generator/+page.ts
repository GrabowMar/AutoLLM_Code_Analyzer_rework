import { redirect } from "@sveltejs/kit";
import type { GenerationMode } from "$lib/api/generation";
import type { PageLoad } from "./$types";

const MODES: GenerationMode[] = ["agent", "scaffolding", "custom"];
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * URL contract: `?mode=agent|scaffolding|custom` (default agent; unknown
 * values such as the retired `copilot` land on agent), `?model=<slug>` and
 * `?app=<slug>` preselect, `?from_job=<uuid>` starts from a job's settings.
 * Legacy `?job=<uuid>` links open the job page.
 */
export const load: PageLoad = ({ url }) => {
  const param = (name: string) => url.searchParams.get(name)?.trim() || null;

  const legacyJob = param("job");
  if (legacyJob && UUID_RE.test(legacyJob)) {
    redirect(301, `/applications/${legacyJob}`);
  }

  const rawMode = param("mode");
  const mode: GenerationMode = MODES.includes(rawMode as GenerationMode)
    ? (rawMode as GenerationMode)
    : "agent";
  const fromJob = param("from_job");

  return {
    mode,
    modelSlug: param("model"),
    appSlug: param("app"),
    fromJobId: fromJob && UUID_RE.test(fromJob) ? fromJob : null,
    invalidFromJob: fromJob && !UUID_RE.test(fromJob) ? fromJob : null,
  };
};
