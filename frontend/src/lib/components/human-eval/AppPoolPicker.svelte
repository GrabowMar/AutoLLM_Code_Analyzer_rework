<script lang="ts">
	/**
	 * AppPoolPicker — choose the generated apps of a task's pool: filter the
	 * candidate runs (completed app runs) by study / search, tick individual
	 * apps, or link a whole study (the current run of every main-stage cell
	 * joins the pool and the task can use the study's baseline condition).
	 *
	 *   <AppPoolPicker bind:selected={runIds} bind:studyId excludeRunIds={poolRunIds} />
	 *
	 * Props:
	 * - `selected` (bindable): individually picked GenerationRun ids (replaced, never mutated).
	 * - `studyId` (bindable): the linked study, or null. Its current apps are
	 *   added by the server; rows of it show as included.
	 * - `excludeRunIds`: runs already in the pool (shown ticked and disabled).
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
	import { listStudies } from '$lib/api/v3/studies';
	import type { StudyListOut } from '$lib/api/v3/types';
	import {
		functionalSourceLabel,
		functionalStatusLabel,
		listCandidateRuns,
		studyOptionDescription,
		type CandidateRun,
		type FunctionalSummary
	} from '$lib/api/human-eval';
	import { formatDate } from '$lib/utils/formatters';
	import { repeatSuffix } from './format';

	interface Props {
		selected: string[];
		studyId?: string | null;
		excludeRunIds?: string[];
	}

	let { selected = $bindable([]), studyId = $bindable(null), excludeRunIds = [] }: Props = $props();

	const PER_PAGE = 25;

	// ── studies ───────────────────────────────────────────────────────
	let studies = $state<StudyListOut[]>([]);
	let studiesError = $state('');
	let linkedCount = $state<number | null>(null);
	let linkedCountFor = '';

	onMount(async () => {
		try {
			// Newest first; drafts have no runs yet.
			const res = await listStudies({ page_size: 100 });
			studies = res.items.filter((x) => x.status !== 'draft');
		} catch (e) {
			studiesError = formatApiError(e, 'Could not load studies.');
		}
	});

	const studyItems = $derived(
		studies.map((x) => ({ value: x.id, label: x.name, description: studyOptionDescription(x) }))
	);
	const linkedName = $derived(studyId ? (studies.find((x) => x.id === studyId)?.name ?? 'Linked study') : '');

	// ── filters ───────────────────────────────────────────────────────
	let filterStudy = $state(untrack(() => studyId ?? ''));
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

	function setFilterStudy(value: string) {
		filterStudy = value;
		pageNo = 1;
	}

	// ── candidates ────────────────────────────────────────────────────
	let runs = $state<CandidateRun[]>([]);
	let total = $state(0);
	let pages = $state(1);
	let loading = $state(true);
	let error = $state('');
	let ctl: AbortController | null = null;

	async function fetchRuns() {
		ctl?.abort();
		const mine = new AbortController();
		ctl = mine;
		loading = true;
		try {
			const res = await listCandidateRuns(
				{
					study_id: filterStudy || undefined,
					search: search || undefined,
					current_only: currentOnly,
					page: pageNo,
					per_page: PER_PAGE,
				},
				{ signal: mine.signal }
			);
			runs = res.items;
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

	const queryKey = $derived(JSON.stringify([filterStudy, search, currentOnly, pageNo]));
	$effect(() => {
		void queryKey;
		untrack(() => void fetchRuns());
	});

	// Size of the linked study's current pool contribution.
	$effect(() => {
		const id = studyId;
		untrack(() => {
			if (!id) {
				linkedCount = null;
				linkedCountFor = '';
				return;
			}
			if (id === linkedCountFor) return;
			linkedCountFor = id;
			linkedCount = null;
			listCandidateRuns({ study_id: id, current_only: true, per_page: 1 })
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
	const excluded = $derived(new Set(excludeRunIds));

	/** Rows the server adds anyway through the linked study (current run per cell). */
	function viaStudy(run: CandidateRun): boolean {
		return !!studyId && currentOnly && run.study?.id === studyId;
	}

	function rowState(run: CandidateRun): 'pool' | 'study' | 'selectable' {
		if (excluded.has(run.id)) return 'pool';
		if (viaStudy(run)) return 'study';
		return 'selectable';
	}

	const selectable = $derived(runs.filter((j) => rowState(j) === 'selectable'));
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

	function linkStudy(on: boolean) {
		studyId = on && filterStudy ? filterStudy : null;
	}

	const anyFilter = $derived(!!(filterStudy || search || !currentOnly));
	function clearFilters() {
		filterStudy = '';
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
			<StatusBadge status={f.status} label={functionalStatusLabel(f.status)} />
			{#if passRate(f)}<span class="font-mono text-xs tabular-nums">{passRate(f)}</span>{/if}
		</span>
		<span class="cell-sub block text-[11px] text-muted-foreground">
			{functionalSourceLabel(f.source)}
			{#if f.deployed === false}<span class="text-warning-fg">{f.source ? ' · ' : ''}not deployed</span>{:else if f.deployed === true}{f.source ? ' · ' : ''}deployed{/if}
		</span>
	{:else}
		<span class="text-xs text-muted-foreground">Not measured</span>
	{/if}
{/snippet}

{#snippet selectBox(run: CandidateRun)}
	{@const state = rowState(run)}
	{#if state === 'selectable'}
		<Checkbox
			checked={selectedSet.has(run.id)}
			onCheckedChange={(v) => toggle(run.id, !!v)}
			aria-label="Select {run.app_name} by {run.model_name}"
		/>
	{:else}
		<Checkbox
			checked
			disabled
			aria-label="{run.app_name} by {run.model_name}: {state === 'pool' ? 'already in the pool' : 'included with the linked study'}"
		/>
	{/if}
{/snippet}

<div class="space-y-3">
	{#if studyId || selected.length > 0}
		<div class="flex flex-wrap items-center gap-2 text-xs">
			{#if studyId}
				<span class="inline-flex items-center gap-1.5 rounded-full border border-primary/30 bg-primary/10 px-2.5 py-1 text-primary">
					<Link2 class="size-3.5" aria-hidden="true" />
					{linkedName}{linkedCount != null ? ` · ${linkedCount} current app${linkedCount === 1 ? '' : 's'}` : ''}
					<button type="button" class="cursor-pointer rounded-full hover:text-foreground" onclick={() => (studyId = null)} aria-label="Unlink {linkedName}">
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
					value={filterStudy}
					placeholder="All my generated apps"
					clearLabel="All my generated apps"
					items={studyItems}
					onValueChange={setFilterStudy}
					aria-label="Study"
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
			<label class="flex items-center gap-2 px-1 text-sm" title="Only the current run of each study cell (condition × task × repeat): its first usable attempt, else its latest">
				<Checkbox
					checked={currentOnly}
					onCheckedChange={(v) => {
						currentOnly = !!v;
						pageNo = 1;
					}}
				/>
				Current run per cell only
			</label>
		</div>
		{#if studiesError}
			<div class="border-b border-border p-2"><ErrorAlert message={studiesError} /></div>
		{/if}

		{#if filterStudy}
			<label class="flex items-start gap-2.5 border-b border-border bg-surface-2 px-3 py-2 text-sm">
				<Checkbox class="mt-0.5" checked={studyId === filterStudy} onCheckedChange={(v) => linkStudy(!!v)} />
				<span>
					<span class="font-medium">Add the whole study</span>
					<span class="block text-xs text-muted-foreground">
						The current run of every main-stage cell of “{studies.find((x) => x.id === filterStudy)?.name ?? 'this study'}” joins the pool (pilot and beyond-plan runs never do), and pairing and condition contrasts can use its baseline condition. To evaluate part of it, pick apps instead (or remove apps from the pool before opening).
					</span>
					{#if studyId && studyId !== filterStudy}
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
			<div class="p-3"><ErrorAlert message={error} onretry={() => fetchRuns()} /></div>
		{:else if loading && runs.length === 0}
			<LoadingState label="loading generated apps" />
		{:else if runs.length === 0}
			{#if anyFilter}
				<EmptyState compact icon={AppWindow} title="No generated apps match these filters">
					{#snippet action()}<Button variant="outline" size="sm" onclick={clearFilters}>Clear filters</Button>{/snippet}
				</EmptyState>
			{:else}
				<EmptyState
					compact
					icon={AppWindow}
					title="No completed app runs yet"
					description="Apps appear here once an agentic or slot run of a study (or an ad-hoc run) has completed."
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
							{#each runs as run (run.id)}
								{@const state = rowState(run)}
								<tr data-selected={state === 'selectable' ? selectedSet.has(run.id) : undefined} class={state === 'selectable' ? '' : 'opacity-75'}>
									<td class="w-8">{@render selectBox(run)}</td>
									<td class="max-w-0 min-w-[12rem] w-[26%]">
										<span class="block truncate font-medium" title={run.app_name}>{run.app_name || run.app_slug || 'Untitled app'}</span>
										<span class="cell-sub block truncate text-xs text-muted-foreground">
											{run.app_slug}{state === 'pool' ? ' · in the pool' : state === 'study' ? ' · with the study' : ''}
										</span>
									</td>
									<td class="max-w-0 min-w-[10rem]">
										<span class="block truncate" title={run.model_name}>{shortModelName(run.model_name) || run.model_id}</span>
										{#if modelNamePrefix(run.model_name)}<span class="cell-sub block truncate text-xs text-muted-foreground">{modelNamePrefix(run.model_name)}</span>{/if}
									</td>
									<td class="max-w-0 min-w-[10rem]">
										{#if run.condition}
											<span class="block truncate" title={run.condition.label}>{run.condition.label}</span>
										{:else}
											<span class="text-muted-foreground">—</span>
										{/if}
										<span class="cell-sub block truncate text-xs text-muted-foreground">
											{run.study?.name ?? 'Ad-hoc run'}{repeatSuffix(run.repeat_index)}
										</span>
									</td>
									<td>{@render functionalCell(run.functional)}</td>
									<td>
										{#if run.running}<StatusBadge status="running" tone="success" label="Running" />{:else}<span class="text-xs text-muted-foreground">—</span>{/if}
									</td>
									<td class="whitespace-nowrap text-xs text-muted-foreground">{formatDate(run.created_at)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</div>

			<!-- Phones -->
			<ul class="data-list rounded-none border-0 shadow-none md:hidden">
				{#each runs as run (run.id)}
					<li>
						<div class="data-list-row flex items-start gap-3" data-active={selectedSet.has(run.id)}>
							<span class="mt-1">{@render selectBox(run)}</span>
							<div class="min-w-0 flex-1 space-y-1">
								<span class="block truncate text-sm font-medium">{run.app_name || run.app_slug}</span>
								<span class="block truncate text-xs text-muted-foreground">
									{shortModelName(run.model_name) || run.model_id}{run.condition ? ` · ${run.condition.label}` : ''}
								</span>
								<span class="flex flex-wrap items-center gap-2">
									{@render functionalCell(run.functional)}
									{#if run.running}<StatusBadge status="running" tone="success" label="Running" />{/if}
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
