<script lang="ts">
	/**
	 * ApplicationAnalyses — the analysis runs of one generated app (newest first,
	 * live while any is active) with links to analyze it again and to the full
	 * list scoped to this app.
	 *
	 * Props: `jobId`.
	 */
	import { onDestroy, onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import Microscope from '@lucide/svelte/icons/microscope';
	import Plus from '@lucide/svelte/icons/plus';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import { formatApiError } from '$lib/api/core';
	import { listRuns, type AnalysisRunListItem } from '$lib/api/runs';
	import { rowLink } from '$lib/components/table';
	import RunStatusCell from '$lib/components/analysis/RunStatusCell.svelte';
	import RunFindingsCell from '$lib/components/analysis/RunFindingsCell.svelte';
	import { RunWatcher } from '$lib/components/analysis/live.svelte';
	import { formatSpan, isActive, runDisplayName } from '$lib/components/analysis/format';
	import { formatDate } from '$lib/utils/formatters';

	interface Props {
		jobId: string;
	}
	let { jobId }: Props = $props();

	let runs = $state<AnalysisRunListItem[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let error = $state('');

	async function refresh() {
		try {
			const res = await listRuns({ generation_job_id: jobId, per_page: 10 });
			runs = res.items;
			total = res.total;
			error = '';
		} catch (e) {
			error = formatApiError(e, 'Could not load analyses.');
		} finally {
			loading = false;
			watcher.sync();
		}
	}

	const activeIds = $derived(runs.filter((r) => isActive(r.status)).map((r) => r.id));
	let timer: ReturnType<typeof setTimeout> | undefined;
	const watcher = new RunWatcher({
		channels: () => activeIds.map((id) => `analysis:${id}`),
		active: () => activeIds.length > 0,
		onEvent: () => {
			clearTimeout(timer);
			timer = setTimeout(() => void watcher.pollNow(), 500);
		},
		poll: refresh,
	});

	onMount(() => {
		watcher.start();
		void refresh();
	});
	onDestroy(() => {
		watcher.destroy();
		clearTimeout(timer);
	});
</script>

<section id="analyses">
	<Card.Root class="gap-0 overflow-clip py-0">
		<Card.Header class="flex flex-row flex-wrap items-start justify-between gap-3 border-b border-border py-3">
			<div class="space-y-1.5">
				<Card.Title>Analyses</Card.Title>
				<Card.Description>
					{#if total > 0}{total} run{total === 1 ? '' : 's'}{#if activeIds.length} · <span class="text-info-fg">{activeIds.length} active</span>{/if}{:else}Static and dynamic analysis of this app's code.{/if}
				</Card.Description>
			</div>
			<div class="flex flex-wrap items-center gap-2">
				{#if total > runs.length}
					<Button size="sm" variant="outline" href="/analysis?job={jobId}">All {total}</Button>
				{/if}
				<Button size="sm" href="/analysis/create?generation_job_id={jobId}"><Plus />Analyze</Button>
			</div>
		</Card.Header>
		<Card.Content class="p-0">
			{#if loading}
				<LoadingState label="loading analyses" class="py-8" />
			{:else if error}
				<div class="p-4"><ErrorAlert message={error} onretry={refresh} /></div>
			{:else if runs.length === 0}
				<div class="p-4">
					<EmptyState compact icon={Microscope} title="Not analyzed yet" description="Run your workspace's tools on this app's code (and on the running app, for dynamic tools).">
						{#snippet action()}<Button size="sm" href="/analysis/create?generation_job_id={jobId}"><Plus />Analyze</Button>{/snippet}
					</EmptyState>
				</div>
			{:else}
				<div class="overflow-x-auto">
					<table>
						<thead>
							<tr>
								<th>Run</th>
								<th>Status</th>
								<th class="num">Findings</th>
								<th class="num">Duration</th>
								<th>Created</th>
								<th><span class="sr-only">Open</span></th>
							</tr>
						</thead>
						<tbody>
							{#each runs as run (run.id)}
								{@const href = `/analysis/${run.id}`}
								<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (the name link is the keyboard path) -->
								<tr class="row-link align-top" onclick={rowLink(href)}>
									<td class="max-w-0 w-[34%]">
										<a {href} class="block truncate font-medium hover:text-primary" title={runDisplayName(run)}>{runDisplayName(run)}</a>
										<span class="cell-sub block font-mono text-[11px] text-muted-foreground">{run.tool_slugs.length} tools</span>
									</td>
									<td><RunStatusCell {run} /></td>
									<td class="num"><RunFindingsCell {run} /></td>
									<td class="num text-muted-foreground">{isActive(run.status) ? '—' : formatSpan(run.duration_seconds)}</td>
									<td class="whitespace-nowrap text-xs text-muted-foreground">{formatDate(run.created_at)}</td>
									<td class="w-px text-right">
										<Button size="sm" variant="ghost" class="size-8 p-0 text-muted-foreground hover:text-foreground" {href} aria-label="Open {runDisplayName(run)}"><ChevronRight class="size-4" /></Button>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</Card.Content>
	</Card.Root>
</section>
