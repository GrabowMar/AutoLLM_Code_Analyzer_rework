<script lang="ts" module>
	export interface LegendItem {
		label: string;
		/** CSS color (a `var(--…)` token). */
		color?: string;
		/** Always-visible count/value next to the label. */
		value?: string;
		/** Mark drawn before the label. */
		shape?: 'dot' | 'square' | 'line' | 'dash' | 'hatch' | 'ring' | 'diamond';
	}
</script>

<script lang="ts">
	/**
	 * ChartLegend — HTML legend for the chart wrappers. Identity never rides
	 * on color alone: every entry has a text label (and counts when given).
	 *
	 * Props: `items: LegendItem[]`, `class`, `label` (accessible list name).
	 */
	import { cn } from '$lib/utils';

	interface Props {
		items: LegendItem[];
		label?: string;
		class?: string;
	}

	let { items, label = 'Legend', class: className }: Props = $props();
</script>

<ul
	class={cn(
		'flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted-foreground',
		className
	)}
	aria-label={label}
>
	{#each items as item (item.label)}
		<li class="inline-flex items-center gap-1.5">
			{#if item.shape === 'line'}
				<span class="h-0.5 w-4 rounded-full" style="background: {item.color ?? 'currentColor'}"
				></span>
			{:else if item.shape === 'dash'}
				<span
					class="h-0 w-4 border-t-2 border-dashed"
					style="border-color: {item.color ?? 'currentColor'}"
				></span>
			{:else if item.shape === 'square'}
				<span
					class="h-2.5 w-2.5 rounded-[2px] border"
					style="background: color-mix(in oklab, {item.color ??
						'currentColor'} 25%, transparent); border-color: {item.color ?? 'currentColor'}"
				></span>
			{:else if item.shape === 'hatch'}
				<span class="na-hatch h-2.5 w-2.5 rounded-[2px] border border-border"></span>
			{:else if item.shape === 'ring'}
				<span
					class="h-2.5 w-2.5 rounded-full border-2"
					style="border-color: {item.color ?? 'currentColor'}"
				></span>
			{:else if item.shape === 'diamond'}
				<span
					class="h-2 w-2 rotate-45 border-2 bg-card"
					style="border-color: {item.color ?? 'currentColor'}"
				></span>
			{:else}
				<span class="h-2 w-2 rounded-full" style="background: {item.color ?? 'currentColor'}"
				></span>
			{/if}
			<span class="text-foreground/90">{item.label}</span>
			{#if item.value !== undefined}
				<span class="tabular-nums">{item.value}</span>
			{/if}
		</li>
	{/each}
</ul>

<style>
	.na-hatch {
		background: repeating-linear-gradient(
			45deg,
			var(--muted) 0 3px,
			color-mix(in oklab, var(--muted-foreground) 25%, transparent) 3px 5px
		);
	}
</style>
