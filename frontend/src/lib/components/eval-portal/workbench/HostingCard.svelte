<script lang="ts">
	/**
	 * HostingCard — where an app stands while it is not on screen: state badge,
	 * the server's message (queue position, build progress, error), a Start /
	 * Try again button and the "Open in new tab" link (http(s) only, with
	 * rel="noopener noreferrer"). Also explains why a running app is not framed.
	 *
	 * Props: `app` (slot + label), `hosting`, `decision` (frameDecision),
	 * `readOnly`, `busy` (a start request is running), `onstart()`, `onopen()`
	 * (the participant opened the app in another tab), `class`.
	 */
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Play from '@lucide/svelte/icons/play';
	import RotateCw from '@lucide/svelte/icons/rotate-cw';
	import type { HostingInfo, PortalApp } from '$lib/api/types/human-eval';
	import { Button } from '$lib/components/ui/button';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { cn } from '$lib/utils';
	import { HOSTING_LABELS, canRequestStart, hostingSentence, hostingTones, type FrameDecision } from './hosting';
	import { slotTitle } from './workbench';

	interface Props {
		app: Pick<PortalApp, 'slot' | 'label'>;
		hosting: HostingInfo;
		decision: FrameDecision;
		readOnly?: boolean;
		busy?: boolean;
		onstart?: () => void;
		onopen?: () => void;
		class?: string;
	}

	let { app, hosting, decision, readOnly = false, busy = false, onstart, onopen, class: className }: Props = $props();

	const sentence = $derived(hostingSentence(hosting));
	const transitional = $derived(['queued', 'building', 'starting', 'stopping'].includes(hosting.state));
	const progress = $derived(
		hosting.progress !== null && hosting.progress !== undefined ? Math.max(0, Math.min(100, Math.round(hosting.progress))) : null
	);
</script>

<div class={cn('w-full max-w-md space-y-3 rounded-md border border-border bg-card px-4 py-4 shadow-[var(--shadow-card)]', className)}>
	<div class="flex flex-wrap items-center justify-between gap-2">
		<p class="font-display text-base font-semibold">{slotTitle(app)}</p>
		<StatusBadge status={hosting.state} tones={hostingTones} label={HOSTING_LABELS[hosting.state] ?? hosting.state} />
	</div>

	<div aria-live="polite" class="space-y-2">
		{#if sentence}
			<p class={cn('text-sm', hosting.state === 'failed' ? 'text-danger-fg' : 'text-muted-foreground')}>{sentence}</p>
		{/if}
		{#if hosting.state === 'failed' && hosting.error && hosting.error !== sentence}
			<p class="break-words font-mono text-xs text-muted-foreground">{hosting.error}</p>
		{/if}
		{#if transitional}
			{#if progress !== null}
				<div
					class="h-1.5 w-full overflow-hidden rounded-full bg-muted"
					role="progressbar"
					aria-label="Progress"
					aria-valuemin={0}
					aria-valuemax={100}
					aria-valuenow={progress}
				>
					<div class="h-full rounded-full bg-primary transition-[width] duration-300" style="width: {progress}%"></div>
				</div>
				<p class="font-mono text-[11px] text-muted-foreground tabular-nums">{progress}%</p>
			{:else}
				<p class="flex items-center gap-1.5 text-xs text-muted-foreground">
					<LoaderCircle class="size-3.5 animate-spin" aria-hidden="true" />
					Checking again every few seconds…
				</p>
			{/if}
		{/if}
		{#if hosting.state === 'running' && decision.reason}
			<p class="text-xs text-muted-foreground">{decision.reason}</p>
		{/if}
	</div>

	<div class="flex flex-wrap items-center gap-2">
		{#if !readOnly && canRequestStart(hosting.state) && onstart}
			<Button size="sm" onclick={onstart} disabled={busy} aria-busy={busy}>
				{#if busy}
					<LoaderCircle class="animate-spin" />
				{:else if hosting.state === 'failed'}
					<RotateCw />
				{:else}
					<Play />
				{/if}
				{hosting.state === 'failed' ? 'Try again' : 'Start app'}
			</Button>
		{/if}
		{#if hosting.state === 'running' && decision.canOpen && decision.url}
			<Button
				size="sm"
				variant={decision.canFrame ? 'outline' : 'default'}
				href={decision.url}
				target="_blank"
				rel="noopener noreferrer"
				onclick={onopen}
			>
				<ExternalLink />
				Open in new tab
			</Button>
		{/if}
	</div>
</div>
