/**
 * Shared display formatters. Prefer these over page-local copies; when a page
 * genuinely needs different behavior (rounding, thresholds), keep it local
 * with a comment rather than forking these.
 */

/** Duration in seconds → "300ms" / "42.3s" / "3m 12s". */
export function formatDuration(
	seconds: number | null | undefined,
	decimals = 1,
): string {
	if (seconds == null) return '—';
	if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
	if (seconds < 60) return `${seconds.toFixed(decimals)}s`;
	const m = Math.floor(seconds / 60);
	const s = Math.round(seconds % 60);
	return `${m}m ${s}s`;
}

/** ISO date → short "5 Mar 14:32". */
export function formatDate(iso: string | null | undefined): string {
	if (!iso) return '—';
	const d = new Date(iso);
	return (
		d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) +
		' ' +
		d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
	);
}

/** ISO date → full locale string, e.g. "3/5/2026, 2:32:10 PM". */
export function formatDateTime(s: string | null | undefined): string {
	return s ? new Date(s).toLocaleString() : '—';
}

/** ISO date → fixed-width "MM/DD HH:mm". */
export function formatDateCompact(d: string | null | undefined): string {
	if (!d) return '—';
	const dt = new Date(d);
	const mo = (dt.getMonth() + 1).toString().padStart(2, '0');
	const day = dt.getDate().toString().padStart(2, '0');
	const hr = dt.getHours().toString().padStart(2, '0');
	const mn = dt.getMinutes().toString().padStart(2, '0');
	return `${mo}/${day} ${hr}:${mn}`;
}

/** Job/run cost in USD → "Free" / "$0.000123" / "$0.0456". */
export function formatCost(c: number): string {
	if (c === 0) return 'Free';
	if (c < 0.01) return `$${c.toFixed(6)}`;
	return `$${c.toFixed(4)}`;
}

/** Model pricing (per-token/million rates) → "Free" / "—" / "$0.0040" / "$1.25". */
export function formatPrice(price: number): string {
	if (price === 0) return 'Free';
	if (price < 0) return '—';
	if (price < 0.01) return `$${price.toFixed(4)}`;
	return `$${price.toFixed(2)}`;
}

/** Token count → "1.5M" / "32K" / "512". */
export function formatTokens(n: number): string {
	if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
	if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
	return String(n);
}

/** Locale number with capped fraction digits. */
export function formatNumber(
	n: number | null | undefined,
	decimals = 1,
): string {
	if (n == null) return '—';
	return n.toLocaleString(undefined, {
		minimumFractionDigits: 0,
		maximumFractionDigits: decimals,
	});
}

/** Capitalize first letter of a status string. */
export function statusLabel(status: string): string {
	return status.charAt(0).toUpperCase() + status.slice(1);
}
