<script lang="ts">
	/**
	 * SettingsBlock — one group of settings inside a section card: a shaded
	 * heading strip (title, optional one-line description and actions) like the
	 * Library's group rows, then its rows (`SettingsRow` or any `.settings-row`)
	 * and an optional `footer` action strip. Without a title, description or
	 * actions it is just the rows.
	 */
	import type { Snippet } from 'svelte';
	import { cn } from '$lib/utils';

	let {
		title,
		description,
		actions,
		footer,
		class: className,
		children,
	}: {
		title?: string;
		description?: string | Snippet;
		actions?: Snippet;
		footer?: Snippet;
		class?: string;
		children: Snippet;
	} = $props();
</script>

<section class={cn('divide-y divide-border', className)}>
	{#if title || description || actions}
		<div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 bg-muted/40 px-4 py-2.5">
			<div class="min-w-0 space-y-0.5">
				{#if title}<h3 class="text-sm font-semibold">{title}</h3>{/if}
				{#if typeof description === 'function'}
					<p class="text-xs leading-5 text-muted-foreground">{@render description()}</p>
				{:else if description}
					<p class="text-xs leading-5 text-muted-foreground">{description}</p>
				{/if}
			</div>
			{#if actions}<div class="flex shrink-0 items-center gap-2">{@render actions()}</div>{/if}
		</div>
	{/if}
	{@render children()}
	{#if footer}<div class="settings-footer">{@render footer()}</div>{/if}
</section>
