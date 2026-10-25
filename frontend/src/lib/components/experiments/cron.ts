/**
 * Schedule helpers: cron presets, a human description of common cron shapes,
 * schedule-action vocabulary and the browser's timezone. The server validates
 * every expression (`GET /generation/cron-preview/`); these only label it.
 */
import type { ScheduleAction, ScheduleSpec } from "$lib/api/types/experiments";

export interface CronPreset {
  value: string;
  label: string;
  cron: string;
}

export const CRON_PRESETS: CronPreset[] = [
  { value: "nightly", label: "Nightly 02:00", cron: "0 2 * * *" },
  { value: "weekly", label: "Weekly Mon 02:00", cron: "0 2 * * 1" },
];

export const CUSTOM_PRESET = "custom";

/** The preset whose expression matches `cron` (whitespace-insensitive), or "custom". */
export function presetForCron(cron: string): string {
  const normalized = normalizeCron(cron);
  return (
    CRON_PRESETS.find((p) => p.cron === normalized)?.value ?? CUSTOM_PRESET
  );
}

export function normalizeCron(cron: string): string {
  return cron.trim().split(/\s+/).join(" ");
}

const DAY_NAMES = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
];

function isInt(part: string, min: number, max: number): boolean {
  if (!/^\d+$/.test(part)) return false;
  const n = Number(part);
  return n >= min && n <= max;
}

function hhmm(hour: string, minute: string): string {
  return `${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`;
}

/**
 * "Every day at 02:00", "Mondays at 02:00", "Every 6 hours at :00",
 * "Weekdays at 09:30"; any other shape is shown as the expression itself.
 */
export function describeCron(cron: string): string {
  const parts = normalizeCron(cron).split(" ");
  if (parts.length !== 5) return cron.trim() || "—";
  const [minute, hour, dom, month, dow] = parts;
  if (month !== "*") return parts.join(" ");
  const minuteOk = isInt(minute, 0, 59);
  if (minuteOk && isInt(hour, 0, 23) && dom === "*") {
    const at = hhmm(hour, minute);
    if (dow === "*") return `Every day at ${at}`;
    if (dow === "1-5") return `Weekdays at ${at}`;
    if (dow === "0,6" || dow === "6,0") return `Weekends at ${at}`;
    if (isInt(dow, 0, 7)) return `${DAY_NAMES[Number(dow) % 7]}s at ${at}`;
  }
  if (minuteOk && isInt(hour, 0, 23) && isInt(dom, 1, 31) && dow === "*") {
    return `Monthly on day ${dom} at ${hhmm(hour, minute)}`;
  }
  const step = /^\*\/(\d+)$/.exec(hour);
  if (minuteOk && step && dom === "*" && dow === "*") {
    const n = Number(step[1]);
    return `Every ${n === 1 ? "hour" : `${n} hours`} at :${minute.padStart(2, "0")}`;
  }
  if (minuteOk && hour === "*" && dom === "*" && dow === "*") {
    return `Every hour at :${minute.padStart(2, "0")}`;
  }
  return parts.join(" ");
}

export const SCHEDULE_ACTION_LABELS: Record<ScheduleAction, string> = {
  top_up: "Top up failed cells",
  add_replicates: "Add replicates",
  clone_and_run: "Clone and run",
};

export const SCHEDULE_ACTION_DESCRIPTIONS: Record<ScheduleAction, string> = {
  top_up:
    "Re-runs failed and cancelled cells with their original seeds; a finished experiment reopens.",
  add_replicates: "Raises the repeat count and runs the new repeat blocks.",
  clone_and_run:
    "Clones the experiment (current model digests) and launches the clone: a fresh replication.",
};

/** The viewer's IANA timezone (falls back to UTC). */
export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function defaultSchedule(): ScheduleSpec {
  return {
    action: "top_up",
    cron: CRON_PRESETS[0].cron,
    timezone: browserTimezone(),
    params: {},
    skip_if_active: true,
    enabled: true,
  };
}

/** Client-side shape check before the server preview (5 space-separated fields). */
export function looksLikeCron(cron: string): boolean {
  const parts = normalizeCron(cron).split(" ");
  return (
    parts.length === 5 && parts.every((p) => /^[\d*,/\-A-Za-z?LW#]+$/.test(p))
  );
}
