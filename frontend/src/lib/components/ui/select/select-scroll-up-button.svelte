<script lang="ts">
	import { Select as SelectPrimitive } from 'bits-ui';
	import ChevronUp from '@lucide/svelte/icons/chevron-up';
	import { cn } from '$lib/utils';

	let {
		ref = $bindable(null),
		class: className,
		...restProps
	}: Omit<SelectPrimitive.ScrollUpButtonProps, 'child' | 'children'> = $props();
</script>

<SelectPrimitive.ScrollUpButton
	bind:ref
	class={cn('flex cursor-default items-center justify-center py-1 text-muted-foreground', className)}
	{...restProps}
>
	<ChevronUp class="size-4" />
</SelectPrimitive.ScrollUpButton>
