<script lang="ts">
	/**
	 * RunsTab — every run of the experiment (`GET /generation/jobs/?experiment_id`),
	 * filtered by condition, endpoint, app and status, current attempts only
	 * (default) or retried runs only; paged on the server. "Retry failed" tops
	 * up failed and cancelled cells; the launch history lists every batch.
	 *
	 * Props: `experiment`, `appName(id)`, `onlaunched()` (after a top-up).
	 */
	import { untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import { Switch } from '$lib/components/ui/switch';
	import * as Select from '$lib/components/ui/select';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import BatchJobsTable from '$lib/components/generator/batches/BatchJobsTable.svelte';
	import BatchProgress from '$lib/components/generator/batches/BatchProgress.svelte';
	import { batchCounts, finishedCount } from '$lib/components/generator/batches/batch-utils';
	import { getGenerationJobs, type GenerationBatchSummary, type PaginatedJobs } from '$lib/api/generation';
	import {
		experimentErrorBody,
		launchExperiment,
		listBatches,
		type ExperimentDetail,
	} from '$lib/api/experiments';
	import { formatApiError } from '$lib/api/core';
	import { formatDateTime } from '$lib/utils/formatters';
	import ListFilter from '@lucide/svelte/icons/list-filter';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import { isTerminal } from '../format';

	interface Props {
		experiment: ExperimentDetail;
		appName: (id: number | null) => string;
		onlaunched?: () => void;
	}

	let { experiment, appName, onlaunched }: Props = $props();

	const PER_PAGE = 100;
	const ALL = 'all';

	let conditionFilter = $state(ALL);
	let endpointFilter = $state(ALL);
	let appFilter = $state(ALL);
	let statusFilter = $state(ALL);
	let currentOnly = $state(true);
	let retriedOnly = $state(false);
	let pageNum = $state(1);

	let data = $state.raw<PaginatedJobs | null>(null);
	let loading = $state(true);
	let error = $state('');
	let seq = 0;

	const STATUS_ITEMS = [
		{ value: ALL, label: 'Any status' },
		{ value: 'pending', label: 'Queued' },
		{ value: 'running', label: 'Running' },
		{ value: 'eval', label: 'Awaiting / in evaluation' },
		{ value: 'completed', label: 'Completed' },
		{ value: 'failed', label: 'Failed' },
		{ value: 'cancelled', label: 'Cancelled' },
	];

	async function load() {
		const mine = ++seq;
		loading = true;
		try {
			const res = await getGenerationJobs({
				experiment_id: experiment.id,
				page: pageNum,
				per_page: PER_PAGE,
				sort_by: 'sequence',
				condition_id: conditionFilter === ALL ? undefined : Number(conditionFilter),
				endpoint_id: endpointFilter === ALL ? undefined : Number(endpointFilter),
				app_requirement_id: appFilter === ALL ? undefined : Number(appFilter),
				status: statusFilter === ALL || statusFilter === 'eval' ? undefined : statusFilter,
				phase: statusFilter === 'eval' ? 'eval_queued,evaluating' : undefined,
				current: currentOnly,
				attempt_gt: retriedOnly ? 1 : undefined,
			});
			if (mine !== seq) return;
			data = res;
			error = '';
		} catch (e) {
			if (mine === seq) error = formatApiError(e, 'Could not load the runs.');
		} finally {
			if (mine === seq) loading = false;
		}
	}

	$effect(() => {
		void [conditionFilter, endpointFilter, appFilter, statusFilter, currentOnly, retriedOnly, pageNum];
		untrack(() => void load());
	});

	function setFilter(apply: () => void) {
		apply();
		pageNum = 1;
	}

	// ── launch history ──────────────────────────────────────────────
	let batches = $state<GenerationBatchSummary[] | null>(null);
	async function loadBatches() {
		try {
			batches = await listBatches(experiment.id);
		} catch {
			batches = [];
		}
	}
	$effect(() => {
		void experiment.batches.length;
		untrack(() => void loadBatches());
	});

	// ── retry failed ────────────────────────────────────────────────
	let retryOpen = $state(false);
	let retrying = $state(false);
	const failedCells = $derived(experiment.progress.failed + experiment.progress.cancelled);

	async function retryFailed() {
		retrying = true;
		try {
			const r = await launchExperiment(experiment.id, { mode: 'top_up' });
			retryOpen = false;
			toast.success(`Queued ${r.jobs_created} run${r.jobs_created === 1 ? '' : 's'}`);
			onlaunched?.();
			void load();
			void loadBatches();
		} catch (e) {
			retryOpen = false;
			const body = experimentErrorBody(e);
			toast.error(body ? `${body.detail}${body.errors.length ? `: ${body.errors.map((x) => x.detail).join('; ')}` : ''}` : formatApiError(e, 'Could not retry.'));
		} finally {
			retrying = false;
		}
	}

	const conditionItems = $derived([
		{ value: ALL, label: 'All conditions' },
		...experiment.conditions.map((c) => ({ value: String(c.id), label: c.label })),
	]);
	const endpointItems = $derived([
		{ value: ALL, label: 'All endpoints' },
		...experiment.endpoints.map((e) => ({ value: String(e.id), label: e.name || e.slug })),
	]);
	const appItems = $derived([
		{ value: ALL, label: 'All apps' },
		...experiment.app_requirement_ids.map((id) => ({ value: String(id), label: appName(id) })),
	]);
	const KIND_LABELS: Record<string, string> = { launch: 'launch', top_up: 'top-up', retry: 'retry', schedule: 'schedule', adhoc: 'ad hoc' };
</script>

<div class="space-y-6">
	<Card.Root>
		<Card.Header class="flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
			<div class="space-y-1">
				<Card.Title>Runs</Card.Title>
				<Card.Description>Every run in launch order. Open one for its logs, code, evaluation and retry options.</Card.Description>
			</div>
			{#if experiment.status !== 'draft' && experiment.status !== 'archived'}
				<Button variant="outline" size="sm" disabled={failedCells === 0} onclick={() => (retryOpen = true)}>
					<RotateCcw />Retry failed{failedCells ? ` (${failedCells})` : ''}
				</Button>
			{/if}
		</Card.Header>
		<Card.Content class="space-y-3">
			<div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
				<div class="space-y-1">
					<Label class="text-xs" for="runs-condition">Condition</Label>
					<Select.Simple id="runs-condition" size="sm" value={conditionFilter} items={conditionItems} onValueChange={(v) => setFilter(() => (conditionFilter = v))} />
				</div>
				<div class="space-y-1">
					<Label class="text-xs" for="runs-endpoint">Endpoint</Label>
					<Select.Simple id="runs-endpoint" size="sm" value={endpointFilter} items={endpointItems} onValueChange={(v) => setFilter(() => (endpointFilter = v))} />
				</div>
				<div class="space-y-1">
					<Label class="text-xs" for="runs-app">App</Label>
					<Select.Simple id="runs-app" size="sm" value={appFilter} items={appItems} onValueChange={(v) => setFilter(() => (appFilter = v))} />
				</div>
				<div class="space-y-1">
					<Label class="text-xs" for="runs-status">Status</Label>
					<Select.Simple id="runs-status" size="sm" value={statusFilter} items={STATUS_ITEMS} onValueChange={(v) => setFilter(() => (statusFilter = v))} />
				</div>
			</div>
			<div class="flex flex-wrap gap-x-5 gap-y-2 text-xs">
				<label class="flex items-center gap-2" title="Only the latest attempt of each cell">
					<Switch size="sm" checked={currentOnly} label="Current attempts only" onCheckedChange={(v) => setFilter(() => (currentOnly = v))} />
					Current attempts only
				</label>
				<label class="flex items-center gap-2" title="Only runs that needed more than one attempt">
					<Switch size="sm" checked={retriedOnly} label="Retried only" onCheckedChange={(v) => setFilter(() => (retriedOnly = v))} />
					Retried only
				</label>
			</div>

			{#if error}
				<ErrorAlert message={error} onretry={load} />
			{/if}
			{#if loading && !data}
				<LoadingState label="loading runs" />
			{:else if data && data.items.length === 0}
				<EmptyState compact icon={ListFilter} title="No runs match" />
			{:else if data}
				<BatchJobsTable jobs={data.items} mode={experiment.mode} graded />
				<PaginationBar
					class="rounded-md border border-border"
					resultsText="{data.total} run{data.total === 1 ? '' : 's'}"
					page={data.page}
					pages={data.pages}
					onGoToPage={(p) => (pageNum = p)}
				/>
			{/if}
		</Card.Content>
	</Card.Root>

	<Card.Root>
		<Card.Header>
			<Card.Title>Launch history</Card.Title>
			<Card.Description>Every launch, top-up and scheduled run as a batch.</Card.Description>
		</Card.Header>
		<Card.Content>
			{#if batches === null}
				<LoadingState label="loading batches" class="py-6" />
			{:else if batches.length === 0}
				<p class="text-xs text-muted-foreground">Not launched yet.</p>
			{:else}
				<ul class="divide-y divide-border/60">
					{#each batches as batch (batch.id)}
						{@const counts = batchCounts(batch)}
						{@const kind = (batch as GenerationBatchSummary & { kind?: string }).kind ?? ''}
						<li class="flex flex-wrap items-center gap-3 py-2 text-xs">
							<a href="/sample-generator/batches/{batch.id}" class="min-w-0 font-medium hover:underline">{batch.name}</a>
							{#if kind}<Badge variant="outline">{KIND_LABELS[kind] ?? kind}</Badge>{/if}
							<span class="text-muted-foreground tabular-nums">{formatDateTime(batch.created_at)}</span>
							<BatchProgress {counts} total={batch.total_jobs} class="min-w-24 flex-1" />
							<span class="font-mono tabular-nums">{finishedCount(counts)}/{batch.total_jobs}</span>
						</li>
					{/each}
				</ul>
			{/if}
		</Card.Content>
	</Card.Root>
</div>

<ConfirmDialog
	bind:open={retryOpen}
	title="Retry {failedCells} failed or cancelled cell{failedCells === 1 ? '' : 's'}?"
	description={`New runs use each cell's original seed${experiment.base_seed == null ? ' (none: each draws a new seed)' : ''}. ${
		isTerminal(experiment.status) ? 'This finished experiment reopens until they are done. ' : ''
	}Completed runs are kept.`}
	confirmLabel="Retry"
	busy={retrying}
	onConfirm={retryFailed}
/>
