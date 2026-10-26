<script lang="ts">
	/**
	 * ReportTableView — one typed report table (`{columns, rows}`) with its
	 * title, description and notes. Numeric columns are right-aligned and
	 * tabular; the first column stays visible while scrolling sideways; long
	 * tables show the first rows with "Show all"; the table can be saved as
	 * CSV exactly as stored (raw values, NA as empty).
	 *
	 * Props: `table`, `prov`, `initialRows` (default 12), `headingLevel`.
	 */
	import type { ReportCell, ReportColumnKind, ReportTable } from '$lib/api/analysis';
	import { saveBlob } from '$lib/utils/download';
	import { Button } from '$lib/components/ui/button';
	import Download from '@lucide/svelte/icons/download';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import ReportCellView from './ReportCellView.svelte';
	import type { ReportProvenance } from './provenance';

	interface Props {
		table: ReportTable;
		prov: ReportProvenance;
		initialRows?: number;
		headingLevel?: 3 | 4;
	}

	let { table, prov, initialRows = 12, headingLevel = 4 }: Props = $props();

	const NUMERIC: ReportColumnKind[] = ['int', 'float', 'pct', 'p', 'usd', 'ci', 'pct_ci'];

	let expanded = $state(false);
	const collapsible = $derived(table.rows.length > initialRows + 3);
	const rows = $derived(collapsible && !expanded ? table.rows.slice(0, initialRows) : table.rows);

	function csvValue(value: ReportCell | undefined): string {
		if (value === null || value === undefined) return '';
		const text = Array.isArray(value)
			? value
					.map((v) => (typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v)))
					.join('; ')
			: typeof value === 'object'
				? JSON.stringify(value)
				: String(value);
		return /[",\n;]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
	}

	function downloadCsv() {
		const header = table.columns.map((c) => csvValue(c.key)).join(',');
		const body = table.rows.map((row) => table.columns.map((c) => csvValue(row[c.key])).join(','));
		const blob = new Blob([[header, ...body].join('\n') + '\n'], {
			type: 'text/csv;charset=utf-8'
		});
		saveBlob(blob, `${table.key.replace(/[^a-zA-Z0-9_.-]+/g, '_')}.csv`);
	}
</script>

<section class="space-y-2" aria-labelledby="tbl-{table.key}">
	<div class="flex flex-wrap items-start justify-between gap-2">
		<div class="min-w-0 space-y-0.5">
			<svelte:element this={`h${headingLevel}`} id="tbl-{table.key}" class="text-sm font-semibold"
				>{table.title}</svelte:element
			>
			{#if table.description}<p class="text-xs text-muted-foreground">
					{table.description}
				</p>{/if}
		</div>
		{#if table.rows.length > 0}
			<Button
				variant="ghost"
				size="sm"
				class="h-7"
				onclick={downloadCsv}
				aria-label="Download {table.title} as CSV"
			>
				<Download />CSV
			</Button>
		{/if}
	</div>

	{#if table.rows.length === 0}
		<p
			class="rounded-md border border-dashed border-border px-3 py-2 text-xs text-muted-foreground"
		>
			No rows: nothing to report here.
		</p>
	{:else}
		<div class="table-scroll-wrapper rounded-md border border-border">
			<table class="report-table">
				<thead>
					<tr>
						{#each table.columns as col, j (col.key)}
							<th
								scope="col"
								class:text-right={NUMERIC.includes(col.kind)}
								class:sticky-col={j === 0}
								class="whitespace-nowrap"
							>
								{col.label}
							</th>
						{/each}
					</tr>
				</thead>
				<tbody>
					{#each rows as row, i (i)}
						<tr class:significant={row.significant === true}>
							{#each table.columns as col, j (col.key)}
								{#if j === 0}
									<th scope="row" class="sticky-col text-left font-normal">
										<ReportCellView column={col} {row} {prov} />
									</th>
								{:else}
									<td
										class:text-right={NUMERIC.includes(col.kind)}
										class:whitespace-nowrap={NUMERIC.includes(col.kind) ||
											(typeof row[col.key] === 'string' && String(row[col.key]).length <= 32)}
									>
										<ReportCellView column={col} {row} {prov} />
									</td>
								{/if}
							{/each}
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#if collapsible}
			<Button
				variant="ghost"
				size="sm"
				onclick={() => (expanded = !expanded)}
				aria-expanded={expanded}
			>
				<ChevronDown
					class={expanded ? 'rotate-180 transition-transform' : 'transition-transform'}
				/>
				{expanded ? 'Show fewer rows' : `Show all ${table.rows.length} rows`}
			</Button>
		{/if}
	{/if}
	{#if table.notes.length > 0}
		<ul class="list-disc space-y-0.5 pl-5 text-[11px] text-muted-foreground">
			{#each table.notes as note, i (i)}<li>{note}</li>{/each}
		</ul>
	{/if}
</section>

<style>
	.report-table :global(th),
	.report-table :global(td) {
		vertical-align: top;
		padding-top: 0.375rem;
		padding-bottom: 0.375rem;
	}
	.report-table tbody th {
		text-transform: none;
		letter-spacing: normal;
		font-size: 0.875rem;
		color: var(--foreground);
		background: var(--card);
		position: sticky;
		top: auto;
		z-index: 5;
		backdrop-filter: none;
		border-bottom: 1px solid color-mix(in oklab, var(--border) 60%, transparent);
	}
	.report-table thead th.sticky-col {
		left: 0;
		z-index: 15;
		background: color-mix(in oklab, var(--muted) 60%, var(--card));
	}
	.report-table .sticky-col {
		min-width: 9rem;
		max-width: 20rem;
	}
	.report-table tbody th.sticky-col {
		left: 0;
	}
	.report-table tr.significant {
		background-color: color-mix(in oklab, var(--success) 6%, transparent);
	}
</style>
