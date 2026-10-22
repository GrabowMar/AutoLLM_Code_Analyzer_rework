import type { PageLoad } from "./$types";

export type SampleGeneratorMode = "agent" | "custom" | "scaffolding";

const MODES: SampleGeneratorMode[] = ["agent", "custom", "scaffolding"];

function parseMode(
  raw: string | null,
  modelSlug: string | null,
): SampleGeneratorMode {
  if (raw && MODES.includes(raw as SampleGeneratorMode)) {
    return raw as SampleGeneratorMode;
  }
  // Model-page links use ?model= only — open Custom tab for preselection
  if (modelSlug) return "custom";
  // Agent mode is the primary generation method (old ?mode=copilot links land here too).
  return "agent";
}

export const load: PageLoad = ({ url }) => {
  const modelSlug = url.searchParams.get("model")?.trim() || null;
  const mode = parseMode(url.searchParams.get("mode"), modelSlug);
  const jobId = url.searchParams.get("job")?.trim() || null;

  return { mode, modelSlug, jobId };
};
