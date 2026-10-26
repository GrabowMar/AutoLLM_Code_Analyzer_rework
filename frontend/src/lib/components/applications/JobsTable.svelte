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
	import ProviderIcon from '$lib/components/ProviderIcon.svelte';
	import { SortableTh, rowLink, tableFrame, shortModelName, modelNamePrefix } from '$lib/components/table';

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

<!-- Rendered inside the page's table card, under its filter strip and toolbar. -->
<div class="table-frame" {@attach tableFrame}>
	<table>
		<thead>
			<tr>
				<SortableTh label="Application" field="created_at" {sortBy} {sortDir} onSort={onToggleSort} help="Sorted by creation time" />
				<SortableTh label="Model" field="model_name" {sortBy} {sortDir} onSort={onToggleSort} ascOnly />
				<th>Status</th>
				<th>App</th>
				<SortableTh label="Duration" field="duration_seconds" {sortBy} {sortDir} onSort={onToggleSort} align="right" />
				<th class="text-right"><span class="sr-only">Actions</span></th>
			</tr>
		</thead>
		<tbody>
			{#each jobs as job (job.id)}
				{@const container = containersByJobId[job.id]}
				{@const isBusy = containerBusy[job.id]}
				{@const href = `/applications/${job.id}`}
				{@const ModeIcon = job.mode === 'custom' ? Pencil : job.mode === 'scaffolding' ? Layers : Bot}
				<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (the name link is the keyboard path) -->
				<tr class="row-link group" onclick={rowLink(href)}>
					<!-- Application -->
					<td class="max-w-0 w-[30%]">
						<div class="flex items-center gap-2.5">
							<span class="flex size-7 shrink-0 items-center justify-center rounded-md border border-border bg-muted/50 text-muted-foreground transition-colors group-hover:text-primary" title={job.mode}>
								<ModeIcon class="size-3.5" aria-hidden="true" />
							</span>
							<div class="min-w-0">
								<a {href} class="block truncate font-medium hover:text-primary" title={jobDescription(job)}>{jobDescription(job)}</a>
								<p class="cell-sub truncate text-[11px] text-muted-foreground">
									<span class="capitalize">{job.mode}</span>
									{#if job.harness}<span class="mx-1 opacity-50">·</span><span class="font-mono" title="Harness">{job.harness}</span>{/if}
									<span class="mx-1 opacity-50">·</span><time class="font-mono tabular-nums" datetime={job.created_at} title={new Date(job.created_at).toLocaleString()}>{timeAgo(job.created_at)}</time>
								</p>
							</div>
						</div>
					</td>

					<!-- Model -->
					<td class="max-w-0 w-[22%]">
						{#if job.model_name}
							{@const provider = modelNamePrefix(job.model_name) || job.model_id_str?.split('/')[0] || ''}
							<div class="flex items-center gap-2" title={job.model_name}>
								{#if job.model_id_str}<ProviderIcon provider={job.model_id_str.split('/')[0]} size={14} class="shrink-0" />{/if}
								<div class="min-w-0">
									<span class="block truncate">{shortModelName(job.model_name)}</span>
									{#if provider}<span class="cell-sub block truncate text-[11px] text-muted-foreground">{provider}</span>{/if}
								</div>
							</div>
						{:else}
							<span class="text-muted-foreground">—</span>
						{/if}
					</td>

					<!-- Status -->
					<td class="whitespace-nowrap">
						<StatusBadge status={jobDisplayStatus(job)} tones={jobPhaseTones} />
						{#if job.error_message}
							<p class="cell-sub mt-1 flex max-w-[200px] items-center gap-1 text-[11px] text-danger-fg" title={job.error_message}>
								<AlertTriangle class="size-3 shrink-0" aria-hidden="true" />
								<span class="truncate">{job.error_message}</span>
							</p>
						{:else if job.pass_rate != null}
							<p class="cell-sub mt-1 font-mono text-[11px] text-muted-foreground tabular-nums" title="Share of checks passed: the conformance suite when the run was graded, otherwise the runtime smoke test">pass {(job.pass_rate * 100).toFixed(0)}%</p>
						{/if}
					</td>

					<!-- App / container: state + the one next step -->
					<td class="whitespace-nowrap">
						{#if container}
							<div class="flex items-center gap-2">
								<StatusBadge status={container.status} tones={containerTones} />
								{#if container.status === 'running' && container.app_url}
									<Button
										id="access-app-btn-{job.id}"
										variant="ghost"
										size="sm"
										href={container.app_url}
										target="_blank"
										rel="noopener noreferrer"
										class="h-7 gap-1 px-2 text-xs text-primary hover:text-primary"
										title="Open the app on port {container.app_port}"
									>
										Open<ExternalLink class="size-3" />
									</Button>
								{:else if container.status === 'stopped' || container.status === 'failed'}
									<Button
										id="start-container-btn-{job.id}"
										variant="ghost"
										size="sm"
										class="h-7 gap-1 px-2 text-xs"
										disabled={isBusy != null}
										title={container.status === 'failed' ? container.last_error || 'Container crashed' : undefined}
										onclick={() => onStart(job.id, container.id)}
									>
										{#if isBusy === 'start'}<LoaderCircle class="size-3 animate-spin" />Starting…{:else}<Play class="size-3" />{container.status === 'failed' ? 'Restart' : 'Start'}{/if}
									</Button>
								{/if}
							</div>
						{:else if job.status === 'completed'}
							<Button
								id="provision-app-btn-{job.id}"
								variant="ghost"
								size="sm"
								class="-ml-2 h-7 gap-1.5 px-2 text-xs text-muted-foreground hover:text-primary"
								disabled={isBusy != null}
								title="Build and start a container for this app"
								onclick={() => onBuild(job.id)}
							>
								{#if isBusy === 'build'}<LoaderCircle class="size-3.5 animate-spin" />Building…{:else}<Hammer class="size-3.5" />Provision{/if}
							</Button>
						{:else}
							<span class="text-muted-foreground">—</span>
						{/if}
					</td>

					<!-- Duration -->
					<td class="num">
						<span class="text-xs">{formatDuration(job.duration_seconds)}</span>
						{#if job.cost_usd != null && job.cost_usd > 0}
							<span class="cell-sub block text-[11px] text-muted-foreground">${job.cost_usd.toFixed(job.cost_usd < 0.1 ? 3 : 2)}</span>
						{/if}
					</td>

					<!-- Actions: view stays visible, the rest appear on hover/focus -->
					<td class="w-px whitespace-nowrap">
						<div class="flex items-center justify-end gap-0.5">
							<div class="row-actions-more flex items-center gap-0.5">
								{#if container?.status === 'running'}
									{@render iconAction(`action-stop-${job.id}`, 'Stop container', Square, () => onStop(job.id, container.id), isBusy === 'stop', isBusy != null)}
								{/if}
								{#if container && container.status !== 'building' && container.status !== 'pending'}
									{@render iconAction(`action-remove-container-${job.id}`, 'Remove container (keeps the code)', Server, () => onRemoveContainer(job.id, container.id), isBusy === 'remove', isBusy != null, true)}
								{/if}
								{#if job.status === 'failed' || job.status === 'cancelled'}
									{@render iconAction(`action-retry-${job.id}`, 'Retry job', RotateCcw, () => onRetry(job.id))}
								{/if}
								{#if job.status === 'pending' || job.status === 'running'}
									{@render iconAction(`action-cancel-${job.id}`, 'Cancel job', Ban, () => onCancel(job.id))}
								{/if}
								{@render iconAction(`action-copy-${job.id}`, 'Copy job ID', Copy, () => onCopyId(job.id))}
								{#if job.status !== 'running'}
									{@render iconAction(`action-delete-${job.id}`, 'Delete job', Trash2, () => onDelete(job.id), false, false, true)}
								{/if}
							</div>
							{#if job.status === 'failed'}
								<Button id="action-failure-{job.id}" variant="ghost" size="sm" class="size-8 p-0 text-danger-fg hover:bg-destructive/10" href="/applications/{job.id}/failure" title="Failure logs" aria-label="Failure logs">
									<AlertTriangle class="size-4" />
								</Button>
							{/if}
							<Button id="action-view-{job.id}" variant="ghost" size="sm" class="size-8 p-0 text-muted-foreground hover:text-foreground" {href} title="View details" aria-label="View details">
								<ChevronRight class="size-4" />
							</Button>
						</div>
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>

{#snippet iconAction(id: string, label: string, Icon: typeof Eye, onclick: () => void, busy = false, disabled = false, danger = false)}
	<Button
		{id}
		variant="ghost"
		size="sm"
		class="size-8 p-0 text-muted-foreground {danger ? 'hover:bg-destructive/10 hover:text-danger-fg' : 'hover:text-foreground'}"
		title={label}
		aria-label={label}
		{disabled}
		{onclick}
	>
		{#if busy}<LoaderCircle class="size-4 animate-spin" />{:else}<Icon class="size-4" />{/if}
	</Button>
{/snippet}
