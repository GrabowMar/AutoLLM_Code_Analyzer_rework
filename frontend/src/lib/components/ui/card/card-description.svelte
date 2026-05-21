<script lang="ts">
	import type { HTMLAttributes } from 'svelte/elements';
	import { cn } from '$lib/utils';

	let { class: className, children, ...restProps }: HTMLAttributes<HTMLDivElement> & { children?: import('svelte').Snippet } = $props();
</script>

<div data-slot="card-description" class={cn('text-muted-foreground text-xs', className)} {...restProps}>
	{#if children}{@render children()}{/if}
</div>
