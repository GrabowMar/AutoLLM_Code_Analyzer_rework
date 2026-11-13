<script lang="ts">
	/**
	 * StudyFigure — a numbered figure or table of the study record:
	 * "FIGURE 1" eyebrow and a serif title above the body, controls on the
	 * right, and a muted caption below (estimator, n, what the marks mean) —
	 * the way a paper prints them.
	 *
	 * Props:
	 * - `id`: anchor; `kind`: 'figure' | 'table'; `number` (1, 2, "M1" …).
	 * - `title`: one line saying what is plotted.
	 * - `flush`: no body padding (flush tables, which bring their own frame).
	 * - `controls`, `caption`, `children` snippets.
	 */
	import type { Snippet } from 'svelte';
	import { cn } from '$lib/utils';

	interface Props {
		id: string;
		kind?: 'figure' | 'table';
		number: number | string;
		title: string;
		flush?: boolean;
		/** Fold the body behind a "Show" toggle (long tables); `summary` says what is inside. */
		collapsible?: boolean;
		summary?: string;
		class?: string;
		controls?: Snippet;
		caption?: Snippet;
		children: Snippet;
	}

	let { id, kind = 'figure', number, title, flush = false, collapsible = false, summary, class: className, controls, caption, children }: Props = $props();
	let open = $state(false);
	const shown = $derived(!collapsible || open);
</script>

<figure
	{id}
	aria-labelledby="{id}-title"
	class={cn('min-w-0 scroll-mt-16 overflow-clip rounded-md border border-border bg-card shadow-[var(--shadow-card)]', className)}
>
	<div class="flex flex-wrap items-end justify-between gap-x-4 gap-y-2 border-b border-border/60 px-4 py-2.5">
		<div class="min-w-0 space-y-0.5">
			<p class="section-label">{kind === 'table' ? 'Table' : 'Figure'} {number}</p>
			<h3 id="{id}-title" class="font-display text-[17px] font-semibold leading-snug tracking-tight">{title}</h3>
		</div>
		<div class="flex flex-wrap items-center gap-x-4 gap-y-2">
			{#if controls && shown}{@render controls()}{/if}
			{#if collapsible}
				{#if summary && !open}<span class="text-xs text-muted-foreground">{summary}</span>{/if}
				<button
					type="button"
					class="inline-flex h-7 cursor-pointer items-center rounded-md border border-border px-2.5 text-xs font-medium hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
					aria-expanded={open}
					aria-controls="{id}-body"
					onclick={() => (open = !open)}>{open ? 'Hide' : 'Show'}</button
				>
			{/if}
		</div>
	</div>
	{#if shown}
		<div id="{id}-body" class={cn('min-w-0', !flush && 'p-4')}>
			{@render children()}
		</div>
	{/if}
	{#if caption && shown}
		<figcaption class="border-t border-border/60 px-4 py-2.5 text-xs leading-relaxed text-muted-foreground">
			{@render caption()}
		</figcaption>
	{/if}
</figure>
