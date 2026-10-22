<script lang="ts">
	import { Select as SelectPrimitive } from 'bits-ui';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import { cn } from '$lib/utils';

	let {
		ref = $bindable(null),
		class: className,
		...restProps
	}: Omit<SelectPrimitive.ScrollDownButtonProps, 'child' | 'children'> = $props();
</script>

<SelectPrimitive.ScrollDownButton
	bind:ref
	class={cn('flex cursor-default items-center justify-center py-1 text-muted-foreground', className)}
	{...restProps}
>
	<ChevronDown class="size-4" />
</SelectPrimitive.ScrollDownButton>
