<script lang="ts">
	import type { HTMLAttributes } from 'svelte/elements';
	import { cn } from '$lib/utils';

	let { class: className, children, ...restProps }: HTMLAttributes<HTMLDivElement> & { children?: import('svelte').Snippet } = $props();
</script>

<div data-slot="card-title" class={cn('font-display text-base font-semibold leading-none tracking-tight', className)} style="font-family: var(--font-display);" {...restProps}>
	{#if children}{@render children()}{/if}
</div>
