<script lang="ts">
	/**
	 * StudySection — a numbered section of the experiment page, written like a
	 * paper: a `SectionHeading` ("2.3  Conditions ────"), an optional
	 * one-paragraph lead, then the content.
	 *
	 * Props:
	 * - `id`: anchor (the heading's id is `{id}-title`).
	 * - `number`: section number ("2.3").
	 * - `title`, `lead` (optional muted paragraph).
	 * - `aside`: right-aligned controls on the heading line.
	 * - `children`: the section body.
	 */
	import type { Snippet } from 'svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';

	interface Props {
		id: string;
		number: string;
		title: string;
		lead?: string;
		aside?: Snippet;
		children: Snippet;
	}

	let { id, number, title, lead, aside, children }: Props = $props();
</script>

<section {id} aria-labelledby="{id}-title" class="scroll-mt-16 space-y-3">
	<SectionHeading id="{id}-title" {number} {title} {lead} {aside} />
	{@render children()}
</section>
