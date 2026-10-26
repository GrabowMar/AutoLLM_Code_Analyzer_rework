<script lang="ts">
	import type { Snippet } from 'svelte';
	import { Popover } from 'bits-ui';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import CircleHelp from '@lucide/svelte/icons/circle-help';
	import { pageTitle } from '$lib/stores/page-title.svelte';

	interface Props {
		title: string;
		description?: string;
		/** Parent page; rendered as a back button before the title on detail/create pages. */
		back?: { href: string; label: string };
		/** Browser tab title; defaults to `title`. */
		documentTitle?: string;
		/** Inline badges/status next to the title. */
		meta?: Snippet;
		/** Right-aligned page actions (primary action last). */
		actions?: Snippet;
		/** Reference text (glossary, how-it-works) in a popover behind a `?` next to the title. */
		help?: Snippet;
		/** A short line under the description (stats chips, IDs); rendered as the card's footer strip. */
		children?: Snippet;
		/** Visual before the title (avatar, entity icon). */
		leading?: Snippet;
		/**
		 * Page-level navigation or mode switch (a `Tabs.List`, a `Segmented`), flush along the card's
		 * bottom edge. Wrap the page in `Tabs.Root` so the list and its panels share state.
		 */
		nav?: Snippet;
	}

	let { title, description, back, documentTitle, meta, actions, help, children, leading, nav }: Props = $props();

	$effect(() => {
		pageTitle.current = title;
		return () => {
			if (pageTitle.current === title) pageTitle.current = '';
		};
	});
</script>

<svelte:head>
	<title>{documentTitle ?? title} · LLM Lab</title>
</svelte:head>

<header class="page-header">
	<div class="page-header-main">
		{#if back}
			<a href={back.href} class="page-header-back" aria-label="Back to {back.label}" title="Back to {back.label}">
				<ArrowLeft class="size-4" aria-hidden="true" />
			</a>
		{/if}
		{@render leading?.()}
		<div class="page-header-body">
			<div class="page-header-titlebar">
				<h1>{title}</h1>
				{#if help}
					<Popover.Root>
						<Popover.Trigger class="page-header-help-trigger" aria-label="About this page">
							<CircleHelp class="size-4" aria-hidden="true" />
						</Popover.Trigger>
						<Popover.Portal>
							<Popover.Content side="bottom" align="start" sideOffset={6} collisionPadding={12} class="page-header-help">
								{@render help()}
							</Popover.Content>
						</Popover.Portal>
					</Popover.Root>
				{/if}
				{@render meta?.()}
			</div>
			{#if description}
				<p class="page-header-desc">{description}</p>
			{/if}
		</div>
		{#if actions}
			<div class="page-header-actions">{@render actions()}</div>
		{/if}
	</div>
	{#if children}
		<div class="page-header-extra">{@render children()}</div>
	{/if}
	{#if nav}
		<div class="page-header-nav">{@render nav()}</div>
	{/if}
</header>
