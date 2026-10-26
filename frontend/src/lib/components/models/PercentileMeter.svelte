<script lang="ts">
	/**
	 * One catalog-relative attribute: label, value, and a neutral meter marking
	 * where the model sits in the catalog distribution (with the median tick).
	 *
	 *   <PercentileMeter label="Input price" value="$3.00" percentile={0.83} median={0.36} hint="pricier than 83% of 441 paid models" />
	 *
	 * Props: `label`, `value` (display text), `percentile` (0–1 or null → no meter),
	 * `hint` (sentence under the meter), `title`.
	 */
	interface Props {
		label: string;
		value: string;
		percentile: number | null | undefined;
		hint?: string;
		title?: string;
	}

	let { label, value, percentile, hint, title }: Props = $props();

	const pct = $derived(percentile == null ? null : Math.max(0, Math.min(1, percentile)) * 100);
</script>

<div class="min-w-0 space-y-1.5" {title}>
	<div class="flex items-baseline justify-between gap-3">
		<span class="text-xs text-muted-foreground">{label}</span>
		<span class="font-mono text-sm font-medium tabular-nums">{value}</span>
	</div>
	{#if pct != null}
		<div class="relative h-1.5 rounded-full bg-muted" role="img" aria-label="{label}: percentile {Math.round(pct)} of the catalog">
			<span class="absolute inset-y-0 left-1/2 w-px bg-border" aria-hidden="true"></span>
			<span
				class="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card bg-primary"
				style:left="{pct}%"
				aria-hidden="true"
			></span>
		</div>
	{/if}
	{#if hint}<p class="text-[11px] leading-snug text-muted-foreground">{hint}</p>{/if}
</div>
