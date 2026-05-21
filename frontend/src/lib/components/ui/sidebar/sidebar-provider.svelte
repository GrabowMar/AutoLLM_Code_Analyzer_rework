<script lang="ts">
	import { cn } from '$lib/utils';

	let { class: className, children, ...restProps }: { class?: string; children?: import('svelte').Snippet; [key: string]: unknown } = $props();
</script>

<div class={cn('flex min-h-screen', className)} {...restProps}>
	{#if children}{@render children()}{/if}
</div>
