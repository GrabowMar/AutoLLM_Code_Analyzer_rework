<script lang="ts">
	/**
	 * SettingsRow — one setting inside a `SettingsBlock`: label and help on the
	 * left, the control on the right. `stack` puts the control under the label
	 * (long inputs, code). Narrow screens always stack.
	 *
	 * Props: `label`, `description?` (string or snippet), `for?` (renders a
	 * <label>), `labelId?` (for radiogroups / segmented controls), `stack`.
	 */
	import type { Snippet } from 'svelte';
	import { cn } from '$lib/utils';

	let {
		label,
		description,
		for: forId,
		labelId,
		stack = false,
		class: className,
		children,
	}: {
		label: string;
		description?: string | Snippet;
		for?: string;
		labelId?: string;
		stack?: boolean;
		class?: string;
		children?: Snippet;
	} = $props();
</script>

<div
	class={cn(
		'settings-row flex flex-col gap-3',
		!stack && 'sm:flex-row sm:items-center sm:justify-between sm:gap-8',
		className,
	)}
>
	<div class="min-w-0 space-y-0.5 sm:flex-1">
		{#if forId}
			<label for={forId} id={labelId} class="block text-sm font-medium">{label}</label>
		{:else}
			<p id={labelId} class="text-sm font-medium">{label}</p>
		{/if}
		{#if typeof description === 'function'}
			<p class="text-[13px] leading-5 text-muted-foreground">{@render description()}</p>
		{:else if description}
			<p class="text-[13px] leading-5 text-muted-foreground">{description}</p>
		{/if}
	</div>
	{#if children}
		<div class={cn('min-w-0', !stack && 'sm:shrink-0')}>{@render children()}</div>
	{/if}
</div>
