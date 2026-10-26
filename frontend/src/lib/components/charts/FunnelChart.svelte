<script lang="ts" module>
	export interface FunnelStep {
		key: string;
		step: string;
		/** null = unknown (NA), never 0. */
		n: number | null;
		/** Share of the first step in [0, 1]. */
		share: number | null;
	}
</script>

<script lang="ts">
	/**
	 * FunnelChart — attrition from the first step to the last as horizontal
	 * bars with counts, shares and the drop between consecutive steps.
	 *
	 * Props: `steps: FunnelStep[]`, `color` (CSS var, default `var(--chart-1)`),
	 * `label` (accessible name).
	 */
	import ChartEmpty from './ChartEmpty.svelte';

	interface Props {
		steps: FunnelStep[];
		color?: string;
		label?: string;
	}

	let { steps, color = 'var(--chart-1)', label = 'Funnel' }: Props = $props();

	function pct(share: number | null): string {
		if (share === null || !Number.isFinite(share)) return '—';
		return `${Number((share * 100).toFixed(1))}%`;
	}

	const rows = $derived(
		steps.map((s, i) => {
			const prev = i > 0 ? steps[i - 1].n : null;
			const drop = prev !== null && s.n !== null ? prev - s.n : null;
			return { ...s, drop };
		})
	);
</script>

{#if steps.length === 0}
	<ChartEmpty message="No funnel data." height={100} />
{:else}
	<figure aria-label={label}>
		<ol class="space-y-2">
			{#each rows as s (s.key)}
				<li
					class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 text-xs sm:grid-cols-[minmax(9rem,16rem)_minmax(0,1fr)_auto]"
				>
					<span class="truncate text-foreground" title={s.step}>{s.step}</span>
					<span
						class="order-3 col-span-2 h-3 overflow-hidden rounded-sm bg-muted sm:order-none sm:col-span-1"
						aria-hidden="true"
					>
						{#if s.share !== null}
							<span
								class="block h-full rounded-sm"
								style="width: {Math.max(0, Math.min(1, s.share)) * 100}%; background: {color}"
							></span>
						{/if}
					</span>
					<span class="whitespace-nowrap text-right tabular-nums">
						<span class="font-medium">{s.n ?? '—'}</span>
						<span class="text-muted-foreground">({pct(s.share)})</span>
						{#if s.drop !== null && s.drop > 0}
							<span class="ml-1 text-warning-fg" title="Lost since the previous step"
								>−{s.drop}</span
							>
						{/if}
					</span>
				</li>
			{/each}
		</ol>
	</figure>
{/if}
