<script lang="ts">
	/**
	 * Tool execution outcomes as a thin stacked meter with a counted legend.
	 * Only `succeeded` executions are scans; the rest produced no data
	 * (failed, timed out, skipped, cancelled) or had nothing to scan (N/A).
	 */
	import { cn } from '$lib/utils';

	interface Props {
		counts: Record<string, number>;
		/** Hide the legend (e.g. inside a dense table cell that lists counts already). */
		legend?: boolean;
		class?: string;
	}

	let { counts, legend = true, class: className }: Props = $props();

	const STATUS_ORDER: { key: string; label: string; color: string }[] = [
		{ key: 'succeeded', label: 'succeeded', color: 'var(--success)' },
		{ key: 'failed', label: 'failed', color: 'var(--destructive)' },
		{
			key: 'timed_out',
			label: 'timed out',
			color: 'color-mix(in oklab, var(--destructive) 55%, var(--warning))'
		},
		{ key: 'skipped', label: 'skipped', color: 'var(--warning)' },
		{ key: 'cancelled', label: 'cancelled', color: 'var(--muted-foreground)' },
		{
			key: 'not_applicable',
			label: 'not applicable',
			color: 'color-mix(in oklab, var(--muted-foreground) 35%, transparent)'
		},
		{ key: 'running', label: 'running', color: 'var(--info)' },
		{ key: 'pending', label: 'pending', color: 'color-mix(in oklab, var(--info) 45%, transparent)' }
	];

	const segments = $derived.by(() => {
		const known = STATUS_ORDER.map((s) => ({ ...s, n: counts[s.key] ?? 0 }));
		const extra = Object.entries(counts)
			.filter(([k]) => !STATUS_ORDER.some((s) => s.key === k))
			.map(([k, n]) => ({
				key: k,
				label: k.replace(/_/g, ' '),
				color: 'var(--muted-foreground)',
				n
			}));
		return [...known, ...extra].filter((s) => s.n > 0);
	});
	const total = $derived(segments.reduce((sum, s) => sum + s.n, 0));
</script>

<div class={cn('min-w-0 space-y-1', className)}>
	{#if total > 0}
		<div
			class="flex h-1.5 w-full min-w-16 gap-[2px]"
			role="img"
			aria-label={segments.map((s) => `${s.label} ${s.n}`).join(', ')}
		>
			{#each segments as seg (seg.key)}
				<div
					class="h-full min-w-[3px] first:rounded-l-[4px] last:rounded-r-[4px]"
					style="flex: {seg.n} 1 0; background: {seg.color}"
					title="{seg.label}: {seg.n}"
				></div>
			{/each}
		</div>
	{/if}
	{#if legend}
		<div class="flex flex-wrap gap-x-2.5 gap-y-0.5 text-[11px] text-muted-foreground">
			{#each segments as seg (seg.key)}
				<span class="inline-flex items-center gap-1 whitespace-nowrap">
					<span class="h-1.5 w-1.5 rounded-full" style="background: {seg.color}" aria-hidden="true"
					></span>
					{seg.label}
					<span class="font-medium tabular-nums text-foreground">{seg.n}</span>
				</span>
			{/each}
		</div>
	{/if}
</div>
