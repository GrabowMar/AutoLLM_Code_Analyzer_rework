/**
 * Models table columns: the registry, the default set (the table as it ships) and the viewer's
 * choice, remembered in localStorage. Also which capability icons the Capabilities column shows.
 * Storage is a per-browser convenience: it may be unavailable (private mode) and then the defaults hold.
 */

export type ColumnKey =
  | "provider"
  | "model_id"
  | "released"
  | "context"
  | "max_out"
  | "input"
  | "output"
  | "ctx_per_dollar"
  | "params"
  | "weights"
  | "tokenizer"
  | "modalities"
  | "research"
  | "capabilities"
  | "description";

export interface ColumnDef {
  key: ColumnKey;
  /** Header text. */
  label: string;
  /** Text in the Columns menu, when different. */
  menuLabel?: string;
  /** Backend sort key; columns without one are not sortable. */
  sortField?: string;
  align?: "left" | "right";
  help?: string;
  group: "Basics" | "Limits & price" | "Research";
}

/** Display order of the table (left → right). */
export const COLUMNS: ColumnDef[] = [
  {
    key: "provider",
    label: "Provider",
    sortField: "provider",
    group: "Basics",
  },
  {
    key: "model_id",
    label: "Model ID",
    menuLabel: "Model ID",
    group: "Basics",
  },
  { key: "description", label: "Description", group: "Basics" },
  {
    key: "released",
    label: "Released",
    sortField: "released",
    align: "right",
    help: "When the model was added to OpenRouter (models.dev release date as a fallback)",
    group: "Basics",
  },
  {
    key: "context",
    label: "Context",
    sortField: "context_window",
    align: "right",
    help: "Context window (tokens)",
    group: "Limits & price",
  },
  {
    key: "max_out",
    label: "Max out",
    sortField: "max_output",
    align: "right",
    help: "Maximum output tokens per response",
    group: "Limits & price",
  },
  {
    key: "input",
    label: "Input",
    sortField: "input_price",
    align: "right",
    help: "Input price, USD per million tokens",
    group: "Limits & price",
  },
  {
    key: "output",
    label: "Output",
    sortField: "output_price",
    align: "right",
    help: "Output price, USD per million tokens",
    group: "Limits & price",
  },
  {
    key: "ctx_per_dollar",
    label: "Ctx/$",
    menuLabel: "Ctx/$",
    sortField: "cost_efficiency",
    align: "right",
    help: "Context window per dollar of average token price, log-scaled to 0–100. A cost lens only: it says nothing about model quality.",
    group: "Limits & price",
  },
  {
    key: "params",
    label: "Params",
    menuLabel: "Parameters",
    align: "right",
    help: "Parameter count: exact from the Hugging Face weights, else as reported by Epoch AI (≈), else the local model's size label",
    group: "Research",
  },
  {
    key: "weights",
    label: "Weights",
    menuLabel: "Weights",
    help: "Per models.dev",
    group: "Research",
  },
  { key: "tokenizer", label: "Tokenizer", group: "Research" },
  {
    key: "modalities",
    label: "Input",
    menuLabel: "Input types",
    help: "What the model accepts besides text",
    group: "Research",
  },
  {
    key: "research",
    label: "Data",
    menuLabel: "Research data",
    help: "External research data attached to the model",
    group: "Research",
  },
  {
    key: "capabilities",
    label: "Capabilities",
    align: "right",
    group: "Research",
  },
];

export const DEFAULT_COLUMNS: ColumnKey[] = [
  "context",
  "max_out",
  "input",
  "output",
  "ctx_per_dollar",
  "capabilities",
];

export type CapabilityKey =
  | "supports_reasoning"
  | "supports_function_calling"
  | "supports_vision"
  | "supports_json_mode"
  | "supports_streaming";

export const DEFAULT_CAPABILITY_ICONS: CapabilityKey[] = [
  "supports_reasoning",
  "supports_function_calling",
  "supports_vision",
  "supports_json_mode",
  "supports_streaming",
];

const STORAGE_KEY = "llmlab.models.columns.v1";

interface Stored {
  columns: ColumnKey[];
  capabilities: CapabilityKey[];
}

function read(): Stored | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as Partial<Stored>;
    const known = new Set(COLUMNS.map((c) => c.key));
    const knownCaps = new Set(DEFAULT_CAPABILITY_ICONS);
    return {
      columns: (v.columns ?? DEFAULT_COLUMNS).filter((k) => known.has(k)),
      capabilities: (v.capabilities ?? DEFAULT_CAPABILITY_ICONS).filter((k) =>
        knownCaps.has(k),
      ),
    };
  } catch {
    return null;
  }
}

function write(v: Stored) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(v));
  } catch {
    /* storage unavailable: the choice lasts for this page view only */
  }
}

class ColumnPrefs {
  columns = $state<ColumnKey[]>([...DEFAULT_COLUMNS]);
  capabilities = $state<CapabilityKey[]>([...DEFAULT_CAPABILITY_ICONS]);

  /** Load the saved choice (call from onMount: localStorage is browser-only). */
  restore() {
    const saved = read();
    if (saved) {
      this.columns = saved.columns;
      this.capabilities = saved.capabilities;
    }
  }

  /** Visible column defs in table order. */
  get visible(): ColumnDef[] {
    return COLUMNS.filter((c) => this.columns.includes(c.key));
  }

  get isDefault(): boolean {
    const same = (a: string[], b: string[]) =>
      a.length === b.length && a.every((x) => b.includes(x));
    return (
      same(this.columns, DEFAULT_COLUMNS) &&
      same(this.capabilities, DEFAULT_CAPABILITY_ICONS)
    );
  }

  toggleColumn(key: ColumnKey) {
    this.columns = this.columns.includes(key)
      ? this.columns.filter((k) => k !== key)
      : [...this.columns, key];
    this.save();
  }

  toggleCapability(key: CapabilityKey) {
    this.capabilities = this.capabilities.includes(key)
      ? this.capabilities.filter((k) => k !== key)
      : DEFAULT_CAPABILITY_ICONS.filter(
          (k) => k === key || this.capabilities.includes(k),
        );
    this.save();
  }

  reset() {
    this.columns = [...DEFAULT_COLUMNS];
    this.capabilities = [...DEFAULT_CAPABILITY_ICONS];
    this.save();
  }

  private save() {
    write({ columns: this.columns, capabilities: this.capabilities });
  }
}

export const columnPrefs = new ColumnPrefs();
