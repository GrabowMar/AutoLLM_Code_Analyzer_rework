<script lang="ts">
	import { cn } from '$lib/utils';

	let { class: className, children, ...restProps }: { class?: string; children?: import('svelte').Snippet; [key: string]: unknown } = $props();
</script>

<main class={cn('flex flex-1 flex-col', className)} {...restProps}>
	{#if children}{@render children()}{/if}
</main>
