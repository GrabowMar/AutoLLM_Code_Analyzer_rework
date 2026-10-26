/**
 * Vocabulary, icons and helpers shared by the analysis profile editor, the
 * profiles pages and the tools page.
 */
import type { Component } from "svelte";
import Activity from "@lucide/svelte/icons/activity";
import BadgeCheck from "@lucide/svelte/icons/badge-check";
import Bot from "@lucide/svelte/icons/bot";
import Braces from "@lucide/svelte/icons/braces";
import CheckCheck from "@lucide/svelte/icons/check-check";
import ContainerIcon from "@lucide/svelte/icons/container";
import Copy from "@lucide/svelte/icons/copy";
import Gauge from "@lucide/svelte/icons/gauge";
import Key from "@lucide/svelte/icons/key";
import KeyRound from "@lucide/svelte/icons/key-round";
import Radar from "@lucide/svelte/icons/radar";
import ScanSearch from "@lucide/svelte/icons/scan-search";
import ShieldAlert from "@lucide/svelte/icons/shield-alert";
import Skull from "@lucide/svelte/icons/skull";
import Sparkles from "@lucide/svelte/icons/sparkles";
import SpellCheck from "@lucide/svelte/icons/spell-check";
import type {
  AnalysisTool,
  FileKind,
  FileOrigin,
  JsonValue,
  ProfileConfig,
  ProfileSummary,
  ReviewerEngine,
  Severity,
  ToolCategory,
  ToolConfigField,
  ToolKind,
} from "$lib/api/analysis";

// ── Icons ─────────────────────────────────────────────────────────────

/** The catalog names a Lucide icon per tool; unknown names fall back to ScanSearch. */
const TOOL_ICONS: Record<string, Component> = {
  activity: Activity,
  "badge-check": BadgeCheck,
  bot: Bot,
  braces: Braces,
  "check-check": CheckCheck,
  container: ContainerIcon,
  copy: Copy,
  gauge: Gauge,
  key: Key,
  "key-round": KeyRound,
  radar: Radar,
  "shield-alert": ShieldAlert,
  skull: Skull,
  sparkles: Sparkles,
  "spell-check": SpellCheck,
};

export function toolIcon(name: string | null | undefined): Component {
  return (name && TOOL_ICONS[name]) || ScanSearch;
}

// ── Labels ────────────────────────────────────────────────────────────

export interface Described {
  label: string;
  description: string;
}

export const TOOL_CATEGORY_INFO: Record<ToolCategory, Described> = {
  security: {
    label: "Security",
    description:
      "Insecure code patterns, mapped to CWE ids where the tool provides them.",
  },
  secrets: {
    label: "Secrets",
    description: "Hard-coded credentials, tokens and high-entropy strings.",
  },
  lint: {
    label: "Lint & bugs",
    description: "Likely bugs and error-prone constructs.",
  },
  types: {
    label: "Types",
    description: "Static type errors.",
  },
  dead_code: {
    label: "Dead code",
    description: "Unused functions, classes, variables and imports.",
  },
  complexity: {
    label: "Complexity",
    description:
      "Cyclomatic complexity metrics; very complex blocks become findings.",
  },
  duplication: {
    label: "Duplication",
    description: "Copy-pasted code blocks and the duplicated-lines share.",
  },
  style: {
    label: "Style",
    description:
      "Spelling and style; not counted as issues unless the outcomes say so.",
  },
  container: {
    label: "Container",
    description:
      "Dockerfile checks; usually not applicable (Dockerfiles are platform-owned).",
  },
  ai: {
    label: "AI review",
    description: "LLM code review, configured under AI reviewer.",
  },
};

export function categoryLabel(category: string): string {
  return (
    TOOL_CATEGORY_INFO[category as ToolCategory]?.label ??
    category.replace(/_/g, " ")
  );
}

export const TOOL_KIND_LABELS: Record<ToolKind, string> = {
  static: "Static",
  metrics: "Metrics",
  ai: "AI",
};

export const ORIGIN_INFO: Record<FileOrigin, Described> = {
  authored: {
    label: "Authored",
    description: "Written by the model (scaffolding and custom jobs).",
  },
  added: {
    label: "Added",
    description: "New files the agent created in the workspace.",
  },
  modified: {
    label: "Modified",
    description:
      "Scaffold files the agent changed (with line attribution, only changed lines).",
  },
  scaffold: {
    label: "Scaffold",
    description: "Stack scaffold files the agent left untouched.",
  },
  platform: {
    label: "Platform",
    description: "Protected platform files (Dockerfile, compose).",
  },
  unknown: {
    label: "Unknown",
    description: "No provenance recorded.",
  },
};

export const KIND_INFO: Record<FileKind, Described> = {
  source: { label: "Source", description: "Program code." },
  config: { label: "Config", description: "Configuration files." },
  manifest: {
    label: "Manifest",
    description: "Dependency manifests (package.json, requirements.txt).",
  },
  lockfile: {
    label: "Lockfile",
    description: "Generated dependency lockfiles.",
  },
  doc: { label: "Docs", description: "Documentation and Markdown." },
  asset: { label: "Asset", description: "Binaries, images and fonts." },
  build: {
    label: "Build output",
    description: "Minified or generated bundles.",
  },
  env: { label: "Env", description: "Environment files (.env)." },
  other: { label: "Other", description: "Anything else." },
};

export const ENGINE_LABELS: Record<ReviewerEngine, string> = {
  single_shot: "single-shot",
  agent: "agent",
};

export const SEVERITY_LABELS: Record<Severity, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
  info: "Info",
};

// ── Values ────────────────────────────────────────────────────────────

/** JSON with sorted object keys: equal configs give equal strings. */
export function stableStringify(value: unknown): string {
  return JSON.stringify(value, (_key, v: unknown) => {
    if (v && typeof v === "object" && !Array.isArray(v)) {
      return Object.fromEntries(
        Object.entries(v as Record<string, unknown>).sort(([a], [b]) =>
          a < b ? -1 : a > b ? 1 : 0,
        ),
      );
    }
    return v;
  });
}

export function sameValue(a: unknown, b: unknown): boolean {
  return stableStringify(a) === stableStringify(b);
}

/** Deep copy of a JSON value (configs are plain JSON). */
export function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function shortHash(
  hash: string | null | undefined,
  length = 10,
): string {
  return hash ? hash.slice(0, length) : "—";
}

/** Human value of a tool option (lists joined, booleans on/off, options by label). */
export function describeOption(
  field: ToolConfigField,
  value: JsonValue,
): string {
  if (typeof value === "boolean") return value ? "on" : "off";
  if (Array.isArray(value)) {
    if (value.length === 0) return "none";
    return value
      .map((v) => field.options?.find((o) => o.value === v)?.label ?? String(v))
      .join(", ");
  }
  if (value === "" || value === null || value === undefined) return "empty";
  if (field.type === "select") {
    return (
      field.options?.find((o) => o.value === value)?.label ?? String(value)
    );
  }
  return String(value);
}

/** The tool's options that differ from its catalog defaults. */
export function modifiedOptions(
  tool: AnalysisTool,
  config: Record<string, JsonValue> | undefined,
): ToolConfigField[] {
  if (!config) return [];
  return tool.config_schema.filter(
    (field) =>
      field.name in config &&
      !sameValue(
        config[field.name],
        tool.default_config[field.name] ?? field.default,
      ),
  );
}

/** Model id without the vendor prefix ("deepseek/deepseek-v4.1-flash" → "deepseek-v4.1-flash"). */
export function shortModel(model: string): string {
  const slash = model.lastIndexOf("/");
  return slash >= 0 ? model.slice(slash + 1) : model;
}

/** "AI · deepseek-v4.1-flash · single-shot · k=3", or "No AI review". */
export function reviewerSummary(reviewer: ProfileSummary["reviewer"]): string {
  if (!reviewer.enabled) return "No AI review";
  const parts = [shortModel(reviewer.model)];
  if (reviewer.engine) {
    parts.push(
      reviewer.engine === "agent" && reviewer.harness
        ? `agent/${reviewer.harness}`
        : ENGINE_LABELS[reviewer.engine],
    );
  }
  if (reviewer.samples != null) parts.push(`k=${reviewer.samples}`);
  return parts.join(" · ");
}

export function requiredCount(config: ProfileConfig): number {
  return config.tools.filter((t) => t.required).length;
}

// ── Field errors ──────────────────────────────────────────────────────

/**
 * Field errors keyed relative to the profile config. Accepts the
 * `/profiles/validate/` keys (`reviewer.samples`) and the 422 keys of a
 * save (`config.reviewer.samples`).
 */
export function normalizeConfigErrors(
  errors: Record<string, string> | null | undefined,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, message] of Object.entries(errors ?? {})) {
    const path = key.startsWith("config.")
      ? key.slice("config.".length)
      : key === "config"
        ? ""
        : key;
    if (!(path in out)) out[path] = message;
  }
  return out;
}

/** Errors under `prefix.` with the prefix removed (`prefix` itself becomes ""). */
export function errorsUnder(
  errors: Record<string, string>,
  prefix: string,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, message] of Object.entries(errors)) {
    if (key === prefix) out[""] = message;
    else if (key.startsWith(`${prefix}.`))
      out[key.slice(prefix.length + 1)] = message;
  }
  return out;
}

/**
 * Tool errors (`tools.<index>.<field>`) regrouped by slug. Indices refer to
 * the `tools` list that was validated, i.e. `config.tools`.
 */
export function toolErrorsBySlug(
  errors: Record<string, string>,
  config: ProfileConfig,
): { bySlug: Record<string, Record<string, string>>; general: string[] } {
  const bySlug: Record<string, Record<string, string>> = {};
  const general: string[] = [];
  for (const [key, message] of Object.entries(errorsUnder(errors, "tools"))) {
    const match = /^(\d+)(?:\.(.*))?$/.exec(key);
    const slug = match ? config.tools[Number(match[1])]?.slug : undefined;
    if (!match || !slug) {
      general.push(message);
      continue;
    }
    const field = (match[2] ?? "").replace(/^config\.?/, "");
    bySlug[slug] = { ...bySlug[slug], [field]: message };
  }
  return { bySlug, general };
}

/**
 * Move `tools.<index>…` keys from the tool list they were reported against
 * (`from`, slugs in order) to the current list (`to`); errors of tools no
 * longer in the list are dropped. Other keys pass through.
 */
export function reindexToolErrors(
  errors: Record<string, string>,
  from: readonly string[],
  to: readonly string[],
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, message] of Object.entries(errors)) {
    const match = /^tools\.(\d+)(\..*)?$/.exec(key);
    if (!match) {
      out[key] = message;
      continue;
    }
    const slug = from[Number(match[1])];
    const index = slug === undefined ? -1 : to.indexOf(slug);
    if (index >= 0) out[`tools.${index}${match[2] ?? ""}`] = message;
  }
  return out;
}

export function countErrors(
  errors: Record<string, string>,
  prefix: string,
): number {
  return Object.keys(errorsUnder(errors, prefix)).length;
}
