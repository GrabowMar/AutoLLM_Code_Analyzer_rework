/**
 * ResultsFeed — one experiment's `GET /results/` for a tab: the latest
 * request wins (earlier ones are aborted), failures keep the previous
 * results on screen, and `tick()` refetches at most every `tickMs` while the
 * experiment runs. The Abstract (default analysis) and Results (the URL's
 * analysis) each own one.
 */
import {
  getResults,
  type ExperimentResults,
  type ResultsQuery,
} from "$lib/api/experiments";
import { formatApiError, isAbortError } from "$lib/api/core";

export class ResultsFeed {
  results = $state.raw<ExperimentResults | null>(null);
  loading = $state(true);
  error = $state("");

  private experimentId: string;
  private tickMs: number;
  private controller: AbortController | null = null;
  private lastFetch = 0;
  private query: ResultsQuery = {};

  constructor(experimentId: string, tickMs = 30_000) {
    this.experimentId = experimentId;
    this.tickMs = tickMs;
  }

  async load(query: ResultsQuery = this.query): Promise<void> {
    this.query = query;
    this.controller?.abort();
    const mine = new AbortController();
    this.controller = mine;
    this.lastFetch = Date.now();
    try {
      const next = await getResults(this.experimentId, query, {
        signal: mine.signal,
      });
      if (mine.signal.aborted) return;
      this.results = next;
      this.error = "";
    } catch (e) {
      if (isAbortError(e) || mine.signal.aborted) return;
      this.error = formatApiError(e, "Could not compute the results.");
    } finally {
      if (!mine.signal.aborted) this.loading = false;
    }
  }

  /** A live tick: refetch when the last fetch is older than `tickMs`. */
  tick(): void {
    if (!this.results) return;
    if (Date.now() - this.lastFetch >= this.tickMs) void this.load();
  }

  destroy(): void {
    this.controller?.abort();
  }
}

/** The pre-registered analysis: what the Results tab shows with no URL options. */
export const DEFAULT_RESULTS_QUERY: ResultsQuery = {
  common_cells: true,
  exclude_invalid: false,
  exclude_truncated: true,
  fingerprint: "all",
};
