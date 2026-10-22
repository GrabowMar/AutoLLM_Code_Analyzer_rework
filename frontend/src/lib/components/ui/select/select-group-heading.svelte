<script lang="ts">
	import { Select as SelectPrimitive } from 'bits-ui';
	import { cn } from '$lib/utils';

	let {
		ref = $bindable(null),
		class: className,
		children,
		...restProps
	}: Omit<SelectPrimitive.GroupHeadingProps, 'child'> = $props();
</script>

<SelectPrimitive.GroupHeading
	bind:ref
	data-slot="select-group-heading"
	class={cn('section-label px-2 pb-1 pt-2', className)}
	{...restProps}
>
	{@render children?.()}
</SelectPrimitive.GroupHeading>
