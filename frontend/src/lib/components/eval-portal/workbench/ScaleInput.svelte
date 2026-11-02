<script lang="ts">
	/**
	 * ScaleInput — one point of an integer rating scale (min..max on its step
	 * grid) as a row of buttons with the end anchors under it, plus an optional
	 * "Can't judge" (N/A). A bits-ui RadioGroup: arrow keys move and select.
	 * Values are not colored by size (a score is not good or bad by itself).
	 *
	 * Props:
	 * - `scale`, `value` (number | "na" | undefined), `allowNa`.
	 * - `onchange(value)`; `readOnly`; `invalid` (still needs an answer).
	 * - `aria-labelledby` / `aria-describedby`: the question text and help.
	 */
	import { RadioGroup } from 'bits-ui';
	import type { Scale } from '$lib/api/types/human-eval';
	import { cn } from '$lib/utils';
	import { scaleValues } from './completeness';

	interface Props {
		scale: Scale;
		value: number | 'na' | null | undefined;
		allowNa?: boolean;
		naLabel?: string;
		onchange?: (value: number | 'na') => void;
		readOnly?: boolean;
		invalid?: boolean;
		'aria-labelledby'?: string;
		'aria-describedby'?: string;
	}

	let {
		scale,
		value,
		allowNa = false,
		naLabel = "Can't judge",
		onchange,
		readOnly = false,
		invalid = false,
		'aria-labelledby': labelledby,
		'aria-describedby': describedby,
	}: Props = $props();

	const points = $derived(scaleValues(scale));
	const key = $derived(value === undefined || value === null ? '' : String(value));
	const pointLabel = (v: number) => scale.labels?.[String(v)] ?? '';
	// Short scales with a label on every point show the labels under the points.
	const labelled = $derived(points.length <= 7 && points.every((v) => !!pointLabel(v)));

	function pick(next: string) {
		if (readOnly || next === key) return;
		if (next === 'na') onchange?.('na');
		else if (next !== '') onchange?.(Number(next));
	}

	function ariaFor(v: number): string {
		const parts = [String(v)];
		const label = pointLabel(v);
		if (label) parts.push(label);
		else if (v === scale.min && scale.min_label) parts.push(scale.min_label);
		else if (v === scale.max && scale.max_label) parts.push(scale.max_label);
		return parts.join(' — ');
	}

	const itemClass =
		'inline-flex cursor-pointer items-center justify-center rounded-md border border-border bg-surface-1 font-mono text-sm tabular-nums text-muted-foreground transition-colors duration-150 hover:border-primary/50 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 data-[state=checked]:border-primary data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground data-[disabled]:cursor-default data-[disabled]:hover:border-border data-[disabled]:data-[state=unchecked]:opacity-55';
</script>

<div class="min-w-0">
	<RadioGroup.Root
		value={key}
		onValueChange={pick}
		disabled={readOnly}
		orientation="horizontal"
		loop
		aria-labelledby={labelledby}
		aria-describedby={describedby}
		class="flex flex-wrap items-start gap-1"
	>
		{#if labelled}
			{#each points as point (point)}
				<div class="flex w-16 flex-col items-center gap-1 sm:w-20">
					<RadioGroup.Item
						value={String(point)}
						aria-label={ariaFor(point)}
						class={cn(itemClass, 'h-9 w-full', invalid && key === '' && 'border-warning/60')}
					>
						{point}
					</RadioGroup.Item>
					<span class="text-center text-[11px] leading-tight text-muted-foreground" aria-hidden="true">
						{pointLabel(point)}
					</span>
				</div>
			{/each}
		{:else}
			{#each points as point (point)}
				<RadioGroup.Item
					value={String(point)}
					aria-label={ariaFor(point)}
					title={pointLabel(point) || undefined}
					class={cn(itemClass, 'h-9 min-w-9 px-2', invalid && key === '' && 'border-warning/60')}
				>
					{point}
				</RadioGroup.Item>
			{/each}
		{/if}
		{#if allowNa}
			<RadioGroup.Item
				value="na"
				class={cn(
					itemClass,
					'h-9 px-3 font-sans text-xs',
					'data-[state=checked]:border-foreground/40 data-[state=checked]:bg-muted data-[state=checked]:text-foreground',
					!labelled && 'sm:ml-2'
				)}
			>
				{naLabel}
			</RadioGroup.Item>
		{/if}
	</RadioGroup.Root>
	{#if !labelled && (scale.min_label || scale.max_label)}
		<div class="mt-1 flex max-w-[34rem] justify-between gap-4 text-[11px] text-muted-foreground" aria-hidden="true">
			<span>{scale.min} = {scale.min_label || '—'}</span>
			<span class="text-right">{scale.max} = {scale.max_label || '—'}</span>
		</div>
	{/if}
</div>
