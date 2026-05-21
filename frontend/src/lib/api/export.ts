/**
 * Export helper — triggers browser download from the export API.
 * Uses window.location so session cookies are automatically sent.
 */
export function downloadExport(path: string, _filename?: string): void {
	window.location.href = `/api/export/${path}`;
}
