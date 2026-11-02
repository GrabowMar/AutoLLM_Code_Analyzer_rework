// Observation (bug / vulnerability report) drafts: field checks mirroring the
// server (models + spec §5.9 / R2), the screenshot rules and the category lists.
import type {
  Observation,
  ObservationInput,
  ObservationKind,
  TesterSeverity,
} from "$lib/api/types/human-eval";

export type Slot = "A" | "B" | "single";

export const IMAGE_TYPES = [
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
];
export const IMAGE_ACCEPT = IMAGE_TYPES.join(",");
export const MAX_ATTACHMENTS = 5;
export const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024;

export const LIMITS = {
  title: 200,
  location: 500,
  category: 60,
  cwe: 20,
  description: 20000,
  steps: 20000,
} as const;

/** The tester's own severity scale ("Your severity"); never a tool's level. */
export const TESTER_SEVERITIES: { value: TesterSeverity; label: string }[] = [
  { value: "critical", label: "Critical" },
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
  { value: "info", label: "Info" },
];

/** Bug categories (mirrors backend services/checklists.py BUG_CATEGORIES; the server validates them). */
export const BUG_CATEGORIES: { value: string; label: string }[] = [
  { value: "functional", label: "Functional (wrong or missing behaviour)" },
  { value: "ui", label: "User interface (layout, display, text)" },
  { value: "crash", label: "Crash or error page" },
  { value: "performance", label: "Performance (slow, hangs)" },
  { value: "data", label: "Data (lost, wrong or inconsistent data)" },
  { value: "other", label: "Other" },
];

/** Vulnerability categories: OWASP Top 10 (2021) codes + other (mirrors VULNERABILITY_CATEGORIES). */
export const VULNERABILITY_CATEGORIES: { value: string; label: string }[] = [
  { value: "A01", label: "A01:2021 Broken Access Control" },
  { value: "A02", label: "A02:2021 Cryptographic Failures" },
  { value: "A03", label: "A03:2021 Injection" },
  { value: "A04", label: "A04:2021 Insecure Design" },
  { value: "A05", label: "A05:2021 Security Misconfiguration" },
  { value: "A06", label: "A06:2021 Vulnerable and Outdated Components" },
  {
    value: "A07",
    label: "A07:2021 Identification and Authentication Failures",
  },
  { value: "A08", label: "A08:2021 Software and Data Integrity Failures" },
  { value: "A09", label: "A09:2021 Security Logging and Monitoring Failures" },
  { value: "A10", label: "A10:2021 Server-Side Request Forgery (SSRF)" },
  { value: "other", label: "Other" },
];

export function categoriesFor(
  kind: ObservationKind,
): { value: string; label: string }[] {
  if (kind === "vulnerability") return VULNERABILITY_CATEGORIES;
  if (kind === "bug") return BUG_CATEGORIES;
  return [];
}

/** Label for a stored category (falls back to the raw value). */
export function categoryLabel(kind: ObservationKind, value: string): string {
  return categoriesFor(kind).find((c) => c.value === value)?.label ?? value;
}

export interface ObservationDraft {
  kind: ObservationKind;
  slot: Slot;
  block_id: string;
  title: string;
  description: string;
  steps: string;
  location: string;
  category: string;
  severity: TesterSeverity | "";
  cwe: string;
  checklist_item_id: string;
}

export function emptyDraft(
  kind: ObservationKind,
  slot: Slot,
  blockId: string,
): ObservationDraft {
  return {
    kind,
    slot,
    block_id: blockId,
    title: "",
    description: "",
    steps: "",
    location: "",
    category: "",
    severity: "",
    cwe: "",
    checklist_item_id: "",
  };
}

export function draftFrom(observation: Observation): ObservationDraft {
  return {
    kind: observation.kind,
    slot: (observation.app_slot as Slot) || "single",
    block_id: observation.block_id,
    title: observation.title,
    description: observation.description,
    steps: observation.steps,
    location: observation.location,
    category: observation.category,
    severity: observation.severity,
    cwe: observation.cwe,
    checklist_item_id: observation.checklist_item_id,
  };
}

/**
 * Example values for the "Where" field. Each one must pass `locationIssue`
 * (and the server's valid_location): the HTTP method belongs in the
 * description or the steps, not here.
 */
export const LOCATION_EXAMPLES = ["/admin/users", "/api/items/3"] as const;

/** Placeholder of the "Where" field, built from `LOCATION_EXAMPLES`. */
export const LOCATION_PLACEHOLDER = LOCATION_EXAMPLES.join(" or ");

/**
 * Why a location is not accepted, or null. The server takes a relative path
 * ("/settings") or an absolute http(s) URL; anything else (javascript:, data:)
 * is refused.
 */
export function locationIssue(value: string): string | null {
  const v = value.trim();
  if (!v) return null;
  if (v.length > LIMITS.location)
    return `Keep it under ${LIMITS.location} characters.`;
  if (v.startsWith("/") && !v.startsWith("//")) {
    return /[\s]/.test(v) ? "A path cannot contain spaces." : null;
  }
  try {
    const url = new URL(v);
    if (url.protocol === "http:" || url.protocol === "https:") return null;
  } catch {
    // fall through
  }
  return "Enter a path starting with “/” (e.g. /admin/users) or a full http(s) URL.";
}

/** Only absolute http(s) locations become links; paths and anything else stay text. */
export function locationHref(value: string): string | null {
  const v = value.trim();
  if (!v || v.startsWith("/")) return null;
  try {
    const url = new URL(v);
    return url.protocol === "http:" || url.protocol === "https:"
      ? url.href
      : null;
  } catch {
    return null;
  }
}

const CWE_RE = /^(CWE-)?\d{1,6}$/i;

/** Field errors of a draft, keyed by field name (empty when it can be saved). */
export function draftErrors(draft: ObservationDraft): Record<string, string> {
  const errors: Record<string, string> = {};
  const title = draft.title.trim();
  if (!title) errors.title = "Give it a short title.";
  else if (title.length > LIMITS.title)
    errors.title = `Keep the title under ${LIMITS.title} characters.`;
  const location = locationIssue(draft.location);
  if (location) errors.location = location;
  if (draft.description.length > LIMITS.description)
    errors.description = "The description is too long.";
  if (draft.steps.length > LIMITS.steps)
    errors.steps = "The steps are too long.";
  const cwe = draft.cwe.trim();
  if (cwe && !CWE_RE.test(cwe)) errors.cwe = "Use a CWE id such as CWE-79.";
  return errors;
}

/** The create payload (trimmed; kind-specific fields only where they apply). */
export function draftToInput(draft: ObservationDraft): ObservationInput {
  const cwe = draft.cwe.trim();
  return {
    slot: draft.slot,
    block_id: draft.block_id,
    kind: draft.kind,
    title: draft.title.trim(),
    description: draft.description.trim(),
    steps: draft.steps.trim(),
    location: draft.location.trim(),
    category: draft.category,
    severity: draft.severity,
    cwe:
      draft.kind === "vulnerability"
        ? cwe && /^\d+$/.test(cwe)
          ? `CWE-${cwe}`
          : cwe.toUpperCase()
        : "",
    checklist_item_id:
      draft.kind === "vulnerability" ? draft.checklist_item_id : "",
  };
}

/** The PATCH payload (no slot change allowed). */
export function draftToPatch(
  draft: ObservationDraft,
): Partial<Omit<ObservationInput, "slot">> {
  const { slot: _slot, ...rest } = draftToInput(draft);
  void _slot;
  return rest;
}

/** Why a picked/pasted file cannot be attached, or null. */
export function imageFileIssue(file: {
  type: string;
  size: number;
  name?: string;
}): string | null {
  if (!IMAGE_TYPES.includes(file.type)) {
    return `${file.name || "This file"} is not a PNG, JPEG, GIF or WebP image.`;
  }
  if (file.size > MAX_ATTACHMENT_BYTES) {
    return `${file.name || "This image"} is larger than 5 MiB.`;
  }
  if (file.size === 0) return `${file.name || "This image"} is empty.`;
  return null;
}

/** Image files from a paste event's clipboard data (screenshots). */
export function pastedImages(data: DataTransfer | null | undefined): File[] {
  if (!data) return [];
  const files: File[] = [];
  for (const item of Array.from(data.items ?? [])) {
    if (item.kind !== "file" || !item.type.startsWith("image/")) continue;
    const file = item.getAsFile();
    if (file) files.push(file);
  }
  if (files.length === 0) {
    for (const file of Array.from(data.files ?? [])) {
      if (file.type.startsWith("image/")) files.push(file);
    }
  }
  return files;
}

/** A filename for an upload (pasted screenshots have none). */
export function uploadName(
  file: { name?: string; type: string },
  index = 0,
): string {
  if (file.name && file.name !== "image.png") return file.name.slice(0, 200);
  const ext = file.type.split("/")[1]?.replace("jpeg", "jpg") || "png";
  return `screenshot-${index + 1}.${ext}`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export const KIND_LABELS: Record<ObservationKind, string> = {
  bug: "Bug",
  vulnerability: "Vulnerability",
  note: "Note",
};
