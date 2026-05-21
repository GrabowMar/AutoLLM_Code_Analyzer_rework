<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import AlertCircle from '@lucide/svelte/icons/alert-circle';
import type { AnalyzerInfo, GenerationJobList } from '$lib/api/client';

type SourceMode = 'job' | 'paste';

interface Props {
	sourceMode: SourceMode;
	selectedJob: GenerationJobList | undefined;
	pasteBackend: string;
	pasteFrontend: string;
	taskName: string;
	selectedAnalyzersList: AnalyzerInfo[];
	autoStart: boolean;
	liveTarget: boolean;
	launchError: string;
}

let {
	sourceMode,
	selectedJob,
	pasteBackend,
	pasteFrontend,
	taskName,
	selectedAnalyzersList,
	autoStart,
	liveTarget,
	launchError,
}: Props = $props();
</script>

<Card.Root>
	<Card.Header>
		<Card.Title>Review & Launch</Card.Title>
		<Card.Description>Review your configuration before launching the analysis.</Card.Description>
	</Card.Header>
	<Card.Content>
		<div class="overflow-x-auto table-scroll-wrapper">
			<table class="w-full text-sm">
				<tbody class="divide-y">
					<tr>
						<td class="w-40 px-4 py-3 font-medium text-muted-foreground">Source</td>
						<td class="px-4 py-3">
							{#if sourceMode === 'job' && selectedJob}
								<Badge variant="outline" class="mr-1 text-[10px]">Job</Badge>
								{selectedJob.model_name ?? selectedJob.id.slice(0, 8)}
							{:else if sourceMode === 'paste'}
								<Badge variant="outline" class="mr-1 text-[10px]">Pasted Code</Badge>
								{pasteBackend.trim() ? 'Backend' : ''}{pasteBackend.trim() && pasteFrontend.trim() ? ' + ' : ''}{pasteFrontend.trim() ? 'Frontend' : ''}
							{:else}
								—
							{/if}
						</td>
					</tr>
					{#if taskName}
						<tr>
							<td class="px-4 py-3 font-medium text-muted-foreground">Name</td>
							<td class="px-4 py-3">{taskName}</td>
						</tr>
					{/if}
					<tr>
						<td class="px-4 py-3 font-medium text-muted-foreground">Analyzers</td>
						<td class="px-4 py-3">
							<div class="flex flex-wrap gap-1">
								{#each selectedAnalyzersList as a}
									<Badge variant="secondary" class="text-[10px]">{a.display_name}</Badge>
								{/each}
							</div>
						</td>
					</tr>
					<tr>
						<td class="px-4 py-3 font-medium text-muted-foreground">Auto-start</td>
						<td class="px-4 py-3">{autoStart ? 'Yes' : 'No'}</td>
					</tr>
					{#if sourceMode === 'job' && liveTarget}
						<tr>
							<td class="px-4 py-3 font-medium text-muted-foreground">Live container</td>
							<td class="px-4 py-3">
								<Badge variant="outline" class="text-[10px]">Enabled</Badge>
							</td>
						</tr>
					{/if}
				</tbody>
			</table>
		</div>

		{#if launchError}
			<div class="mt-4 flex items-center gap-2 rounded-md border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-400">
				<AlertCircle class="h-4 w-4 shrink-0" />
				{launchError}
			</div>
		{/if}
	</Card.Content>
</Card.Root>
