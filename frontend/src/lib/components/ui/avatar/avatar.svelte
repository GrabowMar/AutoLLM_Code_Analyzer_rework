<script lang="ts">
	import type { HTMLAttributes } from 'svelte/elements';
	import { cn } from '$lib/utils';

	let { class: className, children, ...restProps }: HTMLAttributes<HTMLSpanElement> & { children?: import('svelte').Snippet } = $props();
</script>

<span
	class={cn('relative flex h-9 w-9 shrink-0 overflow-hidden rounded-md border border-border', className)}
	{...restProps}
>
	{#if children}{@render children()}{/if}
</span>
