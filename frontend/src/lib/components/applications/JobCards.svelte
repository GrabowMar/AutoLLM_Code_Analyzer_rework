<script lang="ts">
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { jobDisplayStatus, jobPhaseTones } from '$lib/components/generator/batches';
	import { containerTones } from '$lib/constants/colors';
	import { Button } from '$lib/components/ui/button';
	import { formatDuration } from '$lib/utils/formatters';
	import {
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
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import { shortModelName } from '$lib/components/table';

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

<div class="data-list">
	{#each jobs as job (job.id)}
		{@const container = containersByJobId[job.id]}
		{@const isBusy = containerBusy[job.id]}
		{@const ModeIcon = job.mode === 'custom' ? Pencil : job.mode === 'scaffolding' ? Layers : Bot}
		<article class="data-list-row space-y-2.5">
			<!-- Identity -->
			<div class="flex items-start gap-3">
				<span class="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md border border-border bg-muted/50 text-muted-foreground" title={job.mode}>
					<ModeIcon class="size-4" aria-hidden="true" />
				</span>
				<div class="min-w-0 flex-1">
					<div class="flex items-baseline justify-between gap-2">
						<a href="/applications/{job.id}" class="truncate text-sm font-medium hover:text-primary">{jobDescription(job)}</a>
						<time class="shrink-0 font-mono text-[11px] text-muted-foreground" datetime={job.created_at}>{timeAgo(job.created_at)}</time>
					</div>
					<p class="truncate text-xs text-muted-foreground" title={job.model_name ?? undefined}>
						{shortModelName(job.model_name) || '—'}
						<span class="opacity-50">·</span> <span class="capitalize">{job.mode}</span>{#if job.harness}<span class="opacity-50"> ·</span> <span class="font-mono">{job.harness}</span>{/if}
					</p>
				</div>
			</div>

			<!-- State: job + app, with numbers -->
			<div class="flex flex-wrap items-center gap-x-3 gap-y-1.5 pl-11 text-xs">
				<StatusBadge status={jobDisplayStatus(job)} tones={jobPhaseTones} />
				{#if container}<StatusBadge status={container.status} tones={containerTones} />{/if}
				<span class="font-mono tabular-nums text-muted-foreground">{formatDuration(job.duration_seconds)}</span>
				{#if job.cost_usd != null && job.cost_usd > 0}<span class="font-mono tabular-nums text-muted-foreground">${job.cost_usd.toFixed(job.cost_usd < 0.1 ? 3 : 2)}</span>{/if}
				{#if job.pass_rate != null}<span class="font-mono tabular-nums text-muted-foreground" title="Smoke-test pass rate">smoke {(job.pass_rate * 100).toFixed(0)}%</span>{/if}
			</div>
			{#if job.error_message}
				<p class="flex items-center gap-1 pl-11 text-[11px] text-danger-fg" title={job.error_message}>
					<AlertTriangle class="size-3 shrink-0" aria-hidden="true" /><span class="truncate">{job.error_message}</span>
				</p>
			{:else if container?.status === 'failed'}
				<p class="truncate pl-11 font-mono text-[11px] text-danger-fg" title={container.last_error}>{container.last_error || 'Container failed'}</p>
			{/if}

			<!-- Actions: one next step on the left, the rest as icons -->
			<div class="flex items-center justify-between gap-2 pl-11">
				<div>
					{#if container?.status === 'running' && container.app_url}
						<Button id="mobile-access-app-btn-{job.id}" variant="outline" size="sm" href={container.app_url} target="_blank" rel="noopener noreferrer" class="h-9 text-primary">
							<ExternalLink class="size-3.5" />Open app
						</Button>
					{:else if container && (container.status === 'stopped' || container.status === 'failed')}
						<Button id="mobile-start-btn-{job.id}" variant="outline" size="sm" class="h-9" disabled={isBusy != null} onclick={() => onStart(job.id, container.id)}>
							{#if isBusy === 'start'}<LoaderCircle class="size-3.5 animate-spin" />Starting…{:else}<Play class="size-3.5" />{container.status === 'failed' ? 'Restart' : 'Start'}{/if}
						</Button>
					{:else if container?.status === 'building'}
						<span class="flex items-center gap-1.5 text-xs text-warning-fg"><LoaderCircle class="size-3.5 animate-spin" />Provisioning…</span>
					{:else if !container && job.status === 'completed'}
						<Button id="mobile-provision-btn-{job.id}" variant="outline" size="sm" class="h-9" disabled={isBusy != null} onclick={() => onBuild(job.id)}>
							{#if isBusy === 'build'}<LoaderCircle class="size-3.5 animate-spin" />Building…{:else}<Hammer class="size-3.5" />Provision{/if}
						</Button>
					{/if}
				</div>
				<div class="flex items-center">
					{#if container?.status === 'running'}
						{@render iconAction(`mobile-action-stop-${job.id}`, 'Stop container', Square, () => onStop(job.id, container.id), isBusy === 'stop', isBusy != null)}
					{/if}
					{#if container && container.status !== 'building' && container.status !== 'pending'}
						{@render iconAction(`mobile-action-remove-container-${job.id}`, 'Remove container (keeps the code)', Server, () => onRemoveContainer(job.id, container.id), isBusy === 'remove', isBusy != null, true)}
					{/if}
					{#if job.status === 'failed' || job.status === 'cancelled'}
						{@render iconAction(`mobile-action-retry-${job.id}`, 'Retry job', RotateCcw, () => onRetry(job.id))}
					{/if}
					{#if job.status === 'failed'}
						<Button variant="ghost" size="sm" class="size-9 p-0 text-danger-fg" href="/applications/{job.id}/failure" title="Failure details" aria-label="Failure details">
							<AlertTriangle class="size-4" />
						</Button>
					{/if}
					{#if job.status === 'pending' || job.status === 'running'}
						{@render iconAction(`mobile-action-cancel-${job.id}`, 'Cancel job', Ban, () => onCancel(job.id))}
					{/if}
					{@render iconAction(`mobile-action-copy-${job.id}`, 'Copy job ID', Copy, () => onCopyId(job.id))}
					{#if job.status !== 'running'}
						{@render iconAction(`mobile-action-delete-${job.id}`, 'Delete job', Trash2, () => onDelete(job.id), false, false, true)}
					{/if}
					<Button variant="ghost" size="sm" class="size-9 p-0 text-muted-foreground" href="/applications/{job.id}" title="View details" aria-label="View details">
						<ChevronRight class="size-4" />
					</Button>
				</div>
			</div>
		</article>
	{/each}
</div>

{#snippet iconAction(id: string, label: string, Icon: typeof Eye, onclick: () => void, busy = false, disabled = false, danger = false)}
	<Button {id} variant="ghost" size="sm" class="size-9 p-0 text-muted-foreground {danger ? 'hover:bg-destructive/10 hover:text-danger-fg' : ''}" title={label} aria-label={label} {disabled} {onclick}>
		{#if busy}<LoaderCircle class="size-4 animate-spin" />{:else}<Icon class="size-4" />{/if}
	</Button>
{/snippet}
