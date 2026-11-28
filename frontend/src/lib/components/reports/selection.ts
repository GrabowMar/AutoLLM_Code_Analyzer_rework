/**
 * Cross-experiment sample selection of a study report ("newest unique runs", the final assessment):
 * the create page's config builder and deep link, and the pure helpers behind the report page's
 * selection section (normalising `report_data.selection`, the group × task matrix, labels).
 */
import type { ReadonlyURLSearchParams } from "$app/state";
import type {
  ReportSelection,
  SelectionAnalyses,
  SelectionCell,
  SelectionCounts,
  SelectionGroup,
  StudyReportConfig,
} from "$lib/api/reports";
import { OUTCOME_LABELS } from "$lib/components/studies/format";

// ── Create page: config and deep link ────────────────────────────────────────

/** What the researcher picked for a cross-experiment sample. */
export interface SampleSelectionState {
  /** Candidate experiments; empty = every experiment the user can see. */
  studyIds: string[];
  /** Model ids to keep; empty = every model. */
  models: string[];
  /** Task refs (`slug@v`, or a bare slug for any version); empty = every task. */
  tasks: string[];
  commonTasksOnly: boolean;
}

/** A list parameter: comma-separated and/or repeated, trimmed, blanks and duplicates dropped. */
export function listParam(
  params: ReadonlyURLSearchParams,
  key: string,
): string[] {
  return uniqueList(params.getAll(key).flatMap((v) => v.split(",")));
}

/** Trimmed, non-empty, first occurrence kept. */
export function uniqueList(values: Iterable<string>): string[] {
  const out: string[] = [];
  for (const raw of values) {
    const v = raw.trim();
    if (v && !out.includes(v)) out.push(v);
  }
  return out;
}

const TRUE = new Set(["1", "true", "yes", "on"]);

/**
 * The sample part of a create-page link: `?selection=latest_unique&study_ids=a,b&models=…&tasks=…
 * &common_tasks=1`. `null` when the link does not ask for a cross-experiment selection.
 */
export function parseSampleSelection(
  params: ReadonlyURLSearchParams,
): SampleSelectionState | null {
  if (params.get("selection") !== "latest_unique") return null;
  return {
    studyIds: listParam(params, "study_ids"),
    models: listParam(params, "models"),
    tasks: listParam(params, "tasks"),
    commonTasksOnly: TRUE.has(
      (params.get("common_tasks") ?? params.get("common_tasks_only") ?? "")
        .trim()
        .toLowerCase(),
    ),
  };
}

/**
 * The selection keys of a study report config. Empty filters are left out (the backend reads a
 * missing list as "all"); `common_tasks_only` is sent only when on.
 */
export function sampleSelectionConfig(
  s: SampleSelectionState,
): Pick<
  StudyReportConfig,
  "selection" | "study_ids" | "models" | "tasks" | "common_tasks_only"
> {
  const c: Pick<
    StudyReportConfig,
    "selection" | "study_ids" | "models" | "tasks" | "common_tasks_only"
  > = { selection: "latest_unique" };
  const studyIds = uniqueList(s.studyIds);
  const models = uniqueList(s.models);
  const tasks = uniqueList(s.tasks);
  if (studyIds.length) c.study_ids = studyIds;
  if (models.length) c.models = models;
  if (tasks.length) c.tasks = tasks;
  if (s.commonTasksOnly) c.common_tasks_only = true;
  return c;
}

/** The selection as stored in a report config (for the regenerate / "complete cases" links). */
export function sampleSelectionFromConfig(
  config: Record<string, unknown> | null | undefined,
): SampleSelectionState | null {
  if (config?.selection !== "latest_unique") return null;
  const list = (v: unknown) =>
    Array.isArray(v)
      ? uniqueList(v.filter((x): x is string => typeof x === "string"))
      : [];
  return {
    studyIds: list(config.study_ids),
    models: list(config.models),
    tasks: list(config.tasks),
    commonTasksOnly: config.common_tasks_only === true,
  };
}

/** `/reports/create?type=study_report&selection=latest_unique&study_ids=a,b…` (readable commas). */
export function sampleSelectionLink(
  s: SampleSelectionState,
  extra: Record<string, string | null | undefined> = {},
): string {
  const parts = ["type=study_report", "selection=latest_unique"];
  const add = (key: string, values: string[]) => {
    const v = uniqueList(values);
    if (v.length)
      parts.push(`${key}=${v.map((x) => encodeURIComponent(x)).join(",")}`);
  };
  add("study_ids", s.studyIds);
  add("models", s.models);
  add("tasks", s.tasks);
  if (s.commonTasksOnly) parts.push("common_tasks=1");
  for (const [k, v] of Object.entries(extra)) {
    if (v) parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
  }
  return `/reports/create?${parts.join("&")}`;
}

/** The model levels and task units of an experiment design (filter suggestions). */
export function designScope(
  design:
    | {
        factors?: { key: string; levels: string[] }[];
        units?: { tasks?: string[] };
      }
    | null
    | undefined,
): { models: string[]; tasks: string[] } {
  const models =
    design?.factors?.find((f) => f.key === "model")?.levels ?? ([] as string[]);
  return {
    models: uniqueList(models),
    tasks: uniqueList(design?.units?.tasks ?? []),
  };
}

/** Values across several designs with the number of designs holding each (first-seen order). */
export function unionWithCounts(lists: string[][]): [string, number][] {
  const counts = new Map<string, number>();
  for (const list of lists)
    for (const v of new Set(list)) counts.set(v, (counts.get(v) ?? 0) + 1);
  return [...counts.entries()];
}

// ── Report page: normalising `report_data.selection` ──────────────────────────

const asArray = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);
const asString = (v: unknown): string | null =>
  typeof v === "string" && v ? v : null;
const asNumber = (v: unknown): number =>
  typeof v === "number" && Number.isFinite(v) ? v : 0;

/**
 * `report_data.selection`, with every list present and numbers defaulted, or `null` when the report
 * has no cross-experiment selection. Older or partial payloads never throw.
 */
export function reportSelection(
  data: Record<string, unknown> | null | undefined,
): ReportSelection | null {
  const raw = data?.selection;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const s = raw as Record<string, unknown>;
  if (s.mode !== "latest_unique" && !Array.isArray(s.matrix)) return null;
  const filters = (s.filters ?? {}) as Record<string, unknown>;
  const strings = (v: unknown) =>
    asArray<unknown>(v).filter((x): x is string => typeof x === "string");
  const optionalStrings = (v: unknown) =>
    Array.isArray(v) ? strings(v) : null;
  const analyses =
    s.analyses && typeof s.analyses === "object"
      ? (s.analyses as Record<string, unknown>)
      : null;
  const droppedTasks = new Set(strings(s.dropped_tasks));
  return {
    mode: "latest_unique",
    rule: asString(s.rule) ?? "",
    filters: {
      study_ids: strings(filters.study_ids),
      models: optionalStrings(filters.models),
      tasks: optionalStrings(filters.tasks),
      common_tasks_only: filters.common_tasks_only === true,
      exclude_truncated: filters.exclude_truncated === true,
    },
    studies: asArray<Record<string, unknown>>(s.studies).map((x) => ({
      id: String(x.id ?? ""),
      name: asString(x.name) ?? String(x.id ?? ""),
      status: asString(x.status) ?? "",
      runs_total: typeof x.runs_total === "number" ? x.runs_total : undefined,
      runs_considered: asNumber(x.runs_considered),
      runs_selected: asNumber(x.runs_selected),
    })),
    counts: (s.counts && typeof s.counts === "object"
      ? s.counts
      : {}) as Partial<SelectionCounts>,
    groups: asArray<Record<string, unknown>>(s.groups).map((g) => ({
      group: String(g.group ?? ""),
      model: asString(g.model) ?? String(g.group ?? ""),
      treatment_hash: asString(g.treatment_hash),
      conditions: asArray<number>(g.conditions),
      studies: strings(g.studies),
      resource_policies: strings(g.resource_policies),
      resource_policy_runs:
        g.resource_policy_runs && typeof g.resource_policy_runs === "object"
          ? (g.resource_policy_runs as Record<string, number>)
          : undefined,
      tasks_selected: asNumber(g.tasks_selected),
      tasks_in_scope: asNumber(g.tasks_in_scope),
      outcomes:
        g.outcomes && typeof g.outcomes === "object"
          ? (g.outcomes as Record<string, number>)
          : {},
      truncated_excluded: asNumber(g.truncated_excluded),
      dropped_runs: asNumber(g.dropped_runs),
    })),
    tasks: strings(s.tasks),
    matrix: asArray<Record<string, unknown>>(s.matrix).map((c) => {
      const status = (asString(c.status) ??
        "missing") as SelectionCell["status"];
      const task = String(c.task ?? "");
      // Payloads without `in_sample` / `excluded`: a selected run is in the sample unless its task was dropped.
      const droppedTask = droppedTasks.has(task);
      const excluded =
        c.excluded === "truncated" || c.excluded === "dropped_task"
          ? c.excluded
          : c.excluded === undefined && status === "selected" && droppedTask
            ? "dropped_task"
            : null;
      return {
        group: String(c.group ?? ""),
        task,
        status,
        run_id: asString(c.run_id),
        study_id: asString(c.study_id),
        study_name: asString(c.study_name),
        outcome: asString(c.outcome),
        created_at: asString(c.created_at),
        attempts: asNumber(c.attempts),
        superseded_run_ids: strings(c.superseded_run_ids),
        in_sample:
          typeof c.in_sample === "boolean"
            ? c.in_sample
            : status === "selected" && excluded === null,
        excluded,
      };
    }),
    superseded: asArray<Record<string, unknown>>(s.superseded).map((r) => ({
      run_id: String(r.run_id ?? ""),
      group: String(r.group ?? ""),
      task: String(r.task ?? ""),
      study_id: asString(r.study_id),
      study_name: asString(r.study_name),
      outcome: asString(r.outcome),
      usable: typeof r.usable === "boolean" ? r.usable : null,
      created_at: asString(r.created_at),
      replaced_by: asString(r.replaced_by),
      recorded_attempt: asString(r.recorded_attempt),
      reason: asString(r.reason) ?? "",
    })),
    analyses: analyses
      ? ({
          rule: asString(analyses.rule) ?? "",
          analysis_runs_total: asNumber(analyses.analysis_runs_total),
          analysis_runs_contributing: asNumber(
            analyses.analysis_runs_contributing,
          ),
          analysis_runs_superseded: asNumber(analyses.analysis_runs_superseded),
          tool_results_total: asNumber(analyses.tool_results_total),
          tool_results_selected: asNumber(analyses.tool_results_selected),
          tool_results_superseded: asNumber(analyses.tool_results_superseded),
          older_measured_preferred: asNumber(analyses.older_measured_preferred),
          by_launch: asArray<Record<string, unknown>>(analyses.by_launch).map(
            (l) => ({
              launch_id: asString(l.launch_id) ?? "none",
              example_name: asString(l.example_name) ?? asString(l.name),
              analysis_runs: asNumber(l.analysis_runs),
              results_selected: asNumber(l.results_selected),
              results_superseded: asNumber(l.results_superseded),
            }),
          ),
          by_label: asArray<Record<string, unknown>>(analyses.by_label).map(
            (l) => ({
              label: asString(l.label) ?? "(unnamed)",
              example_name: asString(l.example_name) ?? asString(l.name),
              analysis_runs: asNumber(l.analysis_runs),
              results_selected: asNumber(l.results_selected),
              results_superseded: asNumber(l.results_superseded),
            }),
          ),
          tools_mixed_launches: asArray<Record<string, unknown>>(
            analyses.tools_mixed_launches,
          ).map((t) => ({
            tool: String(t.tool ?? ""),
            launches: asNumber(t.launches),
          })),
          tools_mixed_labels: asArray<Record<string, unknown>>(
            analyses.tools_mixed_labels,
          ).map((t) => ({
            tool: String(t.tool ?? ""),
            labels: strings(t.labels),
          })),
        } satisfies SelectionAnalyses)
      : null,
    common_tasks: strings(s.common_tasks),
    dropped_tasks: strings(s.dropped_tasks),
    dropped_by_group: asArray<Record<string, unknown>>(s.dropped_by_group).map(
      (b) => ({
        group: String(b.group ?? ""),
        tasks: asNumber(b.tasks),
        reasons:
          b.reasons && typeof b.reasons === "object"
            ? (b.reasons as Record<string, number>)
            : {},
      }),
    ),
  };
}

// ── Matrix ───────────────────────────────────────────────────────────────────

/**
 * How a matrix cell is drawn. Shape carries the meaning as well as hue: a solid square is a selected
 * tested run, a half-filled square a selected run that failed (build, start or output), a square with a
 * centre dot a selected run with another outcome, a square with a bar a selected run left out of the
 * sample (task dropped, or truncated), an outlined cross attrition (no usable run), a ring a run still in
 * flight, a dashed outline no attempt.
 */
export type CellKind =
  | "tested"
  | "failed"
  | "selected"
  | "excluded"
  | "attrition"
  | "in_flight"
  | "missing";

export const CELL_KINDS: {
  kind: CellKind;
  label: string;
  description: string;
}[] = [
  {
    kind: "tested",
    label: "Selected · tested",
    description:
      "The newest usable run; it built, started and was tested (its checks may still fail).",
  },
  {
    kind: "failed",
    label: "Selected · failed",
    description:
      "The newest usable run; its output was invalid or it did not build or start. A measured failure, in the sample.",
  },
  {
    kind: "selected",
    label: "Selected · other outcome",
    description:
      "The newest usable run; its outcome is not recorded or is none of tested, build, start or output failure.",
  },
  {
    kind: "excluded",
    label: "Selected · left out",
    description:
      "The newest usable run, but not in the sample: its task was dropped for complete cases, or it was truncated and truncated runs are excluded.",
  },
  {
    kind: "attrition",
    label: "Attrition",
    description:
      "Attempts exist but none is usable (infrastructure error, excluded or cancelled); not in the sample.",
  },
  {
    kind: "in_flight",
    label: "Still running",
    description: "Only attempts that have not finished yet; not in the sample.",
  },
  {
    kind: "missing",
    label: "No attempt",
    description: "The task is in scope but this group never ran it.",
  },
];

export const CELL_KIND_LABEL: Record<CellKind, string> = Object.fromEntries(
  CELL_KINDS.map((k) => [k.kind, k.label]),
) as Record<CellKind, string>;

const FAILED_OUTCOMES = new Set(["output_invalid", "build_fail", "start_fail"]);

export function cellKind(cell: SelectionCell | null | undefined): CellKind {
  if (!cell) return "missing";
  switch (cell.status) {
    case "selected":
      if (cell.in_sample === false) return "excluded";
      if (cell.outcome === "tested") return "tested";
      if (cell.outcome && FAILED_OUTCOMES.has(cell.outcome)) return "failed";
      return "selected";
    case "attrition":
      return "attrition";
    case "in_flight":
      return "in_flight";
    default:
      return "missing";
  }
}

export function outcomeText(outcome: string | null | undefined): string {
  if (!outcome) return "no outcome";
  return (OUTCOME_LABELS[outcome] ?? outcome.replace(/_/g, " ")).toLowerCase();
}

/** A run or study id shortened to its first block (the full id goes in a `title`). */
export function shortId(id: string | null | undefined): string {
  if (!id) return "—";
  return id.split("-")[0].slice(0, 8);
}

/**
 * A group label as the page shows it: the model's last path segment as the name, the endpoint
 * before it muted, and a treatment split (`"<model> (treatment ab12cd34)"`) kept apart.
 */
export function groupDisplay(label: string): {
  name: string;
  prefix: string;
  treatment: string;
} {
  let rest = label.trim();
  let treatment = "";
  const m = /\s*\(treatment ([^)]+)\)\s*$/.exec(rest);
  if (m) {
    treatment = m[1];
    rest = rest.slice(0, m.index);
  }
  const slash = rest.lastIndexOf("/");
  return slash > 0 && slash < rest.length - 1
    ? { name: rest.slice(slash + 1), prefix: rest.slice(0, slash), treatment }
    : { name: rest, prefix: "", treatment };
}

/**
 * Display labels for model ids: the last path segment ("deepseek-v4-pro"), or the full id when another id
 * of the list shares that segment (one model served by two endpoints stays distinguishable).
 */
export function modelShortLabels(ids: Iterable<string>): Map<string, string> {
  const unique = uniqueList(ids);
  const byName = new Map<string, number>();
  for (const id of unique) {
    const name = groupDisplay(id).name;
    byName.set(name, (byName.get(name) ?? 0) + 1);
  }
  return new Map(
    unique.map((id) => {
      const name = groupDisplay(id).name;
      return [id, (byName.get(name) ?? 0) > 1 ? id : name];
    }),
  );
}

/** The version suffix every task shares (`"@1"`), so column labels can drop it; null when mixed. */
export function sharedVersion(tasks: string[]): string | null {
  if (tasks.length === 0) return null;
  const versions = new Set(
    tasks.map((t) => {
      const i = t.lastIndexOf("@");
      return i > 0 ? t.slice(i) : "";
    }),
  );
  if (versions.size !== 1) return null;
  const [v] = versions;
  return v || null;
}

export function taskLabel(ref: string, dropVersion: string | null): string {
  return dropVersion && ref.endsWith(dropVersion)
    ? ref.slice(0, -dropVersion.length)
    : ref;
}

export interface MatrixGroup {
  label: string;
  name: string;
  prefix: string;
  treatment: string;
  selected: number;
  inScope: number;
  meta: SelectionGroup | null;
}

export interface MatrixTask {
  ref: string;
  label: string;
  /** Left out by complete cases. */
  dropped: boolean;
  /** Groups with a selected run for it. */
  covered: number;
}

export interface SelectionMatrix {
  groups: MatrixGroup[];
  tasks: MatrixTask[];
  /** `cells[g][t]` (undefined: no row for the unit, drawn as "no attempt"). */
  cells: (SelectionCell | undefined)[][];
  kindCounts: Record<CellKind, number>;
  /** Cells whose unit had older attempts superseded. */
  supersededCells: number;
  /** The shared version suffix dropped from the column labels. */
  version: string | null;
  /** Groups do not all have the same selected tasks. */
  unequalTasks: boolean;
}

/**
 * The group × task grid: groups and tasks in the payload's order (units naming one the lists lack are
 * appended), one cell per unit, counts per drawn kind.
 */
export function selectionMatrix(sel: ReportSelection): SelectionMatrix {
  const groupOrder = uniqueList([
    ...sel.groups.map((g) => g.group),
    ...sel.matrix.map((c) => c.group),
  ]);
  const taskOrder = uniqueList([
    ...sel.tasks,
    ...sel.dropped_tasks,
    ...sel.matrix.map((c) => c.task),
  ]);
  const gi = new Map(groupOrder.map((g, i) => [g, i]));
  const ti = new Map(taskOrder.map((t, i) => [t, i]));
  const cells: (SelectionCell | undefined)[][] = groupOrder.map(() =>
    taskOrder.map(() => undefined),
  );
  for (const c of sel.matrix) {
    const g = gi.get(c.group);
    const t = ti.get(c.task);
    if (g != null && t != null) cells[g][t] = c;
  }
  const kindCounts = Object.fromEntries(
    CELL_KINDS.map((k) => [k.kind, 0]),
  ) as Record<CellKind, number>;
  let supersededCells = 0;
  for (const row of cells)
    for (const c of row) {
      kindCounts[cellKind(c)] += 1;
      if (c && c.superseded_run_ids.length > 0) supersededCells += 1;
    }
  const dropped = new Set(sel.dropped_tasks);
  const version = sharedVersion(taskOrder);
  const meta = new Map(sel.groups.map((g) => [g.group, g]));
  // With complete cases the scope is the kept tasks (the payload's `tasks_in_scope` counts every task).
  const kept = taskOrder.filter((t) => !dropped.has(t)).length;
  const groups: MatrixGroup[] = groupOrder.map((label, g) => {
    const m = meta.get(label) ?? null;
    const selected = cells[g].filter((c) => inSample(c)).length;
    return {
      label,
      ...groupDisplay(label),
      selected: m ? m.tasks_selected : selected,
      inScope: m && dropped.size === 0 ? m.tasks_in_scope : kept,
      meta: m,
    };
  });
  const tasks: MatrixTask[] = taskOrder.map((ref, t) => ({
    ref,
    label: taskLabel(ref, version),
    dropped: dropped.has(ref),
    covered: cells.filter((row) => inSample(row[t])).length,
  }));
  // Groups without a run in the sample are named by the report's validity notes; the sets compared are the rest.
  const taskSets = cells
    .map((row) =>
      row
        .map((c, t) => (inSample(c) ? t : -1))
        .filter((t) => t >= 0)
        .join(","),
    )
    .filter((set) => set !== "");
  return {
    groups,
    tasks,
    cells,
    kindCounts,
    supersededCells,
    version,
    unequalTasks: new Set(taskSets).size > 1,
  };
}

/** The cell's run is in the analysed sample. */
export function inSample(cell: SelectionCell | null | undefined): boolean {
  return !!cell && cell.status === "selected" && cell.in_sample !== false;
}

export const EXCLUSION_LABELS: Record<string, string> = {
  truncated: "truncated run left out (truncated runs are excluded)",
  dropped_task: "task dropped for complete cases",
};

export function exclusionText(cell: SelectionCell | null | undefined): string {
  if (!cell || cell.status !== "selected" || cell.in_sample !== false)
    return "";
  return EXCLUSION_LABELS[cell.excluded ?? ""] ?? "not in the sample";
}

/** One sentence for a cell's `aria-label` / tooltip. */
export function cellLabel(
  group: string,
  task: string,
  cell: SelectionCell | null | undefined,
): string {
  const kind = cellKind(cell);
  const head = `${group}, ${task}: `;
  const older = cell?.superseded_run_ids.length ?? 0;
  const tail =
    older > 0
      ? `; ${older} older attempt${older === 1 ? "" : "s"} superseded`
      : "";
  const where = cell?.study_name ? ` from ${cell.study_name}` : "";
  switch (kind) {
    case "tested":
    case "failed":
    case "selected":
      return `${head}selected, ${outcomeText(cell?.outcome)}; run ${shortId(cell?.run_id)}${where}${tail}`;
    case "excluded":
      return `${head}not in the sample, ${exclusionText(cell)}; newest usable run ${shortId(cell?.run_id)} ${outcomeText(cell?.outcome)}${where}${tail}`;
    case "attrition":
      return `${head}attrition, no usable run in ${cell?.attempts ?? 0} attempt${cell?.attempts === 1 ? "" : "s"}${
        cell?.run_id
          ? `; newest ${shortId(cell.run_id)} ${outcomeText(cell.outcome)}`
          : ""
      }${where}`;
    case "in_flight":
      return `${head}still running${cell?.run_id ? `, run ${shortId(cell.run_id)}` : ""}${where}`;
    default:
      return `${head}no attempt`;
  }
}

export const REASON_LABELS: Record<string, string> = {
  newer_usable_run: "Newer usable run",
  unusable_attempt: "Unusable attempt",
  in_flight: "Still running",
};

export function reasonLabel(reason: string): string {
  return REASON_LABELS[reason] ?? reason.replace(/_/g, " ");
}

/** The group's experiments by name (ids the payload cannot name stay as short ids). */
export function studyNames(
  ids: string[],
  studies: { id: string; name: string }[],
): { id: string; name: string }[] {
  const byId = new Map(studies.map((s) => [s.id, s.name]));
  return ids.map((id) => ({ id, name: byId.get(id) ?? shortId(id) }));
}

/** Min and max selected tasks over groups ("24–30"), or one number when equal. */
export function taskRange(groups: { selected: number }[]): string {
  if (groups.length === 0) return "—";
  const v = groups.map((g) => g.selected);
  const lo = Math.min(...v);
  const hi = Math.max(...v);
  return lo === hi ? String(lo) : `${lo}–${hi}`;
}

/** The server's "Sample selection…" tables of a report, as links for the selection section (each table appears once). */
export function selectionTableLinks(
  tables: { title: string }[],
): { index: number; label: string }[] {
  const out: { index: number; label: string }[] = [];
  tables.forEach((t, index) => {
    const m = /^Sample selection(?::\s*(.+))?$/.exec(t.title.trim());
    if (!m) return;
    const rest = m[1]?.trim();
    out.push({
      index,
      label: rest ? rest.charAt(0).toUpperCase() + rest.slice(1) : "Summary",
    });
  });
  return out;
}
