import { toast } from 'svelte-sonner';

export const statusColors: Record<string, string> = {
	completed: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30',
	failed: 'bg-red-500/15 text-red-400 border-red-500/30',
	running: 'bg-amber-500/15 text-amber-500 border-amber-500/30',
	pending: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30',
};

export const modeColors: Record<string, string> = {
	custom: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
	scaffolding: 'bg-purple-500/15 text-purple-400 border-purple-500/30',
	copilot: 'bg-teal-500/15 text-teal-400 border-teal-500/30',
};

export const httpColors: Record<string, string> = {
	GET: 'text-emerald-400',
	POST: 'text-blue-400',
	PUT: 'text-amber-400',
	DELETE: 'text-red-400',
	PATCH: 'text-purple-400',
};

export const segmentBorderColors: Record<string, string> = {
	metadata: 'border-l-purple-400 bg-purple-500/5',
	system: 'border-l-blue-400 bg-blue-500/5',
	user: 'border-l-emerald-400 bg-emerald-500/5',
	template: 'border-l-sky-400 bg-sky-500/5',
	requirements: 'border-l-emerald-400 bg-emerald-500/5',
	scaffolding: 'border-l-orange-400 bg-orange-500/5',
	default: '',
};

export function fmt(n: number | null | undefined, decimals = 1): string {
	if (n == null) return '—';
	return n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: decimals });
}

export function fmtDur(seconds: number | null): string {
	if (seconds == null) return '—';
	if (seconds < 60) return `${seconds.toFixed(1)}s`;
	const m = Math.floor(seconds / 60);
	const s = Math.round(seconds % 60);
	return `${m}m ${s}s`;
}

export function fmtDate(d: string | null): string {
	if (!d) return '—';
	return new Date(d).toLocaleString();
}

export function fmtDateCompact(d: string | null): string {
	if (!d) return '—';
	const dt = new Date(d);
	const mo = (dt.getMonth() + 1).toString().padStart(2, '0');
	const day = dt.getDate().toString().padStart(2, '0');
	const hr = dt.getHours().toString().padStart(2, '0');
	const mn = dt.getMinutes().toString().padStart(2, '0');
	return `${mo}/${day} ${hr}:${mn}`;
}

export function fmtCost(c: number): string {
	if (c === 0) return 'Free';
	if (c < 0.01) return `$${c.toFixed(6)}`;
	return `$${c.toFixed(4)}`;
}

export function copyText(text: string, label = 'Copied'): void {
	navigator.clipboard.writeText(text);
	toast.success(label);
}

export function parsePromptSegments(text: string): { type: string; content: string }[] {
	const lines = text.split('\n');
	const segments: { type: string; content: string }[] = [];
	let currentType = 'default';
	let currentLines: string[] = [];
	for (const line of lines) {
		let newType = currentType;
		if (line.startsWith('=== REQUEST METADATA ===') || line.startsWith('=== META')) newType = 'metadata';
		else if (line.startsWith('=== SYSTEM ===')) newType = 'system';
		else if (line.startsWith('=== USER ===') || line.startsWith('## Output')) newType = 'user';
		else if (line.startsWith('=== TEMPLATE ===') || line.startsWith('## Mindset')) newType = 'template';
		else if (line.startsWith('=== REQUIREMENTS ===')) newType = 'requirements';
		else if (line.startsWith('=== SCAFFOLDING ===')) newType = 'scaffolding';
		if (newType !== currentType && currentLines.length > 0) {
			segments.push({ type: currentType, content: currentLines.join('\n') });
			currentLines = [];
		}
		currentType = newType;
		currentLines.push(line);
	}
	if (currentLines.length > 0) {
		segments.push({ type: currentType, content: currentLines.join('\n') });
	}
	return segments;
}

export interface VirtualFile {
	name: string;
	code: string;
	lang: string;
}

export interface CodeFootprint {
	totalLines: number;
	totalChars: number;
	languages: Record<string, number>;
	fileCount: number;
	files: VirtualFile[];
	truncated: boolean;
}
