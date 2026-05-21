<script lang="ts">
	import { Dialog } from 'bits-ui';
	import { cn } from '$lib/utils';

	let {
		class: className,
		children,
		...restProps
	}: Dialog.DescriptionProps & { class?: string; children?: import('svelte').Snippet } = $props();
</script>

<Dialog.Description class={cn('text-sm text-muted-foreground', className)} {...restProps}>
	{#if children}{@render children()}{/if}
</Dialog.Description>
