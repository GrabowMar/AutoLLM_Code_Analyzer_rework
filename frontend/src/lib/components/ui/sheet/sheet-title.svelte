<script lang="ts">
	import { Dialog } from 'bits-ui';
	import { cn } from '$lib/utils';

	let {
		class: className,
		children,
		...restProps
	}: Dialog.TitleProps & { class?: string; children?: import('svelte').Snippet } = $props();
</script>

<Dialog.Title class={cn('text-lg font-semibold text-foreground', className)} {...restProps}>
	{#if children}{@render children()}{/if}
</Dialog.Title>
