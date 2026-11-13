<script lang="ts">
	/**
	 * ReportTableFigure — one curated table of a report, set as a numbered table of a paper
	 * ("TABLE 3" eyebrow, serif title, the server note as the caption), in the layout its
	 * shape calls for (`planTable`):
	 * - `pairs` (Item/Value, Term/Meaning): a definition list, two columns of pairs when short;
	 * - `threats` (Level/Affects/Finding): a list of findings with their level badge;
	 * - `list` (one column): a numbered list;
	 * - `grid`: a data table — numbers right-aligned mono, shares with a neutral meter (the leader,
	 *   when held by ≤ 2 rows, in primary), tool levels as chips, columns blank on every row left
	 *   out and named in the caption.
	 *
	 * Interaction: a Table / Chart switch when the table can be drawn (`chartSpec` → ReportChart),
	 * click-to-sort headers (blanks last), a quick filter (tables over 8 rows), a Columns menu (over
	 * 4 columns), CSV download and copy-as-Markdown of what is shown. Long tables show their first
	 * rows behind "Show all N rows" (filtering searches every row).
	 *
	 * Compare mode (`other`: the matched table of another report): numeric cells get a signed delta
	 * ("+0.011", "+1.2 pp") under them, pairs say what the other report had, rows new in this report
	 * are tagged and rows only in the other are named in the caption. Deltas are neutral ink: the
	 * sign carries the direction, never red/green.
	 *
	 * Props: `table`, `number`, `id`, `reportId` (file names), `other`, `otherLabel`, `comparing`.
	 */
	import { Popover } from 'bits-ui';
	import { toast } from 'svelte-sonner';
	import ArrowDown from '@lucide/svelte/icons/arrow-down';
	import ArrowUp from '@lucide/svelte/icons/arrow-up';
	import ArrowUpDown from '@lucide/svelte/icons/arrow-up-down';
	import ChartBar from '@lucide/svelte/icons/chart-bar-big';
	import Columns3 from '@lucide/svelte/icons/columns-3';
	import Copy from '@lucide/svelte/icons/copy';
	import FileDown from '@lucide/svelte/icons/file-down';
	import Search from '@lucide/svelte/icons/search';
	import TableIcon from '@lucide/svelte/icons/table';
	import StudyFigure from '$lib/components/studies/study/StudyFigure.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Segmented } from '$lib/components/ui/segmented';
	import { tableFrame } from '$lib/components/table';
	import { cn } from '$lib/utils';
	import type { ReportTable } from '$lib/api/reports';
	import ReportChart from './ReportChart.svelte';
	import { chartSpec } from './chart';
	import { cellDelta, otherCell, rowIndex, rowsOnlyInOther } from './compare';
	import {
		chipParts,
		humanize,
		isBlank,
		isNumeric,
		leaderRows,
		planTable,
		prettyValue,
		rowMatches,
		shareOf,
		sortRows,
		tableFileName,
		threatTone,
		titleParts,
		toCsv,
		toMarkdown
	} from './format';

	interface Props {
		table: ReportTable;
		number: number;
		id: string;
		reportId: string;
		other?: ReportTable | null;
		otherLabel?: string;
		/** A comparison is on (with `other` null: the compared report has no such table). */
		comparing?: boolean;
	}

	let { table, number, id, reportId, other: matched = null, otherLabel = 'the compared report', comparing = false }: Props = $props();
	/** The compared table, when it has rows to compare with. */
	const other = $derived(matched && matched.rows.length > 0 ? matched : null);
	const otherEmpty = $derived(!!matched && matched.rows.length === 0);

	const PREVIEW = 12;
	const plan = $derived(planTable(table));
	const spec = $derived(chartSpec(table, plan));
	const parts = $derived(titleParts(table.title));
	const twoUp = $derived(plan.layout === 'pairs' && table.rows.length > 8 && table.rows.every((r) => (r[1] ?? '').length <= 48));
	const leaders = $derived(new Map(plan.columns.filter((c) => c.kind === 'share').map((c) => [c.index, leaderRows(table, c)])));

	let view = $state<'table' | 'chart'>('table');
	let query = $state('');
	let expanded = $state(false);
	let hiddenCols = $state<number[]>([]);
	let sortIdx = $state<number | null>(null);
	let sortDir = $state<'asc' | 'desc'>('desc');

	const columns = $derived(plan.columns.filter((c) => !hiddenCols.includes(c.index)));
	const order = $derived.by(() => {
		const col = plan.columns.find((c) => c.index === sortIdx);
		const idx = col ? sortRows(table.rows, col.index, col.kind, sortDir) : table.rows.map((_, i) => i);
		return idx.filter((i) => rowMatches(table.rows[i], query));
	});
	const long = $derived(order.length > PREVIEW + 6 && !query.trim());
	const shown = $derived(long && !expanded ? order.slice(0, PREVIEW) : order);

	const otherRows = $derived(rowIndex(other));
	const gone = $derived(rowsOnlyInOther(table, other));
	const interactive = $derived(table.rows.length > 0 && (plan.layout === 'grid' || plan.layout === 'pairs'));

	function toggleSort(index: number, numeric: boolean) {
		if (sortIdx !== index) {
			sortIdx = index;
			sortDir = numeric ? 'desc' : 'asc';
		} else if (sortDir === (numeric ? 'desc' : 'asc')) {
			sortDir = numeric ? 'asc' : 'desc';
		} else {
			sortIdx = null;
		}
	}

	/** What is shown, for CSV / Markdown: visible columns, filtered and sorted rows (all of them, not the preview). */
	function exported(): { cols: string[]; rows: string[][] } {
		if (plan.layout !== 'grid') return { cols: table.columns, rows: order.map((i) => table.rows[i]) };
		return { cols: columns.map((c) => c.header), rows: order.map((i) => columns.map((c) => table.rows[i][c.index] ?? '')) };
	}

	function downloadCsv() {
		const { cols, rows } = exported();
		const blob = new Blob([toCsv(cols, rows)], { type: 'text/csv;charset=utf-8' });
		const a = document.createElement('a');
		a.href = URL.createObjectURL(blob);
		a.download = `${tableFileName(reportId, number, table.title)}.csv`;
		a.click();
		setTimeout(() => URL.revokeObjectURL(a.href), 1000);
	}

	async function copyMarkdown() {
		const { cols, rows } = exported();
		try {
			await navigator.clipboard.writeText(toMarkdown(cols, rows));
			toast.success(`Table ${number} copied as Markdown`);
		} catch {
			toast.error('Copy failed: the browser blocked clipboard access');
		}
	}

	function toggleCol(index: number, on: boolean) {
		hiddenCols = on ? hiddenCols.filter((i) => i !== index) : [...hiddenCols, index];
	}

	const tool =
		'inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-md border border-border px-2 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring';
</script>

{#snippet cell(value: string)}
	{#if isBlank(value)}<span class="text-muted-foreground" title="Not measured or not applicable">—</span>{:else}{prettyValue(value)}{/if}
{/snippet}

{#snippet delta(current: string, previous: string | null)}
	{@const d = cellDelta(current, previous)}
	{#if d}
		<span class="rt-delta" title="{otherLabel}: {previous}">{d.sign === 0 ? 'same' : d.text}</span>
	{:else if previous != null && previous !== current && !(isBlank(previous) && isBlank(current))}
		<span class="rt-delta" title="{otherLabel}: {previous}">was {isBlank(previous) ? '—' : previous}</span>
	{/if}
{/snippet}

{#snippet captionText()}
	{table.note}{#if plan.hidden.length > 0}{table.note ? ' ' : ''}Not shown (no value on any row): {plan.hidden.join(', ')}.{/if}
	{#if other && gone.length > 0}
		<span class="block pt-1">Only in {otherLabel}: {gone.slice(0, 8).join(', ')}{gone.length > 8 ? ` and ${gone.length - 8} more` : ''}.</span>
	{/if}
	{#if comparing && !matched}
		<span class="block pt-1">{otherLabel} has no table like this one.</span>
	{:else if otherEmpty}
		<span class="block pt-1">{otherLabel} has no rows in this table: nothing to compare.</span>
	{/if}
{/snippet}

<StudyFigure
	{id}
	kind="table"
	{number}
	title={parts.join(' › ')}
	flush
	caption={table.note || plan.hidden.length > 0 || (other && gone.length > 0) || (comparing && !other) ? captionText : undefined}
>
	{#snippet controls()}
		{#if spec}
			<Segmented
				size="sm"
				aria-label="Show table {number} as"
				bind:value={view}
				items={[
					{ value: 'table', label: 'Table', icon: TableIcon },
					{ value: 'chart', label: 'Chart', icon: ChartBar }
				]}
			/>
		{/if}
		<span class="font-mono text-[11px] text-muted-foreground tabular-nums">
			{table.totalRows.toLocaleString()} {table.totalRows === 1 ? 'row' : 'rows'}
		</span>
	{/snippet}

	{#if interactive && view === 'table'}
		<div class="flex flex-wrap items-center gap-2 border-b border-border/60 bg-muted/20 px-4 py-2">
			{#if table.rows.length > 8}
				<label class="relative flex min-w-48 flex-1 items-center sm:max-w-72">
					<Search class="pointer-events-none absolute left-2 size-3.5 text-muted-foreground" aria-hidden="true" />
					<input
						type="search"
						bind:value={query}
						placeholder="Filter {table.rows.length} rows…"
						aria-label="Filter table {number}"
						class="h-7 w-full rounded-md border border-border bg-background pl-7 pr-2 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
					/>
				</label>
				{#if query.trim()}<span class="text-xs text-muted-foreground tabular-nums">{order.length} match{order.length === 1 ? '' : 'es'}</span>{/if}
			{/if}
			<span class="ml-auto flex items-center gap-1.5">
				{#if plan.layout === 'grid' && plan.columns.length > 4}
					<Popover.Root>
						<Popover.Trigger class={tool} aria-label="Choose columns">
							<Columns3 class="size-3.5" aria-hidden="true" />Columns{#if hiddenCols.length}<span class="font-mono">−{hiddenCols.length}</span>{/if}
						</Popover.Trigger>
						<Popover.Portal>
							<Popover.Content align="end" sideOffset={6} class="z-50 max-h-80 w-64 overflow-y-auto rounded-md border border-border bg-popover p-2 text-popover-foreground shadow-md">
								<p class="section-label px-1 pb-1.5">Columns</p>
								{#each plan.columns as c, ci (c.index)}
									<label class="flex cursor-pointer items-center gap-2 rounded-sm px-1 py-1 text-sm hover:bg-muted/60">
										<Checkbox
											checked={!hiddenCols.includes(c.index)}
											disabled={ci === 0}
											onCheckedChange={(v) => toggleCol(c.index, v === true)}
										/>
										<span class="truncate">{humanize(c.header)}</span>
									</label>
								{/each}
								{#if hiddenCols.length}
									<button type="button" class="mt-1 w-full rounded-sm px-1 py-1 text-left text-xs text-primary hover:bg-muted/60" onclick={() => (hiddenCols = [])}>Show all columns</button>
								{/if}
							</Popover.Content>
						</Popover.Portal>
					</Popover.Root>
				{/if}
				<button type="button" class={tool} onclick={downloadCsv} title="Download {query.trim() ? 'the matching rows' : 'this table'} as CSV">
					<FileDown class="size-3.5" aria-hidden="true" />CSV
				</button>
				<button type="button" class={tool} onclick={copyMarkdown} title="Copy as a Markdown table" aria-label="Copy table {number} as Markdown">
					<Copy class="size-3.5" aria-hidden="true" />
				</button>
			</span>
		</div>
	{/if}

	{#if table.rows.length === 0}
		<p class="px-4 py-3 text-sm text-muted-foreground">No rows: nothing in this report's scope for this table.</p>
	{:else if view === 'chart' && spec}
		<ReportChart {table} {spec} {other} {otherLabel} />
	{:else if plan.layout === 'pairs'}
		<dl class={cn('rt-pairs', twoUp && !query.trim() && 'rt-pairs-2')}>
			{#each shown as i (i)}
				{@const row = table.rows[i]}
				{@const v = row[1] ?? ''}
				{@const ov = otherCell(other, otherRows.get(row[0] ?? ''), table.columns[1])}
				<div class="rt-pair">
					<dt>{plan.columns[0].header.toLowerCase() === 'term' ? row[0] : humanize(row[0] ?? '')}</dt>
					<dd class={cn(isNumeric(v) && 'font-mono tabular-nums')}>
						{@render cell(v)}
						{#if other}{@render delta(v, ov)}{/if}
					</dd>
				</div>
			{/each}
		</dl>
	{:else if plan.layout === 'threats'}
		{@const iLevel = table.columns.findIndex((c) => c.toLowerCase() === 'level')}
		{@const iFinding = table.columns.findIndex((c) => c.toLowerCase() === 'finding')}
		{@const iAffects = table.columns.findIndex((c) => c.toLowerCase() === 'affects')}
		<ol class="divide-y divide-border/60">
			{#each shown as i (i)}
				{@const row = table.rows[i]}
				<li class="grid gap-x-3 gap-y-1 px-4 py-2.5 sm:grid-cols-[5.5rem_minmax(0,1fr)]">
					<span><StatusBadge status={row[iLevel]} tone={threatTone(row[iLevel] ?? '')} /></span>
					<div class="min-w-0 space-y-0.5">
						<p class="text-sm leading-relaxed">{row[iFinding]}</p>
						{#if iAffects >= 0 && !isBlank(row[iAffects])}
							<p class="text-xs text-muted-foreground">Affects: {row[iAffects]}</p>
						{/if}
					</div>
				</li>
			{/each}
		</ol>
	{:else if plan.layout === 'list'}
		<ol class="divide-y divide-border/60">
			{#each shown as i (i)}
				<li class="flex gap-3 px-4 py-2 text-sm leading-relaxed">
					<span class="w-5 shrink-0 text-right font-mono text-xs text-muted-foreground tabular-nums">{i + 1}</span>
					<span class="min-w-0">{table.rows[i][0]}</span>
				</li>
			{/each}
		</ol>
	{:else}
		<div class="table-frame" {@attach tableFrame}>
			<table class="rt-grid">
				<thead>
					<tr>
						{#each columns as col (col.index)}
							{@const numeric = col.kind === 'num' || col.kind === 'share'}
							{@const active = sortIdx === col.index}
							<th class={numeric ? 'num' : ''} aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}>
								<button
									type="button"
									class={cn('rt-sort group', numeric && 'justify-end text-right', active && 'text-foreground')}
									onclick={() => toggleSort(col.index, numeric)}
									title="Sort by {col.header}"
								>
									<span>{humanize(col.header)}</span>
									{#if active && sortDir === 'asc'}<ArrowUp class="size-3 shrink-0 text-primary" aria-hidden="true" />
									{:else if active}<ArrowDown class="size-3 shrink-0 text-primary" aria-hidden="true" />
									{:else}<ArrowUpDown class="size-3 shrink-0 opacity-0 transition-opacity group-hover:opacity-60 group-focus-visible:opacity-60" aria-hidden="true" />{/if}
								</button>
							</th>
						{/each}
					</tr>
				</thead>
				<tbody>
					{#each shown as i (i)}
						{@const row = table.rows[i]}
						{@const orow = other ? otherRows.get(row[0] ?? '') : undefined}
						<tr class="align-top">
							{#each columns as col, c (col.index)}
								{@const value = row[col.index] ?? ''}
								{@const ov = other && c > 0 ? otherCell(other, orow, col.header) : null}
								{#if col.kind === 'share' && !isBlank(value)}
									{@const share = shareOf(value) ?? 0}
									{@const lead = leaders.get(col.index) ?? 0}
									{@const best = col.best != null && share > 0 && Math.abs(share - col.best) < 1e-9 && lead <= 2 && lead < table.rows.length}
									<td class="num">
										<div class="ml-auto flex w-fit min-w-16 flex-col items-end gap-1">
											<span class={cn('leading-none', best && 'font-semibold text-primary')}>{value}</span>
											<span class="block h-[3px] w-16 overflow-hidden rounded-full bg-muted" aria-hidden="true">
												<span class={cn('block h-full rounded-full', best ? 'bg-primary' : 'bg-muted-foreground/45')} style:width="{share * 100}%"></span>
											</span>
											{#if other}{@render delta(value, ov)}{/if}
										</div>
									</td>
								{:else if col.kind === 'num' || col.kind === 'share'}
									<td class="num">{@render cell(value)}{#if other}<span class="block">{@render delta(value, ov)}</span>{/if}</td>
								{:else if col.kind === 'chips' && !isBlank(value)}
									<td>
										<span class="flex flex-wrap gap-1">
											{#each chipParts(value) as [k, n] (k)}
												<span class="inline-flex items-center gap-1 rounded-sm border border-border bg-muted/40 px-1.5 py-px text-[11px] leading-4">
													<span class="text-muted-foreground">{k}</span><b class="font-mono font-medium tabular-nums">{n}</b>
												</span>
											{/each}
										</span>
									</td>
								{:else}
									<td class={cn(col.kind === 'wide' ? 'min-w-56 max-w-[32rem] break-words' : 'whitespace-nowrap', c === 0 && 'font-medium text-foreground')}>
										{@render cell(value)}
										{#if c === 0 && other && !orow}<span class="ml-1.5 rounded-sm border border-border px-1 py-px align-middle font-sans text-[11px] font-normal text-muted-foreground" title="No such row in {otherLabel}">new</span>{/if}
										{#if c > 0 && other}{@render delta(value, ov)}{/if}
									</td>
								{/if}
							{/each}
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}

	{#if view === 'table' && table.rows.length > 0 && order.length === 0}
		<p class="px-4 py-3 text-sm text-muted-foreground">No row matches “{query}”.</p>
	{/if}

	{#if long && view === 'table'}
		<div class="flex items-center justify-between gap-3 border-t border-border/60 px-4 py-2">
			<span class="text-xs text-muted-foreground">
				{expanded ? `All ${order.length}` : `First ${PREVIEW} of ${order.length}`} rows
			</span>
			<button
				type="button"
				class="inline-flex h-7 cursor-pointer items-center rounded-md border border-border px-2.5 text-xs font-medium transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
				aria-expanded={expanded}
				onclick={() => (expanded = !expanded)}
			>
				{expanded ? 'Show fewer' : `Show all ${order.length} rows`}
			</button>
		</div>
	{/if}
</StudyFigure>

<style>
	.rt-grid th {
		white-space: normal;
		min-width: 4.5rem;
		max-width: 11rem;
		vertical-align: bottom;
		line-height: 1.35;
	}
	.rt-sort {
		display: inline-flex;
		width: 100%;
		align-items: flex-end;
		gap: 0.25rem;
		cursor: pointer;
		text-transform: inherit;
		letter-spacing: inherit;
		font: inherit;
		color: inherit;
		border-radius: 2px;
	}
	.rt-sort:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: 2px;
	}
	.rt-delta {
		display: block;
		margin-top: 0.125rem;
		font-family: var(--font-mono);
		font-size: 11px;
		font-weight: 400;
		line-height: 1.2;
		color: var(--muted-foreground);
		white-space: nowrap;
	}
	.rt-pairs {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
	}
	@media (min-width: 64rem) {
		.rt-pairs-2 {
			grid-template-columns: repeat(2, minmax(0, 1fr));
			column-gap: 0;
		}
		.rt-pairs-2 .rt-pair:nth-child(odd) {
			border-right: 1px solid color-mix(in oklab, var(--border) 60%, transparent);
		}
	}
	.rt-pair {
		display: grid;
		grid-template-columns: minmax(8rem, 40%) minmax(0, 1fr);
		gap: 0.75rem;
		padding: 0.5rem 1rem;
		border-bottom: 1px solid color-mix(in oklab, var(--border) 60%, transparent);
		font-size: 0.875rem;
		line-height: 1.45;
	}
	.rt-pair dt {
		color: var(--muted-foreground);
	}
	@media (max-width: 39.99rem) {
		.rt-pair {
			grid-template-columns: minmax(0, 1fr);
			gap: 0.125rem;
		}
		.rt-pair dt {
			font-size: 0.75rem;
		}
	}
	.rt-pair dd {
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.rt-pairs > .rt-pair:last-child,
	.rt-pairs-2 > .rt-pair:nth-last-child(2):nth-child(odd) {
		border-bottom: 0;
	}
</style>
