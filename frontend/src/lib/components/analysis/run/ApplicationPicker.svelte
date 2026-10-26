<script lang="ts">
	/**
	 * ApplicationPicker — choose one generated application (a completed
	 * generation job) to analyse: server-side search (model, app, stack or a
	 * job id) with pagination, newest first.
	 *
	 * Props:
	 * - `selectedId` (bindable): the job id ("" = none). A preset id (e.g.
	 *   from `?generation_job_id=`) is looked up and shown even when the job
	 *   is not completed, with a note that it cannot be analysed.
	 * - `selected` (bindable, output): the selected job's list row.
	 * - `onchange()`: after every user change.
	 */
	import { untrack } from 'svelte';
	import {
		getGenerationJobs,
		type GenerationJobList,
		type PaginatedJobs
	} from '$lib/api/generation';
	import { formatApiError } from '$lib/api/core';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { cn } from '$lib/utils';
	import { fmtAgo, fmtRate } from '../run-utils';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Search from '@lucide/svelte/icons/search';

	interface Props {
		selectedId?: string;
		selected?: GenerationJobList | null;
		onchange?: () => void;
	}

	let { selectedId = $bindable(''), selected = $bindable(null), onchange }: Props = $props();

	const PER_PAGE = 8;

	let search = $state('');
	let query = $state('');
	let pageNo = $state(1);
	let results = $state<PaginatedJobs | null>(null);
	let loading = $state(false);
	let error = $state('');
	let browsing = $state(untrack(() => !selectedId));
	let seq = 0;
	let timer: ReturnType<typeof setTimeout> | undefined;

	async function load() {
		const mine = ++seq;
		loading = true;
		try {
			const res = await getGenerationJobs({
				status: 'completed',
				search: query || undefined,
				page: pageNo,
				per_page: PER_PAGE,
				sort_by: 'created_desc'
			});
			if (mine !== seq) return;
			results = res;
			error = '';
		} catch (e) {
			if (mine !== seq) return;
			error = formatApiError(e, 'Could not load applications.');
		} finally {
			if (mine === seq) loading = false;
		}
	}

	$effect(() => {
		if (!browsing) return;
		void query;
		void pageNo;
		untrack(() => void load());
	});

	function onSearch(value: string) {
		search = value;
		clearTimeout(timer);
		timer = setTimeout(() => {
			query = value.trim();
			pageNo = 1;
		}, 300);
	}

	// Resolve a preset id (URL) into its list row once.
	let resolving = $state(false);
	let resolveError = $state('');
	let resolvedFor = '';
	$effect(() => {
		const id = selectedId;
		if (!id || selected?.id === id || resolvedFor === id) return;
		resolvedFor = id;
		untrack(async () => {
			resolving = true;
			resolveError = '';
			try {
				const res = await getGenerationJobs({ search: id, per_page: 1 });
				if (selectedId !== id) return;
				const job = res.items.find((j) => j.id === id) ?? null;
				selected = job;
				if (!job) {
					resolveError = 'This generation job was not found among your applications.';
					browsing = true;
				}
			} catch (e) {
				resolveError = formatApiError(e, 'Could not load this application.');
				browsing = true;
			} finally {
				resolving = false;
			}
		});
	});

	function choose(job: GenerationJobList) {
		selectedId = job.id;
		selected = job;
		browsing = false;
		resolveError = '';
		onchange?.();
	}

	function change() {
		browsing = true;
	}

	function appName(job: GenerationJobList): string {
		return job.template_name || job.app_slug || 'Custom app';
	}

	function mode(job: GenerationJobList): string {
		return [job.mode, job.harness].filter(Boolean).join(' · ');
	}
</script>

{#snippet jobSummary(job: GenerationJobList)}
	<span class="flex min-w-0 flex-col gap-0.5">
		<span class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
			<span class="truncate text-sm font-medium"
				>{job.model_name || job.model_id_str || 'Unknown model'}</span
			>
			<span class="text-xs text-muted-foreground">{appName(job)}</span>
		</span>
		<span class="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted-foreground">
			<span>{mode(job)}</span>
			{#if job.stack_slug}<span>{job.stack_slug}</span>{/if}
			{#if job.repeat_index != null}<span>r{job.repeat_index}</span>{/if}
			<span class="font-mono">{job.id.slice(0, 8)}</span>
			<span>{fmtAgo(job.created_at)}</span>
			{#if job.pass_rate != null}<span>pass {fmtRate(job.pass_rate)}</span>{/if}
			{#if job.validity && job.validity !== 'valid'}
				<Badge
					variant="outline"
					class={job.validity === 'exclude'
						? 'border-destructive/40 text-danger-fg'
						: 'border-warning/40 text-warning-fg'}>validity: {job.validity}</Badge
				>
			{/if}
		</span>
	</span>
{/snippet}

<div class="space-y-3">
	{#if resolveError}
		<ErrorAlert message={resolveError} />
	{/if}

	{#if selectedId && !browsing}
		<div
			class="flex flex-wrap items-start gap-3 rounded-md border border-primary/40 bg-primary/5 px-3 py-2.5"
		>
			<AppWindow class="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
			<div class="min-w-0 flex-1">
				{#if resolving && !selected}
					<span class="flex items-center gap-2 text-sm text-muted-foreground" role="status">
						<LoaderCircle
							class="size-3.5 animate-spin motion-reduce:animate-none"
							aria-hidden="true"
						/>
						Loading application {selectedId.slice(0, 8)}…
					</span>
				{:else if selected}
					{@render jobSummary(selected)}
					{#if selected.status !== 'completed'}
						<p class="mt-1 flex items-center gap-1.5 text-xs text-danger-fg">
							Only completed generation jobs can be analysed. This one is
							<StatusBadge status={selected.status} />.
						</p>
					{/if}
				{/if}
			</div>
			<div class="flex shrink-0 gap-1">
				<Button
					variant="ghost"
					size="sm"
					href="/applications/{selectedId}"
					target="_blank"
					rel="noopener"
				>
					<ExternalLink class="size-3.5" />Open
				</Button>
				<Button variant="outline" size="sm" onclick={change}>Change</Button>
			</div>
		</div>
	{:else}
		<div class="relative">
			<Search
				class="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
				aria-hidden="true"
			/>
			<Input
				type="search"
				value={search}
				oninput={(e) => onSearch(e.currentTarget.value)}
				placeholder="Search completed applications by model, app, stack or job id…"
				class="pl-8"
				aria-label="Search applications"
			/>
		</div>

		{#if error}
			<ErrorAlert message={error} onretry={load} />
		{:else if !results && loading}
			<p class="flex items-center gap-2 px-1 py-4 text-sm text-muted-foreground" role="status">
				<LoaderCircle
					class="size-3.5 animate-spin motion-reduce:animate-none"
					aria-hidden="true"
				/>Loading applications…
			</p>
		{:else if results && results.items.length === 0}
			<p
				class="rounded-md border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground"
			>
				{query
					? `No completed application matches “${query}”.`
					: 'No completed applications yet. Generate one first.'}
			</p>
		{:else if results}
			<div class="overflow-hidden rounded-md border border-border">
				<ul
					class={cn('divide-y divide-border transition-opacity', loading && 'opacity-60')}
					aria-label="Completed applications"
					aria-busy={loading}
				>
					{#each results.items as job (job.id)}
						{@const isSel = job.id === selectedId}
						<li>
							<button
								type="button"
								class={cn(
									'flex w-full cursor-pointer items-center gap-3 px-3 py-2 text-left transition-colors hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none',
									isSel && 'bg-primary/5'
								)}
								aria-pressed={isSel}
								onclick={() => choose(job)}
							>
								<span class="min-w-0 flex-1">{@render jobSummary(job)}</span>
								{#if isSel}
									<CircleCheck class="size-4 shrink-0 text-primary" aria-hidden="true" />
								{/if}
							</button>
						</li>
					{/each}
				</ul>
				<PaginationBar
					class="border-t border-border"
					resultsText="{results.total.toLocaleString()} completed application{results.total === 1
						? ''
						: 's'}"
					page={results.page}
					pages={results.pages}
					onGoToPage={(p) => (pageNo = p)}
				/>
			</div>
			{#if selectedId}
				<div class="flex justify-end">
					<Button variant="ghost" size="sm" onclick={() => (browsing = false)}
						>Keep current selection</Button
					>
				</div>
			{/if}
		{/if}
	{/if}
</div>
