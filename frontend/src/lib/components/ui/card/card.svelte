<script lang="ts">
	import type { HTMLAttributes } from 'svelte/elements';
	import { cn } from '$lib/utils';

	let { class: className, children, ...restProps }: HTMLAttributes<HTMLDivElement> & { children?: import('svelte').Snippet } = $props();
</script>

<div data-slot="card" class={cn('bg-card text-card-foreground flex flex-col gap-4 rounded-md border border-border py-4 shadow-sm', className)} {...restProps}>
	{#if children}{@render children()}{/if}
</div>
