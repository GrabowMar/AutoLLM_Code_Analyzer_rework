<script lang="ts" module>
	export interface SimpleSelectItem {
		value: string;
		label: string;
		/** Muted second line inside the dropdown. */
		description?: string;
		disabled?: boolean;
	}
</script>

<script lang="ts">
	/**
	 * Data-driven single select for the common case:
	 *
	 *   <Label for="net">Network</Label>
	 *   <Select.Simple id="net" bind:value={network} items={[{ value: 'packages', label: 'Package registries' }]} />
	 *
	 * Props:
	 * - `value` (bindable string): '' means nothing selected and shows `placeholder`.
	 * - `items`: `{ value, label, description?, disabled? }[]`.
	 * - `placeholder`: trigger text while `value` is '' (default "Select…").
	 * - `clearLabel`: adds a first option that resets `value` to '' (e.g. "Inherit").
	 * - `id`: set on the trigger so `<Label for>` focuses it. `name`: hidden form input.
	 * - `size`: 'default' | 'sm'; `disabled`; `class` (trigger); `contentClass`.
	 * - `aria-label` / `aria-describedby` / `aria-invalid`: forwarded to the trigger.
	 * - `onValueChange(value)`: called after the user picks an option.
	 */
	import { Select as SelectPrimitive } from 'bits-ui';
	import SelectTrigger from './select-trigger.svelte';
	import SelectContent from './select-content.svelte';
	import SelectItem from './select-item.svelte';
	import SelectSeparator from './select-separator.svelte';

	interface Props {
		value?: string;
		items: SimpleSelectItem[];
		placeholder?: string;
		clearLabel?: string;
		id?: string;
		name?: string;
		size?: 'sm' | 'default';
		disabled?: boolean;
		class?: string;
		contentClass?: string;
		'aria-label'?: string;
		'aria-describedby'?: string;
		'aria-invalid'?: boolean;
		onValueChange?: (value: string) => void;
	}

	let {
		value = $bindable(''),
		items,
		placeholder = 'Select…',
		clearLabel,
		id,
		name,
		size = 'default',
		disabled = false,
		class: className,
		contentClass,
		'aria-label': ariaLabel,
		'aria-describedby': ariaDescribedby,
		'aria-invalid': ariaInvalid,
		onValueChange,
	}: Props = $props();

	// bits-ui treats '' as "no value", so the clear option needs its own token.
	const CLEAR = '__select_clear__';

	const selected = $derived(items.find((item) => item.value === value));
	const typeaheadItems = $derived([
		...(clearLabel ? [{ value: CLEAR, label: clearLabel }] : []),
		...items.map((item) => ({ value: item.value, label: item.label, disabled: item.disabled })),
	]);

	function handleChange(next: string) {
		const resolved = next === CLEAR ? '' : next;
		value = resolved;
		onValueChange?.(resolved);
	}
</script>

<SelectPrimitive.Root
	type="single"
	{value}
	onValueChange={handleChange}
	items={typeaheadItems}
	{disabled}
	{name}
>
	<SelectTrigger
		{id}
		{size}
		class={className}
		aria-label={ariaLabel}
		aria-describedby={ariaDescribedby}
		aria-invalid={ariaInvalid}
	>
		{#if selected}
			{selected.label}
		{:else}
			<span class="truncate text-muted-foreground">{placeholder}</span>
		{/if}
	</SelectTrigger>
	<SelectContent class={contentClass}>
		{#if clearLabel}
			<SelectItem value={CLEAR} label={clearLabel}>
				<span class="text-muted-foreground">{clearLabel}</span>
			</SelectItem>
			<SelectSeparator />
		{/if}
		{#each items as item (item.value)}
			<SelectItem value={item.value} label={item.label} disabled={item.disabled}>
				{#if item.description}
					<span class="flex min-w-0 flex-col">
						<span class="truncate">{item.label}</span>
						<span class="text-[11px] text-muted-foreground">{item.description}</span>
					</span>
				{:else}
					<span class="min-w-0 truncate">{item.label}</span>
				{/if}
			</SelectItem>
		{/each}
	</SelectContent>
</SelectPrimitive.Root>
