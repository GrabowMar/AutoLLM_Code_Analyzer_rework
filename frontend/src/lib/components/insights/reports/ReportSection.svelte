<script lang="ts">
	/** A titled card section of a report (with an anchor id for in-page links). */
	import type { Snippet } from 'svelte';
	import * as Card from '$lib/components/ui/card';

	interface Props {
		id?: string;
		title: string;
		description?: string;
		/** Content flush with the card edges (tables). */
		flush?: boolean;
		children: Snippet;
		actions?: Snippet;
	}

	let { id, title, description, flush = false, children, actions }: Props = $props();
</script>

<Card.Root {id} class="scroll-mt-20">
	<Card.Header class="pb-3">
		<div class="flex flex-wrap items-start justify-between gap-2">
			<div class="min-w-0">
				<Card.Title class="text-base">{title}</Card.Title>
				{#if description}<Card.Description>{description}</Card.Description>{/if}
			</div>
			{#if actions}<div class="flex shrink-0 flex-wrap gap-2">{@render actions()}</div>{/if}
		</div>
	</Card.Header>
	<Card.Content class={flush ? 'px-0 pb-2 sm:px-0' : ''}>
		{@render children()}
	</Card.Content>
</Card.Root>
