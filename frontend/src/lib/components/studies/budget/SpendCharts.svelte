<script lang="ts">
	/**
	 * SpendCharts — where the money went (spec 10.3 §6): spend by condition and by model as ink bars with
	 * the values printed, plus usable / tested runs and cost per usable sample by condition. A condition
	 * with no usable sample shows "—" per sample, never $0.
	 *
	 * Props: `budget` (`StudyBudgetOut`).
	 */
	import type { StudyBudgetOut } from '$lib/api/v3/types';
	import { formatUsd } from '../format';

	interface Props {
		budget: StudyBudgetOut;
	}

	let { budget }: Props = $props();

	const conditionMax = $derived(Math.max(0, ...budget.by_condition.map((c) => c.spent ?? 0)));
	const modelMax = $derived(Math.max(0, ...budget.by_model.map((m) => m.spent ?? 0)));
	const width = (v: number | null, max: number) => (max > 0 && v ? (v / max) * 100 : 0);
</script>

<div class="grid gap-6 lg:grid-cols-2">
	<figure class="space-y-2">
		<figcaption class="section-label">By condition</figcaption>
		<ul class="space-y-1.5">
			{#each budget.by_condition as c (c.condition)}
				<li class="grid grid-cols-[minmax(0,10rem)_1fr_auto] items-center gap-2 text-xs">
					<span class="truncate" title={c.label}>{c.label || `condition ${c.condition}`}</span>
					<span class="h-2 rounded-sm bg-muted" aria-hidden="true"><span class="block h-2 rounded-sm bg-[var(--chart-1)]" style="width: {width(c.spent, conditionMax)}%"></span></span>
					<span class="font-mono tabular-nums">{formatUsd(c.spent)}</span>
					<span class="col-span-3 -mt-1 text-[11px] text-muted-foreground">
						{c.runs} runs · {c.usable} usable · {c.tested} tested · {formatUsd(c.cost_per_usable_sample)} per usable
						{#if c.excluded_cell_budget}· {c.excluded_cell_budget} over the cell cap{/if}
						{#if c.excluded_budget_stop}· {c.excluded_budget_stop} stopped by budget{/if}
						{#if c.excluded_stopped_by_rule}· {c.excluded_stopped_by_rule} stopped by a rule{/if}
					</span>
				</li>
			{:else}
				<li class="text-xs text-muted-foreground">No spend yet.</li>
			{/each}
		</ul>
	</figure>
	<figure class="space-y-2">
		<figcaption class="section-label">By model</figcaption>
		<ul class="space-y-1.5">
			{#each budget.by_model as m (m.model)}
				<li class="grid grid-cols-[minmax(0,10rem)_1fr_auto] items-center gap-2 text-xs">
					<span class="truncate font-mono" title={m.model}>{m.model}</span>
					<span class="h-2 rounded-sm bg-muted" aria-hidden="true"><span class="block h-2 rounded-sm bg-[var(--chart-1)]" style="width: {width(m.spent, modelMax)}%"></span></span>
					<span class="font-mono tabular-nums">{formatUsd(m.spent)}</span>
				</li>
			{:else}
				<li class="text-xs text-muted-foreground">No spend yet.</li>
			{/each}
		</ul>
	</figure>
</div>
