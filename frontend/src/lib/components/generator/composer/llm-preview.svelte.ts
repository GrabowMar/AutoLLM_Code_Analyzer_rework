/**
 * Effective LLM params for template scaffolding / single prompt
 * (`POST /jobs/llm-preview/`): debounced, superseded requests aborted, the
 * last result kept on screen while a newer one loads. Call during component
 * initialisation (uses `$effect`).
 */
import { untrack } from "svelte";
import {
  previewEffectiveLLMParams,
  type LLMPreviewRequest,
  type LLMPreviewResult,
} from "$lib/api/generation";
import { formatApiError, isAbortError } from "$lib/api/core";
import { stableKey } from "./draft";

const DEBOUNCE_MS = 300;

export interface LLMPreviewTracker {
  /** Latest result (possibly for an older form state: see `stale`). */
  readonly result: LLMPreviewResult | null;
  readonly stale: boolean;
  readonly loading: boolean;
  /** Error for the current form state ('' when none). */
  readonly error: string;
  retry(): void;
}

export function trackLLMPreview(
  getRequest: () => LLMPreviewRequest | null,
): LLMPreviewTracker {
  const state = $state({
    result: null as { key: string; data: LLMPreviewResult } | null,
    failure: null as { key: string; message: string } | null,
    loading: false,
    nonce: 0,
  });
  let fetchedNonce = 0;
  const key = $derived.by(() => {
    const req = getRequest();
    return req ? stableKey(req) : "";
  });

  $effect(() => {
    const current = key;
    const nonce = state.nonce;
    if (!current) {
      state.loading = false;
      return;
    }
    if (
      nonce === fetchedNonce &&
      untrack(() => state.result?.key === current)
    ) {
      state.loading = false;
      return;
    }
    const body = JSON.parse(current) as LLMPreviewRequest;
    const controller = new AbortController();
    state.loading = true;
    const timer = setTimeout(() => {
      fetchedNonce = nonce;
      previewEffectiveLLMParams(body, { signal: controller.signal })
        .then((data) => {
          state.result = { key: current, data };
          state.failure = null;
        })
        .catch((e: unknown) => {
          if (controller.signal.aborted || isAbortError(e)) return;
          state.failure = {
            key: current,
            message: formatApiError(
              e,
              "Could not resolve the effective parameters.",
            ),
          };
        })
        .finally(() => {
          if (!controller.signal.aborted) state.loading = false;
        });
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  });

  return {
    get result() {
      return state.result?.data ?? null;
    },
    get stale() {
      return !!state.result && state.result.key !== key;
    },
    get loading() {
      return state.loading;
    },
    get error() {
      return state.failure && state.failure.key === key
        ? state.failure.message
        : "";
    },
    retry() {
      state.nonce += 1;
    },
  };
}
