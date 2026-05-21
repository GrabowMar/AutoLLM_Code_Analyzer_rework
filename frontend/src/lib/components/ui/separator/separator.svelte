<script lang="ts">
	import type { HTMLAttributes } from 'svelte/elements';
	import { cn } from '$lib/utils';

	type Orientation = 'horizontal' | 'vertical';

	let { class: className, orientation = 'horizontal', ...restProps }: HTMLAttributes<HTMLDivElement> & { orientation?: Orientation } = $props();
</script>

<div
	role="separator"
	aria-orientation={orientation}
	class={cn(
		'shrink-0 bg-border',
		orientation === 'horizontal' ? 'h-[1px] w-full' : 'h-full w-[1px]',
		className
	)}
	{...restProps}
></div>
