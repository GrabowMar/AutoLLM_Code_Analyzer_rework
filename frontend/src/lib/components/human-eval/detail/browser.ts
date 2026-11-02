/**
 * Browser-only helpers for the task page: clipboard with a toast, text
 * downloads and printing a generated (already escaped) HTML document.
 */
import { toast } from "svelte-sonner";

/** Copies `text`; toasts `done` on success and a manual-copy hint on failure. */
export async function copyText(
  text: string,
  done = "Copied",
): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(done);
    return true;
  } catch {
    toast.error("Could not copy. Select the text and copy it by hand.");
    return false;
  }
}

/** Saves `text` as a file (CSV and the like). */
export function downloadText(
  filename: string,
  text: string,
  type = "text/csv;charset=utf-8",
): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * Opens `html` (a complete, escaped document without scripts) in a new tab
 * and asks the browser to print it.
 */
export function printHtml(html: string): void {
  const url = URL.createObjectURL(
    new Blob([html], { type: "text/html;charset=utf-8" }),
  );
  const win = window.open(url, "_blank");
  if (!win) {
    URL.revokeObjectURL(url);
    toast.error("Allow pop-ups for this site to print the list.");
    return;
  }
  const print = () => {
    try {
      win.focus();
      win.print();
    } catch {
      // The tab stays open; the user can print it from there.
    }
  };
  win.addEventListener("load", print, { once: true });
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
