<script lang="ts">
	import type { HTMLAttributes } from 'svelte/elements';
	import { cn } from '$lib/utils';

	let { class: className, children, ...restProps }: HTMLAttributes<HTMLDivElement> & { children?: import('svelte').Snippet } = $props();
</script>

<div data-slot="card-header" class={cn('flex flex-col gap-1 px-4', className)} {...restProps}>
	{#if children}{@render children()}{/if}
</div>
