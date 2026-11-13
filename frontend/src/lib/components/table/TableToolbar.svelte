<script lang="ts">
	/**
	 * Strip at the top of a table card: a summary (or, with a selection, the
	 * bulk-action bar) on the left, extra controls on the right. Row density is a
	 * workspace preference (Settings → Workspace → Compact tables), not a per-table control.
	 *
	 *   <TableToolbar selectedCount={selected.size} noun="model" onClearSelection={clear}>
	 *     {#snippet summary()}50 on this page{/snippet}
	 *     {#snippet bulk()}<Button size="sm" onclick={compare}>Compare</Button>{/snippet}
	 *   </TableToolbar>
	 *
		 */
	import type { Snippet } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import X from '@lucide/svelte/icons/x';

	interface Props {
		selectedCount?: number;
		/** Singular noun for the selection label ("3 models selected"). */
		noun?: string;
		onClearSelection?: () => void;
		summary?: Snippet;
		bulk?: Snippet;
		controls?: Snippet;
		/** @deprecated Ignored: density is the workspace preference (Settings → Workspace). */
		density?: boolean;
	}

	let { selectedCount = 0, noun = 'row', onClearSelection, summary, bulk, controls }: Props = $props();

</script>

<div class="table-toolbar">
	<div class="flex min-w-0 flex-wrap items-center gap-2" aria-live="polite">
		{#if selectedCount > 0}
			<span class="inline-flex items-center gap-1.5 font-medium text-foreground">
				<span class="inline-flex h-5 min-w-5 items-center justify-center rounded bg-primary px-1 font-mono text-[11px] text-primary-foreground tabular-nums">{selectedCount}</span>
				{noun}{selectedCount === 1 ? '' : 's'} selected
			</span>
			{@render bulk?.()}
			{#if onClearSelection}
				<Button variant="ghost" size="sm" class="h-7 px-2 text-xs" onclick={onClearSelection}>
					<X class="size-3.5" />Clear
				</Button>
			{/if}
		{:else if summary}
			<span class="truncate">{@render summary()}</span>
		{/if}
	</div>
	<div class="flex items-center gap-2">
		{@render controls?.()}

	</div>
</div>
