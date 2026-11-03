<script lang="ts">
	/**
	 * ByFactorPanel — estimated marginal means per factor level (`results.by_factor`, spec 10.3 §3): for
	 * each varied factor, the mean of the condition means at each level. On a pruned, excluded or
	 * fractional grid the slices are unbalanced (flagged); a level without a measured condition is "—".
	 *
	 * Props: `byFactor` (`ByFactorOut`), `factorLabel(key)`, `format(value)`.
	 */
	import type { ByFactorOut } from '$lib/api/v3/types';
	import { formatPct, refLabel } from '../format';

	interface Props {
		byFactor: ByFactorOut;
		factorLabel?: (key: string) => string;
		format?: (v: number | null) => string;
	}

	let { byFactor, factorLabel = (k) => k.replace(/_/g, ' '), format = (v) => formatPct(v, 1) }: Props = $props();

	const factors = $derived(Object.entries(byFactor.factors ?? {}));
</script>

{#if factors.length === 0}
	<p class="text-xs text-muted-foreground">No factor varies, so there is no by-factor slice.</p>
{:else}
	<div class="grid gap-4 md:grid-cols-2">
		{#each factors as [key, levels] (key)}
			{@const rows = Object.entries(levels)}
			{@const max = Math.max(0, ...rows.map(([, l]) => l.emm ?? 0))}
			<div class="space-y-1.5">
				<p class="section-label">{factorLabel(key)}</p>
				<ul class="space-y-1">
					{#each rows as [label, l] (label)}
						<li class="grid grid-cols-[minmax(0,8rem)_1fr_auto] items-center gap-2 text-xs">
							<span class="truncate font-mono" title={l.level}>{refLabel(label)}</span>
							<span class="h-2 rounded-sm bg-muted" aria-hidden="true">
								<span class="block h-2 rounded-sm bg-[var(--chart-1)]" style="width: {max > 0 && l.emm !== null ? (l.emm / max) * 100 : 0}%"></span>
							</span>
							<span class="font-mono tabular-nums">{format(l.emm)} <span class="text-muted-foreground">({l.n_measured_conditions}/{l.n_conditions})</span></span>
						</li>
					{/each}
				</ul>
			</div>
		{/each}
	</div>
	<p class="mt-2 text-[11px] text-muted-foreground">
		Estimated marginal means of the condition means ({byFactor.method.replace(/_/g, ' ')}); in brackets measured / planned conditions at that level.
		{#if byFactor.unbalanced}<b class="text-warning-fg">Unbalanced grid:</b> levels are not crossed with every other level, so slices are not directly comparable.{/if}
	</p>
{/if}
