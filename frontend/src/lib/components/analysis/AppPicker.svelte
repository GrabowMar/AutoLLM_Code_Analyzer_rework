<script lang="ts" module>
	/** A generated app picked for analysis (what the summary and chips need). */
	export interface PickedApp {
		id: string;
		app: string;
		model: string;
		stack: string;
		created_at: string | null;
	}

	export const MAX_APPS = 100;

	export function toPicked(j: {
		id: string;
		template_name?: string | null;
		model_name?: string | null;
		stack_slug?: string | null;
		created_at?: string | null;
	}): PickedApp {
		return {
			id: j.id,
			app: j.template_name ?? '',
			model: j.model_name ?? '',
			stack: j.stack_slug ?? '',
			created_at: j.created_at ?? null,
		};
	}
</script>

<script lang="ts">
	/**
	 * AppPicker — choose completed generated apps to analyze: server-side search
	 * (app, model, stack, ID), "app running" filter (dynamic tools need it),
	 * paging, select page / clear, and removable chips for the selection (kept
	 * across pages and searches). Each row shows whether the app's container is
	 * running.
	 *
	 * Props: `selected` (bindable `PickedApp[]`, replaced on change), `running`
	 * (bindable output: job ids whose container runs, for the summary).
	 */
	import { onMount, untrack } from 'svelte';
	import { Input } from '$lib/components/ui/input';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Segmented } from '$lib/components/ui/segmented';
	import { Button } from '$lib/components/ui/button';
	import Search from '@lucide/svelte/icons/search';
	import X from '@lucide/svelte/icons/x';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import { modelNamePrefix, shortModelName, tableFrame } from '$lib/components/table';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { getGenerationJobs, type GenerationJobList } from '$lib/api/generation';
	import { getContainers } from '$lib/api/runtime';
	import { formatDate } from '$lib/utils/formatters';
	import { cn } from '$lib/utils';

	interface Props {
		selected: PickedApp[];
		running?: Set<string>;
	}

	let { selected = $bindable(), running = $bindable(new Set()) }: Props = $props();

	const PER_PAGE = 10;
	let items = $state<GenerationJobList[]>([]);
	let total = $state(0);
	let pages = $state(1);
	let pageNo = $state(1);
	let loading = $state(true);
	let error = $state('');
	let search = $state('');
	let searchText = $state('');
	let scope = $state<'all' | 'running'>('all');
	let ctl: AbortController | null = null;

	const selectedIds = $derived(new Set(selected.map((a) => a.id)));

	async function load() {
		ctl?.abort();
		const mine = new AbortController();
		ctl = mine;
		loading = true;
		try {
			const data = await getGenerationJobs(
				{
					status: 'completed',
					page: pageNo,
					per_page: PER_PAGE,
					search: search || undefined,
					container_status: scope === 'running' ? 'running' : undefined,
					sort_by: 'created_desc',
				},
				{ signal: mine.signal }
			);
			items = data.items;
			total = data.total;
			pages = data.pages;
			pageNo = data.page;
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

	const queryKey = $derived(`${pageNo}|${search}|${scope}`);
	$effect(() => {
		void queryKey;
		untrack(() => void load());
	});
	onMount(() => void loadRunning());

	let timer: ReturnType<typeof setTimeout> | undefined;
	function onSearch(value: string) {
		searchText = value;
		clearTimeout(timer);
		timer = setTimeout(() => {
			search = value.trim();
			pageNo = 1;
		}, 300);
	}

	function toggle(job: GenerationJobList, on: boolean) {
		if (on && !selectedIds.has(job.id) && selected.length < MAX_APPS) selected = [...selected, toPicked(job)];
		else if (!on) selected = selected.filter((a) => a.id !== job.id);
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

	let showAllChips = $state(false);
	const chips = $derived(showAllChips ? selected : selected.slice(0, 12));
</script>

<div class="space-y-3">
	<div class="flex flex-wrap items-center gap-2">
		<div class="relative min-w-52 flex-1">
			<Search class="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
			<Input
				type="search"
				value={searchText}
				oninput={(e) => onSearch((e.currentTarget as HTMLInputElement).value)}
				placeholder="Search app, model, stack or job ID…"
				aria-label="Search generated apps"
				class="h-9 pl-8"
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
				{ value: 'all', label: 'All completed' },
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
			title={search || scope === 'running' ? 'No apps match' : 'No completed apps yet'}
			description={search || scope === 'running' ? undefined : 'Generate an app first, or paste code instead.'}
		>
			{#snippet action()}
				{#if search || scope === 'running'}
					<Button variant="outline" size="sm" onclick={() => { search = ''; searchText = ''; scope = 'all'; pageNo = 1; }}>Show all apps</Button>
				{:else}
					<Button variant="outline" size="sm" href="/sample-generator">Generate an app</Button>
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
							<th>Mode</th>
							<th>Created</th>
							<th title="Whether the app's container runs now (needed by dynamic tools)">App</th>
						</tr>
					</thead>
					<tbody>
						{#each items as job (job.id)}
							{@const on = selectedIds.has(job.id)}
							{@const provider = modelNamePrefix(job.model_name)}
							<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (the checkbox is the keyboard path) -->
							<tr class="row-link" data-selected={on} onclick={(e) => { if (!(e.target as HTMLElement).closest('button, a')) toggle(job, !on); }}>
								<td class="w-8"><Checkbox checked={on} onCheckedChange={(v) => toggle(job, !!v)} aria-label="Select {job.template_name ?? job.id}" disabled={!on && selected.length >= MAX_APPS} /></td>
								<td class="max-w-0 w-[30%]">
									<span class="block truncate font-medium">{job.template_name ?? job.stack_slug ?? '—'}</span>
									<span class="cell-sub block truncate font-mono text-[11px] text-muted-foreground">{job.stack_slug ?? ''} · {job.id.slice(0, 8)}</span>
								</td>
								<td class="max-w-0 w-[30%]">
									<span class="block truncate" title={job.model_name ?? ''}>{shortModelName(job.model_name) || '—'}</span>
									{#if provider}<span class="cell-sub block truncate text-[11px] text-muted-foreground">{provider}</span>{/if}
								</td>
								<td class="text-xs capitalize text-muted-foreground">{job.mode}</td>
								<td class="whitespace-nowrap text-xs text-muted-foreground">{formatDate(job.completed_at ?? job.created_at)}</td>
								<td class="whitespace-nowrap text-xs">
									{#if running.has(job.id)}
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
				resultsText="{total} completed app{total === 1 ? '' : 's'}{selected.length ? ` · ${selected.length} selected` : ''}"
				page={pageNo}
				{pages}
				onGoToPage={(p) => (pageNo = p)}
				class="border-t border-border"
			/>
		</div>
		{#if selected.length >= MAX_APPS}
			<p class="text-xs text-warning-fg">A batch analyzes at most {MAX_APPS} apps.</p>
		{/if}
	{/if}
</div>
