<script lang="ts">
import { cn } from '$lib/utils';
let { class: className, children, ...restProps }: { class?: string; children?: import('svelte').Snippet; [key: string]: unknown } = $props();
</script>

<div class={cn('flex flex-col gap-1.5', className)} {...restProps}>
	{#if children}{@render children()}{/if}
</div>
