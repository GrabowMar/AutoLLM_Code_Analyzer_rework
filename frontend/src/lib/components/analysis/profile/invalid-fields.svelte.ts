/**
 * Tracks form fields that hold input the editor could not apply (an
 * incomplete number, invalid JSON). The profile editor provides one tracker
 * through context; fields register themselves, and the editor exposes
 * `invalid` so a page can block saving while anything is unapplied.
 */
import { getContext, setContext } from "svelte";
import { SvelteSet } from "svelte/reactivity";

const KEY = Symbol("analysis-profile-invalid-fields");

export class InvalidFields {
  #ids = new SvelteSet<string>();

  set(id: string, invalid: boolean): void {
    if (invalid) this.#ids.add(id);
    else this.#ids.delete(id);
  }

  get count(): number {
    return this.#ids.size;
  }

  get any(): boolean {
    return this.#ids.size > 0;
  }
}

export function provideInvalidFields(): InvalidFields {
  return setContext(KEY, new InvalidFields());
}

/** The nearest editor's tracker, or null outside a profile editor. */
export function invalidFieldsContext(): InvalidFields | null {
  return getContext<InvalidFields | undefined>(KEY) ?? null;
}
