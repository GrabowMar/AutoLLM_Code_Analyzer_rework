/**
 * Display helpers for the human-evaluation researcher pages: the task page's
 * tabs, lifecycle / hosting / data-quality vocabulary, invite and access-code
 * text (CSV, copy, print), answer formatting for the read-only responses view,
 * and the arithmetic of the results figures (axis domains, ticks, positions).
 * Pure functions only (unit-tested in format.test.ts).
 *
 * Validity rules carried here: an automated finding is only ever labelled with
 * its tool's own `tool_severity` ("unrated" when blank, never the normalized
 * severity); an observation's severity is the tester's own judgement; untrusted
 * strings become links only when they are absolute http(s) URLs.
 */
import type {
  AppHostingAction,
  BlockType,
  Ci,
  CreatedCode,
  GroupBy,
  HostingState,
  PairwiseScale,
  PairwiseValue,
  Question,
  QuestionAnswer,
  ResearcherHostingInfo,
  Scale,
  StandardInstrument,
  TaskAction,
  TaskMode,
  TaskStatus,
  ExportKind,
} from "$lib/api/types/human-eval";
import type { Tone } from "$lib/constants/colors";
import {
  editableAfterFreezeText,
  qualityLockText,
  type DesignLike,
} from "./freeze";

// ── tabs ───────────────────────────────────────────────────────────────

export type TaskTab =
  | "overview"
  | "design"
  | "apps"
  | "participants"
  | "responses"
  | "results"
  | "export";

export const TASK_TABS: { value: TaskTab; label: string }[] = [
  { value: "overview", label: "Overview" },
  { value: "design", label: "Design" },
  { value: "apps", label: "Apps" },
  { value: "participants", label: "Participants" },
  { value: "responses", label: "Responses" },
  { value: "results", label: "Results" },
  { value: "export", label: "Export" },
];

const TAB_ALIASES: Record<string, TaskTab> = {
  summary: "overview",
  pool: "apps",
  hosting: "apps",
  settings: "design",
  people: "participants",
  assignments: "responses",
  data: "export",
};

/** `?tab=` → a tab; unknown or empty values → null (the page's default). */
export function resolveTaskTab(raw: string | null | undefined): TaskTab | null {
  if (!raw) return null;
  const key = raw.trim().toLowerCase();
  if (TASK_TABS.some((t) => t.value === key)) return key as TaskTab;
  return TAB_ALIASES[key] ?? null;
}

// ── small formats ──────────────────────────────────────────────────────

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
}

/** snake_case code → "Snake case". */
export function humanize(code: string | null | undefined): string {
  const text = (code ?? "").replace(/_/g, " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
}

/** 0..1 → "87%" (digits after the point); null → "—". */
export function formatShare(
  value: number | null | undefined,
  digits = 0,
): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

/** A score on its own scale: 4.25 → "4.25"; null → "—". */
export function formatScore(
  value: number | null | undefined,
  digits = 2,
): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toFixed(digits);
}

/** Signed difference: 0.5 → "+0.50", −0.25 → "−0.25". */
export function formatDiff(
  value: number | null | undefined,
  format: (v: number) => string = (v) => v.toFixed(2),
): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "±";
  return `${sign}${format(Math.abs(value))}`;
}

/** "[3.10, 4.20]" with the given value format; null / open bounds → "". */
export function formatCi(
  ci: Ci | null | undefined,
  format: (v: number) => string = (v) => v.toFixed(2),
): string {
  if (!ci || ci.low == null || ci.high == null) return "";
  if (!Number.isFinite(ci.low) || !Number.isFinite(ci.high)) return "";
  return `[${format(ci.low)}, ${format(ci.high)}]`;
}

/** p-value → "0.003" / "<0.001"; null → "—". */
export function formatP(p: number | null | undefined): string {
  if (p == null || !Number.isFinite(p)) return "—";
  if (p < 0.001) return "<0.001";
  return p.toFixed(3);
}

/** Correlation / agreement coefficient → "0.62" / "−0.10". */
export function formatCoef(
  value: number | null | undefined,
  digits = 2,
): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const text = Math.abs(value).toFixed(digits);
  return value < 0 ? `−${text}` : text;
}

/** Seconds → "42 s" / "3 min 12 s" / "1 h 05 min"; null → "—". */
export function formatSeconds(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) {
    const m = Math.floor(s / 60);
    const rest = s % 60;
    return rest ? `${m} min ${rest} s` : `${m} min`;
  }
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${h} h ${String(m).padStart(2, "0")} min`;
}

/** First characters of a hash for display ("3f9a1c2e"). */
export function shortHash(hash: string | null | undefined, length = 8): string {
  return (hash ?? "").slice(0, length);
}

/** File-name-safe slug of a task title ("My Study (2)" → "my-study-2"). */
export function slugify(text: string): string {
  return (
    text
      .toLowerCase()
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60)
      .replace(/-+$/g, "") || "task"
  );
}

/** Byte count → "512 B" / "14.2 KB" / "3.1 MB". */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes)) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ── task lifecycle ─────────────────────────────────────────────────────

export const MODE_LABELS: Record<TaskMode, string> = {
  single: "Single app",
  pairwise: "Pairwise (A/B)",
};

export const TASK_STATUS_HELP: Record<TaskStatus, string> = {
  draft: "Being designed: participants can't join yet.",
  open: "Collecting data: participants can join and work.",
  paused: "Paused: nobody can start or change work until you resume.",
  closed: "Closed: no more data is collected.",
  archived: "Archived: kept for the record, hidden from the active list.",
};

/** The button label of a status action (open from paused reads "Resume", reopen from archived "Unarchive"). */
export function taskActionLabel(
  action: TaskAction,
  status: TaskStatus,
): string {
  switch (action) {
    case "open":
      return status === "paused" ? "Resume" : "Open task";
    case "pause":
      return "Pause";
    case "close":
      return "Close";
    case "reopen":
      return status === "archived" ? "Unarchive" : "Reopen";
    case "archive":
      return "Archive";
  }
}

/** Order of status actions in the header (primary action last). */
const ACTION_ORDER: TaskAction[] = [
  "archive",
  "reopen",
  "close",
  "pause",
  "open",
];

/** Header order of the allowed actions; the primary one (see primaryAction) goes last. */
export function orderedActions(
  actions: TaskAction[],
  status?: TaskStatus,
): TaskAction[] {
  const sorted = [...actions].sort(
    (a, b) => ACTION_ORDER.indexOf(a) - ACTION_ORDER.indexOf(b),
  );
  const primary = status ? primaryAction(actions, status) : null;
  return primary ? [...sorted.filter((a) => a !== primary), primary] : sorted;
}

/** Which header action is the primary (filled) button for this status. */
export function primaryAction(
  actions: TaskAction[],
  status: TaskStatus,
): TaskAction | null {
  const prefer: TaskAction[] =
    status === "draft" || status === "paused"
      ? ["open"]
      : status === "open"
        ? ["close"]
        : status === "closed"
          ? ["archive", "reopen"]
          : ["reopen"];
  return prefer.find((a) => actions.includes(a)) ?? null;
}

export interface ConfirmCopy {
  title: string;
  description: string;
  label: string;
  destructive?: boolean;
}

/**
 * ConfirmDialog copy for a status action; null when the action runs without asking (pause,
 * resume, unarchive). `task.design` is the saved design an open freezes (it decides whether
 * quality stays editable); `unsavedFreezeEdits` describes Design-tab edits that are not saved
 * (describeFreezeLosses), which a first open would discard.
 */
export function taskActionConfirm(
  action: TaskAction,
  task: { status: TaskStatus; frozen_at: string | null; design?: DesignLike },
  opts: { unsavedFreezeEdits?: string } = {},
): ConfirmCopy | null {
  switch (action) {
    case "open":
      if (task.status === "paused") return null;
      if (task.frozen_at) {
        return {
          title: "Open this task?",
          description: "Participants can join and work again.",
          label: "Open task",
        };
      }
      if (opts.unsavedFreezeEdits) {
        return {
          title: "Open without your unsaved edits?",
          description: `The Design tab has unsaved changes to ${opts.unsavedFreezeEdits}. Opening freezes the saved task, so those changes are discarded. Cancel and save them first to include them.`,
          label: "Open with the saved design",
          destructive: true,
        };
      }
      return {
        title: "Open this task?",
        description: [
          "Opening freezes the design and every setting that affects what is measured (the design hash), shuffles the blinded app labels, builds the evaluation items and draws the finding samples. Participants can join right away.",
          `Title, instructions, ${editableAfterFreezeText(task.design)} stay editable; later instruction edits are recorded in the protocol log.`,
          qualityLockText(task.design),
        ]
          .filter(Boolean)
          .join(" "),
        label: "Open and freeze",
      };
    case "pause":
      return null;
    case "close":
      return {
        title: "Close this task?",
        description:
          "Participants can no longer start or change work. Apps the task started are stopped and every app is unpinned. Results become final; reopening later makes them exploratory.",
        label: "Close task",
      };
    case "reopen":
      if (task.status === "archived") return null;
      return {
        title: "Reopen this task?",
        description:
          "Participants can work again. The results will list “reopened after close” as a deviation: data collected after reopening is not confirmatory.",
        label: "Reopen",
      };
    case "archive":
      return {
        title: "Archive this task?",
        description:
          "It leaves the active list. Participants, responses and results are kept.",
        label: "Archive",
      };
  }
}

/** Non-blocking validity warnings of TaskDetail.open_warnings. */
export const OPEN_WARNING_TEXT: Record<string, string> = {
  shared_app_state:
    "Raters share app containers without a reset: later raters see the data earlier raters created or broke. Use exclusive isolation with a per-assignment reset (frozen at open) for security or HTTP-console tasks.",
  mixed_spec_versions:
    "One app spec appears in several versions. Apps built from different versions are never paired and get different requirement ids.",
  model_name_in_source:
    "Source code or finding snippets shown to participants mention a model name, id or provider: blinding may break.",
  layout_reveals_condition:
    "Source code is shown and the pool mixes generation modes or harnesses: the file layout may reveal the condition.",
};

/** HumanEvalEnvironment.warnings. */
export const ENVIRONMENT_WARNING_TEXT: Record<string, string> = {
  apps_not_reachable:
    "Participants can't open apps in the current routing mode. Subdomain (or Traefik) routing is required to show apps in the portal.",
  shared_client_ip:
    "No trusted proxy is configured (TRUSTED_PROXY_CIDRS is empty): every visitor shares one client address, so join and resume throttles act globally.",
};

/** Text for a warning / blocker string: a known code's explanation, a humanized code, or the server's own sentence. */
export function codeText(
  value: string,
  known: Record<string, string> = {},
): string {
  const key = value.trim();
  if (known[key]) return known[key];
  if (/^[a-z0-9_]+$/.test(key)) return humanize(key);
  return key;
}

// ── statuses and labels ────────────────────────────────────────────────

export const HOSTING_STATE_LABELS: Record<HostingState, string> = {
  idle: "Stopped",
  queued: "Queued",
  building: "Building",
  starting: "Starting",
  running: "Running",
  stopping: "Stopping",
  failed: "Failed",
  unavailable: "Not reachable",
};

/** StatusBadge `tones` for hosting states: a running app is the healthy steady state (like containers). */
export const hostingTones: Partial<Record<string, Tone>> = {
  running: "success",
  idle: "neutral",
  queued: "neutral",
  building: "info",
  starting: "info",
  stopping: "info",
  failed: "danger",
  unavailable: "warning",
};

const TRANSITIONAL: ReadonlySet<HostingState> = new Set<HostingState>([
  "queued",
  "building",
  "starting",
  "stopping",
]);

/** A hosting state that changes on its own (poll while any app is in one). */
export function isTransitional(
  state: HostingState | string | null | undefined,
): boolean {
  return TRANSITIONAL.has(state as HostingState);
}

export function anyTransitional(
  infos: readonly { state: HostingState | string }[],
): boolean {
  return infos.some((i) => isTransitional(i.state));
}

/** Is the container itself running (also when participants can't reach it)? */
export function hostingIsRunning(
  info: Pick<ResearcherHostingInfo, "state" | "container_status">,
): boolean {
  if (info.state === "running") return true;
  return info.state === "unavailable" && info.container_status === "running";
}

/**
 * Researcher actions offered for one app's hosting state, in display order.
 * start: stopped / failed / not reachable without a running container;
 * stop: only where the backend acts on it — a running container (also mid-reset, which
 * reports "starting" over a running container) or a place in the queue. A container still
 * coming up (pending, or a start in flight) can't be stopped yet: the backend would answer
 * 200 without stopping anything, and the app would come up anyway.
 * pin / unpin by `pinned`; rebuild: whenever nothing is in flight.
 */
export function hostingActions(
  info: Pick<ResearcherHostingInfo, "state" | "container_status" | "pinned">,
): AppHostingAction[] {
  const actions: AppHostingAction[] = [];
  const running = hostingIsRunning(info);
  const resetting =
    info.state === "starting" && info.container_status === "running";
  if (running || resetting || info.state === "queued") actions.push("stop");
  else if (
    info.state === "idle" ||
    info.state === "failed" ||
    info.state === "unavailable"
  )
    actions.push("start");
  actions.push(info.pinned ? "unpin" : "pin");
  if (!isTransitional(info.state)) actions.push("rebuild");
  return actions;
}

/**
 * " · repeat N" for an app's generation repeat (1-based for display; the stored
 * `repeat_index` is the job's 0-based trial number), "" when there is none. The pool
 * picker, the Apps tab and the generator's "Trial N" all count from 1.
 */
export function repeatSuffix(repeatIndex: number | null | undefined): string {
  return repeatIndex != null ? ` · repeat ${repeatIndex + 1}` : "";
}

export const HOSTING_ACTION_LABELS: Record<AppHostingAction, string> = {
  start: "Start",
  stop: "Stop",
  pin: "Pin",
  unpin: "Unpin",
  rebuild: "Rebuild",
};

export const HOSTING_ACTION_DONE: Record<AppHostingAction, string> = {
  start: "Start requested",
  stop: "Stop requested",
  pin: "Pinned: never idle-stopped or evicted",
  unpin: "Unpinned",
  rebuild: "Rebuild requested",
};

/** Human-eval error codes the hosting endpoints answer with. */
export const HOSTING_ERROR_TEXT: Record<string, string> = {
  shared_container:
    "Another open task uses this container right now (pinned or recently active), so it can't be rebuilt, reset or stopped from here.",
  design_frozen:
    "The design is frozen: this can't change after the task opened.",
  rate_limited: "Too many requests; try again in a moment.",
};

export const PARTICIPANT_KIND_LABELS: Record<string, string> = {
  invite: "Invite link",
  code: "Access code",
  account: "Account",
  preview: "Preview",
};

export const QUALITY_FLAG_TEXT: Record<
  string,
  { label: string; help: string }
> = {
  speeding: {
    label: "Speeding",
    help: "Active and wall-clock time both below the minimum seconds per item.",
  },
  straightlining: {
    label: "Straight-lining",
    help: "Every criterion of a rating block (≥ 4 criteria) got the same value.",
  },
  attention_failed: {
    label: "Attention check failed",
    help: "An attention question was answered with the wrong option.",
  },
  app_not_viewed: {
    label: "App not viewed",
    help: "Submitted without the app ever being shown or seen running.",
  },
  possible_duplicate: {
    label: "Possible duplicate",
    help: "Another participant of this task joined from the same browser.",
  },
};

export function qualityFlagLabel(flag: string): string {
  return QUALITY_FLAG_TEXT[flag]?.label ?? humanize(flag);
}

export function qualityFlagHelp(flag: string): string {
  return QUALITY_FLAG_TEXT[flag]?.help ?? "";
}

export const SKIP_REASON_LABELS: Record<string, string> = {
  app_broken: "App broken",
  platform_unavailable: "Platform unavailable",
  cannot_evaluate: "Can't evaluate",
  conflict_of_interest: "Conflict of interest",
  other: "Other",
};

export function skipReasonLabel(reason: string | null | undefined): string {
  if (!reason) return "";
  return SKIP_REASON_LABELS[reason] ?? humanize(reason);
}

export const REQUIREMENT_VERDICT_LABELS: Record<string, string> = {
  met: "Met",
  partial: "Partially met",
  not_met: "Not met",
  blocked: "Blocked by a defect",
  cannot_test: "Couldn't test",
};

export const CHECKLIST_OUTCOME_LABELS: Record<string, string> = {
  vulnerable: "Vulnerable",
  not_vulnerable: "Not vulnerable",
  not_tested: "Not tested",
};

export const SCENARIO_OUTCOME_LABELS: Record<string, string> = {
  success: "Success",
  partial: "Partial",
  failure: "Failure",
  not_attempted: "Not attempted",
};

export const FINDING_VERDICT_LABELS: Record<string, string> = {
  tp: "True positive",
  fp: "False positive",
  unsure: "Unsure",
};

export const OBSERVATION_KIND_LABELS: Record<string, string> = {
  bug: "Bug",
  vulnerability: "Vulnerability",
  note: "Note",
};

export const BLOCK_TYPE_LABELS: Record<BlockType, string> = {
  rating: "Rating",
  requirements: "Requirements",
  security: "Security",
  scenarios: "Scenarios",
  questionnaire: "Questionnaire",
  bugs: "Bug log",
  pairwise: "Pairwise preference",
};

export const INSTRUMENT_LABELS: Record<StandardInstrument | "custom", string> =
  {
    sus: "System Usability Scale (SUS)",
    umux_lite: "UMUX-Lite",
    nasa_tlx: "NASA-TLX (raw)",
    custom: "Custom questions",
  };

export const GROUP_BY_LABELS: Record<GroupBy, string> = {
  condition: "Condition",
  model: "Model",
  app: "App",
};

/** Why results are not confirmatory (TaskResults.deviations). */
export const DEVIATION_TEXT: Record<string, string> = {
  provisional: "Still collecting data: the task is not closed.",
  reopened_after_close:
    "The task was reopened after it was first closed; later data is exploratory.",
  stopped_early: "Closed before the planned sample was reached.",
  post_hoc_exclusions:
    "Participants or assignments were excluded by hand after the task opened.",
  no_primary: "No primary outcome was pre-registered.",
};

export function deviationText(code: string): string {
  return codeText(code, DEVIATION_TEXT);
}

/** Plan metrics measured as a 0..1 share (contrasts print them in percentage points). */
const SHARE_METRICS = new Set([
  "compliance",
  "vulnerable_share",
  "success_rate",
]);

export function metricIsShare(metric: string): boolean {
  return SHARE_METRICS.has(metric);
}

/** A contrast's difference: shares in percentage points, other metrics on their own scale. */
export function formatContrastDiff(
  value: number | null | undefined,
  metric: string,
): string {
  if (metricIsShare(metric)) {
    return formatDiff(value, (v) => `${(v * 100).toFixed(1)} pp`);
  }
  return formatDiff(value, (v) => v.toFixed(2));
}

/** Pre-registered exclusion rules (TaskResults.rule_exclusions keys). */
export const RULE_LABELS: Record<string, string> = {
  speeding: "Speeding",
  attention_failed: "Failed attention check",
  straightlining: "Straight-lining",
  app_not_viewed: "App not viewed",
};

/**
 * The tool's own level for a finding under review: exactly what it printed,
 * "unrated" when it printed nothing. Never a normalized severity.
 */
export function toolLevelLabel(
  toolSeverity: string | null | undefined,
): string {
  const text = (toolSeverity ?? "").trim();
  return text || "unrated";
}

/** The tester's own severity of an observation ("" → not given). */
export function testerSeverityLabel(
  severity: string | null | undefined,
): string {
  return severity ? humanize(severity) : "Not given";
}

// ── links ──────────────────────────────────────────────────────────────

/** `origin` + a root-relative `path` ("https://lab.example" + "/eval/join/x"). */
export function absoluteUrl(origin: string, path: string): string {
  const base = origin.replace(/\/+$/, "");
  if (!path) return base;
  return path.startsWith("/") ? `${base}${path}` : `${base}/${path}`;
}

/**
 * An untrusted string as a link target: only absolute http(s) URLs qualify
 * (anything else — relative paths, javascript:, data: — renders as text).
 */
export function safeHttpUrl(value: string | null | undefined): string | null {
  if (typeof value !== "string") return null;
  const text = value.trim();
  if (!/^https?:\/\//i.test(text)) return null;
  try {
    const url = new URL(text);
    return url.protocol === "http:" || url.protocol === "https:"
      ? url.href
      : null;
  } catch {
    return null;
  }
}

// ── access codes, invites, accounts ────────────────────────────────────

/** One CSV cell: numbers as numbers; strings quoted when needed and formula-safe (R3.15). */
export function csvCell(
  value: string | number | boolean | null | undefined,
): string {
  if (value == null) return "";
  if (typeof value === "number")
    return Number.isFinite(value) ? String(value) : "";
  if (typeof value === "boolean") return value ? "true" : "false";
  let text = value;
  if (
    /^[=+\-@\t\r]/.test(text) &&
    !/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(text)
  ) {
    text = `'${text}`;
  }
  if (/[",\n\r]/.test(text)) text = `"${text.replace(/"/g, '""')}"`;
  return text;
}

export function toCsv(
  header: string[],
  rows: (string | number | boolean | null | undefined)[][],
): string {
  return (
    [header, ...rows].map((row) => row.map(csvCell).join(",")).join("\n") + "\n"
  );
}

/** The codes dialog's CSV download: participant, code, where to enter it. */
export function codesCsv(codes: CreatedCode[], portalUrl: string): string {
  return toCsv(
    ["participant", "access_code", "portal_url"],
    codes.map((c) => [c.label, c.code, portalUrl]),
  );
}

/** "P001  K7QM-4XT9-BW2R" lines for the clipboard. */
export function codesText(codes: CreatedCode[]): string {
  return codes.map((c) => `${c.label}\t${c.code}`).join("\n");
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * A standalone, print-ready HTML document of cut-out slips (one per code):
 * the task title, where to go and the code. Every value is escaped.
 */
export function codesPrintHtml(
  codes: CreatedCode[],
  opts: { title: string; portalUrl: string },
): string {
  const title = escapeHtml(opts.title);
  const url = escapeHtml(opts.portalUrl);
  const slips = codes
    .map(
      (c) =>
        `<div class="slip"><div class="t">${title}</div><div class="u">Go to <b>${url}</b> and enter your access code:</div><div class="c">${escapeHtml(c.code)}</div><div class="l">${escapeHtml(c.label)}</div></div>`,
    )
    .join("");
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Access codes — ${title}</title><style>
body{font-family:system-ui,sans-serif;margin:12mm;color:#000}
h1{font-size:14pt;margin:0 0 6mm}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:0}
.slip{border:1px dashed #888;padding:5mm;break-inside:avoid}
.t{font-size:9pt;color:#444}.u{font-size:10pt;margin:2mm 0}
.c{font-family:ui-monospace,Menlo,monospace;font-size:18pt;letter-spacing:.08em;font-weight:600}
.l{font-size:8pt;color:#666;margin-top:1mm}
</style></head><body><h1>Access codes — ${title}</h1><div class="grid">${slips}</div></body></html>`;
}

const EMAIL_RE = /^[^\s@,;<>()]+@[^\s@,;<>()]+\.[^\s@,;<>()]+$/;

/** Max emails per accounts call (server limit). */
export const MAX_EMAILS_PER_CALL = 200;

/**
 * Emails pasted into the accounts dialog (one per line, or comma / semicolon /
 * space separated; "Name <a@b.c>" accepted) → lowercased unique valid
 * addresses in input order, plus the entries that aren't addresses.
 */
export function parseEmails(text: string): {
  emails: string[];
  invalid: string[];
} {
  const emails: string[] = [];
  const invalid: string[] = [];
  const seen = new Set<string>();
  const tokens: string[] = [];
  for (const segment of text.split(/[,;\n\r]+/)) {
    const angled = segment.match(/<([^<>]*)>/);
    // "Display Name <a@b.c>": the address in brackets, the name dropped.
    if (angled) tokens.push(angled[1].trim());
    else tokens.push(...segment.split(/\s+/));
  }
  for (const raw of tokens) {
    const token = raw.trim();
    if (!token) continue;
    const email = token.toLowerCase();
    if (!EMAIL_RE.test(email)) {
      invalid.push(token);
      continue;
    }
    if (seen.has(email)) continue;
    seen.add(email);
    emails.push(email);
  }
  return { emails, invalid };
}

// ── answers (read-only responses view) ─────────────────────────────────

/** A rating value with its anchor: 5 → "5 · Good" (scale label), "na" → "N/A". */
export function ratingValueText(
  value: number | "na" | null | undefined,
  scale?: Scale | null,
): string {
  if (value === "na") return "N/A (can't judge)";
  if (value == null || typeof value !== "number") return "—";
  const label =
    scale?.labels?.[String(value)] ??
    (scale && value === scale.min ? scale.min_label : undefined) ??
    (scale && value === scale.max ? scale.max_label : undefined);
  return label ? `${value} · ${label}` : String(value);
}

/** "3 on 1–7" style range of a scale. */
export function scaleRangeText(scale: Scale | null | undefined): string {
  if (!scale) return "";
  return `${scale.min}–${scale.max}`;
}

type SlotApp = { slot: string; label: string };

function slotLabel(slot: "A" | "B", apps: readonly SlotApp[]): string {
  const app = apps.find((a) => a.slot === slot);
  return app ? `${slot} (${app.label})` : slot;
}

/**
 * A pairwise answer as the participant gave it, with the app shown on that
 * side: ab_tie "A" → "A (App 03)", five_point −2 → "A (App 03) much better".
 * `apps` are the assignment's apps with their displayed slots.
 */
export function pairwiseValueText(
  value: PairwiseValue | null | undefined,
  scale: PairwiseScale,
  apps: readonly SlotApp[] = [],
): string {
  if (value == null) return "—";
  if (value === "tie") return "Tie";
  if (value === "A" || value === "B") return `${slotLabel(value, apps)} better`;
  if (typeof value === "number" && scale === "five_point") {
    if (value === 0) return "No difference";
    const side = value < 0 ? "A" : "B";
    const strength = Math.abs(value) >= 2 ? "much better" : "better";
    return `${slotLabel(side, apps)} ${strength}`;
  }
  return String(value);
}

/** A questionnaire / pre-post answer: option values → their labels. */
export function questionAnswerText(
  question: Pick<Question, "kind" | "options" | "scale">,
  value: QuestionAnswer | undefined,
): string {
  if (value == null || value === "") return "—";
  const optionLabel = (v: string) =>
    question.options?.find((o) => o.value === v)?.label ?? v;
  if (Array.isArray(value))
    return value.length ? value.map(optionLabel).join(", ") : "—";
  switch (question.kind) {
    case "single":
    case "attention":
      return optionLabel(String(value));
    case "scale":
      return typeof value === "number"
        ? ratingValueText(value, question.scale)
        : String(value);
    default:
      return String(value);
  }
}

// ── figures ────────────────────────────────────────────────────────────

export type Domain = [number, number];

/** Position of `value` on `domain` in percent (clamped to 0..100). */
export function plotPercent(value: number, domain: Domain): number {
  const [min, max] = domain;
  if (!(max > min)) return 0;
  const p = ((value - min) / (max - min)) * 100;
  return Math.min(100, Math.max(0, p));
}

/** Smallest "nice" number (1, 2, 2.5, 5 × 10^k) ≥ x; x ≤ 0 → 1. */
export function niceCeil(x: number): number {
  if (!(x > 0) || !Number.isFinite(x)) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(x));
  for (const f of [1, 2, 2.5, 5, 10]) {
    const candidate = f * magnitude;
    if (candidate >= x - 1e-12) return Number(candidate.toPrecision(12));
  }
  return 10 * magnitude;
}

/**
 * Axis ticks: every integer on a short integer scale (1..7 → 1,2,…,7),
 * otherwise five evenly spaced values.
 */
export function axisTicks(domain: Domain, count = 5): number[] {
  const [min, max] = domain;
  if (!(max > min)) return [min];
  const span = max - min;
  if (
    Number.isInteger(min) &&
    Number.isInteger(max) &&
    span <= 10 &&
    span >= 1 &&
    !(min === 0 && max === 1)
  ) {
    return Array.from({ length: span + 1 }, (_, i) => min + i);
  }
  const step = span / (count - 1);
  return Array.from({ length: count }, (_, i) =>
    Number((min + step * i).toPrecision(12)),
  );
}

/** 0..max over the values (and CI bounds), rounded up to a nice number. */
export function zeroBasedDomain(
  values: readonly (number | null | undefined)[],
): Domain {
  const max = Math.max(
    0,
    ...values.filter((v): v is number => v != null && Number.isFinite(v)),
  );
  return [0, niceCeil(max)];
}

/** Symmetric −m..m around 0 for log strengths (at least ±0.5). */
export function symmetricDomain(
  values: readonly (number | null | undefined)[],
): Domain {
  const m = Math.max(
    0.5,
    ...values
      .filter((v): v is number => v != null && Number.isFinite(v))
      .map(Math.abs),
  );
  const nice = niceCeil(m);
  return [-nice, nice];
}

/** Tick text: integers plain, fractions with up to two decimals, shares as percent. */
export function tickText(
  value: number,
  kind: "score" | "share" | "log" = "score",
): string {
  if (kind === "share") return `${Math.round(value * 100)}%`;
  if (kind === "log")
    return value === 0
      ? "0"
      : formatCoef(value, Number.isInteger(value) ? 0 : 1);
  if (Number.isInteger(value)) return String(value);
  return String(Number(value.toFixed(2)));
}

// ── exports ────────────────────────────────────────────────────────────

export const EXPORT_KINDS: {
  kind: ExportKind;
  label: string;
  description: string;
}[] = [
  {
    kind: "apps",
    label: "Apps (de-blinding key)",
    description:
      "Blinded label → job, model, condition, spec version, conformance and static-analysis snapshot.",
  },
  {
    kind: "items",
    label: "Items",
    description:
      "Every evaluation unit (app or pair), including items nobody rated.",
  },
  {
    kind: "participants",
    label: "Participants",
    description:
      "Pseudonymous participants: status, timing, pre/post answers, attention checks, quality flags, exclusions.",
  },
  {
    kind: "assignments",
    label: "Assignments",
    description:
      "Participant × item: status, sides, timing (active and wall seconds), hosting provenance per slot, skips.",
  },
  {
    kind: "responses",
    label: "Responses (raw)",
    description:
      "Every saved block answer as JSON, one row per assignment and block.",
  },
  {
    kind: "ratings",
    label: "Ratings",
    description: "One row per rated criterion (long format).",
  },
  {
    kind: "requirements",
    label: "Requirements",
    description: "One row per requirement verdict, with notes.",
  },
  {
    kind: "security_checklist",
    label: "Security checklist",
    description: "One row per checklist outcome.",
  },
  {
    kind: "finding_verdicts",
    label: "Finding verdicts",
    description:
      "TP / FP / unsure per reviewed finding, with the tool and its own level.",
  },
  {
    kind: "observations",
    label: "Observations",
    description: "Bug, vulnerability and note reports (tester's own severity).",
  },
  {
    kind: "scenarios",
    label: "Scenarios",
    description: "Outcome, seconds and ease per usability scenario.",
  },
  {
    kind: "questionnaires",
    label: "Questionnaires",
    description: "Instrument items and scores, custom questions.",
  },
  {
    kind: "pairwise",
    label: "Pairwise",
    description:
      "Displayed sides, raw and canonical values (+ = app A better), winner.",
  },
  {
    kind: "http_log",
    label: "HTTP console log",
    description:
      "Every request sent through the console and its response excerpt.",
  },
];

/** Export kinds that only carry rows when the design has a matching block (others always apply). */
export const EXPORT_KIND_BLOCK: Partial<Record<ExportKind, BlockType>> = {
  ratings: "rating",
  requirements: "requirements",
  security_checklist: "security",
  finding_verdicts: "security",
  scenarios: "scenarios",
  questionnaires: "questionnaire",
  pairwise: "pairwise",
};
