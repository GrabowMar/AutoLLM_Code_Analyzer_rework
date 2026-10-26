<script lang="ts" module>
	import type { ReportOutcomeValue } from '$lib/api/reports';

	export interface StatRow {
		label: string;
		stat: ReportOutcomeValue | null | undefined;
		digits?: number;
		share?: boolean;
		ai?: boolean;
		hint?: string;
	}
</script>

<script lang="ts">
	/**
	 * Per-trial summary statistics as a table (mean, median, sd, n, missing).
	 * AI-reviewer rows are listed in their own group under the static ones.
	 */
	import { isShareValue } from '$lib/api/reports';
	import NaValue from '$lib/components/analysis/NaValue.svelte';
	import { fmtNum, fmtShare } from '../format';

	interface Props {
		rows: StatRow[];
		caption?: string;
	}

	let { rows, caption }: Props = $props();

	const staticRows = $derived(rows.filter((r) => !r.ai));
	const aiRows = $derived(rows.filter((r) => r.ai));

	function fmt(v: number | null | undefined, r: StatRow): string | null {
		return r.share ? fmtShare(v, 1) : fmtNum(v, r.digits ?? 2);
	}
</script>

{#snippet row(r: StatRow)}
	{@const s = r.stat}
	<tr>
		<td class="text-xs">
			<span class="font-medium">{r.label}</span>
			{#if r.hint}<span class="block text-[11px] text-muted-foreground">{r.hint}</span>{/if}
		</td>
		{#if !s}
			<td colspan="5" class="text-xs"><NaValue reason="Not reported" /></td>
		{:else if isShareValue(s)}
			<td class="text-right font-mono text-xs tabular-nums">
				{#if s.share_true == null}<NaValue reason="No observation (n=0)" />{:else}{fmtShare(
						s.share_true,
						1
					)}{/if}
			</td>
			<td class="text-right text-xs"><NaValue reason="Binary outcome: share of trials only" /></td>
			<td class="text-right text-xs"><NaValue reason="Binary outcome: share of trials only" /></td>
			<td class="text-right font-mono text-xs tabular-nums">{s.n}</td>
			<td class="text-right font-mono text-xs tabular-nums text-muted-foreground">{s.n_missing}</td>
		{:else}
			<td class="text-right font-mono text-xs tabular-nums">
				{#if s.mean == null}<NaValue reason="No observation (n=0)" />{:else}{fmt(s.mean, r)}{/if}
			</td>
			<td class="text-right font-mono text-xs tabular-nums">
				{#if s.median == null}<NaValue
						reason={s.median === undefined ? 'Not reported' : 'No observation (n=0)'}
					/>{:else}{fmt(s.median, r)}{/if}
			</td>
			<td class="text-right font-mono text-xs tabular-nums">
				{#if s.stdev == null}<NaValue reason="Undefined below n=2" />{:else}{fmt(s.stdev, r)}{/if}
			</td>
			<td class="text-right font-mono text-xs tabular-nums">{s.n}</td>
			<td class="text-right font-mono text-xs tabular-nums text-muted-foreground">
				{#if s.n_missing == null}<NaValue
						reason="Not tracked for this statistic"
					/>{:else}{s.n_missing}{/if}
			</td>
		{/if}
	</tr>
{/snippet}

<div class="table-scroll-wrapper">
	<table class="min-w-[34rem]">
		{#if caption}<caption class="sr-only">{caption}</caption>{/if}
		<thead>
			<tr>
				<th scope="col">Metric (per trial)</th>
				<th scope="col" class="text-right">Mean</th>
				<th scope="col" class="text-right">Median</th>
				<th scope="col" class="text-right">SD</th>
				<th scope="col" class="text-right">n</th>
				<th
					scope="col"
					class="text-right"
					title="Trials without the value (not measured or unavailable)">Missing</th
				>
			</tr>
		</thead>
		<tbody>
			{#each staticRows as r (r.label)}{@render row(r)}{/each}
		</tbody>
		{#if aiRows.length > 0}
			<tbody>
				<tr>
					<td colspan="6" class="border-t-2 bg-muted/30 text-[11px] text-muted-foreground">
						<span class="font-semibold tracking-wider uppercase">AI review</span> — opinions, kept apart
						from the static measurement
					</td>
				</tr>
				{#each aiRows as r (r.label)}{@render row(r)}{/each}
			</tbody>
		{/if}
	</table>
</div>
