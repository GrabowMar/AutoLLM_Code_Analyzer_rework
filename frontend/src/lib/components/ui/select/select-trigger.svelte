<script lang="ts">
	/**
	 * Select trigger button. Put the visible value inside (consumers render the
	 * selected label themselves, or use `Select.Simple`). `size`: 'default' (h-9,
	 * matches Input) | 'sm' (h-8).
	 */
	import { Select as SelectPrimitive } from 'bits-ui';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import { cn } from '$lib/utils';

	let {
		ref = $bindable(null),
		class: className,
		children,
		size = 'default',
		...restProps
	}: Omit<SelectPrimitive.TriggerProps, 'child'> & { size?: 'sm' | 'default' } = $props();
</script>

<SelectPrimitive.Trigger
	bind:ref
	data-slot="select-trigger"
	data-size={size}
	class={cn(
		'flex w-full min-w-0 cursor-pointer select-none items-center justify-between gap-2 rounded-md border border-input bg-surface-1 px-3 text-left text-sm shadow-xs transition-colors duration-150',
		'hover:border-primary/40 focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30',
		'data-[placeholder]:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive',
		'data-[size=default]:h-9 data-[size=sm]:h-8 data-[size=sm]:text-xs',
		'[&_svg]:pointer-events-none [&_svg]:shrink-0',
		className
	)}
	{...restProps}
>
	<span class="flex min-w-0 flex-1 items-center gap-2 truncate">{@render children?.()}</span>
	<ChevronDown class="size-4 text-muted-foreground" aria-hidden="true" />
</SelectPrimitive.Trigger>
