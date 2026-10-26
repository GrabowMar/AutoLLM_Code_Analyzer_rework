<script lang="ts">
	/**
	 * StudyRunsTable — the runs of a study (server-side status filter,
	 * search and pagination) linking to each run's detail. The static and
	 * AI cells share the runs list's rules (`staticFindingsCell` /
	 * `aiConsensusCell`): a total is "≥ n" when a tool that could add to it
	 * produced no data, and "—" with the reason when nothing was measured.
	 *
	 * Props: `studyId`; `refreshKey` (bump to refetch silently, e.g. from
	 * the page's poll); `onloaded(items)`.
	 */
	import { untrack } from 'svelte';
	import {
		isRunActive,
		isRunSummary,
		listAnalysisRuns,
		type AnalysisRunListItem,
		type RunStatus
	} from '$lib/api/analysis';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { Input } from '$lib/components/ui/input';
	import { Segmented } from '$lib/components/ui/segmented';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import Search from '@lucide/svelte/icons/search';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import Microscope from '@lucide/svelte/icons/microscope';
	import Hint from '../Hint.svelte';
	import NaValue from '../NaValue.svelte';
	import { aiConsensusCell, phaseLabel, staticFindingsCell } from '../run-utils';
	import { fmtInt, fmtSeconds, fmtUsd } from './format';

	interface Props {
		studyId: string;
		refreshKey?: number;
		onloaded?: (items: AnalysisRunListItem[]) => void;
	}

	let { studyId, refreshKey = 0, onloaded }: Props = $props();

	type Filter = 'all' | 'active' | 'completed' | 'partial' | 'failed' | 'cancelled';
	const FILTERS: { value: Filter; label: string }[] = [
		{ value: 'all', label: 'All' },
		{ value: 'active', label: 'Active' },
		{ value: 'completed', label: 'Completed' },
		{ value: 'partial', label: 'Partial' },
		{ value: 'failed', label: 'Failed' },
		{ value: 'cancelled', label: 'Cancelled' }
	];
	const STATUS_FOR: Record<Filter, RunStatus[] | undefined> = {
		all: undefined,
		active: ['queued', 'running'],
		completed: ['completed'],
		partial: ['partial'],
		failed: ['failed'],
		cancelled: ['cancelled']
	};

	let filter = $state<Filter>('all');
	let q = $state('');
	let qApplied = $state('');
	let page = $state(1);
	let items = $state<AnalysisRunListItem[]>([]);
	let total = $state(0);
	let pages = $state(1);
	let loading = $state(true);
	let error = $state('');
	let searchTimer: ReturnType<typeof setTimeout> | null = null;
	let retry = $state(0);

	$effect(() => {
		// Refetch on filter/search/page changes and on every refreshKey bump.
		const params = { filter, qApplied, page, refreshKey, retry };
		const ctrl = new AbortController();
		const silent = untrack(() => items.length > 0);
		if (!silent) loading = true;
		listAnalysisRuns(
			{
				study_id: studyId,
				status: STATUS_FOR[params.filter],
				q: params.qApplied || undefined,
				page: params.page,
				per_page: 25,
				ordering: '-created_at'
			},
			{ signal: ctrl.signal }
		)
			.then((res) => {
				if (ctrl.signal.aborted) return;
				items = res.items;
				total = res.total;
				pages = Math.max(1, res.pages);
				error = '';
				onloaded?.(res.items);
			})
			.catch((e) => {
				if (isAbortError(e) || ctrl.signal.aborted) return;
				error = formatApiError(e, 'Could not load the runs.');
			})
			.finally(() => {
				if (!ctrl.signal.aborted) loading = false;
			});
		return () => ctrl.abort();
	});

	function onSearch() {
		if (searchTimer) clearTimeout(searchTimer);
		searchTimer = setTimeout(() => {
			page = 1;
			qApplied = q.trim();
		}, 300);
	}

	function toolsNotOk(run: AnalysisRunListItem): number {
		const t = run.tools;
		return (t.failed ?? 0) + (t.timed_out ?? 0) + (t.skipped ?? 0) + (t.cancelled ?? 0);
	}
</script>

<div class="space-y-3">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<Segmented
			size="sm"
			aria-label="Filter runs by status"
			bind:value={filter}
			items={FILTERS}
			onValueChange={() => (page = 1)}
		/>
		<div class="relative w-full sm:w-64">
			<Search
				class="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
				aria-hidden="true"
			/>
			<Input
				type="search"
				class="h-8 pl-8"
				placeholder="Search runs…"
				aria-label="Search runs"
				bind:value={q}
				oninput={onSearch}
			/>
		</div>
	</div>

	{#if error}
		<ErrorAlert message={error} onretry={() => (retry += 1)} />
	{/if}
	{#if loading && items.length === 0}
		<LoadingState label="loading runs" />
	{:else if items.length === 0}
		<EmptyState
			compact
			icon={Microscope}
			title="No runs"
			description={filter === 'all' && !qApplied
				? 'Runs appear once the study is launched.'
				: 'No run matches this filter.'}
		/>
	{:else}
		<div class="table-scroll-wrapper rounded-md border border-border">
			<table class="table-card-mobile">
				<thead>
					<tr>
						<th scope="col">Target</th>
						<th scope="col">Status</th>
						<th scope="col">Coverage</th>
						<th scope="col" class="text-right">Static findings</th>
						<th scope="col" class="text-right">AI consensus</th>
						<th scope="col" class="text-right">Measured SLOC</th>
						<th scope="col" class="text-right">Cost</th>
						<th scope="col" class="text-right">Duration</th>
					</tr>
				</thead>
				<tbody>
					{#each items as run (run.id)}
						{@const finished = isRunSummary(run.summary)}
						{@const notOk = toolsNotOk(run)}
						{@const st = staticFindingsCell(run)}
						{@const ai = aiConsensusCell(run)}
						<tr>
							<td data-label="Target" class="max-w-[20rem]">
								<a
									href="/analysis/{run.id}"
									class="block truncate font-medium hover:text-primary hover:underline"
									title={run.target.label}
								>
									{run.target.label}
								</a>
								{#if run.attempt > 1}<span class="text-[11px] text-muted-foreground"
										>attempt {run.attempt}</span
									>{/if}
							</td>
							<td data-label="Status">
								<span class="flex flex-wrap items-center gap-1">
									<StatusBadge status={run.stale ? 'stale' : run.status} />
									{#if isRunActive(run.status) && run.phase && run.phase !== run.status}
										<span class="text-[11px] text-muted-foreground">{phaseLabel(run.phase)}</span>
									{/if}
									{#if run.cancel_requested && isRunActive(run.status)}
										<span class="text-[11px] text-warning-fg">cancelling</span>
									{/if}
								</span>
							</td>
							<td data-label="Coverage">
								{#if isRunActive(run.status)}
									<span class="text-xs text-muted-foreground tabular-nums">
										{run.tools.succeeded + run.tools.not_applicable + notOk}/{run.tools.total} tools done
									</span>
								{:else if run.is_complete}
									<span class="text-xs text-success-fg">All required tools OK</span>
								{:else}
									<Hint
										underline={false}
										text={run.error_message ||
											'A required tool did not succeed: dependent outcomes are missing (NA).'}
									>
										<span class="inline-flex items-center gap-1 text-xs text-warning-fg">
											<TriangleAlert class="h-3.5 w-3.5" aria-hidden="true" />Incomplete{notOk
												? ` · ${notOk} tool${notOk === 1 ? '' : 's'} not OK`
												: ''}
										</span>
									</Hint>
								{/if}
							</td>
							<td data-label="Static findings" class="md:text-right tabular-nums">
								{#if st.value === null}
									<NaValue reason={st.note} />
								{:else if st.lowerBound}
									<Hint text={st.note}>≥ {fmtInt(st.value)}</Hint>
								{:else}
									{fmtInt(st.value)}
								{/if}
							</td>
							<td data-label="AI consensus" class="md:text-right tabular-nums">
								{#if ai.value === null}
									<NaValue reason={ai.note} />
								{:else}
									{fmtInt(ai.value)}
								{/if}
							</td>
							<td data-label="Measured SLOC" class="md:text-right tabular-nums">
								{#if finished}{fmtInt(run.summary.sloc?.measured)}{:else}<NaValue
										reason="Not measured yet."
									/>{/if}
							</td>
							<td data-label="Cost" class="md:text-right tabular-nums">
								{#if finished && run.summary.cost_usd !== null && run.summary.cost_usd !== undefined}
									{fmtUsd(run.summary.cost_usd)}
								{:else}
									<NaValue
										reason="No AI cost recorded (no reviewer, or the run has not finished)."
									/>
								{/if}
							</td>
							<td data-label="Duration" class="md:text-right tabular-nums text-muted-foreground"
								>{fmtSeconds(run.duration_seconds)}</td
							>
						</tr>
					{/each}
				</tbody>
			</table>
			<PaginationBar
				resultsText="{total} run{total === 1 ? '' : 's'}"
				{page}
				{pages}
				onGoToPage={(p) => (page = p)}
			/>
		</div>
	{/if}
</div>
