<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import Clock from '@lucide/svelte/icons/clock';
import { statusColors, severityColors, analyzerTypeLabels } from '$lib/constants/analysis';
import { formatDuration } from '$lib/utils/analysis';
import FindingsTable from './FindingsTable.svelte';
import type { AnalysisFinding, AnalysisResult } from '$lib/api/client';

interface Pagination {
	page: number;
	pages: number;
	total: number;
}

interface Props {
	result: AnalysisResult;
	findings: AnalysisFinding[] | undefined;
	pagination: Pagination | undefined;
	findingsLoading: boolean;
	expandedFindings: Record<number, boolean>;
	onLoadFindings: (result: AnalysisResult, page?: number) => void;
	onToggleExpand: (id: number) => void;
}

let {
	result: r,
	findings,
	pagination,
	findingsLoading,
	expandedFindings,
	onLoadFindings,
	onToggleExpand,
}: Props = $props();
</script>

<div id="result-{r.id}" class="scroll-mt-16 space-y-4">
	<div class="flex flex-wrap items-center gap-3">
		<h2 class="text-lg font-semibold">{r.analyzer_name}</h2>
		<Badge variant="outline" class="text-xs">{analyzerTypeLabels[r.analyzer_type] ?? r.analyzer_type}</Badge>
		<Badge variant="outline" class="{statusColors[r.status] ?? ''} text-xs">{r.status}</Badge>
		{#if r.duration_seconds != null}
			<span class="text-xs text-muted-foreground flex items-center gap-1">
				<Clock class="h-3 w-3" /> {formatDuration(r.duration_seconds)}
			</span>
		{/if}
	</div>

	{#if r.error_message}
		<div class="rounded-md bg-red-500/10 p-3 text-sm text-red-400">
			<strong>Error:</strong> {r.error_message}
		</div>
	{/if}

	{#if Object.keys(r.finding_summary).length > 0}
		<div class="flex flex-wrap gap-2">
			{#each Object.entries(r.finding_summary) as [sev, count]}
				<Badge variant="outline" class="{severityColors[sev] ?? ''} text-xs">{count} {sev}</Badge>
			{/each}
			<Badge variant="outline" class="text-xs">{r.findings_count} total</Badge>
		</div>
	{/if}

	{#if r.summary && Object.keys(r.summary).length > 0}
		<Card.Root>
			<Card.Header><Card.Title class="text-sm">Summary Metrics</Card.Title></Card.Header>
			<Card.Content>
				<dl class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-x-4 gap-y-2 text-sm">
					{#each Object.entries(r.summary) as [key, val]}
						<div>
							<dt class="text-muted-foreground text-xs">{key.replace(/_/g, ' ')}</dt>
							<dd class="font-mono font-medium">
								{#if typeof val === 'object' && val !== null}
									{JSON.stringify(val)}
								{:else}
									{val}
								{/if}
							</dd>
						</div>
					{/each}
				</dl>
			</Card.Content>
		</Card.Root>
	{/if}

	{#if r.findings_count > 0}
		<FindingsTable
			result={r}
			{findings}
			{pagination}
			loading={findingsLoading}
			{expandedFindings}
			{onLoadFindings}
			{onToggleExpand}
		/>
	{:else if r.status === 'completed'}
		<p class="text-sm text-muted-foreground">No findings — clean result.</p>
	{/if}
</div>
