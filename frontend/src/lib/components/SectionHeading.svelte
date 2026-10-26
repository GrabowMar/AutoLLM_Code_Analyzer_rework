<script lang="ts">
	/**
	 * SectionHeading — the one labelled divider of the app: an optional mono
	 * number, the title, then an ink rule running to the right edge (or to the
	 * count / aside), and an optional lead flush below.
	 *
	 *   2.1  Design ──────────────────────────────── [aside]
	 *   One sentence on what the section holds.
	 *
	 * Sizes:
	 * - `lg` — a section of the page on the canvas (serif title), e.g. the
	 *   experiment page's "2.1 Design" (`StudySection` wraps it).
	 * - `sm` — a group label over a run of cards, or between groups inside a
	 *   card (mono caps, the sidebar's group motif), e.g. Settings' "Account".
	 *
	 * Props: `id` (the heading's id, for `aria-labelledby`), `title`, `number`,
	 * `size` ('lg'), `level` (2), `count` (trailing figure, "4 pages"), `lead`,
	 * `aside` snippet (controls at the end of the line), `class`.
	 */
	import type { Snippet } from 'svelte';
	import { cn } from '$lib/utils';

	interface Props {
		id?: string;
		title: string;
		number?: string | number;
		size?: 'lg' | 'sm';
		level?: 2 | 3 | 4;
		count?: string | number;
		lead?: string;
		aside?: Snippet;
		class?: string;
	}

	let { id, title, number, size = 'lg', level = 2, count, lead, aside, class: className }: Props = $props();
</script>

<div class={cn('section-heading', className)} data-size={size}>
	<div class="section-heading-line">
		{#if number != null}<span class="section-heading-num" aria-hidden="true">{number}</span>{/if}
		<svelte:element this={`h${level}`} {id} class="section-heading-title">{title}</svelte:element>
		<span class="section-heading-rule" aria-hidden="true"></span>
		{#if count != null}<span class="section-heading-count">{count}</span>{/if}
		{#if aside}<div class="section-heading-aside">{@render aside()}</div>{/if}
	</div>
	{#if lead}<p class="section-heading-lead">{lead}</p>{/if}
</div>
