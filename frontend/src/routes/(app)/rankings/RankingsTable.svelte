<script lang="ts">
	/**
	 * Rankings leaderboard: measured columns (analysed trials, static issues,
	 * density, functional pass rate), the AI reviewer's consensus apart, then
	 * the metadata decision aid and public benchmarks. Desktop table (md+) and
	 * cards below. Missing measurements render "—" with the reason.
	 */
	import { Badge } from '$lib/components/ui/badge';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import Medal from '@lucide/svelte/icons/medal';
	import ArrowUp from '@lucide/svelte/icons/arrow-up';
	import ArrowDown from '@lucide/svelte/icons/arrow-down';
	import ArrowUpDown from '@lucide/svelte/icons/arrow-up-down';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import NaValue from '$lib/components/analysis/NaValue.svelte';
	import { funnelGapCount, funnelGapLines } from '$lib/components/insights/coverage';
	import { fmtInt, fmtNum, fmtScore, fmtShare } from '$lib/components/insights/format';
	import { rollupTotal, type RankingRow, type RankingSortKey } from '$lib/api/rankings';

	interface Props {
		rows: RankingRow[];
		/** Rank of the first row (1-based). */
		startRank: number;
		sortBy: RankingSortKey;
		sortDir: 'asc' | 'desc';
		onSort: (key: RankingSortKey) => void;
		selected: Set<string>;
		maxSelected: number;
		onToggle: (modelId: string) => void;
	}

	let { rows, startRank, sortBy, sortDir, onSort, selected, maxSelected, onToggle }: Props =
		$props();

	function nStatic(r: RankingRow): number {
		return r.n_static ?? (r.empirical_quality_score != null ? r.n_trials : 0);
	}

	function noTrialsReason(r: RankingRow): string {
		if (!r.apps_completed) return 'No completed job of this model in scope';
		return `None of the ${r.apps_completed} completed job${r.apps_completed === 1 ? '' : 's'} has a complete analysis under the reference plan`;
	}

	function staticReason(r: RankingRow): string {
		if (!r.n_trials) return noTrialsReason(r);
		return `Static measurement unavailable in all ${r.n_trials} analysed trial${r.n_trials === 1 ? '' : 's'} (a required tool failed) — missing, not zero`;
	}

	function aiReason(r: RankingRow): string {
		if (!r.n_trials) return noTrialsReason(r);
		return 'No successful AI review in the analysed trials';
	}

	function densityReason(r: RankingRow): string {
		if (!nStatic(r)) return staticReason(r);
		return 'Density undefined: no analysed trial reached the plan’s minimum measured SLOC';
	}

	function functionalReason(r: RankingRow): string {
		if (!r.n_trials) return noTrialsReason(r);
		return 'No functional (conformance) result in the analysed trials';
	}

	function scoreClass(v: number): string {
		if (v >= 0.8) return 'text-success-fg';
		if (v >= 0.4) return 'text-warning-fg';
		return 'text-danger-fg';
	}

	function bench(r: RankingRow, key: string): string | null {
		const v = r[key];
		return typeof v === 'number' ? v.toFixed(1) : null;
	}

	function issuesOf(r: RankingRow) {
		return r.issues ?? r.findings;
	}

	function gapTitle(r: RankingRow): string {
		const lines = funnelGapLines(r.analysis ?? {});
		return lines.length
			? `Not in the numbers:\n${lines.join('\n')}`
			: 'Every completed job is measured under the reference plan';
	}

	interface Column {
		key: RankingSortKey;
		label: string;
		title: string;
	}

	const DECISION_COLUMNS: Column[] = [
		{
			key: 'mss',
			label: 'MSS',
			title: 'Model Scoring System: opinionated metadata composite (0–100)'
		},
		{
			key: 'composite',
			label: 'Composite',
			title: 'MSS 60% + empirical 40% when measured; MSS otherwise'
		},
		{ key: 'adoption', label: 'Adoption', title: 'OpenRouter rank and local usage' },
		{ key: 'benchmark', label: 'Bench', title: 'Public coding benchmarks' },
		{ key: 'cost_efficiency', label: 'Cost-eff.', title: 'Performance per dollar' },
		{ key: 'accessibility', label: 'Access.', title: 'License, API stability, documentation' }
	];

	function decisionValue(r: RankingRow, key: RankingSortKey): number {
		switch (key) {
			case 'mss':
				return r.mss_score;
			case 'composite':
				return r.composite_score;
			case 'adoption':
				return r.adoption_score;
			case 'benchmark':
				return r.benchmark_score;
			case 'cost_efficiency':
				return r.cost_efficiency_score;
			default:
				return r.accessibility_score;
		}
	}

	/**
	 * Medals only for a real lead: a descending sort on a key the row has a
	 * value for (an unmeasured model is not "#3 empirical").
	 */
	function earnsMedal(r: RankingRow): boolean {
		if (sortDir !== 'desc') return false;
		switch (sortBy) {
			case 'empirical':
				return r.empirical_quality_score != null;
			case 'n_trials':
				return r.n_trials > 0;
			case 'apps':
				return r.apps > 0;
			case 'name':
			case 'price':
			case 'context':
				return false;
			default:
				return true;
		}
	}

	function ariaSort(key: RankingSortKey): 'ascending' | 'descending' | 'none' {
		if (sortBy !== key) return 'none';
		return sortDir === 'asc' ? 'ascending' : 'descending';
	}
</script>

{#snippet sortHeader(key: RankingSortKey, label: string, title: string)}
	<button
		type="button"
		class="inline-flex cursor-pointer items-center gap-1 uppercase hover:text-foreground"
		onclick={() => onSort(key)}
		{title}
	>
		{label}
		{#if sortBy === key}
			{#if sortDir === 'asc'}<ArrowUp
					class="h-3 w-3 text-foreground"
					aria-hidden="true"
				/>{:else}<ArrowDown class="h-3 w-3 text-foreground" aria-hidden="true" />{/if}
		{:else}
			<ArrowUpDown class="h-3 w-3 opacity-50" aria-hidden="true" />
		{/if}
	</button>
{/snippet}

{#snippet rankBadge(rank: number, r: RankingRow)}
	<span class="inline-flex items-center gap-1">
		{#if rank <= 3 && earnsMedal(r)}
			<Medal
				class="h-4 w-4 {rank === 1
					? 'text-warning-fg'
					: rank === 2
						? 'text-muted-foreground'
						: 'text-warning-fg/60'}"
				aria-hidden="true"
			/>
		{/if}
		<span class="text-xs font-semibold tabular-nums">#{rank}</span>
	</span>
{/snippet}

{#snippet empirical(r: RankingRow)}
	{#if r.empirical_quality_score != null}
		<div class="font-mono font-bold tabular-nums {scoreClass(r.empirical_quality_score)}">
			{fmtScore(r.empirical_quality_score)}
		</div>
	{:else}
		<NaValue reason={nStatic(r) ? 'Not scored' : staticReason(r)} />
	{/if}
{/snippet}

{#snippet trials(r: RankingRow)}
	{@const gaps = funnelGapCount(r.analysis ?? {})}
	<span class="inline-flex items-center gap-1 font-mono tabular-nums" title={gapTitle(r)}>
		<span>{r.n_trials}<span class="text-muted-foreground">/{r.apps_completed}</span></span>
		{#if gaps > 0}
			<TriangleAlert class="h-3 w-3 text-warning-fg" aria-hidden="true" />
			<span class="sr-only">{gaps} completed jobs not in the numbers</span>
		{/if}
	</span>
{/snippet}

{#snippet staticIssues(r: RankingRow)}
	{#if nStatic(r) > 0}
		{@const issues = issuesOf(r)}
		<div class="font-mono tabular-nums">{fmtInt(rollupTotal(issues))}</div>
		<div class="text-[11px] whitespace-nowrap text-muted-foreground">
			{issues.critical + issues.high} crit+high · {fmtInt(r.measured_sloc ?? null) ?? '—'} SLOC
		</div>
	{:else}
		<NaValue reason={staticReason(r)} />
	{/if}
{/snippet}

{#snippet density(r: RankingRow)}
	{@const v = r.variance}
	{#if v && v.density_per_kloc_mean != null}
		<div class="font-mono tabular-nums">{fmtNum(v.density_per_kloc_mean, 1)}</div>
		<div class="text-[11px] whitespace-nowrap text-muted-foreground">
			{#if v.density_per_kloc_stdev != null}±{fmtNum(
					v.density_per_kloc_stdev,
					1
				)}{' · '}{/if}n={v.n_jobs}
		</div>
	{:else}
		<NaValue reason={densityReason(r)} />
	{/if}
{/snippet}

{#snippet functional(r: RankingRow)}
	{#if r.functional_pass_rate != null}
		<div class="font-mono tabular-nums">{fmtShare(r.functional_pass_rate, 0)}</div>
		<div class="text-[11px] text-muted-foreground">n={r.functional_n ?? '?'}</div>
	{:else}
		<NaValue reason={functionalReason(r)} />
	{/if}
{/snippet}

{#snippet ai(r: RankingRow)}
	{#if (r.n_ai ?? 0) > 0 && r.ai_findings}
		<div class="font-mono tabular-nums">{rollupTotal(r.ai_findings)}</div>
		<div class="text-[11px] whitespace-nowrap text-muted-foreground">{r.n_ai} reviewed</div>
	{:else}
		<NaValue reason={aiReason(r)} />
	{/if}
{/snippet}

<!-- Desktop table (768px+) -->
<div class="table-scroll-wrapper hidden md:block">
	<table class="min-w-[72rem]">
		<thead>
			<tr>
				<th colspan="3"><span class="sr-only">Model</span></th>
				<th colspan="5" class="border-l text-center">Measured on this platform</th>
				<th class="border-l text-center">AI review</th>
				<th colspan="6" class="border-l text-center">Decision aid (metadata)</th>
				<th colspan="3" class="border-l text-center">Public benchmarks</th>
				<th class="border-l"></th>
			</tr>
			<tr>
				<th class="w-10"><span class="sr-only">Compare</span></th>
				<th class="w-14">Rank</th>
				<th>Model</th>
				<th class="border-l text-right" aria-sort={ariaSort('empirical')}>
					{@render sortHeader(
						'empirical',
						'Empirical',
						'Measured quality (0–100) from analysed trials'
					)}
				</th>
				<th class="text-right" aria-sort={ariaSort('n_trials')}>
					{@render sortHeader(
						'n_trials',
						'Analysed trials',
						'Complete, eligible jobs measured under the reference plan / completed jobs'
					)}
				</th>
				<th
					class="whitespace-nowrap text-right"
					title="Counted static issues (deduplicated across tools)">Static issues</th
				>
				<th
					class="whitespace-nowrap text-right"
					title="Severity-weighted static issues per KLOC of measured SLOC (mean ± sd across trials)"
					>Issues / KLOC</th
				>
				<th class="text-right" title="Functional (conformance) pass rate of the analysed trials"
					>Functional</th
				>
				<th
					class="border-l text-right"
					title="AI consensus findings: opinions, never part of any score">Consensus</th
				>
				{#each DECISION_COLUMNS as col, i (col.key)}
					<th class="text-right {i === 0 ? 'border-l' : ''}" aria-sort={ariaSort(col.key)}>
						{@render sortHeader(col.key, col.label, col.title)}
					</th>
				{/each}
				<th class="border-l text-right">HumanEval</th>
				<th class="text-right">MBPP</th>
				<th class="text-right">SWE-bench</th>
				<th class="border-l text-right" aria-sort={ariaSort('apps')}>
					{@render sortHeader('apps', 'Apps', 'Generation jobs of this model in scope')}
				</th>
			</tr>
		</thead>
		<tbody>
			{#each rows as r, i (r.model_id)}
				{@const rank = startRank + i}
				{@const isSelected = selected.has(r.model_id)}
				<tr class={isSelected ? 'bg-primary/5' : ''}>
					<td>
						<Checkbox
							checked={isSelected}
							disabled={!isSelected && selected.size >= maxSelected}
							onCheckedChange={() => onToggle(r.model_id)}
							aria-label="Select {r.model_name} for comparison"
						/>
					</td>
					<td>{@render rankBadge(rank, r)}</td>
					<td class="min-w-48">
						<a href="/models/{encodeURIComponent(r.model_id)}" class="font-medium hover:underline"
							>{r.model_name}</a
						>
						<div class="text-[11px] text-muted-foreground">
							{r.provider}
							{#if r.is_free}<Badge variant="outline" class="ml-1 px-1 py-0 text-[11px]">Free</Badge
								>{/if}
						</div>
					</td>
					<td class="border-l text-right">{@render empirical(r)}</td>
					<td class="text-right">{@render trials(r)}</td>
					<td class="text-right">{@render staticIssues(r)}</td>
					<td class="text-right">{@render density(r)}</td>
					<td class="text-right">{@render functional(r)}</td>
					<td class="border-l text-right text-muted-foreground">{@render ai(r)}</td>
					{#each DECISION_COLUMNS as col, ci (col.key)}
						{@const v = decisionValue(r, col.key)}
						<td
							class="text-right font-mono text-xs tabular-nums {ci === 0
								? 'border-l'
								: ''} {scoreClass(v)}"
						>
							{fmtScore(v)}
						</td>
					{/each}
					{#each ['humaneval', 'mbpp', 'swebench'] as key, bi (key)}
						{@const b = bench(r, key)}
						<td class="text-right font-mono text-xs tabular-nums {bi === 0 ? 'border-l' : ''}">
							{#if b}{b}{:else}<NaValue reason="No published {key} score" />{/if}
						</td>
					{/each}
					<td class="border-l text-right font-mono text-xs tabular-nums text-muted-foreground"
						>{r.apps}</td
					>
				</tr>
			{/each}
		</tbody>
	</table>
</div>

<!-- Cards (below 768px) -->
<ul class="space-y-3 md:hidden">
	{#each rows as r, i (r.model_id)}
		{@const rank = startRank + i}
		{@const isSelected = selected.has(r.model_id)}
		<li
			class="rounded-md border bg-card p-4 {isSelected ? 'ring-1 ring-inset ring-primary/40' : ''}"
		>
			<div class="flex items-start gap-3">
				<Checkbox
					checked={isSelected}
					disabled={!isSelected && selected.size >= maxSelected}
					onCheckedChange={() => onToggle(r.model_id)}
					aria-label="Select {r.model_name} for comparison"
					class="mt-0.5"
				/>
				<div class="min-w-0 flex-1">
					<div class="flex items-center gap-2">
						{@render rankBadge(rank, r)}
						<a
							href="/models/{encodeURIComponent(r.model_id)}"
							class="truncate text-sm font-medium hover:underline">{r.model_name}</a
						>
					</div>
					<div class="text-[11px] text-muted-foreground">{r.provider}</div>
				</div>
				<div class="shrink-0 text-right">
					<div class="text-[11px] text-muted-foreground">Empirical</div>
					<div class="text-lg">{@render empirical(r)}</div>
				</div>
			</div>
			<dl class="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
				<div>
					<dt class="text-[11px] text-muted-foreground">Analysed trials</dt>
					<dd>{@render trials(r)}</dd>
				</div>
				<div>
					<dt class="text-[11px] text-muted-foreground">Static issues</dt>
					<dd>{@render staticIssues(r)}</dd>
				</div>
				<div>
					<dt class="text-[11px] text-muted-foreground">Issues / KLOC</dt>
					<dd>{@render density(r)}</dd>
				</div>
				<div>
					<dt class="text-[11px] text-muted-foreground">Functional</dt>
					<dd>{@render functional(r)}</dd>
				</div>
				<div>
					<dt class="text-[11px] text-muted-foreground">AI consensus (opinion)</dt>
					<dd>{@render ai(r)}</dd>
				</div>
				<div>
					<dt class="text-[11px] text-muted-foreground">MSS / Composite</dt>
					<dd class="font-mono tabular-nums">
						<span class={scoreClass(r.mss_score)}>{fmtScore(r.mss_score)}</span>
						/
						<span class={scoreClass(r.composite_score)}>{fmtScore(r.composite_score)}</span>
					</dd>
				</div>
			</dl>
		</li>
	{/each}
</ul>
