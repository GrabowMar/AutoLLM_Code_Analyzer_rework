<script lang="ts">
import { cn } from '$lib/utils';
let { class: className, children, ...restProps }: { class?: string; children?: import('svelte').Snippet; [key: string]: unknown } = $props();
</script>

<div class={cn('flex flex-col-reverse gap-2 sm:flex-row sm:justify-end', className)} {...restProps}>
	{#if children}{@render children()}{/if}
</div>
