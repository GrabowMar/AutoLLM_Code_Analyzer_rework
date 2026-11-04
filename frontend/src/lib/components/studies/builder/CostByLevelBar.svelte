<script lang="ts">
	/**
	 * CostByLevelBar — which factor level drives the cost (`Estimate.by_level`): per factor with more than
	 * one level, one bar per level (expected USD of the cells at that level), values printed beside the
	 * bars. Factors with a single level are left out (they do not explain any difference).
	 *
	 * Props: `byLevel` (`{factor: {level: usd}}`), `labelOf(factor, level)` (display label; default the
	 * ref without hash), `factorLabel(factor)`.
	 */
	import { formatUsd, refLabel } from '../format';

	interface Props {
		byLevel: Record<string, Record<string, number>>;
		labelOf?: (factor: string, level: string) => string;
		factorLabel?: (factor: string) => string;
	}

	let { byLevel, labelOf = (_f, level) => refLabel(level), factorLabel = (f) => f.replace(/_/g, ' ') }: Props = $props();

	const rows = $derived(
		Object.entries(byLevel)
			.map(([factor, levels]) => {
				const entries = Object.entries(levels).sort((a, b) => b[1] - a[1]);
				const max = Math.max(0, ...entries.map(([, v]) => v));
				return { factor, entries, max };
			})
			.filter((r) => r.entries.length > 1),
	);
</script>

{#if rows.length > 0}
	<div class="space-y-2.5" role="group" aria-label="Expected cost by factor level">
		{#each rows as row (row.factor)}
			<div class="space-y-1">
				<p class="section-label">{factorLabel(row.factor)}</p>
				<ul class="space-y-1">
					{#each row.entries as [level, usd] (level)}
						<li class="grid grid-cols-[minmax(0,7rem)_1fr_auto] items-center gap-2 text-xs">
							<span class="truncate font-mono" title={level}>{labelOf(row.factor, level)}</span>
							<span class="h-2 rounded-sm bg-muted" aria-hidden="true">
								<span class="block h-2 rounded-sm bg-[var(--chart-1)]" style="width: {row.max > 0 ? (usd / row.max) * 100 : 0}%"></span>
							</span>
							<span class="font-mono tabular-nums">{formatUsd(usd)}</span>
						</li>
					{/each}
				</ul>
			</div>
		{/each}
	</div>
{/if}
