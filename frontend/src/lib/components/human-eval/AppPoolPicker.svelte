<script lang="ts">
	/**
	 * AppPoolPicker — choose the generated apps of a task's pool: filter the
	 * candidate jobs (completed generations) by experiment / search, tick
	 * individual apps, or link a whole experiment (every current attempt joins
	 * the pool and the task can use the experiment's baseline condition).
	 *
	 *   <AppPoolPicker bind:selected={jobIds} bind:experimentId excludeJobIds={poolJobIds} />
	 *
	 * Props:
	 * - `selected` (bindable): individually picked job ids (replaced, never mutated).
	 * - `experimentId` (bindable): the linked experiment, or null. Its current apps
	 *   are added by the server; rows of it show as included.
	 * - `excludeJobIds`: jobs already in the pool (shown ticked and disabled).
	 */
	import { onDestroy, onMount, untrack } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import Link2 from '@lucide/svelte/icons/link-2';
	import Search from '@lucide/svelte/icons/search';
	import X from '@lucide/svelte/icons/x';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { TableToolbar, modelNamePrefix, shortModelName, tableFrame } from '$lib/components/table';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { listExperiments, type ExperimentListItem } from '$lib/api/experiments';
	import { listCandidateJobs, type CandidateJob, type FunctionalSummary } from '$lib/api/human-eval';
	import { formatDate } from '$lib/utils/formatters';
	import { repeatSuffix } from './format';

	interface Props {
		selected: string[];
		experimentId?: string | null;
		excludeJobIds?: string[];
	}

	let { selected = $bindable([]), experimentId = $bindable(null), excludeJobIds = [] }: Props = $props();

	const PER_PAGE = 25;

	// ── experiments ───────────────────────────────────────────────────
	let experiments = $state<ExperimentListItem[]>([]);
	let experimentsError = $state('');
	let linkedCount = $state<number | null>(null);
	let linkedCountFor = '';

	onMount(async () => {
		try {
			const res = await listExperiments({ per_page: 100, ordering: '-updated_at' });
			experiments = res.items;
		} catch (e) {
			experimentsError = formatApiError(e, 'Could not load experiments.');
		}
	});

	const experimentItems = $derived(
		experiments.map((x) => ({
			value: x.id,
			label: x.name,
			description: `${x.status.replace(/_/g, ' ')} · ${x.condition_count} condition${x.condition_count === 1 ? '' : 's'} · ${x.app_count} app spec${x.app_count === 1 ? '' : 's'}`,
		}))
	);
	const linkedName = $derived(
		experimentId ? (experiments.find((x) => x.id === experimentId)?.name ?? 'Linked experiment') : ''
	);

	// ── filters ───────────────────────────────────────────────────────
	let filterExperiment = $state(untrack(() => experimentId ?? ''));
	let searchText = $state('');
	let search = $state('');
	let currentOnly = $state(true);
	let pageNo = $state(1);
	let searchTimer: ReturnType<typeof setTimeout> | undefined;

	function onSearch(value: string) {
		searchText = value;
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => {
			search = value.trim();
			pageNo = 1;
		}, 300);
	}

	function setFilterExperiment(value: string) {
		filterExperiment = value;
		pageNo = 1;
	}

	// ── candidates ────────────────────────────────────────────────────
	let jobs = $state<CandidateJob[]>([]);
	let total = $state(0);
	let pages = $state(1);
	let loading = $state(true);
	let error = $state('');
	let ctl: AbortController | null = null;

	async function fetchJobs() {
		ctl?.abort();
		const mine = new AbortController();
		ctl = mine;
		loading = true;
		try {
			const res = await listCandidateJobs(
				{
					experiment_id: filterExperiment || undefined,
					search: search || undefined,
					current_only: currentOnly,
					page: pageNo,
					per_page: PER_PAGE,
				},
				{ signal: mine.signal }
			);
			jobs = res.items;
			total = res.total;
			pages = res.pages;
			error = '';
		} catch (e) {
			if (isAbortError(e) && mine.signal.aborted) return;
			error = formatApiError(e, 'Could not load generated apps.');
		} finally {
			if (ctl === mine) loading = false;
		}
	}

	const queryKey = $derived(JSON.stringify([filterExperiment, search, currentOnly, pageNo]));
	$effect(() => {
		void queryKey;
		untrack(() => void fetchJobs());
	});

	// Size of the linked experiment's current pool contribution.
	$effect(() => {
		const id = experimentId;
		untrack(() => {
			if (!id) {
				linkedCount = null;
				linkedCountFor = '';
				return;
			}
			if (id === linkedCountFor) return;
			linkedCountFor = id;
			linkedCount = null;
			listCandidateJobs({ experiment_id: id, current_only: true, per_page: 1 })
				.then((res) => {
					if (linkedCountFor === id) linkedCount = res.total;
				})
				.catch(() => {});
		});
	});

	onDestroy(() => {
		ctl?.abort();
		clearTimeout(searchTimer);
	});

	// ── selection ─────────────────────────────────────────────────────
	const selectedSet = $derived(new Set(selected));
	const excluded = $derived(new Set(excludeJobIds));

	/** Rows the server adds anyway through the linked experiment (current attempts). */
	function viaExperiment(job: CandidateJob): boolean {
		return !!experimentId && currentOnly && job.experiment?.id === experimentId;
	}

	function rowState(job: CandidateJob): 'pool' | 'experiment' | 'selectable' {
		if (excluded.has(job.id)) return 'pool';
		if (viaExperiment(job)) return 'experiment';
		return 'selectable';
	}

	const selectable = $derived(jobs.filter((j) => rowState(j) === 'selectable'));
	const pageAllSelected = $derived(selectable.length > 0 && selectable.every((j) => selectedSet.has(j.id)));
	const pageSomeSelected = $derived(!pageAllSelected && selectable.some((j) => selectedSet.has(j.id)));

	function toggle(id: string, on: boolean) {
		const next = new Set(selected);
		if (on) next.add(id);
		else next.delete(id);
		selected = [...next];
	}

	function togglePage(on: boolean) {
		const next = new Set(selected);
		for (const j of selectable) {
			if (on) next.add(j.id);
			else next.delete(j.id);
		}
		selected = [...next];
	}

	function linkExperiment(on: boolean) {
		experimentId = on && filterExperiment ? filterExperiment : null;
	}

	const anyFilter = $derived(!!(filterExperiment || search || !currentOnly));
	function clearFilters() {
		filterExperiment = '';
		search = '';
		searchText = '';
		currentOnly = true;
		pageNo = 1;
	}

	const resultsText = $derived(
		total > 0 ? `${(pageNo - 1) * PER_PAGE + 1}–${Math.min(pageNo * PER_PAGE, total)} of ${total} apps` : ''
	);

	function passRate(f: FunctionalSummary): string {
		return f.pass_rate == null ? '' : `${Math.round(f.pass_rate * 100)}%`;
	}
</script>

{#snippet functionalCell(f: FunctionalSummary)}
	{#if f.status}
		<span class="flex flex-wrap items-center gap-1.5">
			<StatusBadge status={f.status} />
			{#if passRate(f)}<span class="font-mono text-xs tabular-nums">{passRate(f)}</span>{/if}
		</span>
		<span class="cell-sub block text-[11px] text-muted-foreground">
			{f.source === 'conformance' ? 'conformance' : f.source === 'smoke' ? 'smoke test' : ''}
			{#if f.deployed === false}<span class="text-warning-fg"> · not deployed</span>{:else if f.deployed === true} · deployed{/if}
		</span>
	{:else}
		<span class="text-xs text-muted-foreground">Not graded</span>
	{/if}
{/snippet}

{#snippet selectBox(job: CandidateJob)}
	{@const state = rowState(job)}
	{#if state === 'selectable'}
		<Checkbox
			checked={selectedSet.has(job.id)}
			onCheckedChange={(v) => toggle(job.id, !!v)}
			aria-label="Select {job.app_name} by {job.model_name}"
		/>
	{:else}
		<Checkbox
			checked
			disabled
			aria-label="{job.app_name} by {job.model_name}: {state === 'pool' ? 'already in the pool' : 'included with the linked experiment'}"
		/>
	{/if}
{/snippet}

<div class="space-y-3">
	{#if experimentId || selected.length > 0}
		<div class="flex flex-wrap items-center gap-2 text-xs">
			{#if experimentId}
				<span class="inline-flex items-center gap-1.5 rounded-full border border-primary/30 bg-primary/10 px-2.5 py-1 text-primary">
					<Link2 class="size-3.5" aria-hidden="true" />
					{linkedName}{linkedCount != null ? ` · ${linkedCount} current app${linkedCount === 1 ? '' : 's'}` : ''}
					<button type="button" class="cursor-pointer rounded-full hover:text-foreground" onclick={() => (experimentId = null)} aria-label="Unlink {linkedName}">
						<X class="size-3" />
					</button>
				</span>
			{/if}
			{#if selected.length > 0}
				<span class="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted px-2.5 py-1">
					<b class="font-mono tabular-nums">{selected.length}</b> app{selected.length === 1 ? '' : 's'} picked individually
					<button type="button" class="cursor-pointer rounded-full text-muted-foreground hover:text-foreground" onclick={() => (selected = [])} aria-label="Clear picked apps">
						<X class="size-3" />
					</button>
				</span>
			{/if}
		</div>
	{/if}

	<Card.Root class="gap-0 overflow-clip py-0">
		<div class="flex flex-wrap items-center gap-2 border-b border-border p-2" role="search" aria-label="Filter generated apps">
			<div class="w-full sm:w-72">
				<Select.Simple
					size="sm"
					class="h-9"
					value={filterExperiment}
					placeholder="All my generated apps"
					clearLabel="All my generated apps"
					items={experimentItems}
					onValueChange={setFilterExperiment}
					aria-label="Experiment"
				/>
			</div>
			<div class="relative min-w-48 flex-1">
				<Search class="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
				<Input
					type="search"
					value={searchText}
					oninput={(e) => onSearch((e.currentTarget as HTMLInputElement).value)}
					placeholder="Search app, model or ID…"
					aria-label="Search generated apps"
					class="h-9 pl-8"
				/>
			</div>
			<label class="flex items-center gap-2 px-1 text-sm" title="Only the newest attempt of each experiment cell (condition × app × repeat)">
				<Checkbox
					checked={currentOnly}
					onCheckedChange={(v) => {
						currentOnly = !!v;
						pageNo = 1;
					}}
				/>
				Current attempts only
			</label>
		</div>
		{#if experimentsError}
			<div class="border-b border-border p-2"><ErrorAlert message={experimentsError} /></div>
		{/if}

		{#if filterExperiment}
			<label class="flex items-start gap-2.5 border-b border-border bg-surface-2 px-3 py-2 text-sm">
				<Checkbox class="mt-0.5" checked={experimentId === filterExperiment} onCheckedChange={(v) => linkExperiment(!!v)} />
				<span>
					<span class="font-medium">Add the whole experiment</span>
					<span class="block text-xs text-muted-foreground">
						Every current app of “{experiments.find((x) => x.id === filterExperiment)?.name ?? 'this experiment'}” joins the pool, and pairing and condition contrasts can use its baseline. To evaluate part of it, pick apps instead (or remove apps from the pool before opening).
					</span>
					{#if experimentId && experimentId !== filterExperiment}
						<span class="block text-xs text-warning-fg">Linking replaces the current link to “{linkedName}”.</span>
					{/if}
				</span>
			</label>
		{/if}

		<TableToolbar selectedCount={0} noun="app">
			{#snippet summary()}
				{resultsText || (loading ? 'Loading…' : 'No apps')}
			{/snippet}
		</TableToolbar>

		{#if error}
			<div class="p-3"><ErrorAlert message={error} onretry={() => fetchJobs()} /></div>
		{:else if loading && jobs.length === 0}
			<LoadingState label="loading generated apps" />
		{:else if jobs.length === 0}
			{#if anyFilter}
				<EmptyState compact icon={AppWindow} title="No generated apps match these filters">
					{#snippet action()}<Button variant="outline" size="sm" onclick={clearFilters}>Clear filters</Button>{/snippet}
				</EmptyState>
			{:else}
				<EmptyState
					compact
					icon={AppWindow}
					title="No completed generations yet"
					description="Apps appear here once a generation job or experiment run has completed."
				/>
			{/if}
		{:else}
			<div class="hidden md:block">
				<div class="table-frame transition-opacity {loading ? 'opacity-60' : ''}" {@attach tableFrame}>
					<table>
						<thead>
							<tr>
								<th class="w-8">
									<Checkbox
										checked={pageAllSelected}
										indeterminate={pageSomeSelected}
										disabled={selectable.length === 0}
										onCheckedChange={(v) => togglePage(!!v)}
										aria-label="Select every app on this page"
									/>
								</th>
								<th>App</th>
								<th>Model</th>
								<th>Condition</th>
								<th>Functional</th>
								<th>Running</th>
								<th>Created</th>
							</tr>
						</thead>
						<tbody>
							{#each jobs as job (job.id)}
								{@const state = rowState(job)}
								<tr data-selected={state === 'selectable' ? selectedSet.has(job.id) : undefined} class={state === 'selectable' ? '' : 'opacity-75'}>
									<td class="w-8">{@render selectBox(job)}</td>
									<td class="max-w-0 min-w-[12rem] w-[26%]">
										<span class="block truncate font-medium" title={job.app_name}>{job.app_name || job.app_slug || 'Untitled app'}</span>
										<span class="cell-sub block truncate text-xs text-muted-foreground">
											{job.app_slug}{state === 'pool' ? ' · in the pool' : state === 'experiment' ? ' · with the experiment' : ''}
										</span>
									</td>
									<td class="max-w-0 min-w-[10rem]">
										<span class="block truncate" title={job.model_name}>{shortModelName(job.model_name) || job.model_id}</span>
										{#if modelNamePrefix(job.model_name)}<span class="cell-sub block truncate text-xs text-muted-foreground">{modelNamePrefix(job.model_name)}</span>{/if}
									</td>
									<td class="max-w-0 min-w-[10rem]">
										{#if job.condition}
											<span class="block truncate" title={job.condition.label}>{job.condition.label}</span>
										{:else}
											<span class="text-muted-foreground">—</span>
										{/if}
										<span class="cell-sub block truncate text-xs text-muted-foreground">
											{job.experiment?.name ?? 'No experiment'}{repeatSuffix(job.repeat_index)}
										</span>
									</td>
									<td>{@render functionalCell(job.functional)}</td>
									<td>
										{#if job.running}<StatusBadge status="running" tone="success" label="Running" />{:else}<span class="text-xs text-muted-foreground">—</span>{/if}
									</td>
									<td class="whitespace-nowrap text-xs text-muted-foreground">{formatDate(job.created_at)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</div>

			<!-- Phones -->
			<ul class="data-list rounded-none border-0 shadow-none md:hidden">
				{#each jobs as job (job.id)}
					<li>
						<div class="data-list-row flex items-start gap-3" data-active={selectedSet.has(job.id)}>
							<span class="mt-1">{@render selectBox(job)}</span>
							<div class="min-w-0 flex-1 space-y-1">
								<span class="block truncate text-sm font-medium">{job.app_name || job.app_slug}</span>
								<span class="block truncate text-xs text-muted-foreground">
									{shortModelName(job.model_name) || job.model_id}{job.condition ? ` · ${job.condition.label}` : ''}
								</span>
								<span class="flex flex-wrap items-center gap-2">
									{@render functionalCell(job.functional)}
									{#if job.running}<StatusBadge status="running" tone="success" label="Running" />{/if}
								</span>
							</div>
						</div>
					</li>
				{/each}
			</ul>
			<PaginationBar page={pageNo} {pages} onGoToPage={(p) => (pageNo = p)} class="border-t border-border" scrollToCard />
		{/if}
	</Card.Root>
</div>
