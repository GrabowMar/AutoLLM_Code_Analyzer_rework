<script lang="ts">
import { onMount } from 'svelte';
import * as Card from '$lib/components/ui/card';
import StatusBadge from '$lib/components/StatusBadge.svelte';
import LoadingState from '$lib/components/LoadingState.svelte';
import EmptyState from '$lib/components/EmptyState.svelte';
import ErrorAlert from '$lib/components/ErrorAlert.svelte';
import { Button } from '$lib/components/ui/button';
import ChartBar from '@lucide/svelte/icons/chart-bar';
import RefreshCw from '@lucide/svelte/icons/refresh-cw';
import { listRuns, type AnalysisRunListItem } from '$lib/api/runs';
import ChevronRight from '@lucide/svelte/icons/chevron-right';
import { rowLink } from '$lib/components/table';

interface Props { jobId: string }
let { jobId }: Props = $props();

let tasks = $state<AnalysisRunListItem[]>([]);
let loading = $state(true);
let error = $state('');

async function refresh() {
	loading = true;
	error = '';
	try {
		const res = await listRuns({ generation_job_id: jobId, per_page: 50 });
		tasks = res.items;
	} catch (e) {
		error = (e as Error).message ?? 'Failed to load';
	} finally {
		loading = false;
	}
}

onMount(refresh);


const summary = $derived.by(() => {
	const total = tasks.length;
	const done = tasks.filter(t => t.status === 'completed').length;
	const running = tasks.filter(t => t.status === 'running').length;
	const failed = tasks.filter(t => t.status === 'failed').length;
	return { total, done, running, failed };
});

function findingsOf(t: AnalysisRunListItem): number | string {
	const rs = t.summary || {};
	if (typeof rs.total_findings === 'number') return rs.total_findings;
	if (typeof rs.findings === 'number') return rs.findings;
	return '—';
}
</script>

<section id="analyses" class="space-y-4">
	<Card.Root class="gap-0 overflow-clip py-0">
		<Card.Header class="flex flex-row flex-wrap items-start justify-between gap-3 border-b border-border py-3">
			<div class="space-y-1.5">
				<Card.Title>Analyses</Card.Title>
				<Card.Description>
					{summary.total} total · {summary.done} done · {summary.running} running · {summary.failed} failed
				</Card.Description>
			</div>
			<div class="flex flex-wrap items-center gap-2">
				<Button size="sm" variant="outline" onclick={refresh}>
					<RefreshCw class="h-3.5 w-3.5" /> Refresh
				</Button>
				<Button size="sm" href="/analysis/create?generation_job_id={jobId}">New Analysis</Button>
			</div>
		</Card.Header>
		<Card.Content class="p-0">
			{#if loading}
				<LoadingState label="loading analyses" class="py-8" />
			{:else if error}
				<div class="p-4"><ErrorAlert message={error} onretry={refresh} /></div>
			{:else if tasks.length === 0}
				<div class="p-4">
					<EmptyState compact icon={ChartBar} title="No analyses yet" description="Run an analysis to scan this application for issues.">
						{#snippet action()}
							<Button size="sm" href="/analysis/create?generation_job_id={jobId}">New Analysis</Button>
						{/snippet}
					</EmptyState>
				</div>
			{:else}
				<div class="overflow-x-auto">
					<table>
						<thead>
							<tr>
								<th>Task</th>
								<th>Status</th>
								<th class="num">Findings</th>
								<th>Created</th>
								<th><span class="sr-only">Actions</span></th>
							</tr>
						</thead>
						<tbody>
							{#each tasks as t (t.id)}
								{@const href = `/analysis/${t.id}`}
								{@const findings = findingsOf(t)}
								<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (the name link is the keyboard path) -->
								<tr class="row-link" onclick={rowLink(href)}>
									<td class="max-w-0 w-[45%]">
										<a {href} class="block truncate font-medium hover:text-primary" title={t.name}>{t.name}</a>
									</td>
									<td class="whitespace-nowrap"><StatusBadge status={t.status} /></td>
									<td class="num">
										{#if findings === '—'}<span class="text-muted-foreground">—</span>{:else}{findings}{/if}
									</td>
									<td class="whitespace-nowrap text-xs text-muted-foreground">{new Date(t.created_at).toLocaleString()}</td>
									<td class="w-px whitespace-nowrap text-right">
										<Button size="sm" variant="ghost" class="size-8 p-0 text-muted-foreground hover:text-foreground" {href} title="View analysis" aria-label="View analysis">
											<ChevronRight class="size-4" />
										</Button>
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
