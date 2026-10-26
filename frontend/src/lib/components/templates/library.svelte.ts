/**
 * Library data: every asset list the Library sections show, cross-reference
 * (the profile editor needs blocks, stacks and specs) and count in the nav.
 * One instance per Library page; sections own their editors and call the
 * `load*` methods after a write.
 */
import { formatApiError } from "$lib/api/core";
import { getMe } from "$lib/api/users";
import {
  getAgentConfig,
  getAppTemplates,
  getContentBlocks,
  getGenerationProfiles,
  getStacks,
  getStarterTemplatePackages,
  listRunPresets,
  type AgentConfigInfo,
  type AppRequirementTemplate,
  type ContentBlock,
  type GenerationProfile,
  type PresetKind,
  type RunPreset,
  type Stack,
  type StarterTemplatePackage,
} from "$lib/api/generation";
import { invalidatePresets, refreshCatalog } from "$lib/components/generator";
import { PRESET_KINDS } from "./presets";

/** One list with its load state. A silent reload keeps `items` on screen. */
class Resource<T> {
  items = $state<T[]>([]) as T[];
  loading = $state(true);
  error = $state("");
  #seq = 0;

  constructor(
    private fetcher: () => Promise<T[]>,
    private failure: string,
  ) {}

  async load(): Promise<void> {
    // Only the latest request may land (e.g. toggling "Show archived" quickly).
    const mine = ++this.#seq;
    this.loading = this.items.length === 0;
    this.error = "";
    try {
      const items = await this.fetcher();
      if (mine !== this.#seq) return;
      this.items = items;
    } catch (e) {
      if (mine !== this.#seq) return;
      this.error = formatApiError(e, this.failure);
    }
    this.loading = false;
  }
}

export class LibraryData {
  showArchivedPresets = $state(false);

  specs = new Resource<AppRequirementTemplate>(
    getAppTemplates,
    "Could not load app specs.",
  );
  stacks = new Resource<Stack>(getStacks, "Could not load stacks.");
  profiles = new Resource<GenerationProfile>(
    getGenerationProfiles,
    "Could not load profiles.",
  );
  blocks = new Resource<ContentBlock>(
    getContentBlocks,
    "Could not load blocks.",
  );
  presets = new Resource<RunPreset>(
    () =>
      listRunPresets(undefined, {
        include_archived: this.showArchivedPresets,
      }),
    "Could not load presets.",
  );
  starters = new Resource<StarterTemplatePackage>(
    getStarterTemplatePackages,
    "Could not load starter packages.",
  );

  /** Platform defaults, limits and the harness catalog (option schemas). */
  agentConfig = $state<AgentConfigInfo | null>(null);
  agentConfigLoading = $state(true);
  agentConfigError = $state("");

  /** Only staff may edit catalog specs and system profiles (the API answers 403). */
  isStaff = $state(false);

  presetCounts = $derived(
    Object.fromEntries(
      PRESET_KINDS.map((k) => [
        k,
        this.presets.items.filter((p) => p.kind === k && !p.is_archived).length,
      ]),
    ) as Record<PresetKind, number>,
  );

  async loadAgentConfig(): Promise<void> {
    this.agentConfigLoading = true;
    this.agentConfigError = "";
    try {
      this.agentConfig = await getAgentConfig();
    } catch (e) {
      this.agentConfigError = formatApiError(
        e,
        "Could not load the harness catalog and budget limits.",
      );
    }
    this.agentConfigLoading = false;
  }

  init(): void {
    void this.specs.load();
    void this.stacks.load();
    void this.profiles.load();
    void this.blocks.load();
    void this.presets.load();
    void this.starters.load();
    void this.loadAgentConfig();
    getMe()
      .then((me) => (this.isStaff = me.is_staff ?? false))
      .catch(() => (this.isStaff = false));
  }

  /** After a spec write: reload, and tell the generator pickers' cache. */
  async specsChanged(): Promise<void> {
    refreshCatalog("app-specs").catch(() => {});
    await this.specs.load();
  }

  /** After a preset write: reload, and tell mounted preset pickers. */
  async presetsChanged(kind?: PresetKind): Promise<void> {
    invalidatePresets(kind);
    await this.presets.load();
  }

  setShowArchivedPresets(on: boolean): void {
    this.showArchivedPresets = on;
    void this.presets.load();
  }

  /** After a package import: anything may have changed. */
  async refreshAll(): Promise<void> {
    refreshCatalog("app-specs").catch(() => {});
    invalidatePresets();
    await Promise.all([
      this.specs.load(),
      this.blocks.load(),
      this.profiles.load(),
      this.stacks.load(),
      this.presets.load(),
    ]);
  }
}

/** A destructive action awaiting confirmation; the page renders the dialog. */
export interface ConfirmRequest {
  title: string;
  description: string;
  confirmLabel: string;
  action: () => Promise<void>;
}
export type AskConfirm = (request: ConfirmRequest) => void;
