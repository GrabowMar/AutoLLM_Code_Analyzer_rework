<script lang="ts">
	import { formatDateTime as formatDate } from '$lib/utils/formatters';
	import { reportStatusColors as statusColors } from '$lib/constants/colors';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { onMount, onDestroy } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Badge } from '$lib/components/ui/badge';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import { getReport, deleteReport, type ReportDetail, type ReportStatus } from '$lib/api/client';

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

	async function remove() {
		if (!report) return;
		if (!confirm('Delete this report?')) return;
		await deleteReport(report.report_id);
		goto('/reports');
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
	<div class="overflow-auto">
		<table class="min-w-full text-xs">
			<thead>
				<tr class="border-b bg-muted/40 sticky top-0 z-10">
					{#each cols as c (c)}
						<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap capitalize">{c.replace(/_/g, ' ')}</th>
					{/each}
				</tr>
			</thead>
			<tbody>
				{#each rows as row, i (i)}
					<tr class="border-b transition-colors hover:bg-muted/50 {i % 2 === 0 ? '' : 'bg-muted/15'}">
						{#each cols as c (c)}
							<td class="px-3 py-2 align-top text-xs {typeof row[c] === 'number' ? 'font-mono tabular-nums' : ''}">{fmtCompact(row[c])}</td>
						{/each}
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
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
					<div class="overflow-auto">
						<table class="text-xs">
							<thead>
								<tr class="border-b bg-muted/40">
									<th class="px-3 py-2 text-left font-medium text-muted-foreground">Metric</th>
									<th class="px-3 py-2 text-right font-medium text-muted-foreground">Avg</th>
									<th class="px-3 py-2 text-right font-medium text-muted-foreground">Min</th>
									<th class="px-3 py-2 text-right font-medium text-muted-foreground">Max</th>
								</tr>
							</thead>
							<tbody>
								{#each numeric as [k, stat] (k)}
									{@const s = isObject(stat) ? stat : {}}
									<tr class="border-b">
										<td class="px-3 py-1.5 capitalize">{k.replace(/_/g, ' ')}</td>
										<td class="px-3 py-1.5 text-right font-mono tabular-nums">{fmtValue(s['avg'])}</td>
										<td class="px-3 py-1.5 text-right font-mono tabular-nums text-muted-foreground">{fmtValue(s['min'])}</td>
										<td class="px-3 py-1.5 text-right font-mono tabular-nums text-muted-foreground">{fmtValue(s['max'])}</td>
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

<svelte:head>
	<title>{report?.title ?? 'Report'} — LLM Eval Lab</title>
</svelte:head>

<div class="max-w-5xl space-y-6">
	<div class="flex items-center justify-between">
		<Button variant="ghost" size="sm" onclick={() => goto('/reports')}>
			<ArrowLeft class="mr-1 h-4 w-4" />Back to reports
		</Button>
		<div class="flex gap-2">
			<Button variant="outline" size="sm" onclick={load}>
				<RefreshCw class="mr-1 h-4 w-4" />Refresh
			</Button>
			<Button variant="outline" size="sm" onclick={remove}>
				<Trash2 class="mr-1 h-4 w-4" />Delete
			</Button>
		</div>
	</div>

	{#if loading}
		<Card.Root>
			<Card.Content class="flex items-center justify-center py-20">
				<LoaderCircle class="h-8 w-8 animate-spin text-muted-foreground" />
			</Card.Content>
		</Card.Root>
	{:else if error}
		<Card.Root><Card.Content class="p-6 text-destructive text-sm">{error}</Card.Content></Card.Root>
	{:else if report}
		<Card.Root>
			<Card.Header>
				<div class="flex items-start justify-between gap-4 flex-wrap">
					<div>
						<Card.Title class="text-xl">{report.title}</Card.Title>
						{#if report.description}
							<Card.Description class="mt-1">{report.description}</Card.Description>
						{/if}
					</div>
					<Badge class={statusColors[report.status]}>{report.status}</Badge>
				</div>
			</Card.Header>
			<Card.Content class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
				<div>
					<div class="text-muted-foreground">Type</div>
					<div class="font-medium">{report.report_type}</div>
				</div>
				<div>
					<div class="text-muted-foreground">Created</div>
					<div class="font-medium">{formatDate(report.created_at)}</div>
				</div>
				<div>
					<div class="text-muted-foreground">Completed</div>
					<div class="font-medium">{formatDate(report.completed_at)}</div>
				</div>
				<div>
					<div class="text-muted-foreground">Expires</div>
					<div class="font-medium">{formatDate(report.expires_at)}</div>
				</div>
			</Card.Content>
		</Card.Root>

		{#if report.status === 'generating' || report.status === 'pending'}
			<Card.Root>
				<Card.Content class="p-6 flex items-center gap-3 text-sm text-muted-foreground">
					<LoaderCircle class="h-5 w-5 animate-spin" />
					Generating report… ({report.progress_percent}%)
				</Card.Content>
			</Card.Root>
		{:else if report.status === 'failed'}
			<Card.Root>
				<Card.Content class="p-6 text-sm text-destructive">
					{report.error_message || 'Generation failed.'}
				</Card.Content>
			</Card.Root>
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
									<h4 class="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">{k.replace(/_/g, ' ')}</h4>
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
