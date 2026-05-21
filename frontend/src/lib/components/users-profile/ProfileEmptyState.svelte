<script lang="ts">
	import type { Component } from 'svelte';

	interface Props {
		icon: Component;
		title: string;
		description?: string;
	}

	let { icon: Icon, title, description }: Props = $props();
</script>

<div class="flex flex-col items-center justify-center rounded-lg border border-dashed border-border bg-card/30 px-6 py-12 text-center">
	<div class="flex h-10 w-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
		<Icon class="h-4 w-4" />
	</div>
	<p class="mt-3 text-sm font-medium text-foreground">{title}</p>
	{#if description}
		<p class="mt-1 max-w-xs text-xs text-muted-foreground">{description}</p>
	{/if}
</div>
