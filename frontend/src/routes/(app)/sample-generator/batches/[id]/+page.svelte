<script lang="ts">
	// One batch: header (status, counts, cost, pass rate, seed, presets), the
	// results matrix and every run. Polls the summary and the job list every
	// 5 s while runs are in flight (one request each, no per-job streams).
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import { toast } from 'svelte-sonner';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { Button } from '$lib/components/ui/button';
	import { Badge } from '$lib/components/ui/badge';
	import * as Card from '$lib/components/ui/card';
	import {
		cancelGenerationBatch,
		getGenerationBatch,
		getGenerationBatchJobs,
		type GenerationBatchSummary,
		type GenerationJobList,
	} from '$lib/api/generation';
	import { formatApiError } from '$lib/api/core';
	import { formatDateTime } from '$lib/utils/formatters';
	import { copyText } from '$lib/components/applications/utils';
	import {
		BatchJobsTable,
		BatchProgress,
		ResultsMatrix,
		activeCount,
		batchCounts,
		batchDesign,
		batchModeLabel,
		finishedCount,
		formatPassRate,
		formatUsd,
		isNotFoundError,
		presetRefLabel,
		shortId,
	} from '$lib/components/generator/batches';
	import Ban from '@lucide/svelte/icons/ban';
	import Copy from '@lucide/svelte/icons/copy';
	import Layers from '@lucide/svelte/icons/layers';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import CopyPlus from '@lucide/svelte/icons/copy-plus';
	import GitCompareArrows from '@lucide/svelte/icons/git-compare-arrows';
	import SearchX from '@lucide/svelte/icons/search-x';

	const POLL_MS = 5_000;

	const batchId = $derived(page.params.id ?? '');

	let batch = $state<GenerationBatchSummary | null>(null);
	let jobs = $state<GenerationJobList[]>([]);
	let loading = $state(true);
	let refreshing = $state(false);
	let notFound = $state(false);
	let error = $state('');
	let refreshError = $state('');
	let updatedAt = $state<Date | null>(null);
	let seq = 0;

	let cancelOpen = $state(false);
	let cancelling = $state(false);

	// Batch routes only match UUIDs; anything else is a plain (HTML) 404.
	const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

	async function load(id: string, silent = false) {
		const mine = ++seq;
		if (!UUID_RE.test(id)) {
			notFound = true;
			loading = false;
			return;
		}
		if (!silent) refreshing = true;
		try {
			const [summary, list] = await Promise.all([getGenerationBatch(id), getGenerationBatchJobs(id)]);
			if (mine !== seq) return;
			batch = summary;
			jobs = list;
			error = '';
			refreshError = '';
			notFound = false;
			updatedAt = new Date();
		} catch (e) {
			if (mine !== seq) return;
			if (isNotFoundError(e)) {
				notFound = true;
				batch = null;
				jobs = [];
			} else if (silent && batch) {
				// Keep the last good render; the next poll retries.
				refreshError = formatApiError(e, 'Could not refresh.');
			} else {
				error = formatApiError(e, 'Could not load this batch.');
			}
		} finally {
			if (mine === seq) {
				loading = false;
				refreshing = false;
			}
		}
	}

	// A new id (e.g. following a link between batches) starts from a clean slate.
	$effect(() => {
		const id = batchId;
		untrack(() => {
			batch = null;
			jobs = [];
			loading = true;
			notFound = false;
			error = '';
			refreshError = '';
			cancelOpen = false;
			load(id);
		});
	});

	const live = $derived(!!batch && !batch.is_terminal);

	$effect(() => {
		if (!live) return;
		const id = batchId;
		const timer = setInterval(() => {
			if (!document.hidden) load(id, true);
		}, POLL_MS);
		return () => clearInterval(timer);
	});

	const counts = $derived(batchCounts(batch));
	const inFlight = $derived(activeCount(counts));
	// "New batch like this": the whole grid from the batch's stored request;
	// batches that predate it fall back to their earliest run's settings.
	const fromBatch = $derived(
		!!batch?.request && (batch.mode === 'agent' || batch.mode === 'scaffolding') && Object.keys(batch.request).length > 0
	);
	const templateJob = $derived(
		jobs.length ? jobs.reduce((a, b) => (b.created_at < a.created_at ? b : a)) : null
	);

	const kpis = $derived.by(() => {
		if (!batch) return [];
		return [
			{ label: 'finished', value: `${finishedCount(counts)}/${batch.total_jobs}`, title: `${batch.total_jobs} runs in this batch` },
			{
				label: 'mean pass',
				value: batch.graded ? formatPassRate(batch.mean_pass_rate) : '—',
				title: batch.graded ? 'Mean pass rate (hidden conformance suite per app spec)' : 'Not graded (free-form task)',
			},
			{ label: 'cost', value: formatUsd(batch.cost_usd) },
			{ label: 'trials', value: batch.trials != null ? String(batch.trials) : '—' },
			{ label: 'seed', value: batch.base_seed != null ? String(batch.base_seed) : '—', title: 'Base seed' },
		];
	});

	async function confirmCancel() {
		if (!batch) return;
		cancelling = true;
		try {
			const res = await cancelGenerationBatch(batch.id);
			toast.success(res.cancelled === 1 ? 'Cancelled 1 run' : `Cancelled ${res.cancelled} runs`);
			cancelOpen = false;
			await load(batch.id, true);
		} catch (e) {
			toast.error(formatApiError(e, 'Could not cancel the batch.'));
		} finally {
			cancelling = false;
		}
	}
</script>

{#if loading}
	<div class="space-y-4">
		<PageHeader title="Batch" back={{ href: '/sample-generator/batches', label: 'Batches' }} />
		<LoadingState label="loading batch" />
	</div>
{:else if notFound}
	<div class="space-y-4">
		<PageHeader title="Batch not found" back={{ href: '/sample-generator/batches', label: 'Batches' }} />
		<EmptyState icon={SearchX} title="Batch not found" description="It may have been deleted, or the link is wrong.">
			{#snippet action()}
				<Button variant="outline" size="sm" href="/sample-generator/batches">All batches</Button>
			{/snippet}
		</EmptyState>
	</div>
{:else if !batch}
	<div class="space-y-4">
		<PageHeader title="Batch" back={{ href: '/sample-generator/batches', label: 'Batches' }} />
		<ErrorAlert message={error || 'Could not load this batch.'} onretry={() => load(batchId)} />
	</div>
{:else}
	<div class="space-y-4">
		<PageHeader
			title={batch.name || `Batch ${shortId(batch.id)}`}
			documentTitle="Batch {shortId(batch.id)}"
			back={{ href: '/sample-generator/batches', label: 'Batches' }}
		>
			{#snippet meta()}
				<StatusBadge status={batch!.status} />
				<Badge variant="outline">{batchModeLabel(batch!.mode)}</Badge>
			{/snippet}
			{#snippet actions()}
				<Button variant="outline" size="sm" onclick={() => load(batchId)} disabled={refreshing}>
					<RefreshCw class="h-3.5 w-3.5 {refreshing ? 'animate-spin motion-reduce:animate-none' : ''}" />Refresh
				</Button>
				{#if !batch!.is_terminal}
					<Button
						variant="outline"
						size="sm"
						class="border-destructive/30 text-danger-fg hover:bg-destructive/10"
						onclick={() => (cancelOpen = true)}
					>
						<Ban class="h-3.5 w-3.5" />Cancel
					</Button>
				{/if}
				{#if batch!.experiment}
					<Button size="sm" href="/experiments/{batch!.experiment.id}">
						<GitCompareArrows class="h-3.5 w-3.5" />Open experiment
					</Button>
				{:else if fromBatch}
					<Button
						size="sm"
						href="/sample-generator?from_batch={batch!.id}"
						title="Open the Sample Generator with this batch's tasks, models, harness presets, presets, overrides, stack and trials, and a new seed"
					>
						<CopyPlus class="h-3.5 w-3.5" />New batch like this
					</Button>
				{:else if templateJob}
					<Button
						size="sm"
						href="/sample-generator?from_job={templateJob.id}"
						title="Open the Sample Generator with this batch's task, model, stack and presets (from its first run), a new seed"
					>
						<CopyPlus class="h-3.5 w-3.5" />New batch like this
					</Button>
				{/if}
			{/snippet}
			<div class="space-y-1.5">
				<div class="header-stats gap-x-3">
					<button
						type="button"
						class="inline-flex cursor-pointer items-center gap-1 font-mono hover:text-foreground transition-colors"
						onclick={() => copyText(batch!.id, 'Copied batch ID')}
						title="Copy batch ID"
					>
						{shortId(batch.id)}<Copy class="h-3 w-3" />
					</button>
					<span title={formatDateTime(batch.created_at)}>Created {formatDateTime(batch.created_at)}</span>
					{#if batchDesign(batch)}<span>{batchDesign(batch)}</span>{/if}
					<span class="header-stat min-w-0" title="Stack">stack <b class="min-w-0 truncate text-xs">{batch.stack_slug ?? '—'}</b></span>
					{#if batch.mode === 'agent'}
						<span class="inline-flex flex-wrap items-center gap-1">
							{batch.harness_presets?.length ? 'presets' : 'harnesses'}
							{#each batch.harness_presets ?? [] as preset (preset.id)}
								<Badge variant="outline" class="font-mono">{presetRefLabel(preset)}</Badge>
							{:else}
								{#each batch.harnesses ?? [] as harness (harness)}
									<Badge variant="outline" class="font-mono">{harness}</Badge>
								{:else}
									<span>—</span>
								{/each}
							{/each}
						</span>
					{/if}
					{#if batch.experiment}
						<span title="Columns are the experiment's conditions; launch further runs from the experiment page.">
							part of <a href="/experiments/{batch.experiment.id}" class="font-medium text-success-fg hover:underline">{batch.experiment.name}</a>
						</span>
					{/if}
				</div>
				<div class="header-stats">
					{#each kpis as kpi (kpi.label)}
						<span class="header-stat" title={kpi.title}><b>{kpi.value}</b>{kpi.label}</span>
					{/each}
					{#if live && inFlight > 0}
						<span
							class="header-stat"
							title="Pending runs wait for the executor (a free sandbox slot or endpoint capacity); finished runs wait for an evaluation slot. Runs execute trial by trial in a seeded order."
						><b>{inFlight}</b>in flight</span>
					{/if}
					{#if updatedAt}
						<span class="ml-auto text-[11px]" aria-live="polite">
							{#if refreshError}
								<span class="text-warning-fg">Couldn't refresh: {refreshError}</span>
							{:else}
								Updated {updatedAt.toLocaleTimeString()}{live ? ' · refreshing every 5 s' : ''}
							{/if}
						</span>
					{/if}
				</div>
				<BatchProgress {counts} total={batch.total_jobs} legend />
			</div>
		</PageHeader>

		{#if error}
			<ErrorAlert message={error} onretry={() => load(batchId)} />
		{/if}

		<Card.Root>
			<Card.Header class="pb-2">
				<Card.Title class="text-sm">Results</Card.Title>
				<Card.Description>
					Rows are tasks, columns are conditions ({batch.mode === 'agent'
						? 'model × harness preset'
						: batch.mode === 'scaffolding'
							? 'model × profile'
							: 'model'}{batch.experiment ? ', or the experiment condition' : ''}). Select a cell for its trials.
				</Card.Description>
			</Card.Header>
			<Card.Content>
				{#if jobs.length}
					<ResultsMatrix {jobs} mode={batch.mode} graded={batch.graded} />
				{:else}
					<EmptyState compact icon={Layers} title="No runs in this batch" description="Runs appear here as soon as they are created." />
				{/if}
			</Card.Content>
		</Card.Root>

		{#if jobs.length}
			<Card.Root>
				<Card.Header class="pb-2">
					<Card.Title class="text-sm">Runs</Card.Title>
					<Card.Description>Every run in trial order; open one for its files, evaluation and run record.</Card.Description>
				</Card.Header>
				<Card.Content>
					<BatchJobsTable {jobs} mode={batch.mode} graded={batch.graded} />
				</Card.Content>
			</Card.Root>
		{/if}
	</div>

	<ConfirmDialog
		bind:open={cancelOpen}
		title="Cancel this batch?"
		description="Stops the {inFlight} {inFlight === 1 ? 'run' : 'runs'} that are pending, running or awaiting evaluation. Finished runs and their results are kept."
		confirmLabel="Cancel runs"
		cancelLabel="Keep running"
		destructive
		busy={cancelling}
		onConfirm={confirmCancel}
	/>
{/if}
