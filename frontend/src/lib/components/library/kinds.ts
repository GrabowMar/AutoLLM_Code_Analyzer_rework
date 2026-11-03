/**
 * The Library's kinds: labels, the question each group answers about a run, who may edit them, and the
 * list columns. One table, used by the nav, the home page, the lists, the pickers and the badges.
 */
import type { Component } from "svelte";
import Bot from "@lucide/svelte/icons/bot";
import BookOpenCheck from "@lucide/svelte/icons/book-open-check";
import Boxes from "@lucide/svelte/icons/boxes";
import FileCode from "@lucide/svelte/icons/file-code";
import FileText from "@lucide/svelte/icons/file-text";
import FolderTree from "@lucide/svelte/icons/folder-tree";
import Gauge from "@lucide/svelte/icons/gauge";
import Layers from "@lucide/svelte/icons/layers";
import ListTree from "@lucide/svelte/icons/list-tree";
import Palette from "@lucide/svelte/icons/palette";
import ScanSearch from "@lucide/svelte/icons/scan-search";
import ShieldCheck from "@lucide/svelte/icons/shield-check";
import SlidersHorizontal from "@lucide/svelte/icons/sliders-horizontal";
import type {
  LibraryApiKind,
  LibraryItemOut,
  LibraryKind,
} from "$lib/api/v3/types";
import { LIBRARY_API_KINDS, LIBRARY_KIND_API } from "$lib/api/v3/types";

export type KindEditing =
  /** Users create and version entities in the structured editor. */
  | "editable"
  /** Seeded and read-only in the UI (forks come in through YAML packages). */
  | "readonly";

export interface KindInfo {
  kind: LibraryKind;
  api: LibraryApiKind;
  label: string;
  plural: string;
  /** One line under the list title. */
  description: string;
  icon: Component;
  group: string;
  editing: KindEditing;
}

export interface KindGroup {
  id: string;
  /** The question this group answers about a run. */
  title: string;
  kinds: LibraryKind[];
}

export const KIND_INFO: Record<LibraryKind, KindInfo> = {
  task: {
    kind: "task",
    api: "tasks",
    label: "Task",
    plural: "Tasks",
    description:
      "What the model builds: entities, endpoints, UI requirements, acceptance checks, probes and three prose variants.",
    icon: FileText,
    group: "build",
    editing: "editable",
  },
  stack: {
    kind: "stack",
    api: "stacks",
    label: "Stack",
    plural: "Stacks",
    description:
      "Backend, frontend and database the app must use, the run contract and the stack's brief fragment.",
    icon: Layers,
    group: "runs-on",
    editing: "editable",
  },
  starter: {
    kind: "starter",
    api: "starters",
    label: "Starter",
    plural: "Starters",
    description:
      "Seeded workspace trees per stack and level (bare, minimal, full), digest-pinned and lint-checked.",
    icon: FolderTree,
    group: "runs-on",
    editing: "readonly",
  },
  style: {
    kind: "style",
    api: "styles",
    label: "Style",
    plural: "Styles",
    description:
      "How the frontend should look: styling system, component library, tone and accessibility.",
    icon: Palette,
    group: "told",
    editing: "editable",
  },
  guidance: {
    kind: "guidance",
    api: "guidance",
    label: "Guidance",
    plural: "Guidance",
    description:
      "Security guidance given to the model: none, generic or CWE-specific, with library prescriptions.",
    icon: ShieldCheck,
    group: "told",
    editing: "editable",
  },
  prompt: {
    kind: "prompt",
    api: "prompts",
    label: "Prompt template",
    plural: "Prompt templates",
    description:
      "Jinja templates (sandboxed) for briefs, system/user prompts, slot prompts and fragments.",
    icon: FileCode,
    group: "prompts",
    editing: "editable",
  },
  prompt_set: {
    kind: "prompt_set",
    api: "prompt-sets",
    label: "Prompt set",
    plural: "Prompt sets",
    description:
      "Which template fills each role, the auth-contract and network fragments, and the system-prompt mode.",
    icon: ListTree,
    group: "prompts",
    editing: "editable",
  },
  harness_preset: {
    kind: "harness_preset",
    api: "harness-presets",
    label: "Harness preset",
    plural: "Harness presets",
    description:
      "Agent harness, pinned version, options, tool policy and whether the profile is controlled.",
    icon: Bot,
    group: "agents",
    editing: "editable",
  },
  sampling: {
    kind: "sampling",
    api: "sampling-presets",
    label: "Sampling preset",
    plural: "Sampling presets",
    description:
      "Provider defaults or explicit sampling parameters, and whether the run seed is forwarded.",
    icon: SlidersHorizontal,
    group: "agents",
    editing: "editable",
  },
  resource_policy: {
    kind: "resource_policy",
    api: "resource-policies",
    label: "Budget preset",
    plural: "Budget presets",
    description:
      "Every budget and resource knob — caps, routing, time, sandbox, concurrency, pilot — in frugal, standard and generous tiers.",
    icon: Gauge,
    group: "agents",
    editing: "editable",
  },
  eval_suite: {
    kind: "eval_suite",
    api: "eval-suites",
    label: "Eval suite",
    plural: "Eval suites",
    description:
      "Pinned tester and adherence-checker images, probe selection, baseline and timeouts.",
    icon: BookOpenCheck,
    group: "measured",
    editing: "readonly",
  },
  detector: {
    kind: "detector",
    api: "detectors",
    label: "Detector",
    plural: "Detectors",
    description:
      "Pattern detectors (Semgrep, tree-sitter query, regex, probe, manual) and the feature each one marks.",
    icon: ScanSearch,
    group: "measured",
    editing: "editable",
  },
};

export const KIND_GROUPS: KindGroup[] = [
  { id: "build", title: "What to build", kinds: ["task"] },
  { id: "runs-on", title: "Where it runs", kinds: ["stack", "starter"] },
  { id: "told", title: "What else it is told", kinds: ["style", "guidance"] },
  { id: "prompts", title: "Prompts", kinds: ["prompt", "prompt_set"] },
  {
    id: "agents",
    title: "How agents run",
    kinds: ["harness_preset", "sampling", "resource_policy"],
  },
  {
    id: "measured",
    title: "How it is measured",
    kinds: ["eval_suite", "detector"],
  },
];

export const ALL_KINDS: LibraryKind[] = KIND_GROUPS.flatMap((g) => g.kinds);

export const PlaygroundIcon = Boxes;

export function kindInfo(kind: LibraryKind | LibraryApiKind): KindInfo {
  const k = (
    kind in LIBRARY_API_KINDS ? LIBRARY_API_KINDS[kind as LibraryApiKind] : kind
  ) as LibraryKind;
  return KIND_INFO[k];
}

/** `/library/{api}` and `/library/{api}/{slug}[?v=n]`. */
export function libraryHref(
  kind: LibraryKind | LibraryApiKind,
  slug?: string,
  version?: number | null,
): string {
  const api =
    kind in LIBRARY_API_KINDS
      ? (kind as LibraryApiKind)
      : LIBRARY_KIND_API[kind as LibraryKind];
  if (!slug) return `/library/${api}`;
  const base = `/library/${api}/${encodeURIComponent(slug)}`;
  return version ? `${base}?v=${version}` : base;
}

/** Can this user edit (save a new version of) the row? Staff edit system rows; others only their own. */
export function canEdit(
  item: Pick<LibraryItemOut, "is_system" | "created_by">,
  me: { id: number; is_staff: boolean } | null,
): boolean {
  if (!me) return false;
  if (me.is_staff) return true;
  if (item.is_system) return false;
  return item.created_by?.id === me.id;
}

/** One short secondary line per row for a list (kind-specific columns). */
export function itemSubline(item: LibraryItemOut): string {
  const c = item.content as unknown as Record<string, unknown>;
  const col = item.columns ?? {};
  switch (item.kind) {
    case "task": {
      const d = (item.derived ?? {}) as Record<string, unknown>;
      const parts = [String(c.category ?? "")];
      if (d.size_bucket) parts.push(`size ${d.size_bucket}`);
      if (d.endpoint_count !== undefined)
        parts.push(`${d.endpoint_count} endpoints`);
      return parts.filter(Boolean).join(" · ");
    }
    case "stack": {
      const b = (c.backend ?? {}) as Record<string, string>;
      const f = (c.frontend ?? {}) as Record<string, string>;
      return [
        b.framework,
        f.framework !== "none" ? f.framework : "no frontend",
        String(c.database ?? ""),
      ]
        .filter(Boolean)
        .join(" · ");
    }
    case "starter":
      return [
        String(c.level ?? ""),
        String(col.stack_profile ?? c.stack_profile ?? ""),
      ]
        .filter(Boolean)
        .join(" · ");
    case "style":
      return [String(c.system ?? ""), c.tone ? String(c.tone) : ""]
        .filter(Boolean)
        .join(" · ");
    case "guidance":
      return String(c.security_hint ?? "");
    case "prompt":
      return [String(c.role ?? ""), ((c.modes as string[]) ?? []).join("/")]
        .filter(Boolean)
        .join(" · ");
    case "prompt_set":
      return [String(c.mode ?? ""), String(c.system_prompt_mode ?? "")]
        .filter(Boolean)
        .join(" · ");
    case "harness_preset":
      return [
        String(c.harness ?? ""),
        String(c.harness_version ?? ""),
        c.controlled ? "controlled" : "native",
      ]
        .filter(Boolean)
        .join(" · ");
    case "sampling":
      return String(c.policy ?? "").replace("_", " ");
    case "resource_policy":
      return String(col.tier ?? "custom");
    case "eval_suite":
      return `tester v${(c.tester as { version?: number })?.version ?? "?"} · probes ${c.probes ?? "?"}`;
    case "detector":
      return [
        String(c.engine ?? ""),
        String(c.feature_key ?? ""),
        String(c.polarity ?? ""),
      ]
        .filter(Boolean)
        .join(" · ");
    default:
      return "";
  }
}
