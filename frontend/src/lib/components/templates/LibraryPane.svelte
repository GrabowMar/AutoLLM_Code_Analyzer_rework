<script lang="ts">
	/**
	 * LibraryPane — one Library section: a heading with what it is for and
	 * where the generator uses it, a toolbar, the list, and (while editing) an
	 * editor column beside the list. Below `lg` the editor replaces the list
	 * and a back button returns to it.
	 */
	import type { Snippet } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';

	interface Props {
		title: string;
		/** What this asset is, one sentence. */
		description: string;
		/** Where a run picks it, e.g. "Sample Generator → Agent and Template modes". */
		usedIn?: string;
		editing: boolean;
		onCloseEditor: () => void;
		toolbar?: Snippet;
		list: Snippet;
		editor?: Snippet;
	}
	let { title, description, usedIn, editing, onCloseEditor, toolbar, list, editor }: Props = $props();
</script>

<section class="min-w-0 space-y-4" aria-labelledby="library-pane-title">
	<div class="space-y-1">
		<h2 id="library-pane-title" class="font-display text-xl">{title}</h2>
		<p class="text-sm text-muted-foreground">{description}</p>
		{#if usedIn}
			<p class="text-xs text-muted-foreground"><span class="section-label mr-1.5">Used in</span>{usedIn}</p>
		{/if}
	</div>

	<div class="grid gap-6 {editing ? 'xl:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)]' : 'grid-cols-1'}">
		<div class="min-w-0 space-y-4 {editing ? 'hidden xl:block' : ''}">
			{#if toolbar}
				<div class="flex flex-wrap items-center justify-between gap-3">{@render toolbar()}</div>
			{/if}
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
