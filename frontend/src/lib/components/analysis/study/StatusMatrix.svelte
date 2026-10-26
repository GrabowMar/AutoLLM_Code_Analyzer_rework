<script lang="ts" module>
	export interface MatrixRow {
		key: string;
		label: string;
		/** Rows sharing a group get one header row (rows must be sorted by group). */
		group?: string;
		/** Link of the row label (e.g. the run). */
		href?: string | null;
		/** De-emphasise (e.g. a target outside the analysis set). */
		muted?: boolean;
		/** Short note after the label (e.g. "stale"). */
		note?: string;
	}

	export interface MatrixColumn {
		key: string;
		label: string;
	}
</script>

<script lang="ts">
	/**
	 * StatusMatrix — rows (targets/runs) × columns (tools) of status cells.
	 * Each cell is a tinted square with an icon (status is never color
	 * alone), a tooltip with the status and reason, and an optional link to
	 * the run/tool result behind it (provenance click-through). The legend
	 * lists every status present with its count.
	 *
	 * Props:
	 * - `rows: MatrixRow[]`, `columns: MatrixColumn[]`, `cells[i][j]` status token;
	 * - `reasons[i][j]` (optional failure reason / detail);
	 * - `cellHref(i, j)`: link of a cell (null = not clickable);
	 * - `tones`: status → tone overrides (defaults from `statusTone`);
	 * - `labels`: status → legend text; `label`: accessible caption;
	 * - `rowHeader`: first column header (default "Target").
	 */
	import type { Component } from 'svelte';
	import { cn } from '$lib/utils';
	import { statusTone, toneClasses, type Tone } from '$lib/constants/colors';
	import Check from '@lucide/svelte/icons/check';
	import X from '@lucide/svelte/icons/x';
	import Clock from '@lucide/svelte/icons/clock';
	import Minus from '@lucide/svelte/icons/minus';
	import SkipForward from '@lucide/svelte/icons/skip-forward';
	import Circle from '@lucide/svelte/icons/circle';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Ban from '@lucide/svelte/icons/ban';
	import CircleHelp from '@lucide/svelte/icons/circle-help';
	import CircleDashed from '@lucide/svelte/icons/circle-dashed';
	import Play from '@lucide/svelte/icons/play';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import EmptyState from '$lib/components/EmptyState.svelte';

	interface Props {
		rows: MatrixRow[];
		columns: MatrixColumn[];
		cells: string[][];
		reasons?: string[][];
		cellHref?: (i: number, j: number) => string | null;
		tones?: Partial<Record<string, Tone>>;
		labels?: Partial<Record<string, string>>;
		rowHeader?: string;
		label?: string;
		class?: string;
	}

	let {
		rows,
		columns,
		cells,
		reasons,
		cellHref,
		tones,
		labels = {},
		rowHeader = 'Target',
		label = 'Status matrix',
		class: className
	}: Props = $props();

	const ICONS: Record<string, Component> = {
		succeeded: Check,
		completed: Check,
		run: Play,
		failed: X,
		error: X,
		timed_out: Clock,
		not_applicable: Minus,
		skipped: SkipForward,
		pending: Circle,
		queued: Circle,
		running: LoaderCircle,
		cancelled: Ban,
		missing: CircleHelp,
		unavailable: TriangleAlert,
		not_run: CircleDashed
	};

	const DEFAULT_LABELS: Record<string, string> = {
		succeeded: 'Succeeded',
		failed: 'Failed',
		timed_out: 'Timed out',
		not_applicable: 'Not applicable (no files in scope)',
		skipped: 'Skipped (no data)',
		pending: 'Pending',
		running: 'Running',
		cancelled: 'Cancelled',
		missing: 'Missing (no result)',
		not_run: 'Not run',
		run: 'Will run',
		unavailable: 'Unavailable',
		error: 'Error'
	};

	function text(status: string): string {
		return labels[status] ?? DEFAULT_LABELS[status] ?? status.replace(/_/g, ' ');
	}

	function tone(status: string): Tone {
		return statusTone(status, tones);
	}

	const counts = $derived.by(() => {
		const map = new Map<string, number>();
		for (const row of cells) for (const s of row) map.set(s, (map.get(s) ?? 0) + 1);
		return [...map.entries()].sort((a, b) => b[1] - a[1]);
	});

	function cellTitle(i: number, j: number): string {
		const status = cells[i]?.[j] ?? 'missing';
		const reason = reasons?.[i]?.[j];
		const base = `${rows[i]?.label ?? ''} · ${columns[j]?.label ?? ''}: ${text(status)}`;
		return reason ? `${base} (${reason.replace(/_/g, ' ')})` : base;
	}

	/** The row label without its group prefix (the group header already says it). */
	function shortLabel(row: MatrixRow): string {
		const prefix = row.group ? `${row.group} · ` : '';
		return prefix && row.label.startsWith(prefix) ? row.label.slice(prefix.length) : row.label;
	}

	function showGroup(i: number): boolean {
		const g = rows[i]?.group;
		return !!g && (i === 0 || rows[i - 1]?.group !== g);
	}
</script>

{#if rows.length === 0 || columns.length === 0}
	<EmptyState compact title="Nothing to show yet" description="Rows appear once runs exist." />
{:else}
	<figure class={cn('space-y-2', className)}>
		<div
			class="table-scroll-wrapper max-h-[32rem] w-fit max-w-full overflow-y-auto rounded-md border border-border"
		>
			<table class="status-matrix text-xs" style="width: auto">
				<caption class="sr-only">{label}</caption>
				<thead>
					<tr>
						<th
							scope="col"
							class="sticky left-0 z-20 min-w-[7rem] bg-card align-bottom normal-case sm:min-w-[10rem]"
						>
							{rowHeader}
						</th>
						{#each columns as col (col.key)}
							<th
								scope="col"
								class="h-24 w-7 min-w-7 px-0 align-bottom normal-case"
								title={col.label}
							>
								<span
									class="vertical mx-auto block max-h-24 truncate font-mono text-[11px] font-medium"
								>
									{col.label}
								</span>
							</th>
						{/each}
					</tr>
				</thead>
				<tbody>
					{#each rows as row, i (row.key)}
						{#if showGroup(i)}
							<tr class="group-row">
								<th
									scope="rowgroup"
									colspan={columns.length + 1}
									class="sticky left-0 bg-muted/60 px-3 py-1 text-left text-[11px] font-semibold normal-case text-muted-foreground"
								>
									{row.group}
								</th>
							</tr>
						{/if}
						<tr class={cn(row.muted && 'opacity-60')}>
							<th
								scope="row"
								class="sticky left-0 z-10 max-w-[9rem] bg-card px-3 py-1 text-left font-normal normal-case tracking-normal sm:max-w-[18rem]"
							>
								<span class="flex min-w-0 items-center gap-1.5">
									{#if row.href}
										<a
											href={row.href}
											class="truncate text-foreground hover:text-primary hover:underline"
											title={row.label}
										>
											{shortLabel(row)}
										</a>
									{:else}
										<span class="truncate text-foreground" title={row.label}>{shortLabel(row)}</span
										>
									{/if}
									{#if row.note}
										<span class="shrink-0 text-[11px] text-warning-fg">{row.note}</span>
									{/if}
								</span>
							</th>
							{#each columns as col, j (col.key)}
								{@const status = cells[i]?.[j] ?? 'missing'}
								{@const Icon = ICONS[status] ?? CircleHelp}
								{@const href = cellHref?.(i, j) ?? null}
								<td class="px-0.5 py-0.5 text-center">
									{#if href}
										<a
											{href}
											title={cellTitle(i, j)}
											aria-label={cellTitle(i, j)}
											class={cn(
												'mx-auto flex h-6 w-6 items-center justify-center rounded-[4px] border transition-transform duration-150 hover:scale-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50',
												toneClasses[tone(status)]
											)}
										>
											<Icon
												class={cn(
													'h-3.5 w-3.5',
													status === 'running' && 'animate-spin motion-reduce:animate-none'
												)}
												aria-hidden="true"
											/>
										</a>
									{:else}
										<span
											title={cellTitle(i, j)}
											class={cn(
												'mx-auto flex h-6 w-6 items-center justify-center rounded-[4px] border',
												toneClasses[tone(status)]
											)}
										>
											<Icon
												class={cn(
													'h-3.5 w-3.5',
													status === 'running' && 'animate-spin motion-reduce:animate-none'
												)}
												aria-hidden="true"
											/>
											<span class="sr-only">{cellTitle(i, j)}</span>
										</span>
									{/if}
								</td>
							{/each}
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<figcaption>
			<ul
				class="flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-muted-foreground"
				aria-label="Legend"
			>
				{#each counts as [status, n] (status)}
					{@const Icon = ICONS[status] ?? CircleHelp}
					<li class="inline-flex items-center gap-1.5">
						<span
							class={cn(
								'flex h-4 w-4 items-center justify-center rounded-[3px] border',
								toneClasses[tone(status)]
							)}
						>
							<Icon class="h-3 w-3" aria-hidden="true" />
						</span>
						<span class="text-foreground/90">{text(status)}</span>
						<span class="tabular-nums">{n}</span>
					</li>
				{/each}
			</ul>
		</figcaption>
	</figure>
{/if}

<style>
	.vertical {
		writing-mode: vertical-rl;
		transform: rotate(180deg);
	}
	.status-matrix td {
		border-bottom: 1px solid color-mix(in oklab, var(--border) 40%, transparent);
	}
	.status-matrix tbody tr.group-row:hover {
		background-color: transparent;
	}
</style>
