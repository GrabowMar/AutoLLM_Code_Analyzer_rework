/** Shared helpers for the dashboard page and its components. */

export function timeAgo(dateStr: string): string {
	const seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
	if (seconds < 60) return 'just now';
	if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
	if (seconds < 86400) {
		const h = Math.floor(seconds / 3600);
		return `${h} hour${h > 1 ? 's' : ''} ago`;
	}
	const d = Math.floor(seconds / 86400);
	return `${d} day${d > 1 ? 's' : ''} ago`;
}

export function greeting(): string {
	const h = new Date().getHours();
	if (h < 5) return 'Good night';
	if (h < 12) return 'Good morning';
	if (h < 18) return 'Good afternoon';
	return 'Good evening';
}

export const validStatuses = ['completed', 'running', 'failed', 'pending', 'cancelled'] as const;
export type AnalysisStatus = (typeof validStatuses)[number];

export function normalizeStatus(status: string): AnalysisStatus {
	const lower = status.toLowerCase();
	if (validStatuses.includes(lower as AnalysisStatus)) return lower as AnalysisStatus;
	return 'pending';
}

export interface RecentAnalysis {
	id: string;
	name: string;
	status: AnalysisStatus;
	time: string;
}

export interface RecentApp {
	id: string;
	name: string;
	model: string;
	status: AnalysisStatus;
	time: string;
}
