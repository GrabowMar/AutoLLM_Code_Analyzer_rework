<script lang="ts">
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

	interface Props {
		jobs: GenerationJobList[];
		containersByJobId: Record<string, ContainerInstance>;
		containerBusy: Record<string, string | null>;
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

<div class="space-y-2.5">
	{#each jobs as job (job.id)}
		{@const container = containersByJobId[job.id]}
		{@const isBusy = containerBusy[job.id]}
		<div class="block rounded-lg border bg-card p-3.5 transition-colors hover:bg-muted/50 space-y-3">
			<!-- Card Header: Mode & Time -->
			<div class="flex items-center justify-between gap-2">
				<div class="flex items-center gap-2.5 min-w-0 flex-1">
					<div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted">
						{#if job.mode === 'custom'}
							<Pencil class="h-4 w-4 text-muted-foreground" />
						{:else if job.mode === 'scaffolding'}
							<Layers class="h-4 w-4 text-muted-foreground" />
						{:else if job.mode === 'copilot'}
							<Bot class="h-4 w-4 text-muted-foreground" />
						{/if}
					</div>
					<div class="min-w-0">
						<a href="/applications/{job.id}" class="text-sm font-semibold hover:text-primary transition-colors block truncate">{jobDescription(job)}</a>
						<span class="text-[11px] text-muted-foreground block truncate">
							<span class="capitalize font-medium text-primary/80">{job.mode}</span> &middot; {job.model_name ?? '—'}
						</span>
					</div>
				</div>
				<div class="flex items-center gap-1.5 shrink-0 text-[11px] text-muted-foreground font-medium">
					{timeAgo(job.created_at)}
				</div>
			</div>

			<!-- Status Badges & Container States -->
			<div class="grid grid-cols-2 gap-2.5 py-2.5 border-y">
				<!-- Job Status -->
				<div class="flex flex-col gap-1">
					<span class="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Job Status</span>
					<Badge variant="outline" class="w-fit text-[9px] font-mono px-1.5 py-0.5 {statusColors[job.status] ?? ''}">
						{#if job.status === 'running'}
							<span class="mr-1 h-1 w-1 rounded-full bg-amber-500 animate-pulse"></span>
						{/if}
						{job.status}
					</Badge>
				</div>

				<!-- Container / App -->
				<div class="flex flex-col gap-1">
					<span class="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Container</span>
					{#if container}
						<Badge variant="outline" class="w-fit text-[9px] font-mono px-1.5 py-0.5 {containerStatusColors[container.status] ?? ''}">
							{#if container.status === 'running'}
								<span class="h-1 w-1 rounded-full bg-emerald-500 animate-ping mr-1"></span>
							{/if}
							{container.status}
						</Badge>
					{:else}
						<span class="text-xs text-muted-foreground/60 italic">Not created</span>
					{/if}
				</div>
			</div>

			<!-- Container Actions Box -->
			{#if container || job.status === 'completed'}
				<div class="bg-muted/30 border rounded-lg p-2.5 space-y-2">
					{#if container}
						{#if container.status === 'running'}
							{@const accessUrl = container.app_url ?? null}
							<div class="flex items-center justify-between gap-2 flex-wrap">
								<span class="text-[10.5px] text-muted-foreground font-mono">Port: {container.app_port}</span>
								{#if accessUrl}
									<Button
										id="mobile-access-app-btn-{job.id}"
										variant="outline"
										size="sm"
										href={accessUrl}
										target="_blank"
										rel="noopener noreferrer"
										class="h-8 text-emerald-600 dark:text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/10 hover:border-emerald-500/50"
									>
										<ExternalLink class="h-3 w-3 mr-1.5" />
										Access App
									</Button>
								{/if}
							</div>
						{:else if container.status === 'stopped'}
							<div class="flex items-center justify-between gap-2">
								<span class="text-xs text-muted-foreground">App is stopped.</span>
								<Button
									id="mobile-start-btn-{job.id}"
									variant="outline"
									size="sm"
									class="h-8 text-xs gap-1 hover:bg-emerald-500/10 hover:text-emerald-400 border-zinc-700/50"
									disabled={isBusy != null}
									onclick={() => onStart(job.id, container.id)}
								>
									{#if isBusy === 'start'}
										<LoaderCircle class="h-3 w-3 animate-spin" />
										Starting...
									{:else}
										<Play class="h-3 w-3 fill-current" />
										Start App
									{/if}
								</Button>
							</div>
						{:else if container.status === 'failed'}
							<div class="flex flex-col gap-1.5">
								<span class="text-[10px] text-destructive font-mono line-clamp-1">{container.last_error || 'Container failed'}</span>
								<Button
									id="mobile-restart-btn-{job.id}"
									variant="outline"
									size="sm"
									class="h-8 text-xs gap-1 hover:bg-amber-500/10 hover:text-amber-400 border-red-500/20"
									disabled={isBusy != null}
									onclick={() => onStart(job.id, container.id)}
								>
									{#if isBusy === 'start'}
										<LoaderCircle class="h-3 w-3 animate-spin" />
										Starting...
									{:else}
										<Play class="h-3 w-3" />
										Restart App
									{/if}
								</Button>
							</div>
						{:else if container.status === 'building'}
							<span class="text-xs text-amber-400/90 font-medium flex items-center gap-1.5 justify-center py-1">
								<LoaderCircle class="h-3.5 w-3.5 animate-spin" />
								Provisioning environment...
							</span>
						{/if}
					{:else if job.status === 'completed'}
						<div class="flex items-center justify-between gap-2">
							<span class="text-xs text-muted-foreground">No environment built.</span>
							<Button
								id="mobile-provision-btn-{job.id}"
								variant="outline"
								size="sm"
								class="h-8 text-xs font-semibold gap-1.5 border-primary/30 text-primary hover:bg-primary/10 hover:border-primary/50"
								disabled={isBusy != null}
								onclick={() => onBuild(job.id)}
							>
								{#if isBusy === 'build'}
									<LoaderCircle class="h-3 w-3 animate-spin mr-1.5" />
									Building...
								{:else}
									<Hammer class="h-3 w-3 mr-1.5" />
									Provision
								{/if}
							</Button>
						</div>
					{/if}
				</div>
			{/if}

			<!-- Footer Info & Actions -->
			<div class="flex items-center justify-between pt-3 border-t">
				<span class="text-[11px] font-mono text-muted-foreground">{formatDuration(job.duration_seconds)}</span>

				<!-- Mobile Actions Group -->
				<div class="flex items-center gap-1">
					{#if container}
						{#if container.status === 'running'}
							<Button
								id="mobile-action-stop-{job.id}"
								variant="ghost"
								size="sm"
								class="h-8 w-8 p-0 text-amber-500 hover:bg-amber-500/10"
								title="Stop Container"
								disabled={isBusy != null}
								onclick={() => onStop(job.id, container.id)}
							>
								{#if isBusy === 'stop'}
									<LoaderCircle class="h-3.5 w-3.5 animate-spin" />
								{:else}
									<Square class="h-3.5 w-3.5 fill-current" />
								{/if}
							</Button>
						{/if}
						{#if container.status !== 'building' && container.status !== 'pending'}
							<Button
								id="mobile-action-remove-container-{job.id}"
								variant="ghost"
								size="sm"
								class="h-8 w-8 p-0 text-destructive hover:bg-destructive/10"
								title="Remove Container"
								disabled={isBusy != null}
								onclick={() => onRemoveContainer(job.id, container.id)}
							>
								{#if isBusy === 'remove'}
									<LoaderCircle class="h-3.5 w-3.5 animate-spin" />
								{:else}
									<Server class="h-3.5 w-3.5" />
								{/if}
							</Button>
						{/if}
					{/if}

					<Button variant="ghost" size="sm" class="h-8 w-8 p-0" href="/applications/{job.id}" title="View details">
						<Eye class="h-4 w-4" />
					</Button>
					{#if job.status === 'failed' || job.status === 'cancelled'}
						<Button variant="ghost" size="sm" class="h-8 w-8 p-0 text-blue-400 hover:bg-blue-500/10" title="Retry" onclick={() => onRetry(job.id)}>
							<RotateCcw class="h-4 w-4" />
						</Button>
					{/if}
					{#if job.status === 'failed'}
						<Button variant="ghost" size="sm" class="h-8 w-8 p-0 text-destructive hover:bg-destructive/10" href="/applications/{job.id}/failure" title="Failure details">
							<AlertTriangle class="h-4 w-4" />
						</Button>
					{/if}
					{#if job.status === 'pending' || job.status === 'running'}
						<Button variant="ghost" size="sm" class="h-8 w-8 p-0 text-amber-400 hover:bg-amber-500/10" title="Cancel" onclick={() => onCancel(job.id)}>
							<Ban class="h-4 w-4" />
						</Button>
					{/if}
					<Button variant="ghost" size="sm" class="h-8 w-8 p-0 text-muted-foreground" title="Copy ID" onclick={() => onCopyId(job.id)}>
						<Copy class="h-3.5 w-3.5" />
					</Button>
					{#if job.status !== 'running'}
						<Button variant="ghost" size="sm" class="h-8 w-8 p-0 text-destructive hover:bg-destructive/10" title="Delete" onclick={() => onDelete(job.id)}>
							<Trash2 class="h-3.5 w-3.5" />
						</Button>
					{/if}
				</div>
			</div>
		</div>
	{/each}
</div>
