<script lang="ts" module>
	export interface ChoiceItem {
		value: string;
		label: string;
		/** Muted line under the label. */
		description?: string;
		disabled?: boolean;
		/** Tooltip, e.g. why an item is disabled. */
		title?: string;
	}
</script>

<script lang="ts">
	/**
	 * ChoiceChecklist — a multi-choice field as a group of checkboxes.
	 *
	 * Props: `id` (group id; also prefixes item ids), `labelledBy` (id of the
	 * visible label), `items`, `value` (selected values), `onchange(values)` —
	 * values come back in item order so equal sets serialize identically —
	 * `disabled`, `error`, `columns` (Tailwind grid classes), `class`.
	 */
	import { cn } from '$lib/utils';
	import { Checkbox } from '$lib/components/ui/checkbox';

	interface Props {
		id: string;
		labelledBy?: string;
		items: ChoiceItem[];
		value: readonly string[];
		onchange?: (values: string[]) => void;
		disabled?: boolean;
		error?: string;
		columns?: string;
		class?: string;
	}

	let {
		id,
		labelledBy,
		items,
		value,
		onchange,
		disabled = false,
		error = '',
		columns = 'grid-cols-1 sm:grid-cols-2',
		class: className
	}: Props = $props();

	const selected = $derived(new Set(value));

	function toggle(item: ChoiceItem, on: boolean) {
		const next = new Set(selected);
		if (on) next.add(item.value);
		else next.delete(item.value);
		// Unknown values (not in items) are kept at the end, never silently dropped.
		const known = items.map((i) => i.value);
		onchange?.([
			...known.filter((v) => next.has(v)),
			...value.filter((v) => !known.includes(v) && next.has(v))
		]);
	}
</script>

<div class={cn('space-y-1.5', className)}>
	<div
		{id}
		role="group"
		aria-labelledby={labelledBy}
		aria-describedby={error ? `${id}-error` : undefined}
		class={cn('grid gap-x-4 gap-y-2', columns)}
	>
		{#each items as item (item.value)}
			{@const itemId = `${id}-${item.value}`}
			<label
				for={itemId}
				class={cn(
					'flex items-start gap-2 text-sm',
					disabled || item.disabled ? 'cursor-not-allowed opacity-70' : 'cursor-pointer'
				)}
				title={item.title}
			>
				<Checkbox
					id={itemId}
					class="mt-0.5"
					checked={selected.has(item.value)}
					disabled={disabled || item.disabled}
					onCheckedChange={(on) => toggle(item, on === true)}
				/>
				<span class="min-w-0">
					<span class="block leading-5">{item.label}</span>
					{#if item.description}
						<span class="block text-[11px] leading-4 text-muted-foreground">{item.description}</span
						>
					{/if}
				</span>
			</label>
		{/each}
	</div>
	{#if error}
		<p id="{id}-error" class="text-[11px] text-danger-fg">{error}</p>
	{/if}
</div>
