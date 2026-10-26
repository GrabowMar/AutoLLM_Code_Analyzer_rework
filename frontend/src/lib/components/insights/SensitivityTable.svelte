<script lang="ts">
	/**
	 * Rank stability of the empirical ranking under alternative severity
	 * weights: Kendall's tau vs the baseline and the adjacent pairs that swap.
	 * With fewer than two measured models (`defined: false`) every tau is a
	 * placeholder and is greyed out, never presented as "stable".
	 */
	import type { SensitivityResponse } from '$lib/api/rankings';
	import CoverageNotice from './CoverageNotice.svelte';
	import { fmtScore } from './format';

	interface Props {
		sensitivity: SensitivityResponse;
		/** model_id → display name. */
		names?: Record<string, string>;
	}

	let { sensitivity, names = {} }: Props = $props();

	const defined = $derived(sensitivity.defined ?? sensitivity.models_evaluated >= 2);
	const SEV = ['critical', 'high', 'medium', 'low', 'info'] as const;

	function name(id: string): string {
		return names[id] ?? id;
	}

	function tauClass(tau: number): string {
		if (tau >= 0.9) return 'text-success-fg';
		if (tau >= 0.7) return 'text-warning-fg';
		return 'text-danger-fg';
	}
</script>

<div class="space-y-3">
	{#if !defined}
		<CoverageNotice
			tone="info"
			title="Rank stability is undefined: {sensitivity.models_evaluated} model{sensitivity.models_evaluated ===
			1
				? ''
				: 's'} with an empirical score"
		>
			At least two measured models are needed to compare rankings. The τ values below are
			placeholders, not evidence that the ranking is stable.
		</CoverageNotice>
	{/if}

	{#if sensitivity.baseline_ranking.length > 0}
		<div class="space-y-1">
			<p class="section-label">Baseline empirical order</p>
			<ol class="flex flex-wrap gap-x-3 gap-y-1 text-xs">
				{#each sensitivity.baseline_ranking as entry, i (entry.model_id)}
					<li class="inline-flex items-center gap-1">
						<span class="font-mono text-muted-foreground">{i + 1}.</span>
						<span class="font-medium">{name(entry.model_id)}</span>
						<span class="font-mono tabular-nums text-muted-foreground"
							>{fmtScore(entry.empirical_quality) ?? '—'}</span
						>
					</li>
				{/each}
			</ol>
		</div>
	{/if}

	<div class="table-scroll-wrapper">
		<table class="min-w-[36rem] text-xs">
			<thead>
				<tr>
					<th scope="col">Scheme</th>
					<th scope="col">Weights (crit / high / med / low / info)</th>
					<th scope="col" class="text-right">Kendall's τ</th>
					<th scope="col">Rank swaps vs baseline</th>
				</tr>
			</thead>
			<tbody>
				{#each sensitivity.schemes as s (s.scheme)}
					{@const schemeDefined = (s.defined ?? defined) && defined}
					<tr>
						<td class="font-mono">{s.scheme.replace(/_/g, ' ')}</td>
						<td class="font-mono tabular-nums">{SEV.map((k) => s.weights[k] ?? 0).join(' / ')}</td>
						<td class="text-right font-mono tabular-nums">
							{#if schemeDefined}
								<span class={tauClass(s.kendall_tau)}>{s.kendall_tau.toFixed(2)}</span>
							{:else}
								<span
									class="cursor-help text-muted-foreground opacity-60"
									title="Placeholder: fewer than two measured models, nothing to compare"
								>
									{s.kendall_tau.toFixed(2)}<span class="sr-only"> (placeholder, undefined)</span>
								</span>
							{/if}
						</td>
						<td>
							{#if !schemeDefined}
								<span class="text-muted-foreground">not comparable</span>
							{:else if s.adjacent_swaps.length === 0}
								<span class="text-success-fg">none</span>
							{:else}
								{s.adjacent_swaps.map((p) => p.map(name).join(' ⇄ ')).join(', ')}
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
</div>
