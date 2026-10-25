<script lang="ts">
	/**
	 * ConditionGrid — every cell of a launched experiment at a glance: one row
	 * per condition (under collapsible endpoint headers), one column per app,
	 * one 10 px square per repeat coloured by its state. A CSS grid (not SVG):
	 * squares are links with an `aria-label` and a native tooltip, and open
	 * the run. The label column is sticky; wide grids scroll sideways. Below
	 * `md` each row collapses to a progress bar.
	 *
	 * Props:
	 * - `grid`: the live cell grid (`ExperimentLive.grid`).
	 * - `groupBy`: 'endpoint' | 'none'. `sort`: 'sequence' | 'label' | 'progress' | 'pass'.
	 * - `baselineId`: condition marked "baseline".
	 */
	import { cn } from '$lib/utils';
	import BatchProgress from '$lib/components/generator/batches/BatchProgress.svelte';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import {
		CELL_STATES,
		EMPTY_SQUARE,
		cellIndex,
		groupConditions,
		rowSummary,
		type CellGrid,
		type GridGroupBy,
		type GridSort,
	} from '../cells';
	import type { CellCode, ProgressCounts } from '$lib/api/types/experiments';
	import { EMPTY_PROGRESS, formatPercent, toBatchCounts } from '../format';

	interface Props {
		grid: CellGrid;
		groupBy?: GridGroupBy;
		sort?: GridSort;
		baselineId?: number | null;
	}

	let { grid, groupBy = 'endpoint', sort = 'sequence', baselineId = null }: Props = $props();

	let collapsed = $state(new Set<string>());

	const summaries = $derived(grid.conditions.map((_, i) => rowSummary(grid, i)));
	const groups = $derived(groupConditions(grid, groupBy, sort, summaries));
	const repeats = $derived(grid.repeats);
	const appCount = $derived(grid.apps.length);
	// Each app column holds `repeats` squares (10 px + 2 px gap) plus padding.
	const appColumnWidth = $derived(Math.max(28, repeats * 12 + 8));
	const gridTemplate = $derived(
		`minmax(9rem, 16rem) repeat(${appCount}, ${appColumnWidth}px) minmax(5.5rem, auto)`
	);

	const legend = $derived.by(() => {
		const seen = new Set<CellCode>();
		for (const code of grid.codes) if (code) seen.add(code);
		return (Object.keys(CELL_STATES) as CellCode[])
			.filter((c) => seen.has(c))
			.sort((a, b) => CELL_STATES[a].order - CELL_STATES[b].order);
	});

	function rowCounts(ci: number): ProgressCounts {
		const out = { ...EMPTY_PROGRESS };
		const per = appCount * repeats;
		for (let i = ci * per; i < (ci + 1) * per; i++) {
			const code = grid.codes[i];
			if (code) out[CELL_STATES[code].bucket] += 1;
		}
		return out;
	}

	function toggle(key: string) {
		const next = new Set(collapsed);
		if (next.has(key)) next.delete(key);
		else next.add(key);
		collapsed = next;
	}

	function groupDone(rows: number[]): { done: number; total: number } {
		let done = 0;
		let total = 0;
		for (const r of rows) {
			done += summaries[r]?.done ?? 0;
			total += summaries[r]?.total ?? 0;
		}
		return { done, total };
	}

	function squareLabel(ci: number, ai: number, r: number, code: CellCode | null): string {
		const condition = grid.conditions[ci];
		const app = grid.apps[ai];
		const state = code ? CELL_STATES[code].label : 'Not started';
		const i = cellIndex(grid, ci, ai, r);
		const rate = grid.passRates[i];
		const attempt = grid.attempts[i];
		return [
			`${condition.label} · ${app.name} · repeat ${r + 1}`,
			state + (rate != null && (code === 'c' || code === 'f') ? ` (${formatPercent(rate)})` : ''),
			attempt > 1 ? `attempt ${attempt}` : '',
		]
			.filter(Boolean)
			.join(' — ');
	}
</script>

{#snippet row(ci: number)}
	{@const condition = grid.conditions[ci]}
	{@const summary = summaries[ci]}
	<div
		class="contents"
		role="row"
		aria-label="{condition.label}: {summary.done} of {summary.total} cells finished"
	>
		<div
			role="rowheader"
			class="sticky left-0 z-10 flex min-w-0 items-center gap-1.5 border-b border-border/50 bg-card py-1 pr-2 text-xs"
		>
			<span class="min-w-0 truncate font-medium" title={condition.label}>{condition.label}</span>
			{#if condition.id === baselineId}
				<span class="shrink-0 rounded-full border border-primary/40 bg-primary/10 px-1.5 text-[11px] text-success-fg">baseline</span>
			{/if}
		</div>
		{#each grid.apps as app, ai (app.id)}
			<div role="cell" class="hidden items-center gap-0.5 border-b border-border/50 px-1 py-1 md:flex">
				{#each { length: repeats } as _, r (r)}
					{@const i = cellIndex(grid, ci, ai, r)}
					{@const code = grid.codes[i]}
					{#if code && grid.jobIds[i]}
						<a
							href="/applications/{grid.jobIds[i]}"
							class={cn(
								'block size-2.5 shrink-0 rounded-[2px] transition-transform duration-150 hover:scale-125 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
								CELL_STATES[code].square
							)}
							title={squareLabel(ci, ai, r, code)}
							aria-label={squareLabel(ci, ai, r, code)}
						></a>
					{:else}
						<span
							class={cn('block size-2.5 shrink-0 rounded-[2px]', EMPTY_SQUARE)}
							title={squareLabel(ci, ai, r, null)}
							aria-label={squareLabel(ci, ai, r, null)}
							role="img"
						></span>
					{/if}
				{/each}
			</div>
		{/each}
		<div role="cell" class="flex min-w-0 flex-col justify-center gap-1 border-b border-border/50 py-1 pl-2">
			<span class="flex items-baseline gap-1.5 font-mono text-[11px] tabular-nums">
				{#if summary.graded > 0}
					<span class="font-semibold text-foreground" title="Passed every check / graded">{summary.passed}/{summary.graded}</span>
					<span class="text-muted-foreground">{formatPercent(summary.passRate)}</span>
				{:else}
					<span class="text-muted-foreground" title="Finished / cells">{summary.done}/{summary.total}</span>
				{/if}
			</span>
			<BatchProgress counts={toBatchCounts(rowCounts(ci))} total={summary.total} class="w-20 md:w-24" />
		</div>
	</div>
{/snippet}

<div class="space-y-3">
	<div class="relative overflow-x-auto rounded-md border border-border" role="region" aria-label="Condition by app grid">
		<div
			class="grid w-max min-w-full items-stretch px-2 max-md:!grid-cols-[minmax(0,1fr)_auto]"
			style="grid-template-columns: {gridTemplate}"
			role="table"
			aria-label="Runs per condition and app; one square per repeat"
		>
			<div class="contents" role="row">
				<div role="columnheader" class="sticky left-0 z-20 border-b border-border bg-card py-1.5 pr-2 section-label">
					Condition
				</div>
				{#each grid.apps as app (app.id)}
					<div
						role="columnheader"
						class="hidden min-w-0 border-b border-border px-1 py-1.5 text-[11px] text-muted-foreground md:block"
						title={app.name}
					>
						<span class="block truncate">{app.slug || app.name}</span>
					</div>
				{/each}
				<div role="columnheader" class="border-b border-border py-1.5 pl-2 section-label">Done</div>
			</div>

			{#each groups as group (group.key)}
				{#if group.label}
					{@const totals = groupDone(group.rows)}
					{@const open = !collapsed.has(group.key)}
					<div class="contents" role="row">
						<div role="cell" class="sticky left-0 z-10 col-span-full bg-muted/40" style="grid-column: 1 / -1">
							<button
								type="button"
								class="flex w-full cursor-pointer items-center gap-1.5 px-1 py-1 text-left text-xs font-medium transition-colors hover:text-foreground"
								aria-expanded={open}
								onclick={() => toggle(group.key)}
							>
								<ChevronRight class={cn('size-3.5 transition-transform duration-150', open && 'rotate-90')} aria-hidden="true" />
								<span class="font-mono">{group.label}</span>
								<span class="text-muted-foreground">
									· {group.rows.length} condition{group.rows.length === 1 ? '' : 's'} ·
									<span class="tabular-nums">{totals.done}/{totals.total}</span> done
								</span>
							</button>
						</div>
					</div>
					{#if open}
						{#each group.rows as ci (grid.conditions[ci].id)}
							{@render row(ci)}
						{/each}
					{/if}
				{:else}
					{#each group.rows as ci (grid.conditions[ci].id)}
						{@render row(ci)}
					{/each}
				{/if}
			{/each}
		</div>
	</div>

	{#if legend.length > 0}
		<ul class="hidden flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground md:flex" aria-label="Square legend">
			{#each legend as code (code)}
				<li class="inline-flex items-center gap-1.5">
					<span class={cn('inline-block size-2.5 rounded-[2px]', CELL_STATES[code].square, 'animate-none')} aria-hidden="true"></span>
					{CELL_STATES[code].label}
				</li>
			{/each}
		</ul>
	{/if}
</div>
