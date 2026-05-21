<script lang="ts">
import { onMount } from 'svelte';
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import ChartBar from '@lucide/svelte/icons/chart-bar';
import RefreshCw from '@lucide/svelte/icons/refresh-cw';
import { getAnalysisTasks, type AnalysisTaskList } from '$lib/api/analysis';

interface Props { jobId: string }
let { jobId }: Props = $props();

let tasks = $state<AnalysisTaskList[]>([]);
let loading = $state(true);
let error = $state('');

async function refresh() {
	loading = true;
	error = '';
	try {
		const res = await getAnalysisTasks({ generation_job_id: jobId, per_page: 50 });
		tasks = res.items;
	} catch (e) {
		error = (e as Error).message ?? 'Failed to load';
	} finally {
		loading = false;
	}
}

onMount(refresh);

const statusColor: Record<string, string> = {
	pending: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30',
	running: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
	completed: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
	failed: 'bg-red-500/15 text-red-400 border-red-500/30',
	cancelled: 'bg-zinc-500/15 text-zinc-500 border-zinc-500/30',
};

const summary = $derived.by(() => {
	const total = tasks.length;
	const done = tasks.filter(t => t.status === 'completed').length;
	const running = tasks.filter(t => t.status === 'running').length;
	const failed = tasks.filter(t => t.status === 'failed').length;
	return { total, done, running, failed };
});

function findingsOf(t: AnalysisTaskList): number | string {
	const rs = t.results_summary || {};
	if (typeof rs.total_findings === 'number') return rs.total_findings;
	if (typeof rs.findings === 'number') return rs.findings;
	return '—';
}
</script>

<section id="analyses" class="space-y-4">
	<h2 class="text-lg font-semibold flex items-center gap-2"><ChartBar class="h-5 w-5" /> Analyses</h2>
	<Card.Root>
		<Card.Content class="p-4 space-y-3">
			<div class="flex items-center gap-3 flex-wrap">
				<div class="flex gap-2 text-xs">
					<Badge variant="outline">Total {summary.total}</Badge>
					<Badge variant="outline" class="bg-emerald-500/10 text-emerald-400 border-emerald-500/30">Done {summary.done}</Badge>
					<Badge variant="outline" class="bg-amber-500/10 text-amber-400 border-amber-500/30">Running {summary.running}</Badge>
					<Badge variant="outline" class="bg-red-500/10 text-red-400 border-red-500/30">Failed {summary.failed}</Badge>
				</div>
				<Button size="sm" variant="outline" onclick={refresh} class="ml-auto">
					<RefreshCw class="h-3.5 w-3.5 mr-1.5" /> Refresh
				</Button>
				<Button size="sm" href="/analysis/create?generation_job_id={jobId}">New Analysis</Button>
			</div>

			{#if loading}
				<p class="text-sm text-muted-foreground">Loading…</p>
			{:else if error}
				<p class="text-sm text-red-400">{error}</p>
			{:else if tasks.length === 0}
				<p class="text-sm text-muted-foreground italic">No analyses yet for this application.</p>
			{:else}
				<div class="overflow-x-auto">
					<table class="w-full text-sm">
						<thead>
							<tr class="border-b bg-muted/30">
								<th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Task</th>
								<th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Status</th>
								<th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Findings</th>
								<th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Duration</th>
								<th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Created</th>
								<th class="px-3 py-2 text-right text-xs font-medium text-muted-foreground">Actions</th>
							</tr>
						</thead>
						<tbody class="divide-y">
							{#each tasks as t (t.id)}
								<tr class="hover:bg-muted/30">
									<td class="px-3 py-2 font-medium">{t.name}</td>
									<td class="px-3 py-2"><Badge variant="outline" class="text-xs {statusColor[t.status] ?? ''}">{t.status}</Badge></td>
									<td class="px-3 py-2 font-mono text-xs">{findingsOf(t)}</td>
									<td class="px-3 py-2 font-mono text-xs">{t.duration_seconds != null ? `${t.duration_seconds.toFixed(1)}s` : '—'}</td>
									<td class="px-3 py-2 text-xs text-muted-foreground">{new Date(t.created_at).toLocaleString()}</td>
									<td class="px-3 py-2 text-right">
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
