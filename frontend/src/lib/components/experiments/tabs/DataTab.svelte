<script lang="ts">
	/**
	 * DataTab — §4 Data: 4.1 every run (`GET /generation/jobs/?experiment_id`)
	 * as Table D1, filtered by condition, endpoint, app and status, current
	 * attempts only (default) or retried runs only, paged on the server;
	 * "Retry failed" tops up failed and cancelled cells. 4.2 the collection
	 * log (every launch, top-up and scheduled batch) and 4.3 the dataset
	 * downloads (JSON research export, CSV of runs).
	 *
	 * Props: `experiment`, `appName(id)`, `onlaunched()` (after a top-up),
	 * `counts` (live rollups, so "Retry failed" follows the run), `exporting`
	 * and `onexport(format)` (the page downloads), and `refreshSignal` /
	 * `tickSignal` (bumped by the page on `state` / `tick`): the table
	 * refetches silently on `state`, and on `tick` at most every 10 s while the
	 * experiment is not terminal.
	 */
	import { untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
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
	import type { ProgressCounts } from '$lib/api/types/experiments';
	import { experimentErrorBody, launchExperiment, listBatches, type ExperimentDetail } from '$lib/api/experiments';
	import { formatApiError } from '$lib/api/core';
	import { formatDateTime } from '$lib/utils/formatters';
	import Braces from '@lucide/svelte/icons/braces';
	import Download from '@lucide/svelte/icons/download';
	import ListFilter from '@lucide/svelte/icons/list-filter';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import Sheet from '@lucide/svelte/icons/sheet';
	import { conditionName, isTerminal, plural } from '../format';
	import StudyFigure from '../study/StudyFigure.svelte';
	import StudySection from '../study/StudySection.svelte';

	interface Props {
		experiment: ExperimentDetail;
		appName: (id: number | null) => string;
		onlaunched?: () => void;
		counts?: ProgressCounts | null;
		exporting?: boolean;
		onexport?: (format: 'json' | 'csv') => void;
		refreshSignal?: number;
		tickSignal?: number;
	}

	let { experiment, appName, onlaunched, counts = null, exporting = false, onexport, refreshSignal = 0, tickSignal = 0 }: Props = $props();

	const TICK_REFRESH_MS = 10_000;
	let lastFetch = 0;

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
		lastFetch = Date.now();
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
				// `attempt` is 0-based: the first retry is attempt 1.
				attempt_gt: retriedOnly ? 0 : undefined,
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

	// Live refresh: keeps the previous table on screen while refetching.
	$effect(() => {
		void refreshSignal;
		untrack(() => {
			if (data) void load();
		});
	});
	$effect(() => {
		void tickSignal;
		untrack(() => {
			if (!data || isTerminal(experiment.status)) return;
			if (Date.now() - lastFetch >= TICK_REFRESH_MS) void load();
		});
	});

	function setFilter(apply: () => void) {
		apply();
		pageNum = 1;
	}
	const filtered = $derived(
		conditionFilter !== ALL || endpointFilter !== ALL || appFilter !== ALL || statusFilter !== ALL || !currentOnly || retriedOnly
	);

	// ── collection log ──────────────────────────────────────────────
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
	const failedCells = $derived.by(() => {
		const c = counts ?? experiment.progress;
		return c.failed + c.cancelled;
	});

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
		...experiment.conditions.map((c) => ({ value: String(c.id), label: conditionName(c) })),
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

<div class="space-y-10">
	<StudySection id="data-runs" number="4.1" title="Runs" lead="Open a run for its logs, generated code, evaluation and retry options.">
		<StudyFigure id="table-d1" kind="table" number="D1" title="Every run, in launch order" flush>
			{#snippet controls()}
				<Button variant="ghost" size="sm" disabled={loading} onclick={() => void load()} aria-label="Refresh runs">
					<RefreshCw class={loading ? 'animate-spin motion-reduce:animate-none' : ''} />Refresh
				</Button>
				{#if experiment.status !== 'draft' && experiment.status !== 'archived' && failedCells > 0}
					<Button variant="outline" size="sm" onclick={() => (retryOpen = true)}><RotateCcw />Retry failed ({failedCells})</Button>
				{/if}
			{/snippet}
			<div class="space-y-3 border-b border-border/60 bg-muted/20 px-4 py-3">
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
				<div class="flex flex-wrap gap-x-5 gap-y-2 text-xs max-sm:gap-y-0">
					<label class="flex cursor-pointer items-center gap-2 max-sm:min-h-11" title="Only the latest attempt of each cell">
						<Switch size="sm" checked={currentOnly} label="Current attempts only" onCheckedChange={(v) => setFilter(() => (currentOnly = v))} />
						Current attempts only
					</label>
					<label class="flex cursor-pointer items-center gap-2 max-sm:min-h-11" title="Only retry attempts (runs after the first attempt of a cell)">
						<Switch size="sm" checked={retriedOnly} label="Retried only" onCheckedChange={(v) => setFilter(() => (retriedOnly = v))} />
						Retried only
					</label>
				</div>
			</div>
			<div class="space-y-3 p-3">
				{#if error}
					<ErrorAlert message={error} onretry={load} />
				{/if}
				{#if loading && !data}
					<LoadingState label="loading runs" />
				{:else if data && data.items.length === 0}
					<EmptyState compact icon={ListFilter} title={filtered ? 'No runs match' : 'No runs yet'} />
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
			</div>
			{#snippet caption()}
				<strong class="font-medium text-foreground">Table D1.</strong>
				{currentOnly ? 'Current attempt of each cell' : 'Every attempt'}{retriedOnly ? ', retries only' : ''}. Trial = repeat; seed = the seed the
				run used ({experiment.base_seed != null ? 'shared across conditions per repeat' : 'drawn per run'}).
			{/snippet}
		</StudyFigure>
	</StudySection>

	<StudySection id="data-log" number="4.2" title="Collection log" lead="Every launch, top-up and scheduled run, as the batch that queued it.">
		{#if batches === null}
			<LoadingState label="loading batches" class="py-6" />
		{:else if batches.length === 0}
			<p class="text-sm text-muted-foreground">Not launched yet.</p>
		{:else}
			<div class="overflow-x-auto rounded-md border border-border bg-card shadow-[var(--shadow-card)]">
				<table class="min-w-[40rem]">
					<thead>
						<tr>
							<th scope="col">Batch</th>
							<th scope="col">Kind</th>
							<th scope="col">Queued</th>
							<th scope="col" class="w-1/3">Progress</th>
							<th scope="col" class="num">Finished</th>
						</tr>
					</thead>
					<tbody>
						{#each batches as batch (batch.id)}
							{@const batchCount = batchCounts(batch)}
							{@const kind = (batch as GenerationBatchSummary & { kind?: string }).kind ?? ''}
							<tr>
								<td class="max-w-72"><a href="/sample-generator/batches/{batch.id}" class="settings-link line-clamp-1 font-medium">{batch.name}</a></td>
								<td class="whitespace-nowrap font-mono text-xs text-muted-foreground">{kind ? (KIND_LABELS[kind] ?? kind) : '—'}</td>
								<td class="whitespace-nowrap text-xs tabular-nums text-muted-foreground">{formatDateTime(batch.created_at)}</td>
								<td><BatchProgress counts={batchCount} total={batch.total_jobs} class="min-w-24" /></td>
								<td class="num text-xs">{finishedCount(batchCount)}/{batch.total_jobs}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</StudySection>

	<StudySection id="data-downloads" number="4.3" title="Dataset" lead="Downloads for analysis elsewhere. Both include every attempt, not just the current one.">
		<div class="grid gap-3 sm:grid-cols-2">
			<button
				type="button"
				class="group flex cursor-pointer items-start gap-3 rounded-md border border-border bg-card p-4 text-left shadow-[var(--shadow-card)] transition-colors duration-150 hover:border-primary/50 hover:bg-primary/[0.03] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-wait disabled:opacity-60"
				disabled={exporting}
				onclick={() => onexport?.('json')}
			>
				<span class="flex size-9 shrink-0 items-center justify-center rounded-md border border-border bg-muted text-muted-foreground group-hover:text-primary">
					{#if exporting}<LoaderCircle class="size-4 animate-spin motion-reduce:animate-none" />{:else}<Braces class="size-4" />{/if}
				</span>
				<span class="min-w-0 space-y-0.5">
					<span class="flex items-center gap-1.5 font-medium">Research export <span class="font-mono text-xs text-muted-foreground">.json</span></span>
					<span class="block text-xs leading-relaxed text-muted-foreground">
						The resolved design (conditions, presets with versions) and one record per run: seed, fingerprints, weights digest, termination and metrics.
					</span>
				</span>
				<Download class="ml-auto size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
			</button>
			<button
				type="button"
				class="group flex cursor-pointer items-start gap-3 rounded-md border border-border bg-card p-4 text-left shadow-[var(--shadow-card)] transition-colors duration-150 hover:border-primary/50 hover:bg-primary/[0.03] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-wait disabled:opacity-60"
				disabled={exporting}
				onclick={() => onexport?.('csv')}
			>
				<span class="flex size-9 shrink-0 items-center justify-center rounded-md border border-border bg-muted text-muted-foreground group-hover:text-primary">
					{#if exporting}<LoaderCircle class="size-4 animate-spin motion-reduce:animate-none" />{:else}<Sheet class="size-4" />{/if}
				</span>
				<span class="min-w-0 space-y-0.5">
					<span class="flex items-center gap-1.5 font-medium">Runs table <span class="font-mono text-xs text-muted-foreground">.csv</span></span>
					<span class="block text-xs leading-relaxed text-muted-foreground">
						One row per run attempt: condition, model, endpoint, digest, app, repeat, seed, outcome and pass rate — ready for R or pandas.
					</span>
				</span>
				<Download class="ml-auto size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
			</button>
		</div>
		<p class="text-xs text-muted-foreground">{plural(experiment.total_cells, 'cell')} in the design; the statistics in §3 can also be written up as a report from the Results tab.</p>
	</StudySection>
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
