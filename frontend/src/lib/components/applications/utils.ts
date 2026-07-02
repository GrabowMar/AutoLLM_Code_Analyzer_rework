import { toast } from "svelte-sonner";

export { generationStatusColors as statusColors } from "$lib/constants/colors";
export {
  formatNumber as fmt,
  formatDuration as fmtDur,
  formatDateTime as fmtDate,
  formatDateCompact as fmtDateCompact,
  formatCost as fmtCost,
} from "$lib/utils/formatters";

export const modeColors: Record<string, string> = {
  custom: "bg-blue-500/15 text-blue-400 border-blue-500/30",
  scaffolding: "bg-purple-500/15 text-purple-400 border-purple-500/30",
  copilot: "bg-teal-500/15 text-teal-400 border-teal-500/30",
};

export const httpColors: Record<string, string> = {
  GET: "text-emerald-400",
  POST: "text-blue-400",
  PUT: "text-amber-400",
  DELETE: "text-red-400",
  PATCH: "text-purple-400",
};

export const segmentBorderColors: Record<string, string> = {
  metadata: "border-l-purple-400 bg-purple-500/5",
  system: "border-l-blue-400 bg-blue-500/5",
  user: "border-l-emerald-400 bg-emerald-500/5",
  template: "border-l-sky-400 bg-sky-500/5",
  requirements: "border-l-emerald-400 bg-emerald-500/5",
  scaffolding: "border-l-orange-400 bg-orange-500/5",
  default: "",
};

// Applications-list palette: lighter /10 fills with explicit dark: variants,
// unlike the shared /15 maps in $lib/constants/colors.
export const jobListStatusColors: Record<string, string> = {
  completed: "bg-emerald-500/10 text-emerald-500 border-emerald-500/30 dark:text-emerald-400 dark:border-emerald-500/20",
  failed: "bg-red-500/10 text-red-500 border-red-500/30 dark:text-red-400 dark:border-red-500/20",
  running: "bg-amber-500/10 text-amber-500 border-amber-500/30 dark:text-amber-400 dark:border-amber-500/20",
  pending: "bg-zinc-500/10 text-zinc-500 border-zinc-500/30 dark:text-zinc-400 dark:border-zinc-500/20",
  cancelled: "bg-zinc-500/10 text-zinc-500 border-zinc-500/30 dark:text-zinc-400 dark:border-zinc-500/20",
};

export const jobListContainerStatusColors: Record<string, string> = {
  pending: "bg-zinc-500/10 text-zinc-500 border-zinc-500/30 dark:text-zinc-400 dark:border-zinc-500/20",
  building: "bg-amber-500/10 text-amber-500 border-amber-500/30 dark:text-amber-400 dark:border-amber-500/20 animate-pulse",
  running: "bg-emerald-500/10 text-emerald-500 border-emerald-500/30 dark:text-emerald-400 dark:border-emerald-500/20",
  stopped: "bg-zinc-500/10 text-zinc-500 border-zinc-500/30 dark:text-zinc-400 dark:border-zinc-500/20",
  failed: "bg-red-500/10 text-red-500 border-red-500/30 dark:text-red-400 dark:border-red-500/20",
  removed: "bg-zinc-500/10 text-zinc-500 border-zinc-500/30 dark:text-zinc-400 dark:border-zinc-500/20",
};

export function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString();
}

export function jobDescription(job: {
  template_name?: string | null;
  scaffolding_name?: string | null;
}): string {
  if (job.template_name) return job.template_name;
  if (job.scaffolding_name) return job.scaffolding_name;
  return "Untitled Application";
}

export function copyText(text: string, label = "Copied"): void {
  navigator.clipboard.writeText(text);
  toast.success(label);
}

export function parsePromptSegments(
  text: string,
): { type: string; content: string }[] {
  const lines = text.split("\n");
  const segments: { type: string; content: string }[] = [];
  let currentType = "default";
  let currentLines: string[] = [];
  for (const line of lines) {
    let newType = currentType;
    if (
      line.startsWith("=== REQUEST METADATA ===") ||
      line.startsWith("=== META")
    )
      newType = "metadata";
    else if (line.startsWith("=== SYSTEM ===")) newType = "system";
    else if (line.startsWith("=== USER ===") || line.startsWith("## Output"))
      newType = "user";
    else if (
      line.startsWith("=== TEMPLATE ===") ||
      line.startsWith("## Mindset")
    )
      newType = "template";
    else if (line.startsWith("=== REQUIREMENTS ===")) newType = "requirements";
    else if (line.startsWith("=== SCAFFOLDING ===")) newType = "scaffolding";
    if (newType !== currentType && currentLines.length > 0) {
      segments.push({ type: currentType, content: currentLines.join("\n") });
      currentLines = [];
    }
    currentType = newType;
    currentLines.push(line);
  }
  if (currentLines.length > 0) {
    segments.push({ type: currentType, content: currentLines.join("\n") });
  }
  return segments;
}

export interface VirtualFile {
  name: string;
  code: string;
  lang: string;
}

export interface CodeFootprint {
  totalLines: number;
  totalChars: number;
  languages: Record<string, number>;
  fileCount: number;
  files: VirtualFile[];
  truncated: boolean;
}
