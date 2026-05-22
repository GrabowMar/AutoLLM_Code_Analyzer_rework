import type { PageLoad } from "./$types";

export type SampleGeneratorMode = "custom" | "scaffolding" | "copilot";

const MODES: SampleGeneratorMode[] = ["custom", "scaffolding", "copilot"];

function parseMode(
  raw: string | null,
  modelSlug: string | null,
): SampleGeneratorMode {
  if (raw && MODES.includes(raw as SampleGeneratorMode)) {
    return raw as SampleGeneratorMode;
  }
  // Model-page links use ?model= only — open Custom tab for preselection
  if (modelSlug) return "custom";
  return "copilot";
}

export const load: PageLoad = ({ url }) => {
  const modelSlug = url.searchParams.get("model")?.trim() || null;
  const mode = parseMode(url.searchParams.get("mode"), modelSlug);
  const jobId = url.searchParams.get("job")?.trim() || null;

  return { mode, modelSlug, jobId };
};
