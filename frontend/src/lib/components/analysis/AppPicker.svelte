<script lang="ts" module>
	import type { RunListOut } from '$lib/api/v3/types';

	/** A generated app (GenerationRun) picked for analysis (what the summary and chips need). */
	export interface PickedApp {
		id: string;
		app: string;
		model: string;
		stack: string;
		created_at: string | null;
	}

	export const MAX_APPS = 100;

	/**
	 * A run row as the picker reads it. `task` / `task_title` are optional: the runs list may not
	 * carry them (then the row falls back to the stack and kind).
	 */
	export type PickerRun = RunListOut & { task?: string | null; task_title?: string | null };

	const KIND_LABELS: Record<string, string> = {
		app_agentic: 'Agentic app',
		slot_single_shot: 'Slot variant',
		slot_agent: 'Slot variant (agent)',
		reference: 'Reference',
	};

	export function kindLabel(kind: string): string {
		return KIND_LABELS[kind] ?? kind.replace(/_/g, ' ');
	}

	export function toPicked(run: PickerRun): PickedApp {
		const stack = run.labels?.stack_profile ?? '';
		return {
			id: run.id,
			app: run.task_title || run.task || run.labels?.task || '',
			model: run.model ?? run.labels?.model ?? '',
			stack,
			created_at: run.created_at ?? null,
		};
	}
</script>

<script lang="ts">
	/**
	 * AppPicker — choose generated apps (GenerationRuns with a collected tree) to analyze: filter by
	 * study and outcome (default: every usable run, i.e. tested or a measured build/start/output
	 * failure), or show only apps whose container runs (dynamic tools need it); paging, select
	 * page / clear, and removable chips for the selection (kept across pages and filters). Each
	 * row shows whether the app's container is running.
	 *
	 * Props: `selected` (bindable `PickedApp[]`, replaced on change), `running`
	 * (bindable output: run ids whose container runs, for the summary).
	 */
	import { onMount, untrack } from 'svelte';
	import * as Select from '$lib/components/ui/select';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Segmented } from '$lib/components/ui/segmented';
	import { Button } from '$lib/components/ui/button';
	import X from '@lucide/svelte/icons/x';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { modelNamePrefix, shortModelName, tableFrame } from '$lib/components/table';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { getSample, listSamples } from '$lib/api/v3/samples';
	import { listStudies } from '$lib/api/v3/studies';
	import type { RunListQuery, RunOut, StudyListOut } from '$lib/api/v3/types';
	import { getContainers } from '$lib/api/runtime';
	import { outcomeLabels, type RunOutcome } from '$lib/constants/colors';
	import { formatDate } from '$lib/utils/formatters';
	import { cn } from '$lib/utils';

	interface Props {
		selected: PickedApp[];
		running?: Set<string>;
	}

	let { selected = $bindable(), running = $bindable(new Set()) }: Props = $props();

	const PER_PAGE = 10;
	/** Runs shown in "App running" mode (one request per running container). */
	const MAX_RUNNING = 50;
	const ANALYZABLE: RunOutcome[] = ['tested', 'build_fail', 'start_fail', 'output_invalid'];

	let items = $state<PickerRun[]>([]);
	let total = $state(0);
	let pages = $state(1);
	let pageNo = $state(1);
	let loading = $state(true);
	let error = $state('');
	let study = $state('');
	/** '' = every usable run; else one outcome. */
	let outcome = $state<RunOutcome | ''>('');
	let scope = $state<'all' | 'running'>('all');
	let ctl: AbortController | null = null;

	let studies = $state<StudyListOut[]>([]);
	const studyItems = $derived(
		studies.map((x) => ({ value: x.id, label: x.name, description: `${x.status} · ${x.mode} mode · ${x.cells_done}/${x.cells_total} cells` }))
	);
	const outcomeItems = ANALYZABLE.map((o) => ({ value: o, label: outcomeLabels[o] }));

	const selectedIds = $derived(new Set(selected.map((a) => a.id)));

	/** Running containers' runs, fetched one by one (few run at a time), newest first. */
	let runningRows: (PickerRun & Pick<RunOut, 'study'>)[] = [];
	let runningRowsFor = '';

	async function loadRunningRows(signal: AbortSignal): Promise<(PickerRun & Pick<RunOut, 'study'>)[]> {
		const key = [...running].sort().join(',');
		if (key === runningRowsFor) return runningRows;
		const ids = [...running].slice(0, MAX_RUNNING);
		const found = await Promise.allSettled(ids.map((id) => getSample(id, { signal })));
		runningRows = found
			.flatMap((r) => (r.status === 'fulfilled' ? [r.value] : []))
			.sort((x, y) => (y.created_at ?? '').localeCompare(x.created_at ?? ''));
		runningRowsFor = key;
		return runningRows;
	}

	async function load() {
		ctl?.abort();
		const mine = new AbortController();
		ctl = mine;
		loading = true;
		try {
			if (scope === 'running') {
				await loadRunning();
				const rows = (await loadRunningRows(mine.signal)).filter(
					(r) => (!study || r.study?.study_id === study) && (!outcome || r.outcome === outcome)
				);
				total = rows.length;
				pages = Math.max(1, Math.ceil(rows.length / PER_PAGE));
				pageNo = Math.min(pageNo, pages);
				items = rows.slice((pageNo - 1) * PER_PAGE, pageNo * PER_PAGE);
			} else {
				const query: RunListQuery = { page: pageNo, page_size: PER_PAGE };
				if (study) query.study = study;
				if (outcome) query.outcome = outcome;
				else query.usable = true;
				const data = await listSamples(query, { signal: mine.signal });
				items = data.items as PickerRun[];
				total = data.total;
				pages = Math.max(1, Math.ceil(data.total / data.page_size));
				pageNo = data.page;
			}
			error = '';
		} catch (e) {
			if (isAbortError(e) && mine.signal.aborted) return;
			error = formatApiError(e, 'Could not load generated apps.');
		} finally {
			if (ctl === mine) loading = false;
		}
	}

	async function loadRunning() {
		try {
			const res = await getContainers({ status: 'running', per_page: 100 });
			running = new Set((res.containers ?? []).map((c) => c.generation_job_id).filter((id): id is string => !!id));
		} catch {
			// Only a hint: rows just don't show the running dot.
		}
	}

	const queryKey = $derived(`${pageNo}|${study}|${outcome}|${scope}`);
	$effect(() => {
		void queryKey;
		untrack(() => void load());
	});
	onMount(() => {
		void loadRunning();
		listStudies({ page_size: 100 })
			.then((res) => (studies = res.items.filter((x) => x.status !== 'draft')))
			.catch(() => {
				// The study filter is optional: without it every run is listed.
			});
	});

	function toggle(run: PickerRun, on: boolean) {
		if (on && !selectedIds.has(run.id) && selected.length < MAX_APPS) selected = [...selected, toPicked(run)];
		else if (!on) selected = selected.filter((a) => a.id !== run.id);
	}

	const pageAllSelected = $derived(items.length > 0 && items.every((j) => selectedIds.has(j.id)));
	const pageSomeSelected = $derived(items.some((j) => selectedIds.has(j.id)) && !pageAllSelected);

	function togglePage(on: boolean) {
		if (on) {
			const add = items.filter((j) => !selectedIds.has(j.id)).map(toPicked);
			selected = [...selected, ...add].slice(0, MAX_APPS);
		} else {
			const ids = new Set(items.map((j) => j.id));
			selected = selected.filter((a) => !ids.has(a.id));
		}
	}

	const anyFilter = $derived(!!(study || outcome || scope === 'running'));
	function clearFilters() {
		study = '';
		outcome = '';
		scope = 'all';
		pageNo = 1;
	}

	let showAllChips = $state(false);
	const chips = $derived(showAllChips ? selected : selected.slice(0, 12));
</script>

<div class="space-y-3">
	<div class="flex flex-wrap items-center gap-2" role="search" aria-label="Filter generated apps">
		<div class="w-full sm:w-64">
			<Select.Simple
				size="sm"
				class="h-9"
				value={study}
				placeholder="Every experiment and ad-hoc run"
				clearLabel="Every experiment and ad-hoc run"
				items={studyItems}
				onValueChange={(v) => {
					study = v;
					pageNo = 1;
				}}
				aria-label="Experiment"
			/>
		</div>
		<div class="w-full sm:w-52">
			<Select.Simple
				size="sm"
				class="h-9"
				value={outcome}
				placeholder="Every usable outcome"
				clearLabel="Every usable outcome"
				items={outcomeItems}
				onValueChange={(v) => {
					outcome = v as RunOutcome | '';
					pageNo = 1;
				}}
				aria-label="Outcome"
			/>
		</div>
		<Segmented
			size="sm"
			aria-label="Which apps"
			value={scope}
			onValueChange={(v) => {
				scope = v as typeof scope;
				pageNo = 1;
			}}
			items={[
				{ value: 'all', label: 'All apps' },
				{ value: 'running', label: 'App running', title: 'Apps whose container runs now: dynamic tools (ZAP, Nuclei, Wapiti) can scan them' },
			]}
		/>
	</div>

	{#if selected.length > 0}
		<div class="flex flex-wrap items-center gap-1.5" aria-label="Selected apps">
			{#each chips as app (app.id)}
				<span class="inline-flex max-w-full items-center gap-1 rounded-full border border-primary/30 bg-primary/10 py-0.5 pr-1 pl-2 text-xs text-primary">
					<span class="truncate" title="{app.app || app.stack} · {app.model}">{app.app || app.stack || app.id.slice(0, 8)} · {shortModelName(app.model)}</span>
					<button type="button" class="cursor-pointer rounded-full p-0.5 hover:bg-primary/15" aria-label="Remove {app.app || app.id}" onclick={() => (selected = selected.filter((a) => a.id !== app.id))}>
						<X class="size-3" />
					</button>
				</span>
			{/each}
			{#if selected.length > 12}
				<button type="button" class="cursor-pointer text-xs text-muted-foreground hover:text-foreground" onclick={() => (showAllChips = !showAllChips)}>
					{showAllChips ? 'Show fewer' : `+${selected.length - 12} more`}
				</button>
			{/if}
			<button type="button" class="cursor-pointer text-xs text-muted-foreground hover:text-foreground" onclick={() => (selected = [])}>Clear</button>
		</div>
	{/if}

	{#if error}
		<ErrorAlert message={error} onretry={load} />
	{:else if loading && items.length === 0}
		<LoadingState label="loading generated apps" />
	{:else if items.length === 0}
		<EmptyState
			compact
			icon={AppWindow}
			title={anyFilter ? 'No apps match' : 'No generated apps yet'}
			description={anyFilter ? undefined : 'Run an experiment (or generate a single app) first, or paste code instead.'}
		>
			{#snippet action()}
				{#if anyFilter}
					<Button variant="outline" size="sm" onclick={clearFilters}>Show all apps</Button>
				{:else}
					<Button variant="outline" size="sm" href="/experiments/new">New experiment</Button>
				{/if}
			{/snippet}
		</EmptyState>
	{:else}
		<div class="overflow-clip rounded-md border border-border">
			<div class={cn('table-frame transition-opacity', loading && 'opacity-60')} {@attach tableFrame}>
				<table>
					<thead>
						<tr>
							<th class="w-8"><Checkbox checked={pageAllSelected} indeterminate={pageSomeSelected} onCheckedChange={(v) => togglePage(!!v)} aria-label="Select all apps on this page" /></th>
							<th>App</th>
							<th>Model</th>
							<th>Outcome</th>
							<th>Created</th>
							<th title="Whether the app's container runs now (needed by dynamic tools)">App</th>
						</tr>
					</thead>
					<tbody>
						{#each items as run (run.id)}
							{@const on = selectedIds.has(run.id)}
							{@const picked = toPicked(run)}
							{@const modelName = run.model ?? run.labels?.model ?? ''}
							{@const provider = modelNamePrefix(modelName)}
							<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (the checkbox is the keyboard path) -->
							<tr class="row-link" data-selected={on} onclick={(e) => { if (!(e.target as HTMLElement).closest('button, a')) toggle(run, !on); }}>
								<td class="w-8"><Checkbox checked={on} onCheckedChange={(v) => toggle(run, !!v)} aria-label="Select {picked.app || picked.stack || run.id}" disabled={!on && selected.length >= MAX_APPS} /></td>
								<td class="max-w-0 w-[30%]">
									<span class="block truncate font-medium">{picked.app || picked.stack || kindLabel(run.kind)}</span>
									<span class="cell-sub block truncate font-mono text-[11px] text-muted-foreground">{[kindLabel(run.kind), picked.app ? picked.stack : '', run.id.slice(0, 8)].filter(Boolean).join(' · ')}</span>
								</td>
								<td class="max-w-0 w-[30%]">
									<span class="block truncate" title={modelName}>{shortModelName(modelName) || '—'}</span>
									{#if provider}<span class="cell-sub block truncate text-[11px] text-muted-foreground">{provider}</span>{/if}
								</td>
								<td class="text-xs">
									{#if run.outcome}<StatusBadge status={run.outcome} label={outcomeLabels[run.outcome as RunOutcome] ?? run.outcome} />{:else}<span class="text-muted-foreground">{run.status}</span>{/if}
								</td>
								<td class="whitespace-nowrap text-xs text-muted-foreground">{formatDate(run.created_at)}</td>
								<td class="whitespace-nowrap text-xs">
									{#if running.has(run.id)}
										<span class="inline-flex items-center gap-1 text-success-fg"><span class="status-dot status-dot-live h-1.5 w-1.5" aria-hidden="true"></span>running</span>
									{:else}
										<span class="text-muted-foreground">stopped</span>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<PaginationBar
				resultsText="{total} app{total === 1 ? '' : 's'}{selected.length ? ` · ${selected.length} selected` : ''}"
				page={pageNo}
				{pages}
				onGoToPage={(p) => (pageNo = p)}
				class="border-t border-border"
			/>
		</div>
		{#if selected.length >= MAX_APPS}
			<p class="text-xs text-warning-fg">One launch analyzes at most {MAX_APPS} apps.</p>
		{/if}
	{/if}
</div>
