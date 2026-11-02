<script lang="ts">
	// A report: summary card, then the report's curated tables exactly as the
	// Markdown/LaTeX exports have them (`GET /reports/{id}/tables/`, server-side
	// `renderers.report_tables`: labelled columns, condition names, p-values
	// with their interim/withheld state, "-" for unmeasured), one card per table.
	import { formatDateTime as formatDate } from '$lib/utils/formatters';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { onMount, onDestroy } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { toast } from 'svelte-sonner';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import FileDown from '@lucide/svelte/icons/file-down';
	import { getReport, deleteReport, type ReportDetail } from '$lib/api/client';
	import { formatApiError } from '$lib/api/core';
	import {
		getReportTables,
		reportExportUrl,
		summaryItems,
		type ReportDocument,
		type ReportExportFormat,
		type ReportTable,
	} from '$lib/api/reports';
	import { tableFrame } from '$lib/components/table';

	const exportFormats: { format: ReportExportFormat; label: string }[] = [
		{ format: 'md', label: 'Markdown' },
		{ format: 'tex', label: 'LaTeX' },
		{ format: 'json', label: 'JSON' }
	];

	let report = $state<ReportDetail | null>(null);
	let loading = $state(true);
	let error = $state('');
	let timer: ReturnType<typeof setInterval> | null = null;

	let doc = $state<ReportDocument | null>(null);
	let docError = $state('');
	let docKey = '';

	async function load() {
		try {
			report = await getReport(page.params.id ?? '');
			error = '';
		} catch (e) {
			error = (e as Error)?.message || 'Failed to load report';
		} finally {
			loading = false;
		}
	}

	async function loadTables(r: ReportDetail) {
		docError = '';
		try {
			doc = await getReportTables(r.report_id);
		} catch (e) {
			docError = formatApiError(e, 'Could not load the report tables.');
		}
	}

	// The tables of a completed report, once per completion.
	$effect(() => {
		if (report?.status !== 'completed') return;
		const key = `${report.report_id}:${report.completed_at}`;
		if (key === docKey) return;
		docKey = key;
		doc = null;
		void loadTables(report);
	});

	onMount(() => {
		load().then(() => {
			timer = setInterval(() => {
				if (report && (report.status === 'pending' || report.status === 'generating')) {
					load();
				}
			}, 2000);
		});
	});

	onDestroy(() => {
		if (timer) clearInterval(timer);
	});

	let confirmOpen = $state(false);
	let confirmBusy = $state(false);

	async function remove() {
		if (!report) return;
		confirmBusy = true;
		try {
			await deleteReport(report.report_id);
			confirmOpen = false;
			goto('/reports');
		} catch (e) {
			toast.error((e as Error)?.message || 'Failed to delete report');
		} finally {
			confirmBusy = false;
		}
	}

	// Experiment and study reports re-read their leader (or a tie) from the data.
	const summary = $derived(
		summaryItems(
			report?.summary,
			report?.report_type === 'experiment_report' || report?.report_type === 'study_report' ? report.report_data : null
		)
	);

	const NUMERIC = /^[+\-\u2212]?(\d{1,3}(,\d{3})+|\d+)?(\.\d+)?(e[+-]?\d+)?%?$/i;
	/** Columns whose every value is a number (or "-"): right-aligned, mono. */
	function numericColumns(table: ReportTable): Set<number> {
		const out = new Set<number>();
		table.columns.forEach((_, i) => {
			const values = table.rows.map((r) => r[i] ?? '').filter((v) => v !== '-' && v !== '');
			if (values.length > 0 && values.every((v) => NUMERIC.test(v))) out.add(i);
		});
		return out;
	}

	/** Text columns with long values (condition names, notes) get room instead of wrapping word by word. */
	function wideColumns(table: ReportTable): Set<number> {
		const out = new Set<number>();
		table.columns.forEach((_, i) => {
			if (table.rows.some((r) => (r[i] ?? '').length > 24)) out.add(i);
		});
		return out;
	}

	function anchor(i: number): string {
		return `report-table-${i + 1}`;
	}
</script>

{#snippet reportTable(table: ReportTable, i: number)}
	{@const nums = numericColumns(table)}
	{@const wide = wideColumns(table)}
	<Card.Root class="scroll-mt-20 gap-0 overflow-clip py-0" id={anchor(i)}>
		<Card.Header class="border-b border-border py-3">
			<Card.Title class="text-base">{table.title}</Card.Title>
		</Card.Header>
		<Card.Content class="p-0">
			{#if table.rows.length === 0}
				<div class="p-4"><EmptyState compact title="No data" description="Nothing in this report's scope for this table." /></div>
			{:else}
				<div class="table-frame" {@attach tableFrame}>
					<table>
						<thead>
							<tr>
								{#each table.columns as column, c (c)}
									<th class={nums.has(c) ? 'num' : 'whitespace-nowrap'}>{column}</th>
								{/each}
							</tr>
						</thead>
						<tbody>
							{#each table.rows as row, r (r)}
								<tr class="align-top">
									{#each table.columns as _, c (c)}
										{@const value = row[c] ?? ''}
										<td class={nums.has(c) ? 'num' : wide.has(c) ? 'min-w-56 max-w-[32rem] break-words' : 'whitespace-nowrap'}>
											{#if value === '-' || value === ''}<span class="text-muted-foreground" title="No value (not measured or not applicable)">—</span>{:else}{value}{/if}
										</td>
									{/each}
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
			{#if table.note}
				<p class="border-t border-border px-4 py-2 text-xs leading-relaxed text-muted-foreground">{table.note}</p>
			{/if}
		</Card.Content>
	</Card.Root>
{/snippet}

<div class="max-w-5xl space-y-4">
	<PageHeader
		title={report?.title ?? 'Report'}
		description={report?.description || undefined}
		back={{ href: '/reports', label: 'Reports' }}
	>
		{#snippet meta()}
			{#if report}<StatusBadge status={report.status} />{/if}
		{/snippet}
		{#snippet actions()}
			{#if report?.status === 'completed'}
				{#each exportFormats as f (f.format)}
					<Button variant="outline" size="sm" href={reportExportUrl(report.report_id, f.format)} download>
						<FileDown class="mr-1 h-4 w-4" />{f.label}
					</Button>
				{/each}
			{/if}
			<Button variant="outline" size="sm" onclick={load}>
				<RefreshCw class="mr-1 h-4 w-4" />Refresh
			</Button>
			<Button variant="outline" size="sm" disabled={!report} onclick={() => (confirmOpen = true)}>
				<Trash2 class="mr-1 h-4 w-4" />Delete
			</Button>
		{/snippet}
		{#if report && !loading && !error}
			<div class="header-stats">
				<span class="header-stat"><b class="font-sans capitalize">{report.report_type.replace(/_/g, ' ')}</b></span>
				<span>created {formatDate(report.created_at)}</span>
				<span>completed {formatDate(report.completed_at)}</span>
				<span>expires {formatDate(report.expires_at)}</span>
			</div>
		{/if}
	</PageHeader>

	{#if loading}
		<LoadingState label="loading report" />
	{:else if error}
		<ErrorAlert message={error} onretry={load} />
	{:else if report}
		{#if report.status === 'generating' || report.status === 'pending'}
			<LoadingState label="generating report… {report.progress_percent}%" />
		{:else if report.status === 'failed'}
			<ErrorAlert title="Generation failed" message={report.error_message || 'Generation failed.'} />
		{:else if report.status === 'completed'}
			{#if summary.length > 0}
				<Card.Root>
					<Card.Header><Card.Title class="text-base">Summary</Card.Title></Card.Header>
					<Card.Content class="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
						{#each summary as item (item.label)}
							<div class="min-w-0">
								<div class="text-xs text-muted-foreground">{item.label}</div>
								{#if item.href}
									<a href={item.href} class="font-mono font-medium hover:text-primary">{item.value}</a>
								{:else}
									<div class="break-words font-semibold">{item.value}</div>
								{/if}
							</div>
						{/each}
					</Card.Content>
				</Card.Root>
			{/if}

			{#if docError}
				<ErrorAlert message={docError} onretry={() => report && loadTables(report)} />
			{:else if !doc}
				<LoadingState label="loading report tables" />
			{:else if doc.tables.length === 0}
				<EmptyState title="Nothing to tabulate" description="This report has no data; the JSON export has the raw payload." />
			{:else}
				{#if doc.tables.length > 3}
					<nav aria-label="Report tables" class="rounded-md border border-border bg-card px-4 py-2.5 text-xs shadow-[var(--shadow-card)]">
						<p class="section-label mb-1.5">Tables</p>
						<ol class="flex flex-wrap gap-x-4 gap-y-1">
							{#each doc.tables as table, i (i)}
								<li><a href="#{anchor(i)}" class="text-muted-foreground hover:text-primary"><span class="font-mono">{i + 1}.</span> {table.title}</a></li>
							{/each}
						</ol>
					</nav>
				{/if}
				{#each doc.tables as table, i (i)}
					{@render reportTable(table, i)}
				{/each}
			{/if}
		{/if}
	{/if}
</div>

<ConfirmDialog
	bind:open={confirmOpen}
	title="Delete report?"
	description="This permanently removes the report. This cannot be undone."
	confirmLabel="Delete"
	destructive
	busy={confirmBusy}
	onConfirm={remove}
/>
