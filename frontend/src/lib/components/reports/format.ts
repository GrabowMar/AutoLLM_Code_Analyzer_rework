/**
 * Pure helpers of the report pages (list, create, detail): accession ids, labels, the
 * masthead's spec strip from a report's config, and how a server table is set
 * (definition list, threats list or data table; numeric / meter / empty columns).
 *
 * Cells arrive formatted by the server (`renderers.report_tables`); these helpers only
 * decide presentation and never re-format a number ("-" means not measured, never 0).
 */
import {
  summaryItems,
  type ReportStatus,
  type ReportTable,
  type ReportType,
} from "$lib/api/reports";

export const DASH = "—";

/** `rpt_7df60bd5d38755b6` → `RPT-7DF60BD5`: the report's accession number (the study record's motif). */
export function reportAccession(reportId: string): string {
  const core = reportId.replace(/^rpt_/, "").split("-")[0] ?? reportId;
  return `RPT-${core.slice(0, 8).toUpperCase()}`;
}

/** `success_rate` → `Success rate`; leaves already-labelled text alone. */
export function humanize(text: string): string {
  if (!/^[a-z0-9]+(_[a-z0-9]+)*(\.[a-z0-9_]+)*$/.test(text)) return text;
  const s = text.replace(/[._]/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** A generic table's dotted title (`generation_analytics / summary / by_status`) as breadcrumb parts. */
export function titleParts(title: string): string[] {
  return title
    .split(" / ")
    .map((p) => humanize(p.trim()))
    .filter(Boolean);
}

/** Empty or "-" (not measured / not applicable). */
export function isBlank(value: string | undefined | null): boolean {
  return value == null || value === "" || value === "-";
}

const NUMERIC = /^[+\-−]?(\d{1,3}(,\d{3})+|\d+)?(\.\d+)?(e[+-]?\d+)?%?$/i;

export function isNumeric(value: string): boolean {
  return value !== "" && NUMERIC.test(value);
}

/** A share column: the header names a rate/share/probability and the values are fractions or percentages. */
const RATE_HEADER =
  /(rate|share|score|jaccard|fully correct|correct|deployed|p\(rank|probability|coverage)/i;
/** Never meters: counts, intervals, p-values and ranks are not shares. */
const NOT_RATE_HEADER =
  /(\bp\b|p \(|q \(|rank|ci\b|interval|n=|\/ ?app|kloc|usd|cost)/i;

/**
 * The 0–1 share a cell shows, for a neutral meter: "0.9872" → 0.9872, "75.0% (9/12) [60, 88]" → 0.75.
 * null when the cell is not a share.
 */
export function shareOf(value: string): number | null {
  const pct = /^(\d+(?:\.\d+)?)%/.exec(value);
  if (pct) {
    const v = Number(pct[1]) / 100;
    return v >= 0 && v <= 1 ? v : null;
  }
  if (!isNumeric(value) || value.endsWith("%")) return null;
  const v = Number(value.replace(/,/g, ""));
  return Number.isFinite(v) && v >= 0 && v <= 1 ? v : null;
}

export type ColumnKind = "text" | "wide" | "num" | "share" | "chips";

export interface ColumnPlan {
  index: number;
  header: string;
  kind: ColumnKind;
  /** Highest share in the column (the leader gets the primary meter when held by ≤ 2 rows). */
  best?: number;
}

export interface TablePlan {
  /** How the table is set. */
  layout: "pairs" | "threats" | "list" | "grid";
  /** Shown columns in order (columns blank on every row are left out). */
  columns: ColumnPlan[];
  /** Headers of the columns left out because no row has a value. */
  hidden: string[];
}

const PAIR_HEADERS = [
  ["item", "value"],
  ["term", "meaning"],
  ["key", "value"],
  ["field", "value"],
];

/** How to set one server table. */
export function planTable(table: ReportTable): TablePlan {
  const headers = table.columns.map((c) => c.toLowerCase());
  if (
    headers.length === 2 &&
    PAIR_HEADERS.some(([a, b]) => headers[0] === a && headers[1] === b)
  ) {
    return {
      layout: "pairs",
      columns: table.columns.map((header, index) => ({
        index,
        header,
        kind: "text",
      })),
      hidden: [],
    };
  }
  if (
    headers.includes("level") &&
    headers.includes("finding") &&
    headers.length <= 4
  ) {
    return {
      layout: "threats",
      columns: table.columns.map((header, index) => ({
        index,
        header,
        kind: "text",
      })),
      hidden: [],
    };
  }
  if (headers.length === 1) {
    return {
      layout: "list",
      columns: [{ index: 0, header: table.columns[0], kind: "text" }],
      hidden: [],
    };
  }

  const columns: ColumnPlan[] = [];
  const hidden: string[] = [];
  table.columns.forEach((header, index) => {
    const values = table.rows.map((r) => r[index] ?? "");
    const present = values.filter((v) => !isBlank(v));
    if (table.rows.length > 0 && present.length === 0) {
      hidden.push(header);
      return;
    }
    let kind: ColumnKind = "text";
    let best: number | undefined;
    const shares = present.map(shareOf);
    if (
      present.length > 0 &&
      RATE_HEADER.test(header) &&
      !NOT_RATE_HEADER.test(header) &&
      shares.every((s) => s != null)
    ) {
      kind = "share";
      best = Math.max(...(shares as number[]));
    } else if (present.length > 0 && present.every(isNumeric)) {
      kind = "num";
    } else if (
      /levels/i.test(header) &&
      present.every((v) => /^[^,:]+: \d+(, [^,:]+: \d+)*$/.test(v))
    ) {
      kind = "chips";
    } else if (present.some((v) => v.length > 44)) {
      kind = "wide";
    }
    columns.push({ index, header, kind, best });
  });
  return { layout: "grid", columns, hidden };
}

/** "LOW: 4, MEDIUM: 5" → [["LOW", "4"], ["MEDIUM", "5"]]. */
export function chipParts(value: string): [string, string][] {
  return value.split(", ").map((part) => {
    const i = part.lastIndexOf(": ");
    return [part.slice(0, i), part.slice(i + 2)] as [string, string];
  });
}

/** How many rows of the table are leaders in a share column; the meter highlights a leader held by ≤ 2 rows. */
export function leaderRows(table: ReportTable, col: ColumnPlan): number {
  if (col.best == null) return 0;
  return table.rows.filter(
    (r) => Math.abs((shareOf(r[col.index] ?? "") ?? -1) - col.best!) < 1e-9,
  ).length;
}

/** A pairs table's value may be an ISO timestamp; render it as a date. */
export function prettyValue(value: string): string {
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value)) {
    const d = new Date(value);
    if (!Number.isNaN(d.getTime()))
      return d.toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      });
  }
  if (value === "[]") return DASH;
  return value;
}

/** Threat level → StatusBadge tone. */
export function threatTone(
  level: string,
): "warning" | "danger" | "info" | "neutral" {
  const l = level.toLowerCase();
  if (l === "error" || l === "critical" || l === "high") return "danger";
  if (l === "warning" || l === "warn" || l === "medium") return "warning";
  if (l === "info" || l === "low") return "info";
  return "neutral";
}

// ── The masthead's spec strip ───────────────────────────────────────────────

export interface SpecItem {
  label: string;
  value: string;
  sub?: string;
  href?: string;
}

/** What a report covers and how it was computed, read from its config (unknown keys are listed as they are). */
export function specItems(
  type: ReportType,
  config: Record<string, unknown> | null | undefined,
  names: { study?: string | null } = {},
): SpecItem[] {
  const c = { ...(config ?? {}) };
  const items: SpecItem[] = [];
  const studyId = (c.study_id ?? c.experiment_id) as string | undefined;
  delete c.study_id;
  delete c.experiment_id;

  // Scope
  if (studyId) {
    items.push({
      label: "Scope",
      value: names.study || `Experiment ${studyId.split("-")[0]}`,
      sub: "one experiment",
      href: `/experiments/${studyId}`,
    });
  } else if (typeof c.task_slug === "string") {
    items.push({
      label: "Scope",
      value: c.task_slug,
      sub: c.task_version
        ? `task v${c.task_version}`
        : "every version of the task",
    });
    delete c.task_version;
  } else if (typeof c.model_id === "string") {
    items.push({ label: "Scope", value: c.model_id, sub: "one model" });
  } else if (type === "tool_analysis") {
    items.push({
      label: "Scope",
      value:
        typeof c.tool_name === "string" && c.tool_name
          ? c.tool_name
          : "Every tool",
      sub: "analysis results",
    });
  } else if (type === "study_report") {
    items.push({
      label: "Scope",
      value: "All my apps",
      sub: "no experiment filter",
    });
  } else {
    items.push({ label: "Scope", value: "Whole platform", sub: "your data" });
  }
  delete c.task_slug;
  delete c.model_id;
  delete c.tool_name;

  // Window
  if (typeof c.days === "number" || typeof c.days === "string") {
    items.push({ label: "Window", value: `Last ${c.days} days` });
  } else if (
    type === "tool_analysis" ||
    type === "generation_analytics" ||
    type === "comprehensive"
  ) {
    items.push({ label: "Window", value: "All time" });
  }
  delete c.days;

  // Analysis
  if (
    type === "study_report" ||
    type === "experiment_report" ||
    type === "template_comparison"
  ) {
    const excl = c.exclude_truncated === true;
    const parts = [excl ? "Truncated runs excluded" : "Intention-to-treat"];
    if (c.include_ai === true) parts.push("AI-reviewer findings counted");
    if (c.code_scope === "all")
      parts.push("tests and scratch scripts counted as app code");
    else if (c.code_scope === "with_scratch")
      parts.push("scratch scripts counted as app code");
    else if (c.code_scope === "app_named")
      parts.push("scripts matched by name counted as app code");
    items.push({
      label: "Analysis",
      value: parts[0],
      sub: [
        excl ? "sensitivity analysis" : "truncated runs kept",
        ...parts.slice(1),
      ].join(" · "),
    });
  }
  delete c.exclude_truncated;
  delete c.include_ai;
  delete c.code_scope;

  // Randomisation
  if (c.seed != null || c.bootstrap != null) {
    items.push({
      label: "Randomisation",
      value: `seed ${c.seed ?? 0}`,
      sub:
        c.bootstrap === false
          ? "no rank bootstrap"
          : "bootstrap rank intervals",
    });
  }
  delete c.seed;
  delete c.bootstrap;

  for (const [k, v] of Object.entries(c)) {
    if (v == null || v === "" || (Array.isArray(v) && v.length === 0)) continue;
    items.push({
      label: humanize(k),
      value: Array.isArray(v) ? `${v.length} selected` : String(v),
    });
  }
  return items;
}

// ── Lists ────────────────────────────────────────────────────────────────────

/** One key result of a report for the list's "Headline" column ("leader 98.7%", "325 unique issues"). */
export function headline(
  summary: Record<string, unknown> | null | undefined,
): { label: string; value: string } | null {
  const s = summary ?? {};
  const pct = (v: unknown) =>
    typeof v === "number" ? `${(v * 100).toFixed(1)}%` : null;
  if (typeof s.leader === "string" && s.leader) {
    const rate = pct(s.leader_pass_rate);
    return {
      label: "leader",
      value: rate ? `${shortName(s.leader)} · ${rate}` : shortName(s.leader),
    };
  }
  for (const [key, label] of [
    ["unique_issues", "unique issues"],
    ["total_findings", "findings"],
    ["total_runs", "runs"],
    ["jobs", "runs"],
  ] as const) {
    if (typeof s[key] === "number")
      return { label, value: (s[key] as number).toLocaleString() };
  }
  if (typeof s.usable_rate === "number")
    return { label: "usable", value: pct(s.usable_rate)! };
  return null;
}

/** `local-opencode-go/deepseek-v4-pro` → `deepseek-v4-pro`; condition labels keep their harness. */
export function shortName(name: string): string {
  const slash = name.lastIndexOf("/");
  return slash >= 0 && !name.includes(" · ") ? name.slice(slash + 1) : name;
}

/** "in 26 days" / "expired" for a report's expiry. */
export function expiryLabel(
  expiresAt: string | null,
  now = Date.now(),
): string | null {
  if (!expiresAt) return null;
  const ms = new Date(expiresAt).getTime() - now;
  if (Number.isNaN(ms)) return null;
  if (ms <= 0) return "expired";
  const days = Math.round(ms / 86_400_000);
  if (days >= 1) return `expires in ${days} d`;
  return `expires in ${Math.max(1, Math.round(ms / 3_600_000))} h`;
}

/** Seconds a report took to generate, as "1.2 s" / "3 min" (null while unknown). */
export function generationTime(
  createdAt: string,
  completedAt: string | null,
): string | null {
  if (!completedAt) return null;
  const s =
    (new Date(completedAt).getTime() - new Date(createdAt).getTime()) / 1000;
  if (!Number.isFinite(s) || s < 0) return null;
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)} s`;
  return `${Math.round(s / 60)} min`;
}

export const ACTIVE_STATUSES: ReportStatus[] = ["pending", "generating"];

// ── Key figures ─────────────────────────────────────────────────────────────

export interface KeyFigure {
  label: string;
  value: string;
  href?: string;
  /** leader: the top group (wide); rate: a share with a meter; breakdown: counts of a whole. */
  kind: "leader" | "number" | "rate" | "text" | "breakdown";
  /** 0–1 for rates. */
  share?: number;
  /** Breakdown parts, largest first. */
  parts?: { label: string; n: number }[];
  total?: number;
  /** Something the reader should look at (warnings > 0). */
  attention?: boolean;
}

function isCounts(v: unknown): v is Record<string, number> {
  return (
    !!v &&
    typeof v === "object" &&
    !Array.isArray(v) &&
    Object.keys(v).length > 0 &&
    Object.values(v as object).every((x) => typeof x === "number")
  );
}

/**
 * A report's summary as key figures: the leader (ties said as such), rates with a meter,
 * counts, and `by_*` count maps as breakdowns; other nested objects are flattened into
 * "Parent · key" figures. Unmeasured values stay "—".
 */
export function keyFigures(
  summary: Record<string, unknown> | null | undefined,
  data?: Record<string, unknown> | null,
): KeyFigure[] {
  const raw = { ...(summary ?? {}) };
  // The scope (experiment) is the masthead's; not a figure.
  delete raw.study_id;
  delete raw.experiment_id;
  const out: KeyFigure[] = [];
  const nested: Record<string, unknown> = {};
  const breakdowns: KeyFigure[] = [];
  for (const [key, v] of Object.entries(raw)) {
    if (key.startsWith("by_") && isCounts(v)) {
      const parts = Object.entries(v)
        .map(([label, n]) => ({ label: humanize(label), n }))
        .sort((a, b) => b.n - a.n);
      breakdowns.push({
        label: humanize(key),
        value: "",
        kind: "breakdown",
        parts,
        total: parts.reduce((a, p) => a + p.n, 0),
      });
      delete raw[key];
    } else if (v && typeof v === "object" && !Array.isArray(v)) {
      for (const [k, x] of Object.entries(v as Record<string, unknown>)) {
        if (x == null || typeof x !== "object")
          nested[`${humanize(key)} · ${humanize(k).toLowerCase()}`] = x;
      }
      delete raw[key];
    }
  }
  for (const item of summaryItems(raw, data)) {
    const key = Object.keys(raw).find(
      (k) =>
        k.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase()) ===
        item.label,
    );
    const v = key ? raw[key] : undefined;
    if (item.label === "Leader") {
      out.push({
        ...item,
        value: item.value === DASH ? DASH : item.value,
        kind: "leader",
      });
    } else if (key?.endsWith("_rate") && typeof v === "number") {
      out.push({ ...item, kind: "rate", share: Math.max(0, Math.min(1, v)) });
    } else if (typeof v === "number") {
      out.push({
        ...item,
        kind: "number",
        attention: key === "warnings" && v > 0,
      });
    } else {
      out.push({ ...item, kind: "text" });
    }
  }
  for (const [label, v] of Object.entries(nested)) {
    out.push({
      label,
      value:
        v == null
          ? DASH
          : typeof v === "number"
            ? v.toLocaleString(undefined, { maximumFractionDigits: 3 })
            : String(v),
      kind: typeof v === "number" ? "number" : "text",
    });
  }
  // Leader first, then rates, then counts, then the rest; breakdowns last.
  const order = { leader: 0, rate: 1, number: 2, text: 3, breakdown: 4 };
  return [...out.sort((a, b) => order[a.kind] - order[b.kind]), ...breakdowns];
}

// ── Table interaction ────────────────────────────────────────────────────────

/** A cell's sort key: numbers (and shares, and a leading number of "75.0% (9/12)") numerically, else text. */
function sortKey(value: string, kind: ColumnKind): number | string | null {
  if (isBlank(value)) return null;
  if (kind === "num" || kind === "share") {
    const m = /^[+\-−]?[\d,]*\.?\d+(e[+-]?\d+)?/i.exec(value);
    if (m) return Number(m[0].replace(/,/g, "").replace("−", "-"));
  }
  return value.toLowerCase();
}

/** Row indexes sorted by one column; blanks ("-") always last. */
export function sortRows(
  rows: string[][],
  index: number,
  kind: ColumnKind,
  dir: "asc" | "desc",
): number[] {
  const keyed = rows.map((r, i) => ({ i, k: sortKey(r[index] ?? "", kind) }));
  const sign = dir === "asc" ? 1 : -1;
  return keyed
    .sort((a, b) => {
      if (a.k === null) return b.k === null ? a.i - b.i : 1;
      if (b.k === null) return -1;
      if (typeof a.k === "number" && typeof b.k === "number")
        return sign * (a.k - b.k) || a.i - b.i;
      return (
        sign *
          String(a.k).localeCompare(String(b.k), undefined, {
            numeric: true,
          }) || a.i - b.i
      );
    })
    .map((x) => x.i);
}

/** Whether any cell of the row contains the query (case-insensitive). */
export function rowMatches(row: string[], query: string): boolean {
  const q = query.trim().toLowerCase();
  return !q || row.some((c) => (c ?? "").toLowerCase().includes(q));
}

/** RFC 4180 CSV ("-" kept: not measured). */
export function toCsv(columns: string[], rows: string[][]): string {
  const esc = (v: string) =>
    /[",\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
  return (
    [columns, ...rows]
      .map((r) => r.map((v) => esc(v ?? "")).join(","))
      .join("\r\n") + "\r\n"
  );
}

/** A GitHub-flavoured Markdown table (pipes escaped). */
export function toMarkdown(columns: string[], rows: string[][]): string {
  const esc = (v: string) =>
    (v ?? "").replace(/\|/g, "\\|").replace(/\n/g, " ");
  const line = (r: string[]) => `| ${r.map(esc).join(" | ")} |`;
  return (
    [line(columns), line(columns.map(() => "---")), ...rows.map(line)].join(
      "\n",
    ) + "\n"
  );
}

/** A file name from a table title: "Table 4 · Plan metrics by model" → "rpt-…-table-4-plan-metrics-by-model". */
export function tableFileName(
  reportId: string,
  number: number,
  title: string,
): string {
  const slug = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 60);
  return `${reportAccession(reportId).toLowerCase()}-table-${number}-${slug}`;
}
