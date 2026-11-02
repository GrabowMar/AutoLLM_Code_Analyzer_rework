<script lang="ts">
	/**
	 * ObservationCard — one bug / vulnerability / note a participant reported,
	 * read-only: kind, title, app, the TESTER's own severity (labelled as such),
	 * category, CWE, checklist item, location, description, steps and image
	 * attachments (inline thumbnails opening the full image).
	 *
	 * Everything the participant typed renders as text; the location becomes a
	 * link only when it is an absolute http(s) URL (noopener noreferrer).
	 *
	 * Props: `observation` (Observation or ResearcherObservation), `appLabel`
	 * (the app shown in that slot), `who` (participant label, task-wide lists),
	 * `onopen()` (open its assignment), `checklistLabel(id)`.
	 */
	import { severityColors } from '$lib/constants/colors';
	import type { Observation } from '$lib/api/human-eval';
	import { formatDateTime } from '$lib/utils/formatters';
	import { cn } from '$lib/utils';
	import { OBSERVATION_KIND_LABELS, formatBytes, safeHttpUrl, testerSeverityLabel } from '../format';
	import Bug from '@lucide/svelte/icons/bug';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import MessageSquare from '@lucide/svelte/icons/message-square';
	import ShieldAlert from '@lucide/svelte/icons/shield-alert';
	import type { Component } from 'svelte';

	interface Props {
		observation: Observation;
		appLabel?: string;
		who?: string;
		onopen?: () => void;
		checklistLabel?: (id: string) => string;
	}

	let { observation: o, appLabel = '', who = '', onopen, checklistLabel }: Props = $props();

	const ICONS: Record<string, Component> = { bug: Bug, vulnerability: ShieldAlert, note: MessageSquare };
	const Icon = $derived(ICONS[o.kind] ?? MessageSquare);
	const locationUrl = $derived(safeHttpUrl(o.location));
	/** Attachments are served by our own API; anything else is not rendered. */
	const images = $derived(o.attachments.filter((a) => a.url.startsWith('/api/human-eval/')));
</script>

<article class="space-y-2 rounded-md border border-border bg-card p-3">
	<header class="flex flex-wrap items-start justify-between gap-2">
		<div class="flex min-w-0 items-start gap-2">
			<Icon class="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
			<div class="min-w-0">
				<h4 class="break-words text-sm font-medium">{o.title}</h4>
				<p class="text-xs text-muted-foreground">
					{OBSERVATION_KIND_LABELS[o.kind] ?? o.kind}
					{#if appLabel} · {o.app_slot === 'single' ? appLabel : `${o.app_slot} (${appLabel})`}{:else if o.app_slot !== 'single'} · side {o.app_slot}{/if}
					{#if who} · {who}{/if}
					· {formatDateTime(o.created_at)}
				</p>
			</div>
		</div>
		<div class="flex flex-wrap items-center gap-1.5">
			{#if o.kind !== 'note'}
				<span class="text-[11px] text-muted-foreground">Tester’s severity</span>
				{#if o.severity}
					<span class={cn('rounded-full border px-2 py-px text-[11px] font-medium', severityColors[o.severity])}>{testerSeverityLabel(o.severity)}</span>
				{:else}
					<span class="text-[11px] text-muted-foreground">not given</span>
				{/if}
			{/if}
			{#if onopen}
				<button type="button" class="cursor-pointer text-xs text-primary hover:underline" onclick={onopen}>Open assignment</button>
			{/if}
		</div>
	</header>

	{#if o.category || o.cwe || o.checklist_item_id || o.location}
		<dl class="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-[auto_1fr]">
			{#if o.category}<dt class="text-muted-foreground">Category</dt><dd class="break-words">{o.category}</dd>{/if}
			{#if o.cwe}<dt class="text-muted-foreground">CWE</dt><dd class="font-mono">{o.cwe}</dd>{/if}
			{#if o.checklist_item_id}
				<dt class="text-muted-foreground">Checklist item</dt>
				<dd class="break-words">{checklistLabel?.(o.checklist_item_id) ?? o.checklist_item_id}</dd>
			{/if}
			{#if o.location}
				<dt class="text-muted-foreground">Location</dt>
				<dd class="min-w-0 break-all font-mono">
					{#if locationUrl}
						<a href={locationUrl} target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-primary hover:underline">
							{o.location}<ExternalLink class="size-3 shrink-0" aria-hidden="true" />
						</a>
					{:else}
						{o.location}
					{/if}
				</dd>
			{/if}
		</dl>
	{/if}

	{#if o.description}
		<div>
			<p class="section-label">Description</p>
			<p class="whitespace-pre-wrap break-words text-sm">{o.description}</p>
		</div>
	{/if}
	{#if o.steps}
		<div>
			<p class="section-label">Steps to reproduce</p>
			<p class="whitespace-pre-wrap break-words text-sm">{o.steps}</p>
		</div>
	{/if}

	{#if images.length > 0}
		<ul class="flex flex-wrap gap-2" aria-label="Screenshots">
			{#each images as att (att.id)}
				<li>
					<a href={att.url} target="_blank" rel="noopener noreferrer" class="block overflow-hidden rounded border border-border hover:border-primary/50" title="{att.content_type} · {formatBytes(att.size)} — open full size">
						<img src={att.url} alt="Screenshot attached to “{o.title}”" loading="lazy" class="h-28 w-auto max-w-56 object-contain bg-muted" />
					</a>
				</li>
			{/each}
		</ul>
	{/if}
</article>
