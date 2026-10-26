<script lang="ts">
	/**
	 * Analysis coverage of a set of jobs under one plan: reference plan, a
	 * stacked meter (measured / excluded / incomplete / failed / other plan /
	 * in progress / not analysed) with a counted legend, exclusion reasons and
	 * the other plans in use.
	 *
	 * Props:
	 * - `coverage`: rankings `comparability`, report `analysis_coverage` or a
	 *   per-row funnel (plan fields optional).
	 * - `subject`: what the jobs are called ("completed jobs").
	 * - `showPlan`: render the reference-plan line (default true).
	 * - `onSelectPlan(hash)`: offer "Use this plan" next to the other plans.
	 */
	import Check from '@lucide/svelte/icons/check';
	import type { RankingAnalysisFunnel } from '$lib/api/rankings';
	import NaValue from '$lib/components/analysis/NaValue.svelte';
	import PlanHash from './PlanHash.svelte';
	import { FUNNEL_SEGMENTS, funnelTotal } from './coverage';
	import { exclusionLabel } from './format';

	type Coverage = RankingAnalysisFunnel & {
		jobs?: number;
		plan_hash?: string | null;
		plan_explicit?: boolean;
		complete_only?: boolean;
		plans?: Record<string, number>;
		mixed_plans?: boolean;
	};

	interface Props {
		coverage: Coverage;
		subject?: string;
		showPlan?: boolean;
		onSelectPlan?: (hash: string) => void;
	}

	let { coverage, subject = 'completed jobs', showPlan = true, onSelectPlan }: Props = $props();

	const total = $derived(funnelTotal(coverage));
	const segments = $derived(
		FUNNEL_SEGMENTS.map((s) => ({ ...s, n: coverage[s.key] ?? 0 })).filter((s) => s.n > 0)
	);
	const measuredShare = $derived(total ? Math.round((coverage.measured / total) * 100) : null);
	const reasons = $derived(
		Object.entries(coverage.excluded_reasons ?? {}).filter(([, n]) => n > 0)
	);
	const plans = $derived(
		Object.entries(coverage.plans ?? {}).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
	);
	const ariaSummary = $derived(
		segments.map((s) => `${s.label}: ${s.n}`).join(', ') || `No ${subject}`
	);
</script>

<div class="space-y-3">
	{#if showPlan}
		<div class="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
			<span class="section-label">Reference plan</span>
			{#if coverage.plan_hash}
				<PlanHash hash={coverage.plan_hash} />
				<span class="text-muted-foreground">
					{coverage.plan_explicit ? 'pinned' : 'auto-selected: completely measured the most jobs'}
					{#if coverage.complete_only}· complete runs only{/if}
				</span>
			{:else}
				<NaValue reason="No job has a complete analysis under any plan yet" />
				<span class="text-muted-foreground">nothing measured yet</span>
			{/if}
		</div>
	{/if}

	{#if total === 0}
		<p class="text-xs text-muted-foreground">No {subject} in scope.</p>
	{:else}
		<div class="space-y-1.5">
			<div class="flex items-baseline justify-between gap-2 text-xs">
				<span>
					<span class="font-semibold tabular-nums">{coverage.measured}</span>
					<span class="text-muted-foreground">of {total} {subject} measured</span>
				</span>
				{#if measuredShare !== null}
					<span class="tabular-nums text-muted-foreground">{measuredShare}%</span>
				{/if}
			</div>
			<div class="flex h-2.5 w-full gap-[2px]" role="img" aria-label="Coverage: {ariaSummary}">
				{#each segments as seg (seg.key)}
					<div
						class="h-full min-w-[3px] first:rounded-l-[4px] last:rounded-r-[4px]"
						style="flex: {seg.n} 1 0; background: {seg.color}"
						title="{seg.label}: {seg.n} — {seg.help}"
					></div>
				{/each}
			</div>
		</div>

		<ul class="flex flex-wrap gap-x-4 gap-y-1.5 text-xs">
			{#each segments as seg (seg.key)}
				<li class="flex items-center gap-1.5" title={seg.help}>
					<span
						class="h-2 w-2 shrink-0 rounded-full"
						style="background: {seg.color}"
						aria-hidden="true"
					></span>
					<span>{seg.label}</span>
					<span class="font-medium tabular-nums">{seg.n}</span>
				</li>
			{/each}
		</ul>

		{#if reasons.length > 0}
			<div class="flex flex-wrap items-center gap-1.5 text-xs">
				<span class="text-muted-foreground">Excluded because:</span>
				{#each reasons as [code, n] (code)}
					<span class="mono-tag">{exclusionLabel(code)} ×{n}</span>
				{/each}
			</div>
		{/if}
	{/if}

	{#if showPlan && plans.length > 1}
		<div class="space-y-1.5 border-t pt-3">
			<p class="text-xs text-muted-foreground">
				Jobs were analysed under {plans.length} plans. Only runs of one plan are compared; runs of the
				others count as "other plan".
			</p>
			<ul class="space-y-1">
				{#each plans as [hash, n] (hash)}
					<li class="flex flex-wrap items-center gap-2 text-xs">
						<PlanHash {hash} />
						<span class="tabular-nums text-muted-foreground">{n} job{n === 1 ? '' : 's'}</span>
						{#if hash === coverage.plan_hash}
							<span class="inline-flex items-center gap-1 text-success-fg">
								<Check class="h-3 w-3" aria-hidden="true" />reference
							</span>
						{:else if onSelectPlan}
							<button
								type="button"
								class="cursor-pointer text-primary underline-offset-2 hover:underline"
								onclick={() => onSelectPlan?.(hash)}
							>
								Use this plan
							</button>
						{/if}
					</li>
				{/each}
			</ul>
		</div>
	{/if}
</div>
