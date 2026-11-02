<script lang="ts">
	/**
	 * HostingCell — one app's hosting state for the researcher: state badge
	 * (pulsing while building/starting), pinned marker, build progress, the
	 * server's status line, the error and the container identity. All text is
	 * rendered as text.
	 *
	 * Props: `info` (ResearcherHostingInfo), `reachable` (participants can open apps).
	 */
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import type { ResearcherHostingInfo } from '$lib/api/human-eval';
	import { HOSTING_STATE_LABELS, hostingTones } from '../format';
	import Pin from '@lucide/svelte/icons/pin';

	interface Props {
		info: ResearcherHostingInfo;
		reachable?: boolean;
	}

	let { info, reachable = true }: Props = $props();

	const containerLine = $derived(
		[info.container_name, info.container_status && info.container_status !== info.state ? info.container_status : '']
			.filter(Boolean)
			.join(' · ')
	);
</script>

<div class="min-w-0 space-y-0.5">
	<div class="flex flex-wrap items-center gap-1.5">
		<StatusBadge status={info.state} tones={hostingTones} label={HOSTING_STATE_LABELS[info.state] ?? info.state} />
		{#if info.pinned}
			<span class="inline-flex items-center gap-0.5 text-[11px] text-muted-foreground" title="Pinned: never idle-stopped or evicted">
				<Pin class="size-3" aria-hidden="true" />pinned
			</span>
		{/if}
		{#if info.container_id && !info.started_by_task}
			<span class="text-[11px] text-muted-foreground" title="This container was already running; the task adopted it and will not stop it on its own">adopted</span>
		{/if}
	</div>
	{#if info.progress != null && (info.state === 'building' || info.state === 'starting')}
		<div class="flex items-center gap-1.5" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={info.progress} aria-label="Build progress">
			<span class="h-1 w-20 overflow-hidden rounded-full bg-muted"><span class="block h-full bg-info" style="width: {info.progress}%"></span></span>
			<span class="font-mono text-[11px] tabular-nums text-muted-foreground">{Math.round(info.progress)}%</span>
		</div>
	{/if}
	{#if info.message}
		<p class="cell-sub max-w-72 break-words text-xs text-muted-foreground">{info.message}</p>
	{/if}
	{#if info.error}
		<p class="max-w-72 break-words text-xs text-danger-fg">{info.error}</p>
	{/if}
	{#if !reachable && info.state === 'running'}
		<p class="text-xs text-warning-fg">Running, but participants can't reach apps in this routing mode.</p>
	{/if}
	{#if containerLine}
		<p class="cell-sub truncate font-mono text-[11px] text-muted-foreground" title={info.container_id ?? undefined}>{containerLine}</p>
	{/if}
</div>
