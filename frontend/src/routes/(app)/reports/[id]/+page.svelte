<script lang="ts">
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
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import FileDown from '@lucide/svelte/icons/file-down';
	import { getReport, deleteReport, type ReportDetail } from '$lib/api/client';
	import { reportExportUrl, type ReportExportFormat } from '$lib/api/reports';

	const exportFormats: { format: ReportExportFormat; label: string }[] = [
		{ format: 'md', label: 'Markdown' },
		{ format: 'tex', label: 'LaTeX' },
		{ format: 'json', label: 'JSON' }
	];
	import { tableFrame } from '$lib/components/table';

	let report = $state<ReportDetail | null>(null);
	let loading = $state(true);
	let error = $state('');
	let timer: ReturnType<typeof setInterval> | null = null;

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

	function isObject(v: unknown): v is Record<string, unknown> {
		return typeof v === 'object' && v !== null && !Array.isArray(v);
	}

	function isArray(v: unknown): v is unknown[] {
		return Array.isArray(v);
	}

	function entries(v: unknown): [string, unknown][] {
		return isObject(v) ? Object.entries(v) : [];
	}

	function fmtValue(v: unknown): string {
		if (v === null || v === undefined) return '—';
		if (typeof v === 'number') {
			return Number.isInteger(v) ? v.toString() : v.toFixed(2);
		}
		if (typeof v === 'boolean') return v ? 'yes' : 'no';
		return String(v);
	}

	/** One-line rendering for flat objects/arrays ("low: 2, high: 1"), so table
	 *  cells and grid values never fall back to "[object Object]". */
	function fmtCompact(v: unknown): string {
		if (isArray(v)) {
			return v.length ? v.map(fmtCompact).join(', ') : '—';
		}
		if (isObject(v)) {
			const es = Object.entries(v);
			if (!es.length) return '—';
			return es
				.map(([k, val]) =>
					`${k.replace(/_/g, ' ')}: ${isObject(val) || isArray(val) ? JSON.stringify(val) : fmtValue(val)}`
				)
				.join(', ');
		}
		return fmtValue(v);
	}

	function isObjectRows(v: unknown): v is Record<string, unknown>[] {
		return isArray(v) && v.length > 0 && isObject(v[0]);
	}

	/** Entries too structured for the key/value grid: rendered full-width below it. */
	function isWide(k: string, v: unknown): boolean {
		if (k === 'metrics_by_tool') return isObject(v) && Object.keys(v).length > 0;
		if (isObjectRows(v)) return true;
		// An object earns full-width treatment only when it holds a table-shaped
		// list; anything flatter reads fine as a compact "k: v, …" line.
		return isObject(v) && Object.values(v).some((x) => isObjectRows(x));
	}
</script>

{#snippet objectTable(rows: Record<string, unknown>[])}
	{@const cols = Object.keys(rows[0])}
	{@const numCols = new Set(cols.filter((c) => rows.some((r) => typeof r[c] === 'number') && rows.every((r) => r[c] == null || typeof r[c] === 'number')))}
	<div class="table-frame rounded-md border border-border" {@attach tableFrame}>
		<table>
			<thead>
				<tr>
					{#each cols as c (c)}
						<th class="whitespace-nowrap" class:num={numCols.has(c)}>{c.replace(/_/g, ' ')}</th>
					{/each}
				</tr>
			</thead>
			<tbody>
				{#each rows as row, i (i)}
					<tr class="align-top">
						{#each cols as c (c)}
							<td class:num={numCols.has(c)}>
								{#if row[c] == null}<span class="text-muted-foreground">—</span>{:else}{fmtCompact(row[c])}{/if}
							</td>
						{/each}
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/snippet}

{#snippet statValue(v: unknown)}
	{#if v == null}<span class="text-muted-foreground">—</span>{:else}{fmtValue(v)}{/if}
{/snippet}

{#snippet metricsByTool(metrics: Record<string, unknown>)}
	<div class="space-y-5">
		{#each entries(metrics) as [tool, m] (tool)}
			{@const numeric = entries(isObject(m) ? m['numeric'] : undefined)}
			{@const dists = entries(isObject(m) ? m['distributions'] : undefined)}
			{@const nResults = isObject(m) ? m['results_with_metrics'] : undefined}
			<div>
				<div class="mb-1.5 flex items-baseline gap-2">
					<span class="font-medium">{tool}</span>
					{#if nResults !== undefined}
						<span class="text-xs text-muted-foreground">{fmtValue(nResults)} result{nResults === 1 ? '' : 's'} with metrics</span>
					{/if}
				</div>
				{#if numeric.length > 0}
					<div class="overflow-x-auto rounded-md border border-border">
						<table>
							<thead>
								<tr>
									<th>Metric</th>
									<th class="num">Avg</th>
									<th class="num">Min</th>
									<th class="num">Max</th>
								</tr>
							</thead>
							<tbody>
								{#each numeric as [k, stat] (k)}
									{@const s = isObject(stat) ? stat : {}}
									<tr>
										<td class="capitalize">{k.replace(/_/g, ' ')}</td>
										<td class="num">{@render statValue(s['avg'])}</td>
										<td class="num text-muted-foreground">{@render statValue(s['min'])}</td>
										<td class="num text-muted-foreground">{@render statValue(s['max'])}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
				{#each dists as [k, buckets] (k)}
					<div class="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
						<span class="text-muted-foreground capitalize">{k.replace(/_/g, ' ')}:</span>
						{#each entries(buckets) as [bucket, count] (bucket)}
							<span class="rounded border bg-muted/40 px-1.5 py-0.5 font-mono">{bucket}: {fmtValue(count)}</span>
						{/each}
					</div>
				{/each}
			</div>
		{/each}
	</div>
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
			{#if report.summary && Object.keys(report.summary).length > 0}
				<Card.Root>
					<Card.Header><Card.Title class="text-base">Summary</Card.Title></Card.Header>
					<Card.Content class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
						{#each entries(report.summary) as [k, v] (k)}
							<div>
								<div class="text-xs text-muted-foreground capitalize">{k.replace(/_/g, ' ')}</div>
								{#if isObject(v) || isArray(v)}
									<div class="mt-0.5 text-xs">{fmtCompact(v)}</div>
								{:else}
									<div class="font-semibold">{fmtValue(v)}</div>
								{/if}
							</div>
						{/each}
					</Card.Content>
				</Card.Root>
			{/if}

			{@const scalarSections = entries(report.report_data).filter(([, v]) => !isObject(v) && !isArray(v))}
			{#if scalarSections.length > 0}
				<Card.Root>
					<Card.Header><Card.Title class="text-base">Details</Card.Title></Card.Header>
					<Card.Content class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
						{#each scalarSections as [k, v] (k)}
							<div>
								<div class="text-xs text-muted-foreground capitalize">{k.replace(/_/g, ' ')}</div>
								<div class="font-semibold">{fmtValue(v)}</div>
							</div>
						{/each}
					</Card.Content>
				</Card.Root>
			{/if}

			{#each entries(report.report_data).filter(([, v]) => isObject(v) || isArray(v)) as [section, value] (section)}
				<Card.Root>
					<Card.Header>
						<Card.Title class="text-base capitalize">{section.replace(/_/g, ' ')}</Card.Title>
					</Card.Header>
					<Card.Content class="text-sm">
						{#if section === 'metrics_by_tool' && isObject(value) && Object.keys(value).length > 0}
							{@render metricsByTool(value)}
						{:else if isArray(value)}
							{#if value.length === 0}
								<p class="text-xs text-muted-foreground">No items.</p>
							{:else if isObjectRows(value)}
								{@render objectTable(value)}
							{:else}
								<ul class="list-disc pl-5 text-xs">
									{#each value as v, i (i)}<li>{fmtValue(v)}</li>{/each}
								</ul>
							{/if}
						{:else if isObject(value)}
							{@const flat = entries(value).filter(([k, v]) => !isWide(k, v))}
							{@const wide = entries(value).filter(([k, v]) => isWide(k, v))}
							{#if flat.length > 0}
								<dl class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
									{#each flat as [k, v] (k)}
										<div>
											<dt class="text-muted-foreground capitalize">{k.replace(/_/g, ' ')}</dt>
											<dd class="font-medium">
												{#if isObject(v) || isArray(v)}
													<span class="font-normal">{fmtCompact(v)}</span>
												{:else}
													{fmtValue(v)}
												{/if}
											</dd>
										</div>
									{/each}
								</dl>
							{/if}
							{#each wide as [k, v] (k)}
								<div class="{flat.length > 0 ? 'mt-4 border-t pt-4' : ''}">
									<h4 class="section-label mb-2">{k.replace(/_/g, ' ')}</h4>
									{#if k === 'metrics_by_tool' && isObject(v)}
										{@render metricsByTool(v)}
									{:else if isObjectRows(v)}
										{@render objectTable(v)}
									{:else}
										<pre class="text-xs bg-muted/40 rounded p-2 overflow-auto">{JSON.stringify(v, null, 2)}</pre>
									{/if}
								</div>
							{/each}
						{:else}
							<div class="text-xs">{fmtValue(value)}</div>
						{/if}
					</Card.Content>
				</Card.Root>
			{/each}
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
