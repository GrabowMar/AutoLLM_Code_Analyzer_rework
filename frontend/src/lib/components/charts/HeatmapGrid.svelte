<script lang="ts">
	/**
	 * HeatmapGrid — a rows × columns grid of numbers as an accessible table:
	 * every cell prints its value (color is a secondary cue), missing cells
	 * are hatched "—" with the NA reason, never 0.
	 *
	 * Props:
	 * - `rows` / `columns` (keys) with optional `rowLabels` / `columnLabels`;
	 * - `values[i][j]` for rows[i] × columns[j] (`null` = missing);
	 * - `format(v)` cell text; `domain` (default: data range, from 0 when all ≥ 0);
	 * - `color` (CSS var of the sequential hue, default `var(--chart-2)`);
	 * - `rowHeader` (first column header), `legendLabel` (what the shade means);
	 * - `naReason` (tooltip of missing cells), `cellTitle(i, j)` extra tooltip;
	 * - `cellHref(i, j)` makes a cell a link (provenance click-through);
	 * - `label` (accessible caption).
	 */
	import { cn } from '$lib/utils';
	import ChartEmpty from './ChartEmpty.svelte';

	interface Props {
		rows: string[];
		columns: string[];
		values: (number | null)[][];
		rowLabels?: string[];
		columnLabels?: string[];
		format?: (value: number) => string;
		domain?: [number, number];
		color?: string;
		rowHeader?: string;
		legendLabel?: string;
		naReason?: string;
		cellTitle?: (i: number, j: number) => string;
		cellHref?: (i: number, j: number) => string | null;
		label?: string;
		class?: string;
	}

	let {
		rows,
		columns,
		values,
		rowLabels,
		columnLabels,
		format = (v: number) => String(Number(v.toPrecision(3))),
		domain,
		color = 'var(--chart-2)',
		rowHeader = '',
		legendLabel = 'Value',
		naReason = 'Not available (NA): no data for this cell. It is never counted as 0.',
		cellTitle,
		cellHref,
		label = 'Heatmap',
		class: className
	}: Props = $props();

	const numbers = $derived(
		values.flat().filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
	);
	const range = $derived.by((): [number, number] => {
		if (domain) return domain;
		if (numbers.length === 0) return [0, 1];
		const lo = Math.min(...numbers);
		const hi = Math.max(...numbers);
		return [lo >= 0 ? 0 : lo, hi];
	});
	const hasMissing = $derived(values.some((row) => row.some((v) => v === null)));

	/** Fill strength 0..62% — capped so foreground text keeps ≥ 4.5:1 in both modes. */
	function strength(v: number): number {
		const [lo, hi] = range;
		if (hi === lo) return v === 0 ? 0 : 40;
		const t = Math.min(Math.max((v - lo) / (hi - lo), 0), 1);
		return Math.round(t * 62);
	}

	function cellStyle(v: number | null): string {
		if (v === null) return '';
		const s = strength(v);
		return s === 0 ? '' : `background: color-mix(in oklab, ${color} ${s}%, transparent);`;
	}

	function title(i: number, j: number, v: number | null): string {
		const head = `${rowLabels?.[i] ?? rows[i]} × ${columnLabels?.[j] ?? columns[j]}: `;
		const base = v === null ? naReason : format(v);
		const extra = cellTitle?.(i, j);
		return head + base + (extra ? ` (${extra})` : '');
	}
</script>

{#if rows.length === 0 || columns.length === 0}
	<ChartEmpty message="Nothing to show in this grid." height={100} />
{:else}
	<figure class={cn('space-y-2', className)}>
		<div class="table-scroll-wrapper w-fit max-w-full rounded-md border border-border">
			<table class="heatmap text-xs" style="width: auto">
				<caption class="sr-only">{label}</caption>
				<thead>
					<tr>
						<th scope="col" class="sticky left-0 z-20 min-w-[8rem] bg-card normal-case">
							{rowHeader}
						</th>
						{#each columns as col, j (col)}
							<th
								scope="col"
								class="min-w-[3.5rem] text-center normal-case"
								title={columnLabels?.[j] ?? col}
							>
								<span class="block max-w-[12rem] truncate font-mono"
									>{columnLabels?.[j] ?? col}</span
								>
							</th>
						{/each}
					</tr>
				</thead>
				<tbody>
					{#each rows as row, i (row)}
						<tr>
							<th
								scope="row"
								class="sticky left-0 z-10 max-w-[16rem] bg-card text-left font-normal normal-case tracking-normal text-foreground"
							>
								<span class="block truncate text-xs" title={rowLabels?.[i] ?? row}
									>{rowLabels?.[i] ?? row}</span
								>
							</th>
							{#each columns as col, j (col)}
								{@const v = values[i]?.[j] ?? null}
								{@const href = cellHref?.(i, j) ?? null}
								<td
									class={cn('p-0 text-center tabular-nums', v === null && 'na-cell')}
									style={cellStyle(v)}
									title={title(i, j, v)}
								>
									{#if href}
										<a
											{href}
											class="block px-2 py-1.5 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
										>
											{v === null ? '—' : format(v)}
										</a>
									{:else}
										<span
											class={cn(
												'block px-2 py-1.5',
												v === null || v === 0 ? 'text-muted-foreground' : ''
											)}
										>
											{v === null ? '—' : format(v)}
										</span>
									{/if}
								</td>
							{/each}
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<figcaption
			class="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground"
		>
			<span class="inline-flex items-center gap-1.5">
				{legendLabel}
				<span class="tabular-nums">{format(range[0])}</span>
				<span
					class="inline-block h-2.5 w-20 rounded-sm border border-border"
					style="background: linear-gradient(90deg, transparent, color-mix(in oklab, {color} 62%, transparent))"
					aria-hidden="true"
				></span>
				<span class="tabular-nums">{format(range[1])}</span>
			</span>
			{#if hasMissing}
				<span class="inline-flex items-center gap-1.5">
					<span
						class="na-swatch inline-block h-2.5 w-2.5 rounded-sm border border-border"
						aria-hidden="true"
					></span>
					— missing (NA)
				</span>
			{/if}
		</figcaption>
	</figure>
{/if}

<style>
	.heatmap td {
		border-left: 1px solid color-mix(in oklab, var(--border) 60%, transparent);
	}
	.heatmap tbody tr:hover {
		background-color: transparent;
	}
	.na-cell,
	.na-swatch {
		background: repeating-linear-gradient(
			45deg,
			var(--muted) 0 4px,
			color-mix(in oklab, var(--muted-foreground) 18%, transparent) 4px 6px
		);
	}
</style>
