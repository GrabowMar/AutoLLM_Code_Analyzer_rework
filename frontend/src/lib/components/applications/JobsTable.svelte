<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { formatDuration } from '$lib/utils/formatters';
	import {
		jobListStatusColors as statusColors,
		jobListContainerStatusColors as containerStatusColors,
		jobDescription,
		timeAgo,
	} from './utils';
	import type { GenerationJobList } from '$lib/api/client';
	import type { ContainerInstance } from '$lib/api/runtime';
	import Eye from '@lucide/svelte/icons/eye';
	import Bot from '@lucide/svelte/icons/bot';
	import Pencil from '@lucide/svelte/icons/pencil';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import CircleX from '@lucide/svelte/icons/circle-x';
	import Clock from '@lucide/svelte/icons/clock';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Copy from '@lucide/svelte/icons/copy';
	import AlertTriangle from '@lucide/svelte/icons/alert-triangle';
	import Ban from '@lucide/svelte/icons/ban';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import Play from '@lucide/svelte/icons/play';
	import Square from '@lucide/svelte/icons/square';
	import Server from '@lucide/svelte/icons/server';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import Hammer from '@lucide/svelte/icons/hammer';
	import Layers from '@lucide/svelte/icons/layers';
	import ArrowUpDown from '@lucide/svelte/icons/arrow-up-down';
	import ArrowUp from '@lucide/svelte/icons/arrow-up';
	import ArrowDown from '@lucide/svelte/icons/arrow-down';

	interface Props {
		jobs: GenerationJobList[];
		containersByJobId: Record<string, ContainerInstance>;
		containerBusy: Record<string, string | null>;
		sortBy: string;
		sortDir: 'asc' | 'desc';
		onToggleSort: (field: string) => void;
		onBuild: (jobId: string) => void;
		onStart: (jobId: string, containerId: string) => void;
		onStop: (jobId: string, containerId: string) => void;
		onRemoveContainer: (jobId: string, containerId: string) => void;
		onCancel: (jobId: string) => void;
		onDelete: (jobId: string) => void;
		onRetry: (jobId: string) => void;
		onCopyId: (jobId: string) => void;
	}
	let {
		jobs,
		containersByJobId,
		containerBusy,
		sortBy,
		sortDir,
		onToggleSort,
		onBuild,
		onStart,
		onStop,
		onRemoveContainer,
		onCancel,
		onDelete,
		onRetry,
		onCopyId,
	}: Props = $props();
</script>

<Card.Root>
	<Card.Content class="p-0">
		<div class="overflow-x-auto">
			<table class="w-full">
				<thead>
					<tr class="border-b bg-muted/40 transition-colors sticky top-0 z-10">
						<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground tracking-wider whitespace-nowrap">
							<button
								class="inline-flex items-center gap-1 hover:text-foreground transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring rounded"
								onclick={() => onToggleSort('created_at')}
								aria-sort={sortBy === 'created_at' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
							>
								Application
								{#if sortBy === 'created_at'}
									{#if sortDir === 'asc'}<ArrowUp class="h-3 w-3 text-primary" />{:else}<ArrowDown class="h-3 w-3 text-primary" />{/if}
								{:else}
									<ArrowUpDown class="h-3 w-3 opacity-30" />
								{/if}
							</button>
						</th>
						<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground tracking-wider whitespace-nowrap">Job Status</th>
						<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground tracking-wider whitespace-nowrap">App / Container</th>
						<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground tracking-wider whitespace-nowrap">
							<button
								class="inline-flex items-center gap-1 hover:text-foreground transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring rounded"
								onclick={() => onToggleSort('duration_seconds')}
								aria-sort={sortBy === 'duration_seconds' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
							>
								Duration
								{#if sortBy === 'duration_seconds'}
									{#if sortDir === 'asc'}<ArrowUp class="h-3 w-3 text-primary" />{:else}<ArrowDown class="h-3 w-3 text-primary" />{/if}
								{:else}
									<ArrowUpDown class="h-3 w-3 opacity-30" />
								{/if}
							</button>
						</th>
						<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground tracking-wider whitespace-nowrap">
							<button
								class="inline-flex items-center gap-1 hover:text-foreground transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring rounded"
								onclick={() => onToggleSort('model_name')}
								aria-sort={sortBy === 'model_name' ? 'ascending' : 'none'}
							>
								Model
								{#if sortBy === 'model_name'}
									<ArrowUp class="h-3 w-3 text-primary" />
								{:else}
									<ArrowUpDown class="h-3 w-3 opacity-30" />
								{/if}
							</button>
						</th>
						<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground tracking-wider whitespace-nowrap">Actions</th>
					</tr>
				</thead>
				<tbody>
					{#each jobs as job, i (job.id)}
						{@const container = containersByJobId[job.id]}
						{@const isBusy = containerBusy[job.id]}
						<tr class="border-b transition-colors hover:bg-muted/50 group
							{i % 2 === 0 ? '' : 'bg-muted/15'}
							{job.status === 'failed' ? 'hover:bg-destructive/[0.04]' : ''}">

							<!-- Application (unified) -->
							<td class="px-3 py-2 align-middle">
								<div class="flex items-center gap-2.5">
									<div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted group-hover:bg-primary/10 transition-colors">
										{#if job.mode === 'custom'}
											<Pencil class="h-4 w-4 text-muted-foreground group-hover:text-primary transition-colors" />
										{:else if job.mode === 'scaffolding'}
											<Layers class="h-4 w-4 text-muted-foreground group-hover:text-primary transition-colors" />
										{:else if job.mode === 'copilot'}
											<Bot class="h-4 w-4 text-muted-foreground group-hover:text-primary transition-colors" />
										{/if}
									</div>
									<div class="min-w-0">
										<a href="/applications/{job.id}" class="text-sm font-semibold hover:text-primary transition-colors block truncate max-w-[260px]" title={jobDescription(job)}>
											{jobDescription(job)}
										</a>
										<span class="text-[10px] text-muted-foreground block">
											<span class="capitalize font-medium text-primary/70">{job.mode}</span>
											<span class="mx-1 opacity-40">&middot;</span>
											<span class="font-mono tabular-nums">{timeAgo(job.created_at)}</span>
										</span>
									</div>
								</div>
							</td>

							<!-- Job Status -->
							<td class="px-3 py-2.5 align-middle">
								<div class="flex flex-col gap-1">
									<Badge variant="outline" class="w-fit text-[10px] uppercase font-mono px-2 py-0.5 {statusColors[job.status] ?? ''}">
										{#if job.status === 'running'}
											<span class="mr-1 h-1.5 w-1.5 rounded-full bg-amber-500 animate-pulse"></span>
										{/if}
										{#if job.status === 'completed'}
											<CircleCheck class="mr-1 h-3.5 w-3.5" />
										{:else if job.status === 'failed'}
											<CircleX class="mr-1 h-3.5 w-3.5" />
										{:else if job.status === 'pending'}
											<Clock class="mr-1 h-3.5 w-3.5" />
										{/if}
										{job.status}
									</Badge>
									{#if job.error_message}
										<div class="flex items-center gap-1 mt-0.5">
											<AlertTriangle class="h-3 w-3 text-destructive shrink-0" />
											<span class="text-[10px] text-destructive truncate max-w-[160px]" title={job.error_message}>{job.error_message}</span>
										</div>
									{/if}
								</div>
							</td>

							<!-- App / Container -->
							<td class="px-3 py-2.5 align-middle">
								{#if container}
									<div class="flex flex-col gap-1.5">
										<div class="flex items-center gap-2">
											<Badge variant="outline" class="gap-1 text-[10px] uppercase font-mono px-2 py-0.5 {containerStatusColors[container.status] ?? ''}">
												{#if container.status === 'running'}
													<span class="relative flex h-2 w-2">
														<span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
														<span class="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
													</span>
												{:else if container.status === 'building'}
													<LoaderCircle class="h-3 w-3 animate-spin text-amber-400" />
												{/if}
												{container.status}
											</Badge>
											{#if container.status === 'running'}
												<span class="text-[10px] text-muted-foreground/75 font-mono">Port {container.app_port}</span>
											{/if}
										</div>

										{#if container.status === 'running'}
											{@const accessUrl = container.app_url ?? null}
											{#if accessUrl}
												<Button
													id="access-app-btn-{job.id}"
													variant="outline"
													size="sm"
													href={accessUrl}
													target="_blank"
													rel="noopener noreferrer"
													class="h-8 text-emerald-600 dark:text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/10 hover:border-emerald-500/50 w-fit"
												>
													<ExternalLink class="h-3.5 w-3.5 mr-1.5" />
													Access App
												</Button>
											{/if}
										{:else if container.status === 'stopped'}
											<Button
												id="start-container-btn-{job.id}"
												variant="outline"
												size="sm"
												class="h-8 text-xs gap-1.5 border-zinc-700/50 hover:bg-emerald-500/10 hover:text-emerald-400 hover:border-emerald-500/40 w-fit"
												disabled={isBusy != null}
												onclick={() => onStart(job.id, container.id)}
											>
												{#if isBusy === 'start'}
													<LoaderCircle class="h-3 w-3 animate-spin" />
													Starting...
												{:else}
													<Play class="h-3 w-3 fill-current" />
													Start Container
												{/if}
											</Button>
										{:else if container.status === 'failed'}
											<div class="flex flex-col gap-1">
												<span class="text-[10px] text-destructive font-mono line-clamp-1" title={container.last_error}>{container.last_error || 'Container crash'}</span>
												<Button
													id="retry-container-btn-{job.id}"
													variant="outline"
													size="sm"
													class="h-8 text-xs gap-1.5 border-red-500/20 hover:bg-amber-500/10 hover:text-amber-400 hover:border-amber-500/40 w-fit"
													disabled={isBusy != null}
													onclick={() => onStart(job.id, container.id)}
												>
													{#if isBusy === 'start'}
														<LoaderCircle class="h-3 w-3 animate-spin" />
														Starting...
													{:else}
														<Play class="h-3 w-3" />
														Restart Container
													{/if}
												</Button>
											</div>
										{:else if container.status === 'building'}
											<span class="text-[11px] text-amber-400/90 font-medium flex items-center gap-1.5">
												<LoaderCircle class="h-3.5 w-3.5 animate-spin" />
												Provisioning environment...
											</span>
										{/if}
									</div>
								{:else if job.status === 'completed'}
									<Button
										id="provision-app-btn-{job.id}"
										variant="outline"
										size="sm"
										class="h-8 text-xs font-semibold gap-1.5 border-primary/30 text-primary hover:bg-primary/10 hover:border-primary/50 transition-all duration-200 shadow-sm w-fit"
										disabled={isBusy != null}
										onclick={() => onBuild(job.id)}
									>
										{#if isBusy === 'build'}
											<LoaderCircle class="h-3.5 w-3.5 animate-spin mr-1.5" />
											Building...
										{:else}
											<Hammer class="h-3.5 w-3.5 mr-1.5" />
											Provision App
										{/if}
									</Button>
								{:else}
									<span class="text-xs text-muted-foreground/60 italic">Not available</span>
								{/if}
							</td>

							<!-- Duration -->
							<td class="px-3 py-2.5 text-right align-middle">
								<span class="text-xs font-mono tabular-nums text-muted-foreground">{formatDuration(job.duration_seconds)}</span>
							</td>

							<!-- Model -->
							<td class="px-3 py-2.5 align-middle">
								{#if job.model_name}
									<span class="text-xs text-foreground truncate block max-w-[160px]" title={job.model_name}>{job.model_name}</span>
								{:else}
									<span class="text-xs text-muted-foreground/50 italic">—</span>
								{/if}
							</td>

							<!-- Actions -->
							<td class="px-3 py-2.5 align-middle">
								<div class="flex items-center justify-end gap-1">
									<!-- Container quick toggle (Start/Stop) -->
									{#if container}
										{#if container.status === 'running'}
											<Button
												id="action-stop-{job.id}"
												variant="ghost"
												size="sm"
												class="h-8 w-8 p-0 hover:bg-amber-500/10 hover:text-amber-400 text-muted-foreground"
												title="Stop Container"
												disabled={isBusy != null}
												onclick={() => onStop(job.id, container.id)}
											>
												{#if isBusy === 'stop'}
													<LoaderCircle class="h-4 w-4 animate-spin text-amber-500" />
												{:else}
													<Square class="h-4 w-4 fill-current text-amber-500" />
												{/if}
											</Button>
										{:else if container.status === 'stopped' || container.status === 'failed'}
											<Button
												id="action-start-{job.id}"
												variant="ghost"
												size="sm"
												class="h-8 w-8 p-0 hover:bg-emerald-500/10 hover:text-emerald-400 text-muted-foreground"
												title="Start Container"
												disabled={isBusy != null}
												onclick={() => onStart(job.id, container.id)}
											>
												{#if isBusy === 'start'}
													<LoaderCircle class="h-4 w-4 animate-spin text-emerald-500" />
												{:else}
													<Play class="h-4 w-4 fill-current text-emerald-500" />
												{/if}
											</Button>
										{/if}

										<!-- Remove Container -->
										{#if container.status !== 'building' && container.status !== 'pending'}
											<Button
												id="action-remove-container-{job.id}"
												variant="ghost"
												size="sm"
												class="h-8 w-8 p-0 hover:bg-destructive/10 hover:text-destructive text-muted-foreground/60"
												title="Delete Container (Preserves Code)"
												disabled={isBusy != null}
												onclick={() => onRemoveContainer(job.id, container.id)}
											>
												{#if isBusy === 'remove'}
													<LoaderCircle class="h-4 w-4 animate-spin text-red-500" />
												{:else}
													<Server class="h-4 w-4 stroke-[1.5] text-destructive/80" />
												{/if}
											</Button>
										{/if}
									{/if}

									<!-- Job Details / View -->
									<Button
										id="action-view-{job.id}"
										variant="ghost"
										size="sm"
										class="h-8 w-8 p-0 text-muted-foreground hover:text-foreground hover:bg-muted"
										href="/applications/{job.id}"
										title="View details"
									>
										<Eye class="h-4 w-4" />
									</Button>

									<!-- Retry/Failure -->
									{#if job.status === 'failed' || job.status === 'cancelled'}
										<Button
											id="action-retry-{job.id}"
											variant="ghost"
											size="sm"
											class="h-8 w-8 p-0 hover:bg-blue-500/10 text-muted-foreground hover:text-blue-400"
											title="Retry job"
											onclick={() => onRetry(job.id)}
										>
											<RotateCcw class="h-4 w-4" />
										</Button>
									{/if}
									{#if job.status === 'failed'}
										<Button
											id="action-failure-{job.id}"
											variant="ghost"
											size="sm"
											class="h-8 w-8 p-0 hover:bg-destructive/10 text-muted-foreground hover:text-destructive"
											href="/applications/{job.id}/failure"
											title="Failure logs"
										>
											<AlertTriangle class="h-4 w-4 text-destructive" />
										</Button>
									{/if}

									<!-- Cancel pending/running -->
									{#if job.status === 'pending' || job.status === 'running'}
										<Button
											id="action-cancel-{job.id}"
											variant="ghost"
											size="sm"
											class="h-8 w-8 p-0 hover:bg-amber-500/10 text-muted-foreground hover:text-amber-500"
											title="Cancel job"
											onclick={() => onCancel(job.id)}
										>
											<Ban class="h-4 w-4" />
										</Button>
									{/if}

									<!-- Copy ID -->
									<Button
										id="action-copy-{job.id}"
										variant="ghost"
										size="sm"
										class="h-8 w-8 p-0 text-muted-foreground/60 hover:text-foreground hover:bg-muted"
										title="Copy Job ID"
										onclick={() => onCopyId(job.id)}
									>
										<Copy class="h-3.5 w-3.5" />
									</Button>

									<!-- Delete Job -->
									{#if job.status !== 'running'}
										<Button
											id="action-delete-{job.id}"
											variant="ghost"
											size="sm"
											class="h-8 w-8 p-0 hover:bg-destructive/10 text-muted-foreground hover:text-destructive"
											title="Delete Job"
											onclick={() => onDelete(job.id)}
										>
											<Trash2 class="h-4 w-4" />
										</Button>
									{/if}
								</div>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</Card.Content>
</Card.Root>
