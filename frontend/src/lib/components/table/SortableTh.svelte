<script lang="ts">
	/**
	 * Sortable column header: renders the whole `<th>` with `aria-sort`, a sort
	 * button and a direction icon (faint when inactive, primary when active).
	 *
	 *   <SortableTh label="Context" field="context_window" {sortBy} {sortDir} onSort={toggleSort} align="right" />
	 *
	 * Props: `label`, `field`, `sortBy`, `sortDir`, `onSort(field)`, `align` (left | right | center),
	 * `help` (native tooltip explaining the column), `ascOnly` (backend only sorts one way), `class`.
	 */
	import ArrowUp from '@lucide/svelte/icons/arrow-up';
	import ArrowDown from '@lucide/svelte/icons/arrow-down';
	import ChevronsUpDown from '@lucide/svelte/icons/chevrons-up-down';
	import { cn } from '$lib/utils';

	interface Props {
		label: string;
		field: string;
		sortBy: string;
		sortDir: 'asc' | 'desc';
		onSort: (field: string) => void;
		align?: 'left' | 'right' | 'center';
		help?: string;
		ascOnly?: boolean;
		class?: string;
	}

	let { label, field, sortBy, sortDir, onSort, align = 'left', help, ascOnly = false, class: className }: Props = $props();

	const active = $derived(sortBy === field);
	const dir = $derived(ascOnly ? 'asc' : sortDir);
</script>

<th
	class={cn('whitespace-nowrap', align === 'right' && 'num', align === 'center' && 'text-center', className)}
	aria-sort={active ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}
>
	<button
		type="button"
		class={cn(
			'group/sort -mx-1 inline-flex cursor-pointer items-center gap-1 rounded px-1 py-0.5 transition-colors duration-150 hover:text-foreground',
			align === 'right' && 'flex-row-reverse',
			active && 'text-foreground'
		)}
		title={help}
		onclick={() => onSort(field)}
	>
		{label}
		{#if active}
			{#if dir === 'asc'}<ArrowUp class="size-3 text-primary" aria-hidden="true" />{:else}<ArrowDown class="size-3 text-primary" aria-hidden="true" />{/if}
		{:else}
			<ChevronsUpDown class="size-3 opacity-40 transition-opacity group-hover/sort:opacity-90" aria-hidden="true" />
		{/if}
	</button>
</th>
