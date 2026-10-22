<script lang="ts">
	import { Tabs } from 'bits-ui';
	import { cn } from '$lib/utils';

	let { class: className, ...restProps }: Tabs.ListProps = $props();
</script>

<Tabs.List
	class={cn('no-scrollbar flex items-center gap-1 overflow-x-auto border-b border-border', className)}
	{...restProps}
/>
