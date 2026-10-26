<script lang="ts">
	/**
	 * JobPicker — multi-select of completed generation jobs (applications)
	 * with server-side search and pagination. `selected` (job ids) is
	 * replaced, never mutated; selections survive paging and searching.
	 *
	 * Props: `selected` (bindable string[]), `max` (default 5000),
	 * `disabled`, `onchange(selected)`.
	 */
	import { onMount } from 'svelte';
	import { getGenerationJobs, type GenerationJobList } from '$lib/api/generation';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import Search from '@lucide/svelte/icons/search';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import { fmtDate, shortHash } from './format';

	interface Props {
		selected?: string[];
		max?: number;
		disabled?: boolean;
		onchange?: (selected: string[]) => void;
	}

	let { selected = $bindable([]), max = 5000, disabled = false, onchange }: Props = $props();

	const PER_PAGE = 20;

	let search = $state('');
	let page = $state(1);
	let items = $state<GenerationJobList[]>([]);
	let total = $state(0);
	let pages = $state(1);
	let loading = $state(true);
	let error = $state('');
	let controller: AbortController | null = null;
	let timer: ReturnType<typeof setTimeout> | null = null;

	const selectedSet = $derived(new Set(selected));
	const pageIds = $derived(items.map((j) => j.id));
	const pageAllSelected = $derived(
		pageIds.length > 0 && pageIds.every((id) => selectedSet.has(id))
	);
	const pageSomeSelected = $derived(pageIds.some((id) => selectedSet.has(id)));

	function set(next: string[]) {
		selected = next.slice(0, max);
		onchange?.(selected);
	}

	function toggle(id: string, on: boolean) {
		set(on ? [...selected.filter((x) => x !== id), id] : selected.filter((x) => x !== id));
	}

	function togglePage(on: boolean) {
		if (on) set([...selected, ...pageIds.filter((id) => !selectedSet.has(id))]);
		else set(selected.filter((id) => !pageIds.includes(id)));
	}

	async function load() {
		controller?.abort();
		const ctrl = new AbortController();
		controller = ctrl;
		loading = true;
		error = '';
		try {
			const res = await getGenerationJobs({
				status: 'completed',
				search: search.trim() || undefined,
				page,
				per_page: PER_PAGE
			});
			if (ctrl.signal.aborted) return;
			items = res.items;
			total = res.total;
			pages = Math.max(1, res.pages);
		} catch (e) {
			if (isAbortError(e) || ctrl.signal.aborted) return;
			error = formatApiError(e, 'Could not load applications.');
		} finally {
			if (controller === ctrl) loading = false;
		}
	}

	function onSearch() {
		if (timer) clearTimeout(timer);
		timer = setTimeout(() => {
			page = 1;
			void load();
		}, 300);
	}

	function goTo(p: number) {
		page = p;
		void load();
	}

	onMount(() => {
		void load();
		return () => {
			controller?.abort();
			if (timer) clearTimeout(timer);
		};
	});

	function appOf(job: GenerationJobList): string {
		return job.app_slug ?? job.template_name ?? '—';
	}
</script>

<div class="space-y-3">
	<div class="flex flex-wrap items-center gap-2">
		<div class="relative min-w-0 flex-1 sm:max-w-sm">
			<Search
				class="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
				aria-hidden="true"
			/>
			<Input
				type="search"
				class="pl-8"
				placeholder="Search model, app, stack or full job id…"
				aria-label="Search completed applications"
				bind:value={search}
				oninput={onSearch}
				{disabled}
			/>
		</div>
		<span class="text-xs text-muted-foreground tabular-nums">
			<span class="font-medium text-foreground">{selected.length}</span>
			selected{#if selected.length >= max}{' '}(maximum){/if}
		</span>
		{#if selected.length > 0 && !disabled}
			<Button variant="ghost" size="sm" onclick={() => set([])}>Clear selection</Button>
		{/if}
		{#if loading}
			<LoaderCircle
				class="h-4 w-4 animate-spin text-muted-foreground motion-reduce:animate-none"
				aria-label="Loading"
			/>
		{/if}
	</div>

	{#if error}
		<ErrorAlert message={error} onretry={load} />
	{:else if !loading && items.length === 0}
		<EmptyState
			compact
			title="No completed applications"
			description={search
				? 'Nothing matches this search.'
				: 'Generate apps first; only completed jobs can be analysed.'}
		/>
	{:else}
		<div class="table-scroll-wrapper rounded-md border border-border">
			<table>
				<thead>
					<tr>
						<th scope="col" class="w-10">
							<Checkbox
								checked={pageAllSelected}
								indeterminate={!pageAllSelected && pageSomeSelected}
								onCheckedChange={(on) => togglePage(on === true)}
								aria-label="Select every application on this page"
								{disabled}
							/>
						</th>
						<th scope="col">Job</th>
						<th scope="col">Model</th>
						<th scope="col">App</th>
						<th scope="col" class="hide-mobile">Mode / harness</th>
						<th scope="col" class="hide-mobile">Created</th>
					</tr>
				</thead>
				<tbody>
					{#each items as job (job.id)}
						<tr>
							<td>
								<Checkbox
									checked={selectedSet.has(job.id)}
									onCheckedChange={(on) => toggle(job.id, on === true)}
									aria-label="Select application {shortHash(job.id, 8)}"
									{disabled}
								/>
							</td>
							<td class="font-mono text-xs">{shortHash(job.id, 8)}</td>
							<td class="max-w-[14rem]"
								><span class="block truncate" title={job.model_id_str ?? ''}
									>{job.model_name ?? job.model_id_str ?? '—'}</span
								></td
							>
							<td>{appOf(job)}</td>
							<td class="hide-mobile text-muted-foreground">{job.harness || job.mode}</td>
							<td class="hide-mobile text-muted-foreground">{fmtDate(job.created_at)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
			<PaginationBar
				resultsText="{total} completed application{total === 1 ? '' : 's'}"
				{page}
				{pages}
				onGoToPage={goTo}
			/>
		</div>
	{/if}
</div>
