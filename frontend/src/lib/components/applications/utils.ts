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

export function copyText(text: string, label = "Copied"): void {
  navigator.clipboard.writeText(text);
  toast.success(label);
}
