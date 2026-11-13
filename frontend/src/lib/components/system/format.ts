/** Formatting helpers of the System page (bytes and durations). */

export function formatBytes(b: number | null | undefined): string {
  if (b == null || !Number.isFinite(b)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = b;
  for (const u of units) {
    if (Math.abs(v) < 1024)
      return `${v.toFixed(v < 10 && u !== "B" ? 1 : 0)} ${u}`;
    v /= 1024;
  }
  return `${v.toFixed(1)} PB`;
}

export function formatSpan(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m`;
  return `${s}s`;
}

/** "14g" / "512m" → bytes (Docker size strings); null when unparseable. */
export function parseDockerSize(v: string | null | undefined): number | null {
  const m = /^\s*(\d+(?:\.\d+)?)\s*([bkmgt]?)b?\s*$/i.exec(v ?? "");
  if (!m) return null;
  const pow = { "": 0, b: 0, k: 1, m: 2, g: 3, t: 4 }[
    m[2].toLowerCase() as "" | "b" | "k" | "m" | "g" | "t"
  ];
  return Number(m[1]) * 1024 ** pow;
}
