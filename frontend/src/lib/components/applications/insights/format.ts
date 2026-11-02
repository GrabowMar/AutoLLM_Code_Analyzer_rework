/**
 * Small display helpers for the application-page insight cards. Missing
 * values render as "—" (unknown), never as 0.
 */

/** Seconds → "850ms" / "42.3s" / "3m 12s" / "1h 28m". */
export function fmtSeconds(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  if (seconds < 0.9995) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 59.95) return `${seconds.toFixed(1)}s`;
  // Round to the shown unit first, so 119.6 s is "2m 0s" (never "1m 60s").
  if (seconds < 3570) {
    const total = Math.round(seconds);
    return `${Math.floor(total / 60)}m ${total % 60}s`;
  }
  const minutes = Math.round(seconds / 60);
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/** Integer-ish counts with thousands separators; null → "—". */
export function fmtInt(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return Math.round(n).toLocaleString();
}

/** A number with up to *decimals* fraction digits; null → "—". */
export function fmtNum(n: number | null | undefined, decimals = 2): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: decimals });
}

/** Bytes → "512 B" / "4.2 KB" / "255.3 MB". */
export function fmtBytes(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

/** USD amounts as the agent cards print them: "$0.00312" below a cent, "$0.104" above; null → "—". */
export function fmtUsd(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `$${v.toFixed(v < 0.01 ? 5 : 3)}`;
}

/** Share a/b as a whole percent ("—" when b is 0 or either is unknown). */
export function fmtShare(
  a: number | null | undefined,
  b: number | null | undefined,
): string {
  if (a == null || b == null || !b) return "—";
  return `${Math.round((a / b) * 100)}%`;
}

/** First *n* characters of a hash (sha256 / fingerprint), "—" when absent. */
export function shortHash(value: unknown, n = 12): string {
  if (typeof value !== "string" || !value) return "—";
  const bare = value.startsWith("sha256:") ? value.slice(7) : value;
  return bare.slice(0, n);
}

/** Humanize a snake_case key: "max_ccn_function" → "Max ccn function". */
export function humanizeKey(key: string): string {
  const text = key.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Whether *v* is a plain JSON object (not an array, not null). */
export function isPlainObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

/** One scalar for display: numbers localized, booleans as yes/no, null as "—", others stringified. */
export function scalarText(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "number")
    return Number.isInteger(v)
      ? v.toLocaleString()
      : v.toLocaleString(undefined, { maximumFractionDigits: 4 });
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "string") return v === "" ? "—" : v;
  return JSON.stringify(v);
}

/**
 * Output tokens per LLM-second: the decode-side throughput of an agent run,
 * from the gateway's LLM time (`result_data.agent.llm_seconds`, else the
 * trajectory's `time.llm_s`). `null` when either side is unknown.
 */
export function outputTokensPerLlmSecond(
  completionTokens: number | null | undefined,
  llmSeconds: number | null | undefined,
): number | null {
  if (completionTokens == null || llmSeconds == null || !(llmSeconds > 0))
    return null;
  return completionTokens / llmSeconds;
}

/** LLM seconds of a run: gateway total first, then the trajectory split; null when neither was recorded. */
export function llmSecondsOf(
  resultData: Record<string, any> | null | undefined,
): number | null {
  const agent = resultData?.agent;
  if (!isPlainObject(agent)) return null;
  const direct = agent.llm_seconds;
  if (typeof direct === "number" && Number.isFinite(direct)) return direct;
  const traj = isPlainObject(agent.trajectory) ? agent.trajectory : null;
  const time = traj && isPlainObject(traj.time) ? traj.time : null;
  const split = time?.llm_s;
  return typeof split === "number" && Number.isFinite(split) ? split : null;
}
