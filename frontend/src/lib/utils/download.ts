/**
 * Save data produced in the browser as a file (report JSON, a table as CSV).
 *
 * Files served by the API should go through an authenticated fetch instead
 * (e.g. `downloadAnalysisFile` in `$lib/api/analysis`), so an error answer
 * is shown as a message rather than saved as the file.
 */

/** Save a Blob as a file. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Revoke after the click has been handled (Safari needs a tick).
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

/** Save a JSON value as a pretty-printed `.json` file. */
export function saveJson(value: unknown, filename: string): void {
  saveBlob(
    new Blob([JSON.stringify(value, null, 2)], {
      type: "application/json;charset=utf-8",
    }),
    filename,
  );
}
