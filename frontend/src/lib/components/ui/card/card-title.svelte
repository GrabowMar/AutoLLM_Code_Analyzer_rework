<script lang="ts">
	import type { HTMLAttributes } from 'svelte/elements';
	import { cn } from '$lib/utils';

	let { class: className, children, ...restProps }: HTMLAttributes<HTMLDivElement> & { children?: import('svelte').Snippet } = $props();
</script>

<div data-slot="card-title" class={cn('font-display text-[17px] font-semibold leading-tight tracking-tight', className)} {...restProps}>
	{#if children}{@render children()}{/if}
</div>
