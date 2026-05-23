<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import {
		getGenerationJob,
		getJobArtifacts,
		type GenerationJob,
		type GenerationArtifact,
		type PaginatedJobs,
	} from '$lib/api/client';
	import { downloadExport } from '$lib/api/export';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Square from '@lucide/svelte/icons/square';
	import FileCode from '@lucide/svelte/icons/file-code';
	import Download from '@lucide/svelte/icons/download';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import Lock from '@lucide/svelte/icons/lock';
	import AlertTriangle from '@lucide/svelte/icons/alert-triangle';

	interface Props {
		historyData: PaginatedJobs | null;
		historyLoading: boolean;
		historyModeFilter: string;
		historyStatusFilter: string;
		statusColors: Record<string, string>;
		modeLabels: Record<string, string>;
		formatDuration: (s: number | null) => string;
		formatDate: (s: string | null) => string;
		onRefresh: () => void;
		onFilterChange: () => void;
		onPageChange: (page: number) => void;
		onCancelJob: (id: string) => void;
		onOpenJob?: (mode: string, id: string) => void;
	}

	let {
		historyData,
		historyLoading,
		historyModeFilter = $bindable(),
		historyStatusFilter = $bindable(),
		statusColors,
		modeLabels,
		formatDuration,
		formatDate,
		onRefresh,
		onFilterChange,
		onPageChange,
		onCancelJob,
		onOpenJob,
	}: Props = $props();

	let expandedJobId = $state<string | null>(null);
	let expandedJob = $state<GenerationJob | null>(null);
	let expandedArtifacts = $state<GenerationArtifact[]>([]);
	let expandedLoading = $state(false);
	let resultTab = $state<'backend' | 'frontend' | 'scan'>('backend');

	async function toggleExpandJob(id: string) {
		if (expandedJobId === id) {
			expandedJobId = null;
			expandedJob = null;
			expandedArtifacts = [];
			return;
		}
		expandedJobId = id;
		expandedLoading = true;
		try {
			const [job, artifacts] = await Promise.all([
				getGenerationJob(id),
				getJobArtifacts(id),
			]);
			expandedJob = job;
			expandedArtifacts = artifacts;
		} catch {
			expandedJob = null;
			expandedArtifacts = [];
		} finally {
			expandedLoading = false;
		}
	}
</script>

<Card.Root>
	<Card.Header>
		<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
			<div>
				<Card.Title>Generation History</Card.Title>
				<Card.Description>Past generation jobs and their results.</Card.Description>
			</div>
			<div class="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center">
				<!-- Mode filter pills -->
				<div class="flex items-center gap-0.5 rounded-md border bg-muted/30 p-0.5">
					{#each [['', 'All'], ['custom', 'Custom'], ['scaffolding', 'Scaffolding'], ['copilot', 'Copilot']] as [val, label]}
						<button
							type="button"
							class="rounded px-2.5 py-1 text-xs font-medium transition-colors {historyModeFilter === val ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}"
							onclick={() => { historyModeFilter = val; onFilterChange(); }}
						>{label}</button>
					{/each}
				</div>
				<!-- Status filter pills -->
				<div class="flex items-center gap-0.5 rounded-md border bg-muted/30 p-0.5">
					{#each [['', 'All'], ['pending', 'Pending'], ['running', 'Running'], ['completed', 'Completed'], ['failed', 'Failed'], ['cancelled', 'Cancelled']] as [val, label]}
						<button
							type="button"
							class="rounded px-2.5 py-1 text-xs font-medium transition-colors {historyStatusFilter === val ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}"
							onclick={() => { historyStatusFilter = val; onFilterChange(); }}
						>{label}</button>
					{/each}
				</div>
				<div class="flex items-center gap-1.5">
					<Button variant="outline" size="sm" onclick={onRefresh} disabled={historyLoading}>
						<RefreshCw class="h-3.5 w-3.5 {historyLoading ? 'animate-spin' : ''}" />
					</Button>
					<!-- Export generation jobs -->
					<details class="relative">
						<summary class="list-none cursor-pointer">
							<Button variant="outline" size="sm" tag="span">
								<Download class="h-3.5 w-3.5" />
							</Button>
						</summary>
						<div class="absolute right-0 z-50 mt-1 w-48 rounded-md border bg-popover p-1 shadow-md">
							<button class="w-full rounded px-3 py-1.5 text-left text-sm hover:bg-accent" onclick={() => downloadExport('generation-jobs.csv')}>Jobs CSV</button>
							<button class="w-full rounded px-3 py-1.5 text-left text-sm hover:bg-accent" onclick={() => downloadExport('generation-jobs.json')}>Jobs JSON</button>
						</div>
					</details>
				</div>
			</div>
		</div>
	</Card.Header>
	<Card.Content class="p-0">
		{#if historyLoading && !historyData}
			<div class="flex items-center justify-center py-12 text-sm text-muted-foreground">
				<LoaderCircle class="mr-2 h-4 w-4 animate-spin" /> Loading history…
			</div>
		{:else if historyData && historyData.items.length > 0}
			<div class="overflow-x-auto">
			<table class="w-full text-sm">
				<thead>
					<tr class="border-b bg-muted/30">
						<th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Mode</th>
						<th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Status</th>
						<th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Model</th>
						<th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Template</th>
						<th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Duration</th>
						<th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Created</th>
						<th class="px-3 py-2 text-right text-xs font-medium text-muted-foreground w-20"></th>
					</tr>
				</thead>
				<tbody class="divide-y">
					{#each historyData.items as job}
						<tr
							class="hover:bg-muted/30 cursor-pointer transition-colors {expandedJobId === job.id ? 'bg-muted/20' : ''}"
							onclick={() => toggleExpandJob(job.id)}
						>
							<td class="px-3 py-2">
								<div class="flex flex-wrap items-center gap-1">
									<Badge variant="secondary" class="text-[10px]">{modeLabels[job.mode] ?? job.mode}</Badge>
									{#if job.mode === 'copilot'}
										<Badge variant="outline" class="text-[9px]">Aider</Badge>
									{/if}
								</div>
							</td>
							<td class="px-3 py-2">
								<Badge variant="outline" class="text-[10px] {statusColors[job.status] ?? ''}">
									{#if job.status === 'running' || job.status === 'pending'}
										<LoaderCircle class="mr-1 h-3 w-3 animate-spin" />
									{/if}
									{job.status}
								</Badge>
							</td>
							<td class="px-3 py-2 text-xs">{job.model_name ?? '—'}</td>
							<td class="px-3 py-2 text-xs">{job.template_name ?? job.scaffolding_name ?? '—'}</td>
							<td class="px-3 py-2 text-xs font-mono text-muted-foreground">{formatDuration(job.duration_seconds)}</td>
							<td class="px-3 py-2 text-xs text-muted-foreground">{formatDate(job.created_at)}</td>
							<td class="px-3 py-2 text-right">
								{#if onOpenJob}
									<Button
										variant="ghost"
										size="sm"
										class="h-7 px-2 text-xs"
										onclick={(e) => {
											e.stopPropagation();
											onOpenJob(job.mode, job.id);
										}}
									>
										<ExternalLink class="h-3 w-3 mr-1" /> Open
									</Button>
								{/if}
							</td>
						</tr>

						<!-- Expanded row -->
						{#if expandedJobId === job.id}
							<tr>
								<td colspan="7" class="bg-muted/10 px-4 py-4">
									{#if expandedLoading}
										<div class="flex items-center gap-2 text-sm text-muted-foreground">
											<LoaderCircle class="h-4 w-4 animate-spin" /> Loading details…
										</div>
									{:else if expandedJob}
										<div class="space-y-3">
											<div class="grid gap-4 grid-cols-1 sm:grid-cols-3 text-sm">
												<div>
													<span class="text-muted-foreground">Job ID:</span>
													<span class="ml-1 font-mono text-xs">{expandedJob.id}</span>
												</div>
												{#if expandedJob.error_message}
													<div class="md:col-span-2">
														<span class="text-muted-foreground">Error:</span>
														<span class="ml-1 text-red-400">{expandedJob.error_message}</span>
													</div>
												{/if}
												{#if expandedJob.temperature}
													<div>
														<span class="text-muted-foreground">Temperature:</span>
														<span class="ml-1 font-mono">{expandedJob.temperature}</span>
													</div>
												{/if}
												{#if expandedJob.max_tokens}
													<div>
														<span class="text-muted-foreground">Max Tokens:</span>
														<span class="ml-1 font-mono">{expandedJob.max_tokens}</span>
													</div>
												{/if}
											</div>

											{#if expandedJob.result_data && Object.keys(expandedJob.result_data).length > 0}
												<div>
													<h4 class="text-xs font-medium text-muted-foreground mb-1">Result</h4>
													{#if expandedJob.mode === 'scaffolding' && expandedJob.result_data.backend_code}
														<!-- Scaffolding: show backend + frontend tabs -->
														<div class="space-y-2">
															<div class="flex gap-1">
																<button
																	class="rounded-md px-2 py-1 text-xs {resultTab === 'backend' ? 'bg-primary text-primary-foreground' : 'bg-muted/50 text-muted-foreground hover:text-foreground'}"
																	onclick={() => resultTab = 'backend'}
																>Backend ({expandedJob.result_data.backend_code?.length ?? 0} chars)</button>
																<button
																	class="rounded-md px-2 py-1 text-xs {resultTab === 'frontend' ? 'bg-primary text-primary-foreground' : 'bg-muted/50 text-muted-foreground hover:text-foreground'}"
																	onclick={() => resultTab = 'frontend'}
																>Frontend ({expandedJob.result_data.frontend_code?.length ?? 0} chars)</button>
																{#if expandedJob.result_data.backend_scan}
																	<button
																		class="rounded-md px-2 py-1 text-xs {resultTab === 'scan' ? 'bg-primary text-primary-foreground' : 'bg-muted/50 text-muted-foreground hover:text-foreground'}"
																		onclick={() => resultTab = 'scan'}
																	>API Scan</button>
																{/if}
															</div>
															{#if resultTab === 'backend'}
																<pre class="max-h-64 overflow-auto rounded-md bg-muted/50 p-3 text-xs font-mono">{expandedJob.result_data.backend_code}</pre>
															{:else if resultTab === 'frontend'}
																<pre class="max-h-64 overflow-auto rounded-md bg-muted/50 p-3 text-xs font-mono">{expandedJob.result_data.frontend_code}</pre>
															{:else if resultTab === 'scan'}
																<div class="rounded-md bg-muted/50 p-3 text-xs space-y-2">
																	<div><span class="text-muted-foreground">Endpoints:</span> {expandedJob.result_data.backend_scan?.endpoints?.length ?? 0}</div>
																	<div><span class="text-muted-foreground">Models:</span> {expandedJob.result_data.backend_scan?.models?.length ?? 0}</div>
																	{#if expandedJob.result_data.backend_dependencies?.length}
																		<div><span class="text-muted-foreground">Dependencies:</span> {expandedJob.result_data.backend_dependencies.join(', ')}</div>
																	{/if}
																	{#if expandedJob.result_data.backend_scan?.endpoints}
																		<div class="mt-2">
																			<span class="font-medium">Endpoints:</span>
																			{#each expandedJob.result_data.backend_scan.endpoints as ep}
																				<div class="ml-2 flex items-center gap-1 font-mono">{ep.method} {ep.path}{#if ep.requires_auth} <Lock class="inline h-3 w-3 text-muted-foreground" />{/if}</div>
																			{/each}
																		</div>
																	{/if}
																</div>
															{/if}
															{#if expandedJob.result_data.backend_truncated || expandedJob.result_data.frontend_truncated}
																<div class="flex items-center gap-1 text-xs text-amber-400"><AlertTriangle class="h-3 w-3 shrink-0" /> Output was truncated ({expandedJob.result_data.backend_truncated ? 'backend' : ''}{expandedJob.result_data.backend_truncated && expandedJob.result_data.frontend_truncated ? ' + ' : ''}{expandedJob.result_data.frontend_truncated ? 'frontend' : ''})</div>
															{/if}
														</div>
													{:else if expandedJob.mode === 'copilot' && expandedJob.result_data.content}
														<!-- Copilot: show result with iteration info -->
														<div class="space-y-2">
															{#if expandedJob.result_data.iterations_completed}
																<div class="flex items-center gap-3 text-xs">
																	<span class="text-muted-foreground">Iterations: {expandedJob.result_data.iterations_completed}</span>
																	{#if expandedJob.result_data.dependencies?.length}
																		<span class="text-muted-foreground">Deps: {expandedJob.result_data.dependencies.join(', ')}</span>
																	{/if}
																	{#if expandedJob.result_data.final_errors?.length}
																		<span class="flex items-center gap-1 text-amber-400"><AlertTriangle class="h-3 w-3 shrink-0" /> {expandedJob.result_data.final_errors.length} remaining errors</span>
																	{/if}
																</div>
															{/if}
															<pre class="max-h-64 overflow-auto rounded-md bg-muted/50 p-3 text-xs font-mono">{expandedJob.result_data.content}</pre>
														</div>
													{:else}
														<!-- Custom/other: show content or JSON -->
														<pre class="max-h-48 overflow-auto rounded-md bg-muted/50 p-3 text-xs font-mono">{expandedJob.result_data.content ?? JSON.stringify(expandedJob.result_data, null, 2)}</pre>
													{/if}
												</div>
											{/if}

											{#if expandedArtifacts.length > 0}
												<div>
													<h4 class="text-xs font-medium text-muted-foreground mb-1">Artifacts ({expandedArtifacts.length})</h4>
													<div class="space-y-2">
														{#each expandedArtifacts as artifact}
															<div class="rounded-md border p-3 text-xs">
																<div class="flex items-center gap-3 mb-1">
																	<Badge variant="outline" class="text-[10px]">{artifact.stage}</Badge>
																	<span class="text-muted-foreground">Tokens: {artifact.prompt_tokens} + {artifact.completion_tokens}</span>
																	{#if artifact.total_cost > 0}
																		<span class="text-muted-foreground">Cost: ${artifact.total_cost.toFixed(4)}</span>
																	{/if}
																</div>
															</div>
														{/each}
													</div>
												</div>
											{/if}

											<div class="flex gap-2">
												{#if expandedJob.status === 'running' || expandedJob.status === 'pending'}
													<Button variant="destructive" size="sm" onclick={() => onCancelJob(expandedJob!.id)}>
														<Square class="mr-1.5 h-3.5 w-3.5" /> Cancel Job
													</Button>
												{/if}
											</div>
										</div>
									{/if}
								</td>
							</tr>
						{/if}
					{/each}
				</tbody>
			</table>
			</div>

			<!-- Pagination -->
			{#if historyData.pages > 1}
				<div class="flex items-center justify-between border-t px-4 py-3">
					<span class="text-xs text-muted-foreground">
						Page {historyData.page} of {historyData.pages} ({historyData.total} total)
					</span>
					<div class="flex gap-1">
						<Button
							variant="outline"
							size="sm"
							disabled={historyData.page <= 1}
							onclick={() => onPageChange(historyData!.page - 1)}
						>
							Previous
						</Button>
						<Button
							variant="outline"
							size="sm"
							disabled={historyData.page >= historyData.pages}
							onclick={() => onPageChange(historyData!.page + 1)}
						>
							Next
						</Button>
					</div>
				</div>
			{/if}
		{:else}
			<div class="flex flex-col items-center justify-center py-12 text-sm text-muted-foreground">
				<FileCode class="mb-3 h-10 w-10 text-muted-foreground/30" />
				<p>No generation jobs yet.</p>
				<p class="text-xs mt-1">Create your first job using any of the tabs above.</p>
			</div>
		{/if}
	</Card.Content>
</Card.Root>
