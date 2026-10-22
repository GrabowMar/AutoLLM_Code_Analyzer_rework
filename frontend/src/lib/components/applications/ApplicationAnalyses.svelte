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
	<Card.Root>
		<Card.Header class="flex flex-row flex-wrap items-start justify-between gap-3">
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
		<Card.Content class="space-y-3">
			{#if loading}
				<LoadingState label="loading analyses" class="py-8" />
			{:else if error}
				<ErrorAlert message={error} onretry={refresh} />
			{:else if tasks.length === 0}
				<EmptyState compact icon={ChartBar} title="No analyses yet" description="Run an analysis to scan this application for issues.">
					{#snippet action()}
						<Button size="sm" href="/analysis/create?generation_job_id={jobId}">New Analysis</Button>
					{/snippet}
				</EmptyState>
			{:else}
				<div class="overflow-x-auto">
					<table class="w-full text-sm">
						<thead>
							<tr>
								<th>Task</th>
								<th>Status</th>
								<th>Findings</th>
								<th>Created</th>
								<th class="text-right">Actions</th>
							</tr>
						</thead>
						<tbody>
							{#each tasks as t (t.id)}
								<tr>
									<td class="font-medium">{t.name}</td>
									<td><StatusBadge status={t.status} /></td>
									<td class="font-mono text-xs">{findingsOf(t)}</td>
									<td class="text-xs text-muted-foreground">{new Date(t.created_at).toLocaleString()}</td>
									<td class="text-right">
										<Button size="sm" variant="ghost" href="/analysis/{t.id}">View</Button>
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
