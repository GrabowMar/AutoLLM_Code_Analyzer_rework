<script lang="ts">
	import type { HTMLAttributes } from 'svelte/elements';
	import { cn } from '$lib/utils';

	let { class: className, children, ...restProps }: HTMLAttributes<HTMLSpanElement> & { children?: import('svelte').Snippet } = $props();
</script>

<span
	class={cn('flex h-full w-full items-center justify-center rounded-md bg-muted text-xs font-medium text-muted-foreground', className)}
	style="font-family: var(--font-mono);"
	{...restProps}
>
	{#if children}{@render children()}{/if}
</span>
