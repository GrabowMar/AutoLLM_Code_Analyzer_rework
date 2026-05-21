/** Format a duration in seconds to human-readable string */
export function formatDuration(seconds: number | null): string {
	if (seconds == null) return '—';
	if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
	if (seconds < 60) return `${seconds.toFixed(1)}s`;
	const m = Math.floor(seconds / 60);
	const s = Math.round(seconds % 60);
	return `${m}m ${s}s`;
}

/** Format ISO date string to short display format */
export function formatDate(iso: string | null): string {
	if (!iso) return '—';
	const d = new Date(iso);
	return (
		d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) +
		' ' +
		d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
	);
}

/** Capitalize first letter of a status string */
export function statusLabel(status: string): string {
	return status.charAt(0).toUpperCase() + status.slice(1);
}
