<script lang="ts">
	/**
	 * Warning banner for data that is incomplete: jobs missing from the
	 * numbers, mixed plans, failed tools. Totals next to it must read as
	 * partial, never as "clean".
	 *
	 *   <CoverageNotice title="3 of 11 jobs are not in these numbers" items={['1 analysis failed', …]} />
	 */
	import type { Snippet } from 'svelte';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import Info from '@lucide/svelte/icons/info';
	import { cn } from '$lib/utils';

	interface Props {
		title: string;
		/** Short lines listed under the title. */
		items?: string[];
		/** `info` for context that is not a data gap (e.g. a placeholder statistic). */
		tone?: 'warning' | 'info';
		children?: Snippet;
		class?: string;
	}

	let { title, items = [], tone = 'warning', children, class: className }: Props = $props();
</script>

<div
	role="status"
	class={cn(
		'flex gap-3 rounded-md border border-y-border border-r-border border-l-4 px-4 py-3 text-sm',
		tone === 'warning' ? 'border-l-warning bg-warning/5' : 'border-l-info bg-info/5',
		className
	)}
>
	{#if tone === 'warning'}
		<TriangleAlert class="mt-0.5 h-4 w-4 shrink-0 text-warning-fg" aria-hidden="true" />
	{:else}
		<Info class="mt-0.5 h-4 w-4 shrink-0 text-info-fg" aria-hidden="true" />
	{/if}
	<div class="min-w-0 space-y-1">
		<p class="font-medium">{title}</p>
		{#if items.length > 0}
			<ul class="list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">
				{#each items as item (item)}<li>{item}</li>{/each}
			</ul>
		{/if}
		{#if children}<div class="text-xs text-muted-foreground">{@render children()}</div>{/if}
	</div>
</div>
