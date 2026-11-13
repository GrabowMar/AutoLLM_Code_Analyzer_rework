<script lang="ts">
	/**
	 * ComparisonTable — condition-pair differences per feature (spec 7.9, 10.3 §4): the difference in points
	 * with its 95% CI, the method actually used (paired when both conditions forwarded the seed, else
	 * independent), the n behind it, and for pre-registered (planned) rows the raw p, the p adjusted over the
	 * planned family by the plan's correction and the decision at the plan's alpha, or BH q for exploratory
	 * rows — all withheld while the study is interim. The pooled-across-targets estimate (Mantel–Haenszel, with
	 * "few clusters" notes) is shown under the table.
	 *
	 * Props: `patternRunId`, `feature?`.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { tableFrame, TableToolbar } from '$lib/components/table';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { getComparisons, getPooled } from '$lib/api/v3/patterns';
	import type { ComparisonRow, PooledOut, PooledRow } from '$lib/api/v3/types';
	import { fmtComparisonN, fmtDecision, fmtDiff, fmtDiffCI, fmtSignificance, pooledSignificance } from './format';

	interface Props {
		patternRunId: string;
		feature?: string;
	}

	let { patternRunId, feature }: Props = $props();

	let rows = $state<ComparisonRow[]>([]);
	let pooled = $state<PooledOut | null>(null);
	let loading = $state(true);
	let error = $state('');

	$effect(() => {
		const controller = new AbortController();
		loading = true;
		error = '';
		Promise.all([
			getComparisons(patternRunId, feature, { signal: controller.signal }),
			getPooled(patternRunId, feature, { signal: controller.signal }).catch(() => null),
		])
			.then(([c, p]) => {
				rows = c;
				pooled = p;
			})
			.catch((e) => {
				if (!isAbortError(e)) error = formatApiError(e, 'Could not load the comparisons.');
			})
			.finally(() => (loading = false));
		return () => controller.abort();
	});

	const interim = $derived(rows.some((r) => r.interim));


	const labels = $derived(
		new Map(rows.flatMap((r) => [[r.a, r.a_label ?? ''] as const, [r.b, r.b_label ?? ''] as const]).filter(([, l]) => !!l)),
	);
	function labelOf(id: number | undefined): string {
		return id === undefined ? '—' : labels.get(id) || `#${id}`;
	}
	const planned = $derived(rows.filter((r) => r.planned).length);

	// Progressive disclosure: planned rows and rows with a non-zero estimate lead; comparisons whose
	// estimate is exactly 0 or not estimable sit behind "Show all" (counted, never dropped).
	let showAll = $state(false);
	const informative = (r: ComparisonRow) => !!r.planned || (r.diff !== null && r.diff !== 0);
	const lead = $derived(rows.filter(informative));
	const shown = $derived(showAll ? rows : lead);
	const quiet = $derived(rows.length - lead.length);
	let showAllPooled = $state(false);
	const pooledRows = $derived((pooled?.rows ?? []) as PooledRow[]);
	const pooledLead = $derived(pooledRows.filter((p) => p.planned || (p.pooled?.rd != null && p.pooled.rd !== 0)));
	const pooledShown = $derived(showAllPooled ? pooledRows : pooledLead);
</script>

<Card.Root class="gap-0 overflow-clip py-0">
	<TableToolbar>
		{#snippet summary()}
			<span>
				{rows.length} comparisons ({lead.length} shown first) · {planned} planned (p, plan-adjusted p and decision) · {rows.length - planned} exploratory (Benjamini–Hochberg q)
				{#if interim}· <b>interim: p and q withheld</b>{/if}
			</span>
		{/snippet}
	</TableToolbar>
	{#if loading}
		<LoadingState label="loading comparisons" />
	{:else if error}
		<div class="p-4"><ErrorAlert message={error} /></div>
	{:else if rows.length === 0}
		<EmptyState compact title="No comparisons" description="Comparisons need at least two conditions." />
	{:else}
		<div class="table-frame" {@attach tableFrame}>
			<table>
				<thead>
					<tr>
						<th>Feature</th>
						<th>A vs B</th>
						<th class="num">Difference</th>
						<th class="num">95% CI (pts)</th>
						<th>Method</th>
						<th class="num">n</th>
						<th class="num">Significance</th>
						<th class="num" title="Size of the multiplicity family">Family</th>
					</tr>
				</thead>
				<tbody>
					{#each shown as row, i (i)}
						<tr>
							<td class="text-xs">
								<span class="font-mono">{row.feature}</span>{#if row.scope}<span class="text-muted-foreground"> · {row.scope}</span>{/if}
								{#if row.planned}<Badge variant="default" class="ml-1" title="Pre-registered primary feature">planned</Badge>{/if}
							</td>
							<td class="text-xs">{row.a_label || `#${row.a}`} <span class="text-muted-foreground">vs</span> {row.b_label || `#${row.b}`}</td>
							<td class="num">{fmtDiff(row.diff)}</td>
							<td class="num text-xs">{fmtDiffCI(row.ci_low, row.ci_high)}</td>
							<td class="text-xs">{row.method}{#if row.test}<div class="cell-sub">{row.test.replace(/_/g, ' ')}</div>{/if}</td>
							<td class="num text-xs">{fmtComparisonN(row)}</td>
							<td class="num text-xs">
								{fmtSignificance(row)}
								{#if fmtDecision(row)}<div class="cell-sub" class:font-semibold={row.significant === true}>{fmtDecision(row)}</div>{/if}
							</td>
							<td class="num text-xs">{row.family_size}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#if quiet > 0}
			<button type="button" class="w-full border-t border-border py-2 text-xs font-medium text-muted-foreground hover:bg-surface-2 hover:text-foreground" aria-expanded={showAll} onclick={() => (showAll = !showAll)}>
				{showAll ? `Hide the ${quiet} comparisons with a 0-point or not-estimable difference` : `Show ${quiet} more comparisons (0-point or not-estimable difference)`}
			</button>
		{/if}
		{#if pooled && pooled.rows.length}
			<div class="border-t border-border px-4 py-2.5 text-xs">
				<p class="section-label">Pooled across targets · {pooled.method === 'mh_stratified' ? 'Mantel–Haenszel stratified' : 'per target only'}</p>
				<ul class="mt-1 space-y-0.5">
					{#each pooledShown as pr, i (i)}
						<li>
							<span class="font-mono">{pr.feature ?? ''}</span> · {labelOf(pr.a)} vs {labelOf(pr.b)} · {pr.n_targets ?? '—'} targets:
							{#if pr.pooled}
								{fmtDiff(pr.pooled.rd ?? null)} {pr.pooled.ci ? fmtDiffCI(pr.pooled.ci[0], pr.pooled.ci[1]) : ''}
							{:else}
								<span class="text-muted-foreground">not pooled (one target); see the per-target rows</span>
							{/if}
							{#if !interim && (pr.planned ? pr.permutation_p != null : pr.q != null)}
								{@const sig = pooledSignificance(pr)}
								· {pr.planned ? 'permutation ' : ''}{fmtSignificance(sig)}{#if fmtDecision(sig)} · {fmtDecision(sig)}{/if}
							{/if}
						</li>
					{/each}
				</ul>
				{#if pooledRows.length > pooledLead.length}
					<button type="button" class="mt-1 text-primary hover:underline" onclick={() => (showAllPooled = !showAllPooled)}>
						{showAllPooled ? 'Show fewer' : `Show ${pooledRows.length - pooledLead.length} more pooled rows (0-point or not pooled)`}
					</button>
				{/if}
				{#if pooled.notes.length}
					<p class="mt-1 text-muted-foreground">Notes: {pooled.notes.map((n) => (n === 'few_clusters' ? 'few clusters (< 20 targets: permutation inference, no cluster-robust SEs)' : n)).join(' · ')}</p>
				{/if}
			</div>
		{/if}
	{/if}
</Card.Root>
