<script lang="ts">
	import { rowLink, tableFrame } from '$lib/components/table';
	// Every generation batch (ad-hoc and experiment launches), newest first.
	// Filters and page live in the URL; the list refreshes itself every 10 s
	// while any visible batch still has runs in flight.
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { Button } from '$lib/components/ui/button';
	import { Badge } from '$lib/components/ui/badge';
	import * as Card from '$lib/components/ui/card';
	import { Segmented } from '$lib/components/ui/segmented';
	import {
		listGenerationBatches,
		type GenerationBatchSummary,
		type PaginatedGenerationBatches,
	} from '$lib/api/generation';
	import { formatApiError } from '$lib/api/core';
	import { formatDateTime } from '$lib/utils/formatters';
	import { timeAgo } from '$lib/components/applications/utils';
	import {
		BatchProgress,
		batchCounts,
		batchDesign,
		batchHref,
		batchModeLabel,
		finishedCount,
		formatPassRate,
		formatUsd,
		shortId,
	} from '$lib/components/generator/batches';
	import Layers from '@lucide/svelte/icons/layers';
	import Plus from '@lucide/svelte/icons/plus';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import GitCompareArrows from '@lucide/svelte/icons/git-compare-arrows';
	import WandSparkles from '@lucide/svelte/icons/wand-sparkles';

	const PER_PAGE = 20;
	const POLL_MS = 10_000;

	const modeFilter = $derived(page.url.searchParams.get('mode') ?? '');
	const sourceFilter = $derived(page.url.searchParams.get('source') ?? '');
	const pageNum = $derived(Math.max(1, Math.floor(Number(page.url.searchParams.get('page'))) || 1));
	const filtered = $derived(!!modeFilter || !!sourceFilter);

	let data = $state<PaginatedGenerationBatches | null>(null);
	let loading = $state(true);
	let busy = $state(false);
	let error = $state('');
	let refreshError = $state('');
	let updatedAt = $state<Date | null>(null);
	let seq = 0;

	const items = $derived(data?.items ?? []);
	const anyActive = $derived(items.some((b) => !b.is_terminal));

	async function load(silent = false) {
		const mine = ++seq;
		if (!silent) busy = true;
		try {
			const res = await listGenerationBatches({
				page: pageNum,
				per_page: PER_PAGE,
				mode: modeFilter || undefined,
				experiment: sourceFilter === 'experiment' ? true : sourceFilter === 'adhoc' ? false : undefined,
			});
			if (mine !== seq) return;
			data = res;
			error = '';
			refreshError = '';
			updatedAt = new Date();
		} catch (e) {
			if (mine !== seq) return;
			const message = formatApiError(e, 'Could not load batches.');
			// A failed background refresh keeps the last good list on screen.
			if (silent && data) refreshError = message;
			else error = message;
		} finally {
			if (mine === seq) {
				loading = false;
				busy = false;
			}
		}
	}

	// URL (filters, page) → data.
	$effect(() => {
		void modeFilter;
		void sourceFilter;
		void pageNum;
		untrack(() => load());
	});

	$effect(() => {
		if (!anyActive) return;
		const timer = setInterval(() => {
			if (!document.hidden) load(true);
		}, POLL_MS);
		return () => clearInterval(timer);
	});

	function setParams(updates: Record<string, string | null>) {
		const url = new URL(page.url);
		for (const [key, value] of Object.entries(updates)) {
			if (value) url.searchParams.set(key, value);
			else url.searchParams.delete(key);
		}
		goto(`${url.pathname}${url.search}`, { replaceState: true, keepFocus: true, noScroll: true });
	}

	const modeItems = [
		{ value: 'all', label: 'All modes' },
		{ value: 'agent', label: 'Agent' },
		{ value: 'scaffolding', label: 'Template scaffolding' },
	];
	const sourceItems = [
		{ value: 'all', label: 'All' },
		{ value: 'adhoc', label: 'Ad-hoc', title: 'Launched from the Sample Generator' },
		{ value: 'experiment', label: 'Experiments', title: 'Launched from an experiment' },
	];

	const resultsText = $derived(
		data && data.total > 0
			? `Showing ${(data.page - 1) * data.per_page + 1}–${Math.min(data.page * data.per_page, data.total)} of ${data.total} batches`
			: ''
	);

</script>

{#snippet passRate(batch: GenerationBatchSummary)}
	{#if batch.graded}
		<span class="font-mono tabular-nums" title="Mean pass rate over graded trials">{formatPassRate(batch.mean_pass_rate)}</span>
	{:else}
		<span class="text-xs text-muted-foreground" title="Free-form task without an app spec">not graded</span>
	{/if}
{/snippet}

{#snippet batchTitle(batch: GenerationBatchSummary)}
	<a href={batchHref(batch.id)} class="block truncate font-medium hover:text-primary transition-colors" title={batch.name}>
		{batch.name || `Batch ${shortId(batch.id)}`}
	</a>
	<span class="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
		<Badge variant="outline">{batchModeLabel(batch.mode)}</Badge>
		{#if batch.experiment}
			<a
				href="/experiments/{batch.experiment.id}"
				class="inline-flex max-w-48 items-center gap-1 text-info-fg hover:underline"
				title="Part of experiment {batch.experiment.name}"
			>
				<GitCompareArrows class="h-3 w-3 shrink-0" /><span class="truncate">{batch.experiment.name}</span>
			</a>
		{/if}
		<span class="font-mono">{shortId(batch.id)}</span>
	</span>
{/snippet}

{#snippet progress(batch: GenerationBatchSummary)}
	{@const counts = batchCounts(batch)}
	<div class="flex min-w-36 items-center gap-2">
		<BatchProgress {counts} total={batch.total_jobs} class="flex-1" />
		<span class="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">{finishedCount(counts)}/{batch.total_jobs}</span>
	</div>
{/snippet}

<div class="space-y-6">
	<PageHeader
		title="Batches"
		description="Every generation batch with its progress, pass rate and cost. Open one for the results matrix."
		back={{ href: '/sample-generator', label: 'Sample Generator' }}
	>
		{#snippet actions()}
			<Button variant="outline" size="sm" onclick={() => load()} disabled={busy}>
				<RefreshCw class="h-3.5 w-3.5 {busy ? 'animate-spin motion-reduce:animate-none' : ''}" />Refresh
			</Button>
			<Button size="sm" href="/sample-generator"><Plus class="h-3.5 w-3.5" />New batch</Button>
		{/snippet}
	</PageHeader>

	<div class="flex flex-wrap items-center gap-2">
		<Segmented
			aria-label="Mode"
			size="sm"
			items={modeItems}
			value={modeFilter || 'all'}
			onValueChange={(v) => setParams({ mode: v === 'all' ? null : v, page: null })}
		/>
		<Segmented
			aria-label="Source"
			size="sm"
			items={sourceItems}
			value={sourceFilter || 'all'}
			onValueChange={(v) => setParams({ source: v === 'all' ? null : v, page: null })}
		/>
		{#if updatedAt}
			<span class="text-[11px] text-muted-foreground sm:ml-auto" aria-live="polite">
				{#if refreshError}
					<span class="text-warning-fg">Couldn't refresh: {refreshError}</span>
				{:else}
					Updated {updatedAt.toLocaleTimeString()}{anyActive ? ' · refreshing every 10 s while runs are in flight' : ''}
				{/if}
			</span>
		{/if}
	</div>

	{#if error}
		<ErrorAlert message={error} onretry={() => load()} />
	{/if}

	{#if loading}
		<LoadingState label="loading batches" />
	{:else if data && items.length === 0}
		{#if filtered}
			<EmptyState icon={Layers} title="No batches match these filters" description="Try another mode or source.">
				{#snippet action()}
					<Button variant="outline" size="sm" onclick={() => setParams({ mode: null, source: null, page: null })}>Clear filters</Button>
				{/snippet}
			</EmptyState>
		{:else}
			<EmptyState
				icon={Layers}
				title="No batches yet"
				description="Start a batch in the Sample Generator: pick tasks, models and harness presets, and every combination runs here."
			>
				{#snippet action()}
					<Button size="sm" href="/sample-generator"><WandSparkles class="h-3.5 w-3.5" />Open Sample Generator</Button>
				{/snippet}
			</EmptyState>
		{/if}
	{:else if data}
		<div class="space-y-3 transition-opacity duration-200 {busy ? 'opacity-60' : ''}" aria-busy={busy}>
			<!-- Table (md and up) -->
			<Card.Root class="hidden gap-0 overflow-clip py-0 md:block">
				<Card.Content class="p-0">
					<div class="table-frame" {@attach tableFrame}>
						<table>
							<thead>
								<tr>
									<th scope="col">Batch</th>
									<th scope="col">Status</th>
									<th scope="col">Progress</th>
									<th scope="col">Design</th>
									<th scope="col" class="num">Pass rate</th>
									<th scope="col" class="num">Cost</th>
									<th scope="col" class="num">Created</th>
								</tr>
							</thead>
							<tbody>
								{#each items as batch (batch.id)}
									<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (the title link is the keyboard path) -->
									<tr class="row-link" onclick={rowLink(batchHref(batch.id))}>
										<td class="max-w-72">{@render batchTitle(batch)}</td>
										<td><StatusBadge status={batch.status} /></td>
										<td>{@render progress(batch)}</td>
										<td class="max-w-64">
											<span class="block truncate text-xs text-muted-foreground" title={batchDesign(batch)}>{batchDesign(batch) || '—'}</span>
											{#if batch.stack_slug}<span class="font-mono text-[11px] text-muted-foreground">{batch.stack_slug}</span>{/if}
										</td>
										<td class="text-right text-sm">{@render passRate(batch)}</td>
										<td class="text-right font-mono text-sm tabular-nums">{formatUsd(batch.cost_usd)}</td>
										<td class="whitespace-nowrap text-right text-xs text-muted-foreground" title={formatDateTime(batch.created_at)}>
											{timeAgo(batch.created_at)}
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</Card.Content>
			</Card.Root>

			<!-- Cards (below md) -->
			<ul class="space-y-2 md:hidden">
				{#each items as batch (batch.id)}
					<li>
						<Card.Root>
							<Card.Content class="space-y-2.5 p-3">
								<div class="flex items-start justify-between gap-2">
									<div class="min-w-0">{@render batchTitle(batch)}</div>
									<StatusBadge status={batch.status} />
								</div>
								{@render progress(batch)}
								{#if batchDesign(batch)}
									<p class="text-xs text-muted-foreground">{batchDesign(batch)}</p>
								{/if}
								<dl class="grid grid-cols-3 gap-2 text-xs">
									<div>
										<dt class="text-[11px] text-muted-foreground">Pass rate</dt>
										<dd>{@render passRate(batch)}</dd>
									</div>
									<div>
										<dt class="text-[11px] text-muted-foreground">Cost</dt>
										<dd class="font-mono tabular-nums">{formatUsd(batch.cost_usd)}</dd>
									</div>
									<div>
										<dt class="text-[11px] text-muted-foreground">Created</dt>
										<dd title={formatDateTime(batch.created_at)}>{timeAgo(batch.created_at)}</dd>
									</div>
								</dl>
							</Card.Content>
						</Card.Root>
					</li>
				{/each}
			</ul>

			<PaginationBar
				{resultsText}
				page={data.page}
				pages={data.pages}
				onGoToPage={(p) => setParams({ page: p > 1 ? String(p) : null })}
				class="rounded-md border border-border"
			/>
		</div>
	{/if}
</div>
