<script lang="ts">
	import { Tabs } from 'bits-ui';
	import { cn } from '$lib/utils';

	let { class: className, ...restProps }: Tabs.ContentProps = $props();
</script>

<Tabs.Content class={cn('pt-4 focus-visible:outline-none', className)} {...restProps} />
