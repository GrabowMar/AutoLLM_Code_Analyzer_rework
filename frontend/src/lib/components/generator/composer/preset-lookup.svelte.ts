/**
 * Reactive preset resolution for the composer: the latest-version list of a
 * kind (shared cache, reloads after `invalidatePresets`) plus any wanted ids
 * the list lacks — older or archived versions pinned by a draft or a job —
 * fetched one by one. Call during component initialisation (uses `$effect`).
 */
import { untrack } from "svelte";
import {
  getRunPreset,
  type HarnessPresetConfig,
  type PresetKind,
  type RunPreset,
} from "$lib/api/generation";
import { apiErrorStatus, formatApiError } from "$lib/api/core";
import { isNotFoundError } from "../batches/batch-utils";
import {
  invalidatePresets,
  loadPresets,
  presetsVersion,
} from "../presets.svelte";

export interface PresetTracker<K extends PresetKind> {
  /** Latest version per slug (system ∪ own), defaults first. */
  readonly list: RunPreset<K>[];
  /** The list request finished (successfully or not). */
  readonly loaded: boolean;
  readonly error: string;
  /**
   * A wanted id the list lacks could not be looked up (network error, 5xx):
   * it stays selected and is not "missing"; `retry()` looks it up again.
   */
  readonly lookupError: string;
  /** A preset by id: from the list, or a fetched older/archived version. */
  find(id: number | null | undefined): RunPreset<K> | undefined;
  /** The lookup answered 404: the id does not exist (or is not visible). */
  isMissing(id: number): boolean;
  /** The latest version of the same slug, when `preset` is not it. */
  newerVersion(preset: RunPreset<K>): RunPreset<K> | undefined;
  /** The system default of the kind (harness: of `harnessName`). */
  systemDefault(harnessName?: string): RunPreset<K> | undefined;
  retry(): void;
}

export function trackPresets<K extends PresetKind>(
  kind: K,
  wantedIds: () => (number | null | undefined)[],
): PresetTracker<K> {
  const state = $state({
    list: [] as RunPreset<K>[],
    loaded: false,
    error: "",
    lookupError: "",
    extras: {} as Record<number, RunPreset<K> | "missing">,
  });
  const requested = new Set<number>();

  $effect(() => {
    presetsVersion(kind); // reload after invalidatePresets(kind)
    let cancelled = false;
    loadPresets(kind)
      .then((list) => {
        if (cancelled) return;
        state.list = list;
        state.error = "";
      })
      .catch((e: unknown) => {
        if (!cancelled)
          state.error = formatApiError(e, "Could not load presets.");
      })
      .finally(() => {
        if (!cancelled) state.loaded = true;
      });
    return () => {
      cancelled = true;
    };
  });

  $effect(() => {
    if (!state.loaded) return;
    const inList = new Set(state.list.map((p) => p.id));
    for (const id of wantedIds()) {
      if (id == null || inList.has(id) || requested.has(id)) continue;
      requested.add(id);
      getRunPreset(id)
        .then((preset) => {
          state.extras[id] = preset as RunPreset<K>;
        })
        .catch((e: unknown) => {
          // Only a 404 means gone. Anything else (an outage, a backend
          // reload) must not make the composers swap the selection: keep
          // it, say so, and let retry() look it up again.
          if (isNotFoundError(e)) {
            state.extras[id] = "missing";
            return;
          }
          requested.delete(id);
          const status = apiErrorStatus(e);
          state.lookupError = formatApiError(
            e,
            status ? `the server answered ${status}` : "the request failed",
          );
        });
    }
  });

  return {
    get list() {
      return state.list;
    },
    get loaded() {
      return state.loaded;
    },
    get error() {
      return state.error;
    },
    get lookupError() {
      return state.lookupError;
    },
    find(id) {
      if (id == null) return undefined;
      const listed = state.list.find((p) => p.id === id);
      if (listed) return listed;
      const extra = state.extras[id];
      return extra && extra !== "missing" ? extra : undefined;
    },
    isMissing(id) {
      return (
        state.extras[id] === "missing" && !state.list.some((p) => p.id === id)
      );
    },
    newerVersion(preset) {
      if (state.list.some((p) => p.id === preset.id)) return undefined;
      return state.list.find(
        (p) => p.slug === preset.slug && p.id !== preset.id,
      );
    },
    systemDefault(harnessName) {
      const pool = state.list.filter(
        (p) =>
          p.is_system &&
          (harnessName === undefined ||
            (p.config as HarnessPresetConfig).harness === harnessName),
      );
      return pool.find((p) => p.is_default) ?? pool[0];
    },
    retry() {
      untrack(() => {
        requested.clear();
        state.extras = {};
        state.lookupError = "";
      });
      invalidatePresets(kind);
    },
  };
}
