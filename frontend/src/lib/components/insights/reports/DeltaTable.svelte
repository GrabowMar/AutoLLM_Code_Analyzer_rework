<script lang="ts">
	/**
	 * Pairwise condition differences (b − a of the per-trial means). Purely
	 * descriptive — no test, no effect size. A metric where either side has no
	 * observation shows "—": a difference against missing data is not a
	 * difference.
	 */
	import type { ReportConditionComparison } from '$lib/api/reports';
	import NaValue from '$lib/components/analysis/NaValue.svelte';
	import { fmtNum, fmtShare, fmtSigned, humanize } from '../format';

	interface Props {
		comparisons: ReportConditionComparison[];
	}

	let { comparisons }: Props = $props();

	const METRICS: { key: string; label: string; share?: boolean; digits: number }[] = [
		{ key: 'weighted_per_kloc', label: 'Weighted issues / KLOC', digits: 2 },
		{ key: 'issues_total', label: 'Static issues / trial', digits: 2 },
		{ key: 'findings_total', label: 'Static findings / trial', digits: 2 },
		{ key: 'critical_high', label: 'Crit+high / trial', digits: 2 },
		{ key: 'functional_pass_rate', label: 'Functional pass rate', share: true, digits: 1 },
		{ key: 'cost', label: 'Cost / trial (USD)', digits: 4 },
		{ key: 'duration', label: 'Duration / trial (s)', digits: 1 }
	];

	function metricsOf(c: ReportConditionComparison) {
		const known = METRICS.filter((m) => m.key in c.deltas);
		const extra = Object.keys(c.deltas)
			.filter((k) => !METRICS.some((m) => m.key === k))
			.map((k) => ({ key: k, label: humanize(k), digits: 2, share: false }));
		return [...known, ...extra];
	}

	function val(
		v: number | null | undefined,
		share: boolean | undefined,
		digits: number
	): string | null {
		return share ? fmtShare(v, digits) : fmtNum(v, digits);
	}
</script>

{#if comparisons.length === 0}
	<p class="text-xs text-muted-foreground">Fewer than two conditions: nothing to compare.</p>
{:else}
	<div class="space-y-5">
		{#each comparisons as c (`${c.condition_a.id}-${c.condition_b.id}`)}
			<section class="min-w-0 space-y-2">
				<h3 class="text-sm">
					<span class="font-medium">{c.condition_b.label}</span>
					<span class="text-muted-foreground"> vs </span>
					<span class="font-medium">{c.condition_a.label}</span>
					<span class="text-xs text-muted-foreground"> (Δ = b − a)</span>
				</h3>
				<div class="table-scroll-wrapper">
					<table class="min-w-[36rem] text-xs">
						<thead>
							<tr>
								<th scope="col">Metric</th>
								<th scope="col" class="text-right">a (n)</th>
								<th scope="col" class="text-right">b (n)</th>
								<th scope="col" class="text-right">Δ</th>
								<th scope="col" class="text-right">% change</th>
							</tr>
						</thead>
						<tbody>
							{#each metricsOf(c) as m (m.key)}
								{@const d = c.deltas[m.key]}
								<tr>
									<td>{m.label}</td>
									{#if d}
										<td class="text-right font-mono tabular-nums">
											{val(d.a, m.share, m.digits)}
											<span class="text-muted-foreground">({d.n_a})</span>
										</td>
										<td class="text-right font-mono tabular-nums">
											{val(d.b, m.share, m.digits)}
											<span class="text-muted-foreground">({d.n_b})</span>
										</td>
										<td class="text-right font-mono font-medium tabular-nums">
											{m.share ? `${fmtSigned(d.delta * 100, 1)} pp` : fmtSigned(d.delta, m.digits)}
										</td>
										<td class="text-right font-mono tabular-nums text-muted-foreground">
											{#if d.pct_change == null}<NaValue
													reason="Baseline a is 0: relative change undefined"
												/>{:else}{fmtSigned(d.pct_change, 1)}%{/if}
										</td>
									{:else}
										<td colspan="4" class="text-right">
											<NaValue
												reason="A condition has no observation of this metric (n=0): no difference against missing data"
											/>
										</td>
									{/if}
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</section>
		{/each}
		<p class="text-[11px] text-muted-foreground">
			Descriptive differences of per-trial means only. Hypothesis tests, corrections and effect
			sizes with confidence intervals are produced by Studies.
		</p>
	</div>
{/if}
