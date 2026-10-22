import { toast } from "svelte-sonner";

export {
  formatNumber as fmt,
  formatDuration as fmtDur,
  formatDateTime as fmtDate,
  formatDateCompact as fmtDateCompact,
  formatCost as fmtCost,
} from "$lib/utils/formatters";

export const httpColors: Record<string, string> = {
  GET: "text-success-fg",
  POST: "text-info-fg",
  PUT: "text-warning-fg",
  DELETE: "text-danger-fg",
  PATCH: "text-primary",
};

export const segmentBorderColors: Record<string, string> = {
  metadata: "border-l-muted-foreground bg-muted/40",
  system: "border-l-info bg-info/5",
  user: "border-l-success bg-success/5",
  template: "border-l-primary bg-primary/5",
  requirements: "border-l-success bg-success/5",
  scaffolding: "border-l-warning bg-warning/5",
  default: "",
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
