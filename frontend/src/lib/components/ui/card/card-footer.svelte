<script lang="ts">
	import type { HTMLAttributes } from 'svelte/elements';
	import { cn } from '$lib/utils';

	let { class: className, children, ...restProps }: HTMLAttributes<HTMLDivElement> & { children?: import('svelte').Snippet } = $props();
</script>

<div data-slot="card-footer" class={cn('flex items-center px-4 pt-2 border-t border-border/50', className)} {...restProps}>
	{#if children}{@render children()}{/if}
</div>
