// Pure helpers for the agent trajectory views (Behaviour card, Trajectory
// timeline, agent_call artifacts, model behaviour panel). Missing values are
// rendered as "—", never as 0.

export const CATEGORY_LABELS: Record<string, string> = {
  explore: "Explore / read",
  write_file: "Write file",
  edit: "Edit file",
  install: "Install deps",
  registry_lookup: "Registry lookup",
  build: "Build",
  run_server: "Run server",
  probe_http: "Probe HTTP",
  test: "Test",
  syntax_check: "Syntax / lint",
  process: "Process mgmt",
  git: "Git",
  todo: "Todo list",
  submit: "Submit",
  other: "Other",
};

export const OUTCOME_LABELS: Record<string, string> = {
  ok: "ok",
  no_match: "no match (exit 1)",
  nonzero_exit: "non-zero exit",
  killed: "killed / timed out",
  policy_denial: "denied by harness policy",
  edit_no_match: "edit did not match",
  not_found: "command not found",
  tool_error: "tool error",
  output_error: "output shows failure",
  not_executed: "not executed",
  unknown: "outcome unknown",
};

export const ROLE_LABELS: Record<string, string> = {
  main: "main",
  summarizer: "summarizer",
  compaction: "compaction",
  title: "title",
  subagent: "subagent",
};

export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category.replace(/_/g, " ");
}

export function outcomeLabel(outcome: string): string {
  return OUTCOME_LABELS[outcome] ?? outcome.replace(/_/g, " ");
}

export function isNum(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function fmtInt(value: unknown): string {
  return isNum(value) ? Math.round(value).toLocaleString() : "—";
}

export function fmtPct(value: unknown, digits = 0): string {
  return isNum(value) ? `${(value * 100).toFixed(digits)}%` : "—";
}

export function fmtNum(value: unknown, digits = 1): string {
  return isNum(value) ? value.toFixed(digits) : "—";
}

/** "2h 05m", "13m 37s", "45.2 s", "—". */
export function fmtSeconds(value: unknown): string {
  if (!isNum(value)) return "—";
  if (value < 60)
    return `${value < 10 ? value.toFixed(1) : Math.round(value)} s`;
  const total = Math.round(value);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

/** "+1:15" style offset from the first call. */
export function fmtOffset(value: unknown): string {
  if (!isNum(value)) return "—";
  const total = Math.max(0, Math.round(value));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `+${m}:${String(s).padStart(2, "0")}`;
}

export function fmtBool(value: unknown): string {
  return value === true ? "yes" : value === false ? "no" : "—";
}

/** `[key, count]` pairs, largest first (ties keep the given order). */
export function sortedCounts(
  counts: Record<string, number> | null | undefined,
): [string, number][] {
  return Object.entries(counts ?? {})
    .filter(([, n]) => isNum(n) && n > 0)
    .sort((a, b) => b[1] - a[1]);
}

export interface EffectiveCount {
  value: number | null;
  source: "harness" | "gateway" | null;
  note: string | null;
}

/**
 * Tool-call count to show for a run. The harness's self-reported count wins when
 * it exists; when it is missing/zero but the gateway log shows tool calls
 * (mini-swe-agent runs that lost traj.json), the gateway count is shown instead.
 */
export function effectiveToolCalls(
  harnessCount: unknown,
  gatewayCount: unknown,
): EffectiveCount {
  const harness = isNum(harnessCount) ? harnessCount : null;
  const gateway = isNum(gatewayCount) ? gatewayCount : null;
  if (harness && harness > 0) {
    const note =
      gateway != null && gateway !== harness ? `gateway log: ${gateway}` : null;
    return { value: harness, source: "harness", note };
  }
  if (gateway != null && gateway > 0) {
    return {
      value: gateway,
      source: "gateway",
      note: "harness report missing — counted from the gateway log",
    };
  }
  if (harness != null || gateway != null)
    return {
      value: harness ?? gateway,
      source: harness != null ? "harness" : "gateway",
      note: null,
    };
  return { value: null, source: null, note: null };
}

/** Steps: harness-reported steps, else the gateway's main-role (model turn) count. */
export function effectiveSteps(
  harnessSteps: unknown,
  modelTurns: unknown,
): EffectiveCount {
  const harness = isNum(harnessSteps) ? harnessSteps : null;
  const turns = isNum(modelTurns) ? modelTurns : null;
  if (harness && harness > 0)
    return { value: harness, source: "harness", note: null };
  if (turns != null && turns > 0) {
    return {
      value: turns,
      source: "gateway",
      note: "harness report missing — model turns from the gateway log",
    };
  }
  return {
    value: harness ?? turns,
    source: harness != null ? "harness" : turns != null ? "gateway" : null,
    note: null,
  };
}

/** "2 of 3 LLM calls were summarizer calls" for runs with auxiliary roles, else null. */
export function auxiliaryCallsNote(
  roles: Record<string, number> | null | undefined,
  llmCalls: unknown,
): string | null {
  const entries = Object.entries(roles ?? {}).filter(
    ([role, n]) => role !== "main" && isNum(n) && n > 0,
  );
  if (!entries.length) return null;
  const aux = entries.reduce((sum, [, n]) => sum + n, 0);
  const total = isNum(llmCalls) ? llmCalls : aux + (roles?.main ?? 0);
  const kinds = entries.map(([role, n]) => `${n} ${role}`).join(", ");
  return `${aux} of ${total} LLM calls were auxiliary (${kinds}), not agent turns`;
}

export interface LinePoint {
  x: number;
  y: number;
  value: number;
  index: number;
}

export interface LineGeometry {
  points: LinePoint[];
  path: string;
  max: number;
  yTicks: { y: number; value: number }[];
}

/** SVG geometry for a series drawn into [padLeft..width-padRight] × [padTop..height-padBottom]. */
export function lineGeometry(
  series: number[],
  width: number,
  height: number,
  pad = { left: 44, right: 8, top: 8, bottom: 18 },
): LineGeometry {
  const values = series.filter(isNum);
  const max = values.length ? Math.max(...values) : 0;
  const top = niceCeil(max);
  const innerW = Math.max(1, width - pad.left - pad.right);
  const innerH = Math.max(1, height - pad.top - pad.bottom);
  const n = values.length;
  const points = values.map((value, index) => ({
    x: pad.left + (n > 1 ? (index / (n - 1)) * innerW : innerW / 2),
    y: pad.top + innerH - (top > 0 ? (value / top) * innerH : 0),
    value,
    index,
  }));
  const path = points
    .map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    .join(" ");
  const yTicks = [0, 0.5, 1].map((f) => ({
    y: pad.top + innerH - f * innerH,
    value: Math.round(top * f),
  }));
  return { points, path, max, yTicks };
}

/** Round up to 1/2/5 × 10^k (axis top). */
export function niceCeil(value: number): number {
  if (!isNum(value) || value <= 0) return 0;
  const exp = Math.pow(10, Math.floor(Math.log10(value)));
  for (const step of [1, 2, 5, 10]) {
    if (step * exp >= value) return step * exp;
  }
  return 10 * exp;
}

export function fmtTokensShort(value: unknown): string {
  if (!isNum(value)) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1000)
    return `${(value / 1000).toFixed(value >= 10_000 ? 0 : 1)}k`;
  return String(Math.round(value));
}

export interface CallTelemetryView {
  duration_s: number | null;
  provider: string | null;
  served_model: string | null;
  ttft_ms: number | null;
  generation_ms: number | null;
  decode_tps: number | null;
  cached: number | null;
  reasoning: number | null;
  retries: number | null;
  finish_reason: string | null;
  list_cost: number | null;
  openrouter_status: string | null;
}

function rec(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function num(value: unknown): number | null {
  return isNum(value) ? value : null;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

/** Defensive read of GenerationArtifact.telemetry (contract C1); null when nothing usable is there. */
export function readTelemetry(telemetry: unknown): CallTelemetryView | null {
  const t = rec(telemetry);
  if (!Object.keys(t).length) return null;
  const tokens = rec(t.tokens);
  const or = rec(t.openrouter);
  const view: CallTelemetryView = {
    duration_s: num(t.duration_s),
    provider: str(t.provider) ?? str(or.provider_name),
    served_model: str(t.served_model),
    ttft_ms: num(or.latency_ms),
    generation_ms: num(or.generation_time_ms),
    decode_tps: num(or.decode_tps),
    cached: num(tokens.cached),
    reasoning: num(tokens.reasoning),
    retries: num(t.upstream_retries),
    finish_reason: str(t.finish_reason),
    list_cost: num(t.list_cost),
    openrouter_status: str(or.status),
  };
  const any = Object.values(view).some((v) => v != null);
  return any ? view : null;
}

/** Pretty JSON for tool-call arguments stored as a JSON string (falls back to the raw text). */
export function prettyArgs(raw: unknown): string {
  if (typeof raw !== "string")
    return raw == null ? "" : JSON.stringify(raw, null, 2);
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}
