<script lang="ts">
	/**
	 * One option. Without children it renders `label ?? value`; pass a children
	 * snippet (`{#snippet children({ selected, highlighted })}`) for rich rows.
	 * Always pass `label` for rich rows so typeahead and `Select.Simple` work.
	 */
	import { Select as SelectPrimitive } from 'bits-ui';
	import Check from '@lucide/svelte/icons/check';
	import { cn } from '$lib/utils';

	let {
		ref = $bindable(null),
		class: className,
		value,
		label,
		children: childrenProp,
		...restProps
	}: Omit<SelectPrimitive.ItemProps, 'child'> = $props();
</script>

<SelectPrimitive.Item
	bind:ref
	{value}
	{label}
	data-slot="select-item"
	class={cn(
		'relative flex w-full cursor-pointer select-none items-center gap-2 rounded-sm py-1.5 pl-2 pr-8 text-sm outline-none transition-colors duration-150',
		'data-[highlighted]:bg-surface-2 data-[highlighted]:text-foreground data-[selected]:font-medium',
		'data-[disabled]:pointer-events-none data-[disabled]:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0',
		className
	)}
	{...restProps}
>
	{#snippet children({ selected, highlighted })}
		<span class="absolute right-2 top-2 flex size-3.5 items-center justify-center">
			{#if selected}<Check class="size-4 text-primary" />{/if}
		</span>
		{#if childrenProp}
			{@render childrenProp({ selected, highlighted })}
		{:else}
			<span class="min-w-0 truncate">{label || value}</span>
		{/if}
	{/snippet}
</SelectPrimitive.Item>
