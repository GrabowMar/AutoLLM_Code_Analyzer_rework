/**
 * Debounced server validation of draft content (`POST /library/{kind}/validate/`): lint issues, the
 * content hash the draft would get (so "unchanged" is visible before saving) and derived values. A newer
 * draft aborts the request in flight.
 */
import { formatApiError, isAbortError } from "$lib/api/core";
import { validateLibraryContent } from "$lib/api/v3/library";
import type { Issue, LibraryKind } from "$lib/api/v3/types";

export class ContentValidation {
  ok = $state<boolean | null>(null);
  issues = $state<Issue[]>([]);
  contentHash = $state<string | null>(null);
  derived = $state<Record<string, unknown> | null>(null);
  validating = $state(false);
  error = $state("");
  #timer: ReturnType<typeof setTimeout> | null = null;
  #controller: AbortController | null = null;

  constructor(private delay = 600) {}

  schedule(kind: LibraryKind, content: unknown, system = false): void {
    if (this.#timer) clearTimeout(this.#timer);
    this.#controller?.abort();
    this.#timer = setTimeout(
      () => void this.run(kind, content, system),
      this.delay,
    );
  }

  async run(
    kind: LibraryKind,
    content: unknown,
    system = false,
  ): Promise<void> {
    const controller = new AbortController();
    this.#controller = controller;
    this.validating = true;
    this.error = "";
    try {
      const out = await validateLibraryContent(kind, content, {
        signal: controller.signal,
        system,
      });
      if (controller.signal.aborted) return;
      this.ok = out.ok;
      this.issues = out.issues;
      this.contentHash = out.content_hash;
      this.derived = out.derived ?? null;
    } catch (e) {
      if (isAbortError(e)) return;
      const body = e as { issues?: Issue[] };
      this.ok = false;
      this.issues = body.issues ?? [];
      this.contentHash = null;
      this.error = formatApiError(e, "Validation failed.");
    } finally {
      if (this.#controller === controller) this.validating = false;
    }
  }

  cancel(): void {
    if (this.#timer) clearTimeout(this.#timer);
    this.#controller?.abort();
    this.validating = false;
  }

  get errors(): Issue[] {
    return this.issues.filter((i) => i.severity === "error");
  }
}
