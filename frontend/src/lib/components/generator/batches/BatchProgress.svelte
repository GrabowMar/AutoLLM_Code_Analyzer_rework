<script lang="ts">
	/**
	 * Stacked progress bar of a batch's job counts, with an optional legend.
	 *
	 * Props:
	 * - `counts`: `{pending, queued, running, completed, failed, cancelled}`.
	 * - `total`: jobs in the batch (bar width base; falls back to the counts' sum).
	 * - `legend` (default false): counts as labelled chips under the bar.
	 * - `class`: wrapper classes.
	 */
	import type { GenerationBatchCounts } from '$lib/api/generation';
	import { cn } from '$lib/utils';
	import { finishedCount } from './batch-utils';

	interface Props {
		counts: GenerationBatchCounts;
		total?: number;
		legend?: boolean;
		class?: string;
	}

	let { counts, total, legend = false, class: className }: Props = $props();

	// Finished segments first, then in-flight, then waiting.
	const SEGMENTS: { key: keyof GenerationBatchCounts; label: string; bar: string }[] = [
		{ key: 'completed', label: 'completed', bar: 'bg-success' },
		{ key: 'failed', label: 'failed', bar: 'bg-destructive' },
		{ key: 'cancelled', label: 'cancelled', bar: 'bg-muted-foreground/50' },
		{ key: 'running', label: 'running', bar: 'bg-info' },
		{ key: 'queued', label: 'queued', bar: 'bg-info/40' },
		{ key: 'pending', label: 'pending', bar: 'bg-muted-foreground/20' },
	];

	const sum = $derived(SEGMENTS.reduce((acc, s) => acc + (counts[s.key] ?? 0), 0));
	const base = $derived(Math.max(total ?? 0, sum, 1));
	const done = $derived(finishedCount(counts));
	const summary = $derived(
		SEGMENTS.filter((s) => counts[s.key] > 0)
			.map((s) => `${counts[s.key]} ${s.label}`)
			.join(', ') || 'no runs'
	);
</script>

<div class={cn('min-w-0 space-y-1.5', className)}>
	<div
		class="flex h-1.5 w-full overflow-hidden rounded-full bg-muted"
		role="img"
		aria-label="{done} of {base} runs finished: {summary}"
	>
		{#each SEGMENTS as seg (seg.key)}
			{#if counts[seg.key] > 0}
				<span
					class="{seg.bar} h-full transition-[width] duration-300 motion-reduce:transition-none"
					style="width: {(counts[seg.key] / base) * 100}%"
				></span>
			{/if}
		{/each}
	</div>
	{#if legend}
		<ul class="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground" aria-hidden="true">
			{#each SEGMENTS as seg (seg.key)}
				{#if counts[seg.key] > 0}
					<li class="inline-flex items-center gap-1.5">
						<span class="{seg.bar} inline-block h-2 w-2 rounded-full"></span>
						<span class="font-mono tabular-nums text-foreground">{counts[seg.key]}</span>{seg.label}
					</li>
				{/if}
			{/each}
		</ul>
	{/if}
</div>
