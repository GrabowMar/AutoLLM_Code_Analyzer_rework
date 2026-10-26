<script lang="ts">
	/**
	 * LibraryPane — one Library section as a single card: a heading strip (what it
	 * is for and where the generator uses it sit behind a `?` popover) with an
	 * optional summary and the toolbar, then the list flush below it, and (while
	 * editing) an editor column beside the card.
	 * Below `xl` the editor replaces the list and a back button returns to it.
	 */
	import type { Snippet } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import { Popover } from 'bits-ui';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import CircleHelp from '@lucide/svelte/icons/circle-help';

	interface Props {
		title: string;
		/** What this asset is, one sentence. */
		description: string;
		/** Where a run picks it, e.g. "Sample Generator → Agent and Template modes". */
		usedIn?: string;
		editing: boolean;
		onCloseEditor: () => void;
		/** Short muted text after the heading, e.g. "12 of 33". */
		summary?: Snippet;
		toolbar?: Snippet;
		list: Snippet;
		editor?: Snippet;
	}
	let { title, description, usedIn, editing, onCloseEditor, summary, toolbar, list, editor }: Props = $props();
</script>

<section class="min-w-0" aria-labelledby="library-pane-title">
	<div class="grid gap-4 {editing ? 'xl:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)]' : 'grid-cols-1'}">
		<!-- One card: heading strip, toolbar strip, then the flush list. -->
		<div class="library-pane-card min-w-0 overflow-clip rounded-md border border-border bg-card shadow-[var(--shadow-card)] {editing ? 'hidden xl:block' : ''}">
			<div class="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-4 py-2.5">
				<div class="mr-auto flex min-w-0 items-center gap-1.5">
					<h2 id="library-pane-title" class="font-display text-base font-semibold">{title}</h2>
					<Popover.Root>
						<Popover.Trigger class="page-header-help-trigger" aria-label="About {title.toLowerCase()}">
							<CircleHelp class="size-4" aria-hidden="true" />
						</Popover.Trigger>
						<Popover.Portal>
							<Popover.Content side="bottom" align="start" sideOffset={6} collisionPadding={12} class="page-header-help space-y-2">
								<p>{description}</p>
								{#if usedIn}
									<p class="text-xs text-muted-foreground"><span class="section-label mr-1.5">Used in</span>{usedIn}</p>
								{/if}
							</Popover.Content>
						</Popover.Portal>
					</Popover.Root>
					{#if summary}<span class="ml-1 truncate text-xs text-muted-foreground">{@render summary()}</span>{/if}
				</div>
				{@render toolbar?.()}
			</div>
			{@render list()}
		</div>
		{#if editing && editor}
			<div class="min-w-0 space-y-4">
				<Button variant="ghost" size="sm" class="xl:hidden" onclick={onCloseEditor}>
					<ArrowLeft /> Back to {title.toLowerCase()}
				</Button>
				{@render editor()}
			</div>
		{/if}
	</div>
</section>

<style>
	/* Lists and states render flush inside the pane card instead of as their own surfaces. */
	.library-pane-card :global(.data-list) {
		border: 0;
		border-radius: 0;
		box-shadow: none;
	}
	.library-pane-card > :global([role='alert']) {
		margin: 1rem;
		width: auto;
	}
</style>
