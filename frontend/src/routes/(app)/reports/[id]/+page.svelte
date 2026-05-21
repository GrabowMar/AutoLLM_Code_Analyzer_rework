<script lang="ts">
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

	const statusColors: Record<ReportStatus, string> = {
		completed: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30',
		generating: 'bg-amber-500/15 text-amber-500 border-amber-500/30',
		pending: 'bg-slate-500/15 text-slate-400 border-slate-500/30',
		failed: 'bg-red-500/15 text-red-400 border-red-500/30'
	};

	async function load() {
		try {
			report = await getReport(page.params.id);
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

	function formatDate(s: string | null): string {
		return s ? new Date(s).toLocaleString() : '—';
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
</script>

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
		<Card.Root><Card.Content class="p-6 text-red-400 text-sm">{error}</Card.Content></Card.Root>
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
				<Card.Content class="p-6 text-sm text-red-400">
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
								{#if isObject(v)}
									<pre class="text-xs bg-muted/40 rounded p-1 mt-1 overflow-auto">{JSON.stringify(v, null, 2)}</pre>
								{:else}
									<div class="font-semibold">{fmtValue(v)}</div>
								{/if}
							</div>
						{/each}
					</Card.Content>
				</Card.Root>
			{/if}

			{#each entries(report.report_data) as [section, value] (section)}
				<Card.Root>
					<Card.Header>
						<Card.Title class="text-base capitalize">{section.replace(/_/g, ' ')}</Card.Title>
					</Card.Header>
					<Card.Content class="text-sm">
						{#if isArray(value)}
							{#if value.length === 0}
								<p class="text-xs text-muted-foreground">No items.</p>
							{:else if isObject(value[0])}
								{@const first = value[0] as Record<string, unknown>}
								{@const cols = Object.keys(first)}
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
											{#each value as row, i (i)}
												{@const r = row as Record<string, unknown>}
												<tr class="border-b transition-colors hover:bg-muted/50 {i % 2 === 0 ? '' : 'bg-muted/15'}">
													{#each cols as c (c)}
														<td class="px-3 py-2 align-top text-xs {typeof r[c] === 'number' ? 'font-mono tabular-nums' : ''}">{fmtValue(r[c])}</td>
													{/each}
												</tr>
											{/each}
										</tbody>
									</table>
								</div>
							{:else}
								<ul class="list-disc pl-5 text-xs">
									{#each value as v, i (i)}<li>{fmtValue(v)}</li>{/each}
								</ul>
							{/if}
						{:else if isObject(value)}
							<dl class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
								{#each entries(value) as [k, v] (k)}
									<div>
										<dt class="text-muted-foreground capitalize">{k.replace(/_/g, ' ')}</dt>
										<dd class="font-medium">
											{#if isObject(v) || isArray(v)}
												<pre class="bg-muted/40 rounded p-1 overflow-auto">{JSON.stringify(v, null, 2)}</pre>
											{:else}
												{fmtValue(v)}
											{/if}
										</dd>
									</div>
								{/each}
							</dl>
						{:else}
							<div class="text-xs">{fmtValue(value)}</div>
						{/if}
					</Card.Content>
				</Card.Root>
			{/each}
		{/if}
	{/if}
</div>
