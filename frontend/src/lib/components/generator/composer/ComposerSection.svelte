<script lang="ts">
	/**
	 * ComposerSection — one numbered step of the composer form (a Card with a
	 * step badge, heading, one-line description and optional header actions).
	 */
	import type { Snippet } from 'svelte';
	import * as Card from '$lib/components/ui/card';

	interface Props {
		step: number;
		title: string;
		/** Section anchor; the heading id is `${id}-title`. */
		id: string;
		description?: string;
		actions?: Snippet;
		children: Snippet;
	}

	let { step, title, id, description, actions, children }: Props = $props();
</script>

<section {id} aria-labelledby="{id}-title" class="scroll-mt-20">
	<Card.Root class="gap-4">
		<Card.Header class="flex-row items-start gap-3">
			<span
				class="mt-px flex size-6 shrink-0 items-center justify-center rounded-full border border-primary/40 bg-primary/10 font-mono text-xs font-semibold text-success-fg"
				aria-hidden="true">{step}</span
			>
			<div class="min-w-0 flex-1 space-y-1">
				<Card.Title><h2 id="{id}-title" class="leading-6">{title}</h2></Card.Title>
				{#if description}
					<Card.Description class="text-xs">{description}</Card.Description>
				{/if}
			</div>
			{#if actions}
				<div class="flex shrink-0 flex-wrap items-center justify-end gap-2">{@render actions()}</div>
			{/if}
		</Card.Header>
		<Card.Content class="@container space-y-4">
			{@render children()}
		</Card.Content>
	</Card.Root>
</section>
