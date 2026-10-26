/**
 * The one set of number / time / hash formatters for every analysis UI
 * (runs, studies, profiles, tools, insights).
 *
 * Research-validity rule: `null`, `undefined` and non-finite numbers are
 * missing data. Every formatter here returns {@link NA} ("—") for them, never
 * "0"; render the marker through `NaValue` so it carries a reason. The
 * insights helpers (`insights/format.ts`) wrap these and return `null`
 * instead, for `MaybeValue`.
 */

/** The missing-value marker. */
export const NA = "—";

/** Default tooltip of a missing value when no specific reason is known (see `NaValue`). */
export const NA_REASON =
  "Not available (NA): the value could not be measured or computed (e.g. a required tool failed or n is too small). It is never counted as 0.";

export function isMissing(value: unknown): value is null | undefined {
  return (
    value === null ||
    value === undefined ||
    (typeof value === "number" && !Number.isFinite(value))
  );
}

/** Integer with grouping (1,234). */
export function fmtInt(value: number | null | undefined): string {
  if (isMissing(value)) return NA;
  return Math.round(value).toLocaleString();
}

/** Number with at most `digits` fraction digits (grouped). */
export function fmtNum(value: number | null | undefined, digits = 1): string {
  if (isMissing(value)) return NA;
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
}

/**
 * A measured number with precision by magnitude: 1234.5 → "1,235",
 * 207.7151 → "207.7", 25.51 → "25.51", 0.0909 → "0.0909". With `digits`,
 * the same as {@link fmtNum}.
 */
export function fmtFloat(
  value: number | null | undefined,
  digits?: number,
): string {
  if (isMissing(value)) return NA;
  if (digits !== undefined) return fmtNum(value, digits);
  const abs = Math.abs(value);
  if (abs === 0) return "0";
  if (abs >= 1000) return Math.round(value).toLocaleString();
  if (abs >= 100)
    return value.toLocaleString(undefined, { maximumFractionDigits: 1 });
  if (abs >= 1)
    return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return String(Number(value.toPrecision(3)));
}

/** Share in [0, 1] as a percentage: 0.8333 → "83.3%", 1 → "100%". */
export function fmtPct(value: number | null | undefined, digits = 1): string {
  if (isMissing(value)) return NA;
  const fixed = Number((value * 100).toFixed(digits));
  return `${fixed.toLocaleString(undefined, { maximumFractionDigits: digits })}%`;
}

/** A value that already is a percentage (jscpd duplication): 12.5 → "12.5%". */
export function fmtPercent(
  value: number | null | undefined,
  digits = 1,
): string {
  if (isMissing(value)) return NA;
  return `${fmtNum(value, digits)}%`;
}

/** p-value: "< 0.001" below the display floor, else 3 decimals. */
export function fmtP(value: number | null | undefined): string {
  if (isMissing(value)) return NA;
  if (value < 0.001) return "< 0.001";
  return value.toFixed(3);
}

/** `[lo, hi]` interval; NA unless both ends are numbers. */
export function fmtInterval(
  value: unknown,
  fmt: (n: number) => string = (n) => fmtFloat(n),
): string {
  if (!Array.isArray(value) || value.length !== 2) return NA;
  const [lo, hi] = value as unknown[];
  if (typeof lo !== "number" || typeof hi !== "number") return NA;
  return `[${fmt(lo)}, ${fmt(hi)}]`;
}

/** US dollars with enough precision for per-call LLM costs; a known $0 stays "$0.00". */
export function fmtUsd(value: number | null | undefined): string {
  if (isMissing(value)) return NA;
  const abs = Math.abs(value);
  if (abs === 0) return "$0.00";
  if (abs >= 100) return `$${value.toFixed(0)}`;
  if (abs >= 1) return `$${value.toFixed(2)}`;
  if (abs >= 0.01) return `$${value.toFixed(4)}`;
  return `$${Number(value.toPrecision(3))}`;
}

/** Seconds → "420 ms" / "42 s" / "3 min 12 s" / "1 h 05 min". */
export function fmtSeconds(value: number | null | undefined): string {
  if (isMissing(value)) return NA;
  const s = Math.max(0, value);
  if (s < 1) return `${Math.round(s * 1000)} ms`;
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)} s`;
  const minutes = Math.floor(s / 60);
  if (minutes < 60) return `${minutes} min ${Math.round(s % 60)} s`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${String(minutes % 60).padStart(2, "0")} min`;
}

/** Bytes → "1.4 GB". */
export function fmtBytes(value: number | null | undefined): string {
  if (isMissing(value)) return NA;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = value;
  let unit = 0;
  while (Math.abs(n) >= 1024 && unit < units.length - 1) {
    n /= 1024;
    unit += 1;
  }
  return `${n.toFixed(n >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

/** ISO timestamp → locale date + time. */
export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return NA;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** ISO timestamp → "5 Mar 2026". */
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return NA;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** Compact relative time ("5m ago"); the date after a month. */
export function fmtAgo(iso: string | null | undefined): string {
  if (!iso) return NA;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const minutes = Math.floor((Date.now() - t) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

/** First characters of a sha256 (a `sha256:` prefix is dropped) or uuid. */
export function shortHash(
  value: string | null | undefined,
  length = 12,
): string {
  if (!value) return NA;
  const clean = value.startsWith("sha256:") ? value.slice(7) : value;
  return clean.slice(0, length);
}

/** First 8 characters of an id. */
export function shortId(value: string | null | undefined): string {
  return shortHash(value, 8);
}
