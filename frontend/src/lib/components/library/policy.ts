/**
 * Budget preset (ResourcePolicy) editing logic, shared by `ResourcePolicyEditor` and WP-15's builder step.
 * Pure functions over `ResourcePolicyContent` and the `KNOB_META` rows of `GET /budget/knobs/`:
 *
 * - knob paths: `getKnob` / `setKnob` (immutable; the `reasoning` knob covers mode/effort/max_tokens and
 *   leaves `reasoning.exclude`, its own knob, alone);
 * - grouping: the always-visible **Key limits** (`key_limit`) and the accordion groups (`POLICY_GROUPS`);
 * - inheritance: a knob is *inherited* when it equals the base (tier preset or the version it was edited
 *   from) and an *override* otherwise; `overrides`, `resetKnob`;
 * - validity classes (4.1): C/R knobs "affect treatment" (they enter `generation_policy_hash`), S knobs are
 *   study design (in `prereg_hash`), O knobs are operational. `run.max_cost_usd` is O under
 *   `budget_basis = tokens`;
 * - display: `formatKnobValue`, `inputKind`, `enumOptions`, `summaryLine` (the backend's `summary_line`).
 */
import type {
  KnobClass,
  KnobMeta,
  PolicyTier,
  ResourcePolicyContent,
  ResourcePolicyTier,
} from "$lib/api/v3/types";
import { POLICY_TIERS } from "$lib/api/v3/types";

// ── paths ────────────────────────────────────────────────────────────────

type Json = unknown;

function clone<T>(value: T): T {
  return value === undefined ? value : (JSON.parse(JSON.stringify(value)) as T);
}

/** Deep equality over JSON values (key order ignored). */
export function jsonEqual(a: Json, b: Json): boolean {
  if (a === b) return true;
  if (
    a === null ||
    b === null ||
    typeof a !== "object" ||
    typeof b !== "object"
  )
    return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((item, i) => jsonEqual(item, b[i]));
  }
  const ak = Object.keys(a as object);
  const bk = Object.keys(b as object);
  if (ak.length !== bk.length) return false;
  return ak.every(
    (k) =>
      Object.hasOwn(b as object, k) &&
      jsonEqual((a as Record<string, Json>)[k], (b as Record<string, Json>)[k]),
  );
}

/** The value of knob `key` (`reasoning` → `{mode, effort, max_tokens}`). */
export function getKnob(content: ResourcePolicyContent, key: string): Json {
  let node: Json = content;
  for (const part of key.split(".")) {
    if (node === null || typeof node !== "object") return undefined;
    node = (node as Record<string, Json>)[part];
  }
  if (key === "reasoning" && node && typeof node === "object") {
    const { exclude: _exclude, ...rest } = node as Record<string, Json>;
    return rest;
  }
  return node;
}

/** A copy of `content` with knob `key` set to `value`. */
export function setKnob(
  content: ResourcePolicyContent,
  key: string,
  value: Json,
): ResourcePolicyContent {
  const next = clone(content) as unknown as Record<string, Json>;
  const parts = key.split(".");
  let node = next;
  for (const part of parts.slice(0, -1)) {
    const child = node[part];
    if (child === null || typeof child !== "object") node[part] = {};
    node = node[part] as Record<string, Json>;
  }
  const last = parts[parts.length - 1];
  if (key === "reasoning") {
    node[last] = {
      ...(node[last] as Record<string, Json>),
      ...clone(value as Record<string, Json>),
    };
  } else {
    node[last] = clone(value);
  }
  return next as unknown as ResourcePolicyContent;
}

// ── classes ──────────────────────────────────────────────────────────────

export type BudgetBasis = "usd" | "tokens" | "auto";

/** The class of a knob under the policy's budget basis (`auto` counts as usd here: the conservative view). */
export function knobClass(
  meta: KnobMeta,
  budgetBasis: BudgetBasis = "usd",
): KnobClass {
  return budgetBasis === "tokens" && meta.class_when_tokens
    ? meta.class_when_tokens
    : meta.class;
}

/** C and R knobs enter `treatment_view()`, so `generation_policy_hash` and `config_fingerprint`. */
export function affectsTreatment(cls: KnobClass): boolean {
  return cls === "C" || cls === "R";
}

export interface ClassBadge {
  cls: KnobClass;
  label: string;
  /** Tooltip text. */
  help: string;
  tone: "treatment" | "design" | "operational";
}

const CLASS_HELP: Record<KnobClass, string> = {
  C: "Class C (confound): changes what the model sees or how it samples. It is part of the generation policy hash and the config fingerprint — runs with different values are different treatments.",
  R: "Class R (recorded limit): can change the outcome only through truncation or stopping. It is hashed into the fingerprint and the stop reason is recorded per run.",
  S: "Class S (stopping/design): a study-level rule that changes inference. It is part of the pre-registration hash and frozen by the confirmatory lock.",
  O: "Class O (operational): affects only cost, speed or scheduling. Recorded in the manifest, never hashed.",
};

export function classBadge(
  meta: KnobMeta,
  budgetBasis: BudgetBasis = "usd",
): ClassBadge {
  const cls = knobClass(meta, budgetBasis);
  let help = CLASS_HELP[cls];
  if (meta.class_when_tokens && meta.class_when_tokens !== meta.class) {
    help += ` Under budget basis "tokens" it is class ${meta.class_when_tokens} instead of ${meta.class}.`;
  }
  if (affectsTreatment(cls))
    return { cls, label: "affects treatment", help, tone: "treatment" };
  if (cls === "S") return { cls, label: "study design", help, tone: "design" };
  return { cls, label: "operational", help, tone: "operational" };
}

/** The knobs of class C/R (under `budgetBasis`) whose value differs from `base`: a new fingerprint. */
export function fingerprintChanges(
  value: ResourcePolicyContent,
  base: ResourcePolicyContent | undefined | null,
  knobs: KnobMeta[],
  budgetBasis: BudgetBasis = value.study?.budget_basis ?? "usd",
): string[] {
  if (!base) return [];
  return knobs
    .filter(
      (m) =>
        affectsTreatment(knobClass(m, budgetBasis)) &&
        !jsonEqual(getKnob(value, m.key), getKnob(base, m.key)),
    )
    .map((m) => m.key);
}

// ── groups ───────────────────────────────────────────────────────────────

export interface PolicyGroup {
  id: string;
  title: string;
  description: string;
}

export const KEY_LIMITS_GROUP: PolicyGroup = {
  id: "key",
  title: "Key limits",
  description:
    "Caps and settings that decide most of an experiment's cost; always visible.",
};

/** Accordion groups, in display order (spec 10.2 step 5). */
export const POLICY_GROUPS: PolicyGroup[] = [
  {
    id: "tokens",
    title: "Tokens & context",
    description:
      "Per-run token and request caps, per-call caps, the advertised context window and compaction.",
  },
  {
    id: "routing",
    title: "Routing & caching",
    description:
      "OpenRouter provider routing, price filters, serving tier and prompt caching.",
  },
  {
    id: "time",
    title: "Time",
    description: "Wall clock, idle and per-command timeouts.",
  },
  {
    id: "retries",
    title: "Retries",
    description:
      "Gateway retries before the first byte and whole-run infra retries.",
  },
  {
    id: "sandbox",
    title: "Sandbox",
    description:
      "Agent container memory and CPUs, network egress and the workspace size limit.",
  },
  {
    id: "concurrency",
    title: "Concurrency",
    description:
      "How many runs and evaluations of the experiment may run at once, per model, endpoint and provider.",
  },
  {
    id: "caches",
    title: "Caches",
    description:
      "Read-only dependency caches and reuse of images, parent builds and evaluations.",
  },
  {
    id: "study",
    title: "Experiment caps & estimate",
    description:
      "Token, run and wall-clock caps for the whole experiment, cap basis, budget alerts and the estimator.",
  },
  {
    id: "stop",
    title: "Stop rules & pilot gates",
    description:
      "What happens when the budget runs out, the stop rules, the pilot stage and its gates.",
  },
];

const GROUP_PREFIXES: [string, string][] = [
  ["run.max_workspace_bytes", "sandbox"],
  ["run.", "tokens"],
  ["call.", "tokens"],
  ["context.", "tokens"],
  ["reasoning", "tokens"],
  ["slot.", "tokens"],
  ["routing.", "routing"],
  ["caching.", "routing"],
  ["time.", "time"],
  ["retries.", "retries"],
  ["sandbox.", "sandbox"],
  ["network_policy", "sandbox"],
  ["concurrency.", "concurrency"],
  ["caches.", "caches"],
  ["study.on_budget_exhausted", "stop"],
  ["study.stop_rules", "stop"],
  ["pilot.", "stop"],
  ["study.", "study"],
  ["cell.", "study"],
  ["estimate.", "study"],
];

/** The group a knob is shown in: `key` for key limits, else its accordion group (never undefined). */
export function groupOf(meta: KnobMeta): string {
  if (meta.key_limit) return KEY_LIMITS_GROUP.id;
  for (const [prefix, group] of GROUP_PREFIXES) {
    if (meta.key === prefix || meta.key.startsWith(prefix)) return group;
  }
  return "study";
}

/** `{groupId: knobs}` in the catalogue's order (key limits first). */
export function groupKnobs(knobs: KnobMeta[]): Map<string, KnobMeta[]> {
  const out = new Map<string, KnobMeta[]>([
    [KEY_LIMITS_GROUP.id, []],
    ...POLICY_GROUPS.map((g) => [g.id, []] as [string, KnobMeta[]]),
  ]);
  for (const meta of knobs) out.get(groupOf(meta))!.push(meta);
  return out;
}

// ── inheritance ──────────────────────────────────────────────────────────

export type KnobState = "inherited" | "override";

export function knobState(
  value: ResourcePolicyContent,
  base: ResourcePolicyContent | null | undefined,
  key: string,
): KnobState {
  if (!base) return "inherited";
  return jsonEqual(getKnob(value, key), getKnob(base, key))
    ? "inherited"
    : "override";
}

/** Keys of every knob whose value differs from `base` (catalogue order). */
export function overrides(
  value: ResourcePolicyContent,
  base: ResourcePolicyContent | null | undefined,
  knobs: KnobMeta[],
): string[] {
  if (!base) return [];
  return knobs
    .filter((m) => knobState(value, base, m.key) === "override")
    .map((m) => m.key);
}

/** `value` with knob `key` restored to the base value. */
export function resetKnob(
  value: ResourcePolicyContent,
  base: ResourcePolicyContent,
  key: string,
): ResourcePolicyContent {
  return setKnob(value, key, getKnob(base, key));
}

/** The tier whose preset differs from `content` in the fewest knobs (`diff_from_tier` without a tier). */
export function closestTier(
  content: ResourcePolicyContent,
  tiers: Record<PolicyTier, ResourcePolicyContent>,
  knobs: KnobMeta[],
): PolicyTier {
  let best: PolicyTier = "frugal";
  let bestCount = Number.POSITIVE_INFINITY;
  for (const tier of POLICY_TIERS) {
    const count = overrides(content, tiers[tier], knobs).length;
    if (count < bestCount) {
      best = tier;
      bestCount = count;
    }
  }
  return best;
}

/** The base a policy is compared against: its own tier's preset, or the closest tier for `custom`. */
export function baseTier(
  tier: ResourcePolicyTier | string | null | undefined,
  content: ResourcePolicyContent,
  tiers: Record<PolicyTier, ResourcePolicyContent>,
  knobs: KnobMeta[],
): PolicyTier {
  return POLICY_TIERS.includes(tier as PolicyTier)
    ? (tier as PolicyTier)
    : closestTier(content, tiers, knobs);
}

// ── input kinds and display ──────────────────────────────────────────────

export type KnobInputKind =
  | "number"
  | "bool"
  | "enum"
  | "text"
  | "list"
  | "fractions"
  | "reasoning"
  | "max_price"
  | "study_cap"
  | "units"
  | "bytes"
  | "nano_cpus"
  | "json";

/** How the editor renders a knob (from its display type and unit). */
export function inputKind(meta: KnobMeta): KnobInputKind {
  const t = meta.type.trim();
  if (meta.key === "reasoning") return "reasoning";
  if (meta.key === "routing.max_price") return "max_price";
  if (meta.key === "study.max_cost_usd") return "study_cap";
  if (meta.key === "pilot.units") return "units";
  if (meta.key === "sandbox.nano_cpus") return "nano_cpus";
  if (meta.unit === "bytes" && t.startsWith("int")) return "bytes";
  if (t === "bool") return "bool";
  if (t.startsWith("enum ")) return "enum";
  if (t === "list[str]" || t === "set") return "list";
  if (t === "list[float]") return "fractions";
  if (t.startsWith("int") || t.startsWith("float")) return "number";
  if (t === "str") return "text";
  return "json";
}

/** `"enum a,b,c"` → `["a", "b", "c"]`. */
export function enumOptions(meta: KnobMeta): string[] {
  const t = meta.type.trim();
  return t.startsWith("enum ")
    ? t
        .slice(5)
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean)
    : [];
}

export function isNullable(meta: KnobMeta): boolean {
  return /\|\s*null\b/.test(meta.type) || meta.type.includes("null");
}

export function isInteger(meta: KnobMeta): boolean {
  return meta.type.trim().startsWith("int");
}

/** Lower/upper bounds the contract enforces (`int 0-8`, fractions 0..1). */
export function numberBounds(meta: KnobMeta): {
  min: number;
  max?: number;
  step: number;
} {
  const range = /^int\s+(\d+)-(\d+)/.exec(meta.type);
  if (range) return { min: Number(range[1]), max: Number(range[2]), step: 1 };
  if (meta.unit === "fraction") return { min: 0, max: 1, step: 0.01 };
  if (isInteger(meta))
    return { min: meta.key.endsWith("max_aux_requests") ? 0 : 1, step: 1 };
  return { min: 0, step: meta.unit === "usd" ? 0.01 : 0.1 };
}

const MIB = 1024 * 1024;

export function formatBytes(n: number): string {
  if (n >= 1024 * MIB && n % (1024 * MIB) === 0)
    return `${n / (1024 * MIB)} GB`;
  if (n >= MIB) return `${Math.round((n / MIB) * 10) / 10} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}

export function formatCount(n: number): string {
  if (n >= 1_000_000 && n % 100_000 === 0) return `${n / 1_000_000}M`;
  if (n >= 10_000 && n % 1000 === 0) return `${n / 1000}k`;
  return n.toLocaleString("en-US");
}

export function formatMoney(n: number): string {
  return n < 100
    ? `$${n.toFixed(2)}`
    : `$${Math.round(n).toLocaleString("en-US")}`;
}

function formatSeconds(n: number): string {
  if (n >= 3600 && n % 3600 === 0) return `${n / 3600} h`;
  if (n >= 60 && n % 60 === 0) return `${n / 60} min`;
  return `${n} s`;
}

/** A knob value for summaries and "base: …" hints ("$0.30", "3M tokens", "20 min", "unset"). */
export function formatKnobValue(meta: KnobMeta, value: Json): string {
  if (value === null || value === undefined) return "unset";
  if (meta.key === "reasoning" && typeof value === "object") {
    const r = value as {
      mode?: string;
      effort?: string | null;
      max_tokens?: number | null;
    };
    if (r.mode === "effort") return `effort ${r.effort ?? "?"}`;
    if (r.mode === "max_tokens")
      return `${formatCount(r.max_tokens ?? 0)} reasoning tokens`;
    return "provider default";
  }
  if (meta.key === "sandbox.nano_cpus" && typeof value === "number")
    return `${value / 1e9} CPU`;
  if (typeof value === "boolean") return value ? "on" : "off";
  if (typeof value === "number") {
    switch (meta.unit) {
      case "usd":
        return formatMoney(value);
      case "bytes":
        return formatBytes(value);
      case "s":
        return formatSeconds(value);
      case "fraction":
        return `${Math.round(value * 100)}%`;
      case "tokens":
        return `${formatCount(value)} tokens`;
      case null:
        return String(value);
      default:
        return `${formatCount(value)} ${meta.unit.replace(/_/g, " ")}`;
    }
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return "none";
    if (meta.unit === "fraction")
      return value.map((v) => `${Math.round(Number(v) * 100)}%`).join(", ");
    if (value.every((v) => typeof v !== "object")) return value.join(", ");
    return `${value.length} rule${value.length === 1 ? "" : "s"}`;
  }
  if (typeof value === "object") {
    const p = value as { prompt?: number; completion?: number };
    if (meta.key === "routing.max_price")
      return `$${p.prompt}/$${p.completion} per Mtok`;
    return JSON.stringify(value);
  }
  if (meta.key === "study.max_cost_usd" && value === "auto") return "auto";
  return String(value);
}

const TIER_LABEL: Record<string, string> = {
  frugal: "Frugal",
  standard: "Standard",
  generous: "Generous",
  custom: "Custom",
};

export function tierLabel(tier: string | null | undefined): string {
  return TIER_LABEL[tier ?? "custom"] ?? "Custom";
}

/**
 * "Frugal v1 · $0.30/run · auto study cap · 2 parallel · effort low · no overrides" — the same line as the
 * backend's `budget/policy.summary_line`. Overrides are counted against `base`; without one (a `custom`
 * preset) they are counted against `tiers[baseTier(tier, …)]` — the policy's own tier, else the closest
 * tier, exactly like the backend's `diff_from_tier(content, None)`. Pass `tiers` (the knobs payload's
 * presets) so custom presets never read "no overrides" by accident.
 */
export function summaryLine(
  content: ResourcePolicyContent,
  opts: {
    name?: string | null;
    version?: number | null;
    tier?: string | null;
    base?: ResourcePolicyContent | null;
    tiers?: Record<PolicyTier, ResourcePolicyContent> | null;
    knobs: KnobMeta[];
  },
): string {
  const name = opts.name || tierLabel(opts.tier);
  const head = opts.version ? `${name} v${opts.version}` : name;
  const cap = content.study.max_cost_usd;
  const capText =
    cap === "auto"
      ? "auto experiment cap"
      : `${formatMoney(Number(cap))} experiment cap`;
  const r = content.reasoning;
  const reasoning =
    r.mode === "effort"
      ? `effort ${r.effort}`
      : r.mode === "max_tokens"
        ? `reasoning ${r.max_tokens} tok`
        : "provider reasoning";
  const base =
    opts.base ??
    (opts.tiers
      ? opts.tiers[baseTier(opts.tier, content, opts.tiers, opts.knobs)]
      : null);
  const count = overrides(content, base, opts.knobs).length;
  const parts = [
    head,
    `${formatMoney(content.run.max_cost_usd)}/run`,
    capText,
    `${content.concurrency.study_max_parallel} parallel`,
    reasoning,
  ];
  if (!content.pilot.enabled) parts.push("no pilot");
  parts.push(
    count === 0 ? "no overrides" : `${count} override${count === 1 ? "" : "s"}`,
  );
  return parts.join(" · ");
}

/** Client-side mirror of the blocking policy-lint rules the editor can check without a model (4.3). */
export function policyHints(
  content: ResourcePolicyContent,
): { key: string; severity: "error" | "warn"; message: string }[] {
  const out: { key: string; severity: "error" | "warn"; message: string }[] =
    [];
  const r = content.reasoning;
  if (
    (r.mode === "effort" && (r.effort === null || r.max_tokens !== null)) ||
    (r.mode === "max_tokens" && (r.max_tokens === null || r.effort !== null))
  ) {
    out.push({
      key: "reasoning",
      severity: "error",
      message:
        "Set exactly one of effort or max tokens for the reasoning mode.",
    });
  }
  if (
    r.mode === "provider_default" &&
    (r.effort !== null || r.max_tokens !== null)
  ) {
    out.push({
      key: "reasoning",
      severity: "error",
      message:
        "Provider-default reasoning takes neither effort nor max tokens.",
    });
  }
  if (
    r.mode === "max_tokens" &&
    r.max_tokens !== null &&
    r.max_tokens >= content.call.max_output_tokens
  ) {
    out.push({
      key: "reasoning",
      severity: "error",
      message: "The reasoning budget must be below the per-call output cap.",
    });
  }
  const ctx = content.context.max_context_tokens;
  const callIn = content.call.max_input_tokens;
  if (ctx !== null && callIn !== null && callIn < 1.1 * ctx) {
    out.push({
      key: "call.max_input_tokens",
      severity: "error",
      message: `The per-call input cap must be at least 1.1 × the context window (${formatCount(Math.ceil(1.1 * ctx))}).`,
    });
  }
  return out;
}

// ── labels ───────────────────────────────────────────────────────────────

const KNOB_LABELS: Record<string, string> = {
  "run.max_cost_usd": "Run cap",
  "cell.max_cost_usd": "Cell cap",
  "study.max_cost_usd": "Experiment cap",
  "concurrency.study_max_parallel": "Max parallel runs",
  reasoning: "Reasoning",
  "reasoning.exclude": "Exclude reasoning text",
  "routing.serving_tier": "Serving tier",
  "pilot.enabled": "Pilot stage",
  network_policy: "Network policy",
  "run.max_llm_requests": "Max LLM requests",
  "run.max_aux_requests": "Max auxiliary requests",
  "run.max_uncached_input_tokens": "Max uncached input tokens",
  "call.max_output_tokens": "Per-call output tokens",
  "call.max_input_tokens": "Per-call input tokens",
  "context.max_context_tokens": "Context window",
  "slot.max_output_tokens": "Slot call output tokens",
  "routing.max_price": "Max price",
  "routing.zdr": "Zero data retention",
  "routing.only": "Only providers",
  "routing.ignore": "Ignore providers",
  "routing.order": "Provider order",
  "caching.ttl": "Cache TTL",
  "time.wall_clock_s": "Wall clock",
  "time.idle_timeout_s": "Idle timeout",
  "time.command_timeout_s": "Command timeout",
  "time.llm_call_timeout_s": "LLM call timeout",
  "sandbox.nano_cpus": "CPUs",
  "sandbox.memory": "Memory",
  "concurrency.rpm_per_model": "Requests/min per model",
  "study.on_budget_exhausted": "When the budget runs out",
  "estimate.require_confirm_above_usd": "Typed confirmation above",
};

const WORDS: Record<string, string> = {
  usd: "(USD)",
  llm: "LLM",
  rpm: "RPM",
  oom: "OOM",
  ttl: "TTL",
  zdr: "ZDR",
  s: "",
};

/** A readable label for a knob ("Run cap", "Max total tokens", "Min build rate"). */
export function knobLabel(key: string): string {
  if (KNOB_LABELS[key]) return KNOB_LABELS[key];
  const last = key.split(".").pop() ?? key;
  const words = last
    .split("_")
    .map((w) => WORDS[w] ?? w)
    .filter(Boolean);
  const text = words.join(" ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}
