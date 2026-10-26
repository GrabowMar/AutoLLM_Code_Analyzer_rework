<script lang="ts" module>
	import type { ReportGroupRow } from '$lib/api/reports';

	export interface ComparisonGroup {
		key: string;
		label: string;
		sublabel?: string;
		href?: string;
		row: ReportGroupRow;
	}
</script>

<script lang="ts">
	/**
	 * Groups (models, experiment conditions) side by side on per-trial
	 * statistics of the same plan. Static measurements first; the AI
	 * reviewer's consensus sits in its own column group. Every cell shows
	 * mean ± sd with n, and "—" when a group has no observation.
	 */
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import StatCell from '../StatCell.svelte';
	import MaybeValue from '../MaybeValue.svelte';
	import { funnelGapCount, funnelGapLines } from '../coverage';
	import { fmtShare } from '../format';

	interface Props {
		groups: ComparisonGroup[];
		groupLabel?: string;
	}

	let { groups, groupLabel = 'Group' }: Props = $props();

	function gapTitle(g: ComparisonGroup): string {
		const lines = funnelGapLines(g.row.stats.analysis ?? {});
		const excluded = g.row.stats.excluded
			? [`${g.row.stats.excluded} completed job(s) excluded by the eligibility rules`]
			: [];
		const all = [...excluded, ...lines];
		return all.length ? `Not in the numbers:\n${all.join('\n')}` : 'Every trial is measured';
	}
</script>

<div class="table-scroll-wrapper">
	<table class="table-card-mobile md:min-w-[68rem]">
		<thead>
			<tr>
				<th scope="col">{groupLabel}</th>
				<th scope="col" class="text-right">Jobs</th>
				<th
					scope="col"
					class="text-right"
					title="Trials with a canonical measured run / eligible completed jobs">Analysed</th
				>
				<th scope="col" class="text-right" title="Counted static findings per trial">Findings</th>
				<th scope="col" class="text-right" title="Counted static issues (deduplicated) per trial"
					>Issues</th
				>
				<th scope="col" class="text-right" title="Critical + high static findings per trial"
					>Crit+high</th
				>
				<th
					scope="col"
					class="text-right"
					title="Severity-weighted issues per KLOC of measured SLOC">Weighted / KLOC</th
				>
				<th scope="col" class="text-right">Functional</th>
				<th scope="col" class="text-right" title="Generation cost per trial (USD)">Cost</th>
				<th
					scope="col"
					class="border-l text-right"
					title="AI consensus findings per trial — opinions">AI consensus</th
				>
			</tr>
		</thead>
		<tbody>
			{#each groups as g (g.key)}
				{@const s = g.row.stats}
				{@const gaps = funnelGapCount(s.analysis ?? {}) + (s.excluded ?? 0)}
				<tr>
					<td data-label="" class="min-w-44">
						{#if g.href}
							<a href={g.href} class="font-medium hover:underline">{g.label}</a>
						{:else}
							<span class="font-medium">{g.label}</span>
						{/if}
						{#if g.sublabel}<span class="block font-mono text-[11px] text-muted-foreground"
								>{g.sublabel}</span
							>{/if}
					</td>
					<td data-label="Jobs" class="text-xs md:text-right">
						<span class="font-mono tabular-nums"
							>{g.row.generation.completed_jobs}<span class="text-muted-foreground"
								>/{g.row.generation.total_jobs}</span
							></span
						>
						<span class="block text-[11px] text-muted-foreground">
							<MaybeValue
								value={fmtShare(g.row.generation.success_rate, 0)}
								reason="No finished job"
							/> success
						</span>
					</td>
					<td data-label="Analysed" class="text-xs md:text-right">
						<span class="inline-flex items-center gap-1 font-mono tabular-nums" title={gapTitle(g)}>
							{s.analyzed}<span class="text-muted-foreground">/{s.trials}</span>
							{#if gaps > 0}
								<TriangleAlert class="h-3 w-3 text-warning-fg" aria-hidden="true" />
								<span class="sr-only">{gaps} jobs not in the numbers</span>
							{/if}
						</span>
					</td>
					<td data-label="Findings" class="text-xs md:text-right"
						><StatCell stat={s.findings_total} digits={1} /></td
					>
					<td data-label="Issues" class="text-xs md:text-right"
						><StatCell stat={s.issues_total} digits={1} /></td
					>
					<td data-label="Crit+high" class="text-xs md:text-right"
						><StatCell stat={s.critical_high} digits={1} /></td
					>
					<td data-label="Weighted / KLOC" class="text-xs md:text-right"
						><StatCell stat={s.weighted_per_kloc} digits={2} /></td
					>
					<td data-label="Functional" class="text-xs md:text-right">
						<StatCell stat={s.functional?.pass_rate} share />
					</td>
					<td data-label="Cost" class="text-xs md:text-right"
						><StatCell stat={s.cost} digits={4} /></td
					>
					<td
						data-label="AI consensus"
						class="border-l text-xs text-muted-foreground md:text-right"
					>
						<StatCell stat={s.ai_findings_total} digits={1} />
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>
