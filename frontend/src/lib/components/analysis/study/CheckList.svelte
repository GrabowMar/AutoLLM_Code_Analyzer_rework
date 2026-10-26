<script lang="ts" module>
	export interface CheckItem {
		value: string;
		label: string;
		/** Muted trailing text (e.g. "6 jobs"). */
		hint?: string;
		disabled?: boolean;
	}
</script>

<script lang="ts">
	/**
	 * CheckList — a multi-choice field as a grid of checkboxes with
	 * "all / none" shortcuts. `selected` is replaced, never mutated.
	 *
	 * Props: `items`, `selected` (bindable string[]), `legend` (fieldset
	 * label), `emptyMeans` (shown when nothing is ticked, e.g. "every app"),
	 * `columns` (max columns, default 3), `disabled`, `onchange(selected)`.
	 */
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { cn } from '$lib/utils';

	interface Props {
		items: CheckItem[];
		selected?: string[];
		legend: string;
		emptyMeans?: string;
		columns?: 1 | 2 | 3 | 4;
		disabled?: boolean;
		onchange?: (selected: string[]) => void;
	}

	let {
		items,
		selected = $bindable([]),
		legend,
		emptyMeans = '',
		columns = 3,
		disabled = false,
		onchange
	}: Props = $props();

	function set(next: string[]) {
		selected = next;
		onchange?.(next);
	}

	function toggle(value: string, on: boolean) {
		const next = on ? [...new Set([...selected, value])] : selected.filter((v) => v !== value);
		// Keep the catalog order (stable specs, stable hashes).
		set(items.map((i) => i.value).filter((v) => next.includes(v)));
	}

	const gridClass: Record<number, string> = {
		1: 'grid-cols-1',
		2: 'grid-cols-1 sm:grid-cols-2',
		3: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3',
		4: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-4'
	};
</script>

<fieldset class="min-w-0 space-y-2" {disabled}>
	<legend class="mb-2 flex w-full flex-wrap items-center justify-between gap-2">
		<span class="section-label">{legend}</span>
		{#if !disabled && items.length > 1}
			<span class="flex items-center gap-2 text-[11px]">
				<button
					type="button"
					class="cursor-pointer rounded text-muted-foreground hover:text-foreground"
					aria-label="Select every {legend.toLowerCase()}"
					onclick={() => set(items.filter((i) => !i.disabled).map((i) => i.value))}
				>
					All
				</button>
				<span class="text-border" aria-hidden="true">|</span>
				<button
					type="button"
					class="cursor-pointer rounded text-muted-foreground hover:text-foreground"
					aria-label="Clear {legend.toLowerCase()}"
					onclick={() => set([])}
				>
					None
				</button>
			</span>
		{/if}
	</legend>
	{#if items.length === 0}
		<p class="text-xs text-muted-foreground">Nothing to choose from.</p>
	{:else}
		<div class={cn('grid gap-x-4 gap-y-1.5', gridClass[columns])}>
			{#each items as item (item.value)}
				<label
					class={cn(
						'flex min-w-0 cursor-pointer items-center gap-2 rounded px-1 py-0.5 text-sm hover:bg-muted/50',
						(item.disabled || disabled) && 'cursor-not-allowed opacity-60'
					)}
				>
					<Checkbox
						checked={selected.includes(item.value)}
						disabled={item.disabled || disabled}
						onCheckedChange={(on) => toggle(item.value, on === true)}
					/>
					<span class="min-w-0 truncate" title={item.label}>{item.label}</span>
					{#if item.hint}
						<span class="ml-auto shrink-0 text-[11px] tabular-nums text-muted-foreground"
							>{item.hint}</span
						>
					{/if}
				</label>
			{/each}
		</div>
		{#if emptyMeans && selected.length === 0}
			<p class="text-[11px] text-muted-foreground">
				None ticked: {emptyMeans}.
			</p>
		{/if}
	{/if}
</fieldset>
