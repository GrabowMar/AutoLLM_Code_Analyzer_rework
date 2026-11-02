import type { HandleClientError } from "@sveltejs/kit";

/** Runs before root layout mount — must not wait on network (see app.html 6s watchdog). */
export function init() {
  (
    globalThis as typeof globalThis & { __sveltekit_hydrated?: boolean }
  ).__sveltekit_hydrated = true;
  // The client bundle loaded, so re-arm app.html's one-shot chunk-failure reload.
  try {
    sessionStorage.removeItem("llm_lab_chunk_reload");
  } catch {
    // sessionStorage unavailable (privacy mode) — nothing to re-arm.
  }
}

export const handleError: HandleClientError = async ({ error, message }) => {
  console.error("Client error:", error);
  return {
    message: message || "An unexpected error occurred",
  };
};
