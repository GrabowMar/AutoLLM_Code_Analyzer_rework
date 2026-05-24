<script lang="ts">
	import { Dialog } from 'bits-ui';
	import { cn } from '$lib/utils';

	let {
		class: className,
		...restProps
	}: Dialog.OverlayProps & { class?: string } = $props();
</script>

<Dialog.Overlay
	class={cn(
		'fixed inset-0 z-50 bg-black/50 backdrop-blur-[2px]',
		className
	)}
	{...restProps}
/>
