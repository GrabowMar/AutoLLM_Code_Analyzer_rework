<script lang="ts">
	import type { Snippet } from 'svelte';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import { page } from '$app/state';
	import { navSectionFor } from '$lib/nav';

	interface Props {
		title: string;
		description?: string;
		/** Parent page link shown above the title on detail/create pages. */
		back?: { href: string; label: string };
		/** Browser tab title; defaults to `title`. */
		documentTitle?: string;
		/** Inline badges/status next to the title. */
		meta?: Snippet;
		/** Right-aligned page actions (primary action last). */
		actions?: Snippet;
		/** Extra content under the description (chips, stats line). */
		children?: Snippet;
	}

	let { title, description, back, documentTitle, meta, actions, children }: Props = $props();

	const owner = $derived(navSectionFor(page.url?.pathname ?? '/'));
</script>

<svelte:head>
	<title>{documentTitle ?? title} · LLM Lab</title>
</svelte:head>

<header class="page-header">
	{#if back}
		<a href={back.href} class="back-link">
			<ArrowLeft class="h-3.5 w-3.5" aria-hidden="true" />
			{back.label}
		</a>
	{:else if owner}
		<p class="page-eyebrow">
			<span class="tabular-nums text-primary">{String(owner.index).padStart(2, '0')}</span>
			<span aria-hidden="true">·</span>
			{owner.section.title}
		</p>
	{/if}
	<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
		<div class="min-w-0 space-y-1">
			<div class="flex min-w-0 flex-wrap items-center gap-2">
				<h1 class="min-w-0 break-words">{title}</h1>
				{@render meta?.()}
			</div>
			{#if description}
				<p class="max-w-3xl">{description}</p>
			{/if}
			{@render children?.()}
		</div>
		{#if actions}
			<div class="flex shrink-0 flex-wrap items-center gap-2">{@render actions()}</div>
		{/if}
	</div>
</header>
