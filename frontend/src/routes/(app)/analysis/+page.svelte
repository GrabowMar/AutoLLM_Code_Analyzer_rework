<script lang="ts">
	// Every analysis run the user can see (study runs and ad-hoc runs; self-
	// tests on request). Filters live in the URL; the list refreshes silently
	// every 5 s while a run on the page is queued or running.
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import type {
		AnalysisRunListItem,
		Page,
		RunListParams,
		RunOrdering,
		RunPurpose,
		RunStatus
	} from '$lib/api/analysis';
	import {
		isRunActive,
		listAnalysisRuns,
		RUN_ORDERINGS,
		RUN_PURPOSES,
		RUN_STATUSES
	} from '$lib/api/analysis';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import FilterBar, { type FilterTag } from '$lib/components/FilterBar.svelte';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { cn } from '$lib/utils';
	import Hint from '$lib/components/analysis/Hint.svelte';
	import NaValue from '$lib/components/analysis/NaValue.svelte';
	import RunDataBadge from '$lib/components/analysis/RunDataBadge.svelte';
	import {
		aiConsensusCell,
		fmtAgo,
		fmtDateTime,
		fmtInt,
		fmtSeconds,
		measuredSloc,
		phaseLabel,
		PURPOSE_LABELS,
		shortHash,
		staticFindingsCell,
		toolProgress
	} from '$lib/components/analysis/run-utils';
	import { csvParam, intParam, replaceParams } from '$lib/components/analysis/run/url';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import Microscope from '@lucide/svelte/icons/microscope';
	import Plus from '@lucide/svelte/icons/plus';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import SearchX from '@lucide/svelte/icons/search-x';

	const PER_PAGE = 25;
	const POLL_MS = 5_000;

	const ORDER_LABELS: Record<RunOrdering, string> = {
		'-created_at': 'Newest first',
		created_at: 'Oldest first',
		'-completed_at': 'Recently finished',
		completed_at: 'First finished',
		status: 'Status',
		name: 'Name',
		'-duration': 'Longest',
		duration: 'Shortest'
	};

	// ── URL state ──────────────────────────────────────────────────────
	const sp = $derived(page.url.searchParams);
	const statuses = $derived(
		csvParam(sp.get('status')).filter((s): s is RunStatus =>
			(RUN_STATUSES as readonly string[]).includes(s)
		)
	);
	const purposes = $derived(
		csvParam(sp.get('purpose')).filter((s): s is RunPurpose =>
			(RUN_PURPOSES as readonly string[]).includes(s)
		)
	);
	const complete = $derived(sp.get('complete') ?? '');
	const q = $derived(sp.get('q') ?? '');
	const order = $derived<RunOrdering>(
		(RUN_ORDERINGS as readonly string[]).includes(sp.get('order') ?? '')
			? (sp.get('order') as RunOrdering)
			: '-created_at'
	);
	const pageNo = $derived(intParam(sp.get('page'), 1));
	const planHash = $derived(sp.get('plan_hash') ?? '');
	const studyId = $derived(sp.get('study_id') ?? '');
	const jobId = $derived(sp.get('generation_job_id') ?? '');

	const params = $derived<RunListParams>({
		page: pageNo,
		per_page: PER_PAGE,
		status: statuses.length ? statuses : undefined,
		purpose: purposes.length ? purposes : undefined,
		is_complete: complete === 'yes' ? true : complete === 'no' ? false : undefined,
		q: q || undefined,
		ordering: order,
		plan_hash: planHash || undefined,
		study_id: studyId || undefined,
		generation_job_id: jobId || undefined
	});
	const paramsKey = $derived(JSON.stringify(params));

	function update(updates: Record<string, string | number | null>, resetPage = true) {
		void replaceParams(page.url, resetPage ? { ...updates, page: null } : updates);
	}

	// ── Data ───────────────────────────────────────────────────────────
	let data = $state<Page<AnalysisRunListItem> | null>(null);
	let loading = $state(true);
	let refreshing = $state(false);
	let error = $state('');
	let controller: AbortController | null = null;

	async function load(silent = false) {
		controller?.abort();
		const mine = new AbortController();
		controller = mine;
		if (!silent) refreshing = true;
		try {
			data = await listAnalysisRuns(params, { signal: mine.signal });
			error = '';
		} catch (e) {
			if (isAbortError(e)) return;
			if (!(silent && data)) error = formatApiError(e, 'Could not load runs.');
		} finally {
			if (controller === mine) {
				loading = false;
				refreshing = false;
			}
		}
	}

	$effect(() => {
		void paramsKey;
		untrack(() => void load());
		return () => controller?.abort();
	});

	const runs = $derived(data?.items ?? []);
	const anyActive = $derived(runs.some((r) => isRunActive(r.status)));

	$effect(() => {
		if (!anyActive) return;
		const timer = setInterval(() => {
			if (!document.hidden) void load(true);
		}, POLL_MS);
		return () => clearInterval(timer);
	});

	// ── Filters UI ─────────────────────────────────────────────────────
	let searchText = $state(untrack(() => q));
	let searchTimer: ReturnType<typeof setTimeout> | undefined;
	function onSearch(value: string) {
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => update({ q: value.trim() || null }), 300);
	}

	function toggle(list: string[], value: string, key: string) {
		const next = list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
		update({ [key]: next.length ? next.join(',') : null });
	}

	const activeTags = $derived.by((): FilterTag[] => {
		const tags: FilterTag[] = [];
		if (statuses.length)
			tags.push({
				key: 'status',
				label: `Status: ${statuses.join(', ')}`,
				onRemove: () => update({ status: null })
			});
		if (purposes.length)
			tags.push({
				key: 'purpose',
				label: `Purpose: ${purposes.map((p) => PURPOSE_LABELS[p]).join(', ')}`,
				onRemove: () => update({ purpose: null })
			});
		if (complete)
			tags.push({
				key: 'complete',
				label: complete === 'yes' ? 'Complete only' : 'Incomplete only',
				onRemove: () => update({ complete: null })
			});
		if (planHash)
			tags.push({
				key: 'plan',
				label: `Plan ${shortHash(planHash, 10)}`,
				onRemove: () => update({ plan_hash: null })
			});
		if (studyId)
			tags.push({
				key: 'study',
				label: `Study ${studyId.slice(0, 8)}`,
				onRemove: () => update({ study_id: null })
			});
		if (jobId)
			tags.push({
				key: 'job',
				label: `Job ${jobId.slice(0, 8)}`,
				onRemove: () => update({ generation_job_id: null })
			});
		if (q)
			tags.push({
				key: 'q',
				label: `Search: ${q}`,
				onRemove: () => {
					searchText = '';
					update({ q: null });
				}
			});
		return tags;
	});

	function clearAll() {
		searchText = '';
		void replaceParams(page.url, {}, [
			'status',
			'purpose',
			'complete',
			'q',
			'order',
			'page',
			'plan_hash',
			'study_id',
			'generation_job_id'
		]);
	}

	const resultsText = $derived(
		data
			? `${data.total.toLocaleString()} run${data.total === 1 ? '' : 's'}${purposes.includes('canary') ? '' : ' · self-tests hidden'}`
			: ''
	);
</script>

<div class="space-y-6">
	<PageHeader
		title="Analysis runs"
		description="Every analysis run: study runs under a locked protocol and ad-hoc checks. Runs are comparable only when their plan hashes match."
	>
		{#snippet actions()}
			<Button variant="outline" size="sm" onclick={() => load()} disabled={refreshing}>
				<RefreshCw
					class="size-3.5 {refreshing ? 'animate-spin motion-reduce:animate-none' : ''}"
				/>Refresh
			</Button>
			<Button variant="outline" size="sm" href="/analysis/studies">
				<FlaskConical class="size-3.5" />Studies
			</Button>
			<Button size="sm" href="/analysis/create">
				<Plus class="size-3.5" />Ad-hoc run
			</Button>
		{/snippet}
	</PageHeader>

	<FilterBar
		searchPlaceholder="Search name, model, app or harness…"
		bind:searchValue={searchText}
		onSearchInput={onSearch}
		{activeTags}
		onClearAll={clearAll}
		{resultsText}
		page={data?.page}
		pages={data?.pages}
		onGoToPage={(p) => update({ page: p }, false)}
	>
		{#snippet filters()}
			<div class="fb-group">
				<span class="fb-group-label">Status</span>
				{#each RUN_STATUSES as s (s)}
					<button
						type="button"
						class={cn('fb-chip capitalize', statuses.includes(s) && 'fb-chip-on')}
						aria-pressed={statuses.includes(s)}
						onclick={() => toggle(statuses, s, 'status')}>{s}</button
					>
				{/each}
			</div>
			<div class="fb-group">
				<span class="fb-group-label">Purpose</span>
				{#each RUN_PURPOSES as p (p)}
					<button
						type="button"
						class={cn('fb-chip', purposes.includes(p) && 'fb-chip-on')}
						aria-pressed={purposes.includes(p)}
						onclick={() => toggle(purposes, p, 'purpose')}>{PURPOSE_LABELS[p]}</button
					>
				{/each}
			</div>
			<div class="fb-group">
				<span class="fb-group-label">Data</span>
				{#each [{ value: 'yes', label: 'Complete' }, { value: 'no', label: 'Incomplete' }] as option (option.value)}
					<button
						type="button"
						class={cn('fb-chip', complete === option.value && 'fb-chip-on')}
						aria-pressed={complete === option.value}
						onclick={() =>
							update({
								complete: complete === option.value ? null : option.value
							})}>{option.label}</button
					>
				{/each}
				<span class="fb-group-label ml-auto w-auto">Order</span>
				<Select.Simple
					size="sm"
					class="w-44"
					aria-label="Order"
					value={order}
					items={RUN_ORDERINGS.map((o) => ({
						value: o,
						label: ORDER_LABELS[o]
					}))}
					onValueChange={(v) => update({ order: v === '-created_at' ? null : v }, false)}
				/>
			</div>
		{/snippet}
	</FilterBar>

	{#if error}
		<ErrorAlert message={error} onretry={() => load()} />
	{/if}

	{#if loading && !data}
		<LoadingState label="loading runs" />
	{:else if data && runs.length === 0}
		{#if activeTags.length > 0}
			<EmptyState
				icon={SearchX}
				title="No runs match these filters"
				description="Remove a filter or clear them all."
			>
				{#snippet action()}
					<Button variant="outline" size="sm" onclick={clearAll}>Clear filters</Button>
				{/snippet}
			</EmptyState>
		{:else}
			<EmptyState
				icon={Microscope}
				title="No analysis runs yet"
				description="Start a study to analyse generated apps under a locked protocol, or check one application with an ad-hoc run."
			>
				{#snippet action()}
					<Button variant="outline" size="sm" href="/analysis/studies/new">New study</Button>
					<Button size="sm" href="/analysis/create">Ad-hoc run</Button>
				{/snippet}
			</EmptyState>
		{/if}
	{:else if data}
		<Card.Root class="gap-0 py-0">
			<Card.Content class="p-0">
				<div
					class={cn('table-scroll-wrapper transition-opacity', refreshing && 'opacity-60')}
					aria-busy={refreshing}
				>
					<table class="table-card-mobile">
						<thead>
							<tr>
								<th>Run</th>
								<th>Purpose</th>
								<th>Status</th>
								<th>Data</th>
								<th class="text-right">Static findings</th>
								<th class="text-right">AI consensus</th>
								<th class="text-right">SLOC</th>
								<th>Created</th>
							</tr>
						</thead>
						<tbody>
							{#each runs as run (run.id)}
								{@const st = staticFindingsCell(run)}
								{@const ai = aiConsensusCell(run)}
								{@const active = isRunActive(run.status)}
								{@const progress = toolProgress(run)}
								{@const sloc = measuredSloc(run)}
								<tr>
									<td data-label="Run" class="max-w-sm">
										<div class="min-w-0 flex-1">
											<a
												href="/analysis/{run.id}"
												class="block truncate text-sm font-medium hover:text-primary hover:underline"
												title={run.name}>{run.name}</a
											>
											<span
												class="block truncate font-mono text-[11px] text-muted-foreground"
												title={run.target.label}>{run.target.label}</span
											>
										</div>
									</td>
									<td data-label="Purpose" class="whitespace-nowrap">
										{#if run.study_id}
											<a
												href="/analysis/studies/{run.study_id}"
												class="text-xs text-primary underline-offset-2 hover:underline"
												>{PURPOSE_LABELS[run.purpose]}</a
											>
										{:else}
											<span class="text-xs">{PURPOSE_LABELS[run.purpose]}</span>
										{/if}
										{#if run.source === 'inline' && run.purpose !== 'canary'}
											<Badge variant="outline" class="ml-1 border-warning/40 text-warning-fg"
												>non-research</Badge
											>
										{/if}
									</td>
									<td data-label="Status">
										<div class="flex flex-col items-start gap-0.5">
											<span class="flex flex-wrap items-center gap-1">
												<StatusBadge status={run.status} />
												{#if run.stale}<StatusBadge status="stale" />{/if}
											</span>
											{#if active}
												<span class="text-[11px] text-muted-foreground">
													{run.cancel_requested ? 'cancelling' : phaseLabel(run.phase)} · {progress.done}/{progress.total}
													tools
												</span>
											{/if}
										</div>
									</td>
									<td data-label="Data" class="whitespace-nowrap">
										<RunDataBadge {run} />
									</td>
									<td data-label="Static findings" class="tabular-nums md:text-right">
										{#if st.value === null}
											<NaValue reason={st.note} />
										{:else if st.lowerBound}
											<Hint text={st.note}>≥ {fmtInt(st.value)}</Hint>
										{:else}
											{fmtInt(st.value)}
										{/if}
									</td>
									<td data-label="AI consensus" class="tabular-nums md:text-right">
										{#if ai.value === null}
											<NaValue reason={ai.note} />
										{:else}
											{fmtInt(ai.value)}
										{/if}
									</td>
									<td data-label="SLOC" class="tabular-nums md:text-right">
										{#if sloc === null}
											<NaValue reason="The sample has not been resolved" />
										{:else}
											{fmtInt(sloc)}
										{/if}
									</td>
									<td data-label="Created" class="whitespace-nowrap">
										<span class="text-xs" title={fmtDateTime(run.created_at)}
											>{fmtAgo(run.created_at)}</span
										>
										{#if run.duration_seconds != null}
											<span class="block text-[11px] text-muted-foreground"
												>{fmtSeconds(run.duration_seconds)}</span
											>
										{/if}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</Card.Content>
		</Card.Root>
	{/if}
</div>
