<script lang="ts">
	import { Tabs } from 'bits-ui';
	import { cn } from '$lib/utils';

	let { class: className, ...restProps }: Tabs.TriggerProps = $props();
</script>

<Tabs.Trigger
	class={cn(
		'-mb-px inline-flex h-9 shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 border-transparent px-3 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground disabled:pointer-events-none disabled:opacity-50 data-[state=active]:border-primary data-[state=active]:text-foreground [&_svg]:size-4 [&_svg]:shrink-0',
		className
	)}
	{...restProps}
/>
