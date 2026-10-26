<script lang="ts">
	/**
	 * Rankings leaderboard: measured columns (analysed trials, static issues,
	 * density, functional pass rate), the AI reviewer's consensus apart, then
	 * the metadata decision aid and public benchmarks. Desktop table (md+) and
	 * a data list below. Missing measurements render "—" with the reason.
	 */
	import { Checkbox } from '$lib/components/ui/checkbox';
	import {
		ScoreCell,
		SortableTh,
		modelNamePrefix,
		rowLink,
		shortModelName,
		tableFrame
	} from '$lib/components/table';
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

	const sort = (field: string) => onSort(field as RankingSortKey);

	/**
	 * Column leaders on this page; a "best" shared by more than two rows
	 * (ties at 100.0) is not a signal, so it is not marked.
	 */
	const colMax = $derived.by(() => {
		const out: Partial<Record<RankingSortKey, number | null>> = {};
		for (const col of DECISION_COLUMNS) {
			let max: number | null = null;
			for (const r of rows) {
				const v = decisionValue(r, col.key);
				if (v > 0 && (max == null || v > max)) max = v;
			}
			out[col.key] =
				max != null && rows.filter((r) => decisionValue(r, col.key) === max).length <= 2
					? max
					: null;
		}
		return out;
	});
</script>

{#snippet rankBadge(rank: number, r: RankingRow)}
	<span
		class="inline-flex h-6 w-8 shrink-0 items-center justify-center rounded-sm font-mono text-xs tabular-nums {rank <=
			3 && earnsMedal(r)
			? 'bg-primary/12 font-semibold text-primary'
			: 'text-muted-foreground'}">{rank}</span
	>
{/snippet}

{#snippet empirical(r: RankingRow)}
	{#if r.empirical_quality_score != null}
		<div class="font-mono font-semibold tabular-nums">
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
<div class="table-frame hidden md:block" {@attach tableFrame}>
	<table class="min-w-[72rem]">
		<thead>
			<tr class="[&>th]:pt-2 [&>th]:pb-1">
				<th class="col-sticky" rowspan="2">
					<div class="flex items-center gap-3">
						<span class="w-4"><span class="sr-only">Compare</span></span>
						<span class="w-8">Rank</span>
						<span>Model</span>
					</div>
				</th>
				<th colspan="5" class="border-l border-border text-center">Measured on this platform</th>
				<th class="border-l border-border text-center">AI review</th>
				<th
					colspan={DECISION_COLUMNS.length}
					class="border-l border-border text-center"
					title="Model Scoring System: an opinionated composite of metadata, not a measurement"
					>Decision aid (metadata)</th
				>
				<th colspan="3" class="border-l border-border text-center">Public benchmarks</th>
				<th class="border-l border-border" rowspan="2"
					><span class="block text-right" title="Generation jobs of this model in scope">Apps</span
					></th
				>
			</tr>
			<tr>
				<SortableTh
					label="Empirical"
					field="empirical"
					{sortBy}
					{sortDir}
					onSort={sort}
					align="right"
					help="Measured quality (0–100) from analysed trials"
					class="border-l border-border"
				/>
				<SortableTh
					label="Trials"
					field="n_trials"
					{sortBy}
					{sortDir}
					onSort={sort}
					align="right"
					help="Complete, eligible jobs measured under the reference plan / completed jobs"
				/>
				<th class="num" title="Counted static issues (deduplicated across tools)">Static issues</th>
				<th
					class="num"
					title="Severity-weighted static issues per KLOC of measured SLOC (mean ± sd across trials)"
					>Issues / KLOC</th
				>
				<th class="num" title="Functional (conformance) pass rate of the analysed trials"
					>Functional</th
				>
				<th
					class="num border-l border-border"
					title="AI consensus findings: opinions, never part of any score">Consensus</th
				>
				{#each DECISION_COLUMNS as col, i (col.key)}
					<SortableTh
						label={col.label}
						field={col.key}
						{sortBy}
						{sortDir}
						onSort={sort}
						align="right"
						help={col.title}
						class={i === 0 ? 'border-l border-border' : ''}
					/>
				{/each}
				<th class="num border-l border-border">HumanEval</th>
				<th class="num">MBPP</th>
				<th class="num">SWE-bench</th>
			</tr>
		</thead>
		<tbody>
			{#each rows as r, i (r.model_id)}
				{@const rank = startRank + i}
				{@const isSelected = selected.has(r.model_id)}
				{@const href = `/models/${encodeURIComponent(r.model_id)}`}
				<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (the name link is the keyboard path) -->
				<tr class="row-link" data-selected={isSelected} onclick={rowLink(href)}>
					<td class="col-sticky">
						<div class="flex items-center gap-3">
							<Checkbox
								checked={isSelected}
								disabled={!isSelected && selected.size >= maxSelected}
								onCheckedChange={() => onToggle(r.model_id)}
								aria-label="Select {r.model_name} for comparison"
							/>
							{@render rankBadge(rank, r)}
							<div class="max-w-[280px] min-w-0">
								<a {href} class="block truncate font-medium hover:text-primary" title={r.model_name}
									>{shortModelName(r.model_name)}</a
								>
								<div class="cell-sub flex items-center gap-1.5 text-[11px] text-muted-foreground">
									<span class="truncate">{modelNamePrefix(r.model_name) || r.provider}</span>
									{#if r.is_free}<span
											class="rounded-sm bg-success/12 px-1 font-medium text-success-fg">Free</span
										>{/if}
								</div>
							</div>
						</div>
					</td>
					<td class="num border-l border-border/60">{@render empirical(r)}</td>
					<td class="num">{@render trials(r)}</td>
					<td class="num">{@render staticIssues(r)}</td>
					<td class="num">{@render density(r)}</td>
					<td class="num">{@render functional(r)}</td>
					<td class="num border-l border-border/60 text-muted-foreground">{@render ai(r)}</td>
					{#each DECISION_COLUMNS as col, ci (col.key)}
						{@const v = decisionValue(r, col.key)}
						<ScoreCell
							value={v}
							strong={col.key === 'mss'}
							best={v === colMax[col.key]}
							class={ci === 0 ? 'border-l border-border/60' : ''}
						/>
					{/each}
					{#each ['humaneval', 'mbpp', 'swebench'] as key, bi (key)}
						{@const b = bench(r, key)}
						<td class="num text-xs {bi === 0 ? 'border-l border-border/60' : ''}">
							{#if b}{b}{:else}<NaValue reason="No published {key} score" />{/if}
						</td>
					{/each}
					<td class="num border-l border-border/60 text-xs {r.apps ? '' : 'text-muted-foreground'}"
						>{r.apps || '—'}</td
					>
				</tr>
			{/each}
		</tbody>
	</table>
</div>

<!-- Mobile list (below 768px) -->
<ul class="md:hidden">
	{#each rows as r, i (r.model_id)}
		{@const rank = startRank + i}
		{@const isSelected = selected.has(r.model_id)}
		<li class="data-list-row px-3" data-active={isSelected}>
			<div class="flex items-start gap-3">
				<Checkbox
					checked={isSelected}
					disabled={!isSelected && selected.size >= maxSelected}
					onCheckedChange={() => onToggle(r.model_id)}
					aria-label="Select {r.model_name} for comparison"
					class="mt-1.5"
				/>
				{@render rankBadge(rank, r)}
				<div class="min-w-0 flex-1">
					<div class="flex items-start justify-between gap-2">
						<div class="min-w-0">
							<a
								href="/models/{encodeURIComponent(r.model_id)}"
								class="block truncate text-sm font-medium hover:text-primary"
								title={r.model_name}>{shortModelName(r.model_name)}</a
							>
							<p class="truncate text-[11px] text-muted-foreground">
								{modelNamePrefix(r.model_name) || r.provider}
							</p>
						</div>
						<div class="shrink-0 text-right">
							<div class="text-[11px] text-muted-foreground">Empirical</div>
							<div class="text-lg">{@render empirical(r)}</div>
						</div>
					</div>
					<dl class="mt-2.5 grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
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
								{fmtScore(r.mss_score)} / {fmtScore(r.composite_score)}
							</dd>
						</div>
					</dl>
				</div>
			</div>
		</li>
	{/each}
</ul>
