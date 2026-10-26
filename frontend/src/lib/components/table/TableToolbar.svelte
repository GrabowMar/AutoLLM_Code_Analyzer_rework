<script lang="ts">
	/**
	 * Strip at the top of a table card: a summary (or, with a selection, the
	 * bulk-action bar) on the left, extra controls + the density toggle on the right.
	 *
	 *   <TableToolbar selectedCount={selected.size} noun="model" onClearSelection={clear}>
	 *     {#snippet summary()}50 on this page · click a row to open{/snippet}
	 *     {#snippet bulk()}<Button size="sm" onclick={compare}>Compare</Button>{/snippet}
	 *   </TableToolbar>
	 *
	 * Density writes the global "Compact tables" preference, so every table follows it.
	 */
	import type { Snippet } from 'svelte';
	import { Segmented } from '$lib/components/ui/segmented';
	import { Button } from '$lib/components/ui/button';
	import { getPreferences } from '$lib/stores/preferences.svelte';
	import Rows3 from '@lucide/svelte/icons/rows-3';
	import Rows4 from '@lucide/svelte/icons/rows-4';
	import X from '@lucide/svelte/icons/x';

	interface Props {
		selectedCount?: number;
		/** Singular noun for the selection label ("3 models selected"). */
		noun?: string;
		onClearSelection?: () => void;
		summary?: Snippet;
		bulk?: Snippet;
		controls?: Snippet;
		density?: boolean;
	}

	let { selectedCount = 0, noun = 'row', onClearSelection, summary, bulk, controls, density = true }: Props = $props();

	const prefs = getPreferences();
	let densityValue = $state(prefs.compactTables ? 'compact' : 'comfortable');
	$effect(() => {
		densityValue = prefs.compactTables ? 'compact' : 'comfortable';
	});
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
		{#if density}
			<!-- Density only changes table rows; mobile card views ignore it. -->
			<div class="hidden md:block">
			<Segmented
				size="sm"
				aria-label="Row density"
				bind:value={densityValue}
				onValueChange={(v) => prefs.setCompactTables(v === 'compact')}
				items={[
					{ value: 'comfortable', label: 'Comfortable', icon: Rows3, title: 'Two-line rows with details' },
					{ value: 'compact', label: 'Compact', icon: Rows4, title: 'One line per row' },
				]}
			/>
			</div>
		{/if}
	</div>
</div>
