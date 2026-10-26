/**
 * Display helpers shared by the endpoint pages, EndpointChip / EndpointCard and
 * the experiment monitor (Track D): labels, tones, relative times, slug rules
 * and patching an endpoint row from its SSE events. Pure functions only.
 */
import { PROBE_FINAL_STATUSES } from "$lib/api/inference";
import type {
  EndpointConnection,
  EndpointKind,
  EndpointStatus,
  EndpointSummary,
  SessionOut,
} from "$lib/api/types/inference";
import type { BlockedReason } from "$lib/api/types/execution";
import type { ProbeResult } from "$lib/api/types/inference";
import type { Tone } from "$lib/constants/colors";

export const KIND_LABELS: Record<EndpointKind, string> = {
  openrouter: "OpenRouter",
  ollama: "Ollama",
  openai_compatible: "OpenAI-compatible",
};

export const CONNECTION_LABELS: Record<EndpointConnection, string> = {
  relay: "Relay",
  direct: "Direct",
};

export function kindLabel(kind: string | null | undefined): string {
  return KIND_LABELS[kind as EndpointKind] ?? (kind || "Unknown");
}

const STATUS_TONES: Record<EndpointStatus, Tone> = {
  online: "success",
  degraded: "warning",
  offline: "danger",
  disabled: "neutral",
};

export function endpointStatusTone(status: string | null | undefined): Tone {
  return STATUS_TONES[status as EndpointStatus] ?? "neutral";
}

/** Plain-words scheduler blocked reasons (spec §G.5 lane footers). */
export const BLOCKED_REASON_LABELS: Record<BlockedReason, string> = {
  sandbox_full: "Sandbox slots full",
  llm_threads_full: "LLM job threads full",
  endpoint_offline: "Waiting for relay",
  endpoint_full: "Endpoint at capacity",
  model_not_served: "Model not on the relay",
  residency_wait: "Waiting for a model swap slot",
  warming_model: "Loading model",
  experiment_cap: "Experiment concurrency cap reached",
  circuit_open: "Paused after repeated errors",
  draining: "Executor draining",
  retry_backoff: "Retrying after backoff",
};

export function blockedReasonText(reason: string | null | undefined): string {
  if (!reason) return "";
  return (
    BLOCKED_REASON_LABELS[reason as BlockedReason] ?? reason.replace(/_/g, " ")
  );
}

/** "just now" / "4 s ago" / "3 min ago" / "2 h ago" / "5 d ago"; "never" for null. */
export function formatAgo(
  iso: string | null | undefined,
  now: number = Date.now(),
): string {
  if (!iso) return "never";
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return "—";
  const s = Math.max(0, Math.round((now - at) / 1000));
  if (s < 2) return "just now";
  if (s < 60) return `${s} s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.floor(h / 24)} d ago`;
}

/** Bytes → "18.2 GB" / "512 MB"; "—" for null. */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes)) return "—";
  const gb = bytes / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  return `${Math.round(bytes / 1024 ** 2)} MB`;
}

/** "sha256:abcdef0123…" → "abcdef01" (the first 8 hex characters). */
export function shortDigest(digest: string | null | undefined): string {
  if (!digest) return "";
  return digest.replace(/^sha256:/, "").slice(0, 8);
}

/** Context sizes → "32K" / "128K" / "4,096". */
export function formatCtx(n: number | null | undefined): string {
  if (n == null || n <= 0) return "—";
  if (n >= 1024 && n % 1024 === 0) return `${n / 1024}K`;
  return n.toLocaleString();
}

// ── slugs (immutable after create; backend pattern ^[a-z0-9][a-z0-9-]{1,38}$) ──

export const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{1,38}$/;
export const SLUG_MAX_LENGTH = 39;

/** A valid endpoint slug derived from a display name ("" when nothing usable is left). */
export function endpointSlugFromName(name: string): string {
  const slug = name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+/, "")
    .slice(0, SLUG_MAX_LENGTH)
    .replace(/-+$/, "");
  return SLUG_PATTERN.test(slug) ? slug : "";
}

export function slugError(slug: string): string {
  if (!slug) return "";
  if (slug.length < 2) return "At least 2 characters";
  if (slug.length > SLUG_MAX_LENGTH)
    return `At most ${SLUG_MAX_LENGTH} characters`;
  if (!SLUG_PATTERN.test(slug))
    return "Lowercase letters, digits and dashes; starts with a letter or digit";
  return "";
}

// ── live patching from SSE (`endpoint:<id>`, owner or `:public` variant) ──

/** Distinct relay hostnames of an endpoint's live sessions (owner/staff only). */
export function sessionHostnames(endpoint: EndpointSummary): string[] {
  const names = (endpoint.live.sessions ?? [])
    .map((s) => s.hostname)
    .filter(Boolean);
  return [...new Set(names)];
}

/**
 * The endpoint row with an SSE `endpoint` event applied. The owner payload
 * carries sessions and loaded models; the `:public` payload only counts, so
 * fields it lacks keep their previous value. Other event types return the
 * row unchanged (callers refetch on `session` events).
 */
export function applyEndpointEvent<T extends EndpointSummary>(
  endpoint: T,
  data: Record<string, unknown>,
): T {
  if (data.type !== "endpoint") return endpoint;
  const live = { ...endpoint.live };
  if (typeof data.inflight === "number") live.inflight_requests = data.inflight;
  if (typeof data.queued === "number") live.queued_requests = data.queued;
  if (Array.isArray(data.sessions)) {
    const sessions = data.sessions as NonNullable<
      EndpointSummary["live"]["sessions"]
    >;
    live.session_count = sessions.length;
    // The event carries the identity fields only; keep capacity / last seen.
    const previous = new Map((live.sessions ?? []).map((s) => [s.id, s]));
    const seenAt = new Date().toISOString();
    live.sessions = sessions.map((s) => {
      const prev = previous.get(s.id);
      return {
        ...s,
        capacity: prev?.capacity ?? 0,
        last_seen_at: prev?.last_seen_at ?? seenAt,
      };
    });
  } else if (typeof data.session_count === "number") {
    live.session_count = data.session_count;
  }
  if (Array.isArray(data.loaded)) {
    live.loaded = normalizeLoaded(data.loaded);
  }
  const next: T = { ...endpoint, live };
  if (typeof data.status === "string") {
    next.status = data.status as EndpointStatus;
  }
  if ("last_error" in data) {
    next.last_error = (data.last_error as string | null) ?? null;
  }
  return next;
}

/**
 * Coerce an event's `loaded` list to the REST `{name, size_vram, cpu_spill}`
 * shape (older servers sent bare names), dropping nameless and duplicate
 * entries so keyed `{#each}` blocks never see a duplicate key.
 */
export function normalizeLoaded(
  items: readonly unknown[],
): EndpointSummary["live"]["loaded"] {
  const out: EndpointSummary["live"]["loaded"] = [];
  const seen = new Set<string>();
  for (const item of items) {
    let entry: EndpointSummary["live"]["loaded"][number] | null = null;
    if (typeof item === "string") {
      entry = { name: item, size_vram: null, cpu_spill: false };
    } else if (
      item &&
      typeof item === "object" &&
      typeof (item as { name?: unknown }).name === "string"
    ) {
      const raw = item as {
        name: string;
        size_vram?: unknown;
        cpu_spill?: unknown;
      };
      entry = {
        name: raw.name,
        size_vram: typeof raw.size_vram === "number" ? raw.size_vram : null,
        cpu_spill: Boolean(raw.cpu_spill),
      };
    }
    if (!entry || !entry.name || seen.has(entry.name)) continue;
    seen.add(entry.name);
    out.push(entry);
  }
  return out;
}

/**
 * Hostnames and IPs of live sessions that their token never connected from
 * before (spec §K.13: "the UI flags a new IP or hostname for a token"). A
 * token's first-ever session is not flagged.
 */
export function newSessionHosts(sessions: readonly SessionOut[]): Set<string> {
  const flagged = new Set<string>();
  const byToken = new Map<string, SessionOut[]>();
  for (const s of sessions) {
    const list = byToken.get(s.token_prefix) ?? [];
    list.push(s);
    byToken.set(s.token_prefix, list);
  }
  for (const list of byToken.values()) {
    const ordered = [...list].sort(
      (a, b) => Date.parse(a.started_at) - Date.parse(b.started_at),
    );
    const seenHosts = new Set<string>();
    const seenIps = new Set<string>();
    ordered.forEach((s, i) => {
      if (i > 0 && !s.ended_at) {
        if (s.hostname && !seenHosts.has(s.hostname)) flagged.add(s.hostname);
        if (s.client_ip && !seenIps.has(s.client_ip)) flagged.add(s.client_ip);
      }
      if (s.hostname) seenHosts.add(s.hostname);
      if (s.client_ip) seenIps.add(s.client_ip);
    });
  }
  return flagged;
}

/** Text colour per probe tone (static class names, so Tailwind sees them). */
export const PROBE_TONE_TEXT = {
  success: "text-success-fg",
  warning: "text-warning-fg",
  danger: "text-danger-fg",
} as const;

/**
 * One line for a probe (Test) result. `tone` is success for a reachable
 * endpoint, danger for a finished failure and warning while the request is
 * still queued or leased when the poll gave up (nothing answered yet).
 */
export function probeSummary(result: ProbeResult): {
  tone: "success" | "warning" | "danger";
  text: string;
} {
  if (result.status === "done") {
    const parts = ["Reachable"];
    if (result.latency_ms != null) {
      parts.push(`in ${result.latency_ms.toLocaleString()} ms`);
    }
    if (result.sample) parts.push(`· ${result.sample.slice(0, 60)}`);
    return { tone: "success", text: parts.join(" ") };
  }
  if (!PROBE_FINAL_STATUSES.has(result.status)) {
    return {
      tone: "warning",
      text: `No answer yet (${result.status}): check that the relay or the model server is running.`,
    };
  }
  return {
    tone: "danger",
    text: `Test ${result.status}${result.error ? `: ${result.error}` : ""}`,
  };
}

/**
 * Why a relay on another network could not use `url` as its `--server`, or
 * null when it looks fine. The relay requires https (plain http only for
 * localhost) and a host it can resolve, so internal names such as
 * `django:8000` or single-label hosts are flagged.
 */
export function relayServerUrlProblem(
  url: string | null | undefined,
): string | null {
  if (!url) return "The server URL is unknown.";
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return `"${url}" is not a valid URL.`;
  }
  const host = parsed.hostname.replace(/^\[|\]$/g, "").toLowerCase();
  const local = host === "localhost" || host === "127.0.0.1" || host === "::1";
  if (local)
    return `${host} is only reachable from this server, not from a model machine.`;
  if (parsed.protocol !== "https:")
    return "The relay only accepts an https:// server URL.";
  if (!host.includes(".") && !host.includes(":")) {
    return `"${host}" looks like an internal host name that a model machine cannot resolve.`;
  }
  return null;
}
