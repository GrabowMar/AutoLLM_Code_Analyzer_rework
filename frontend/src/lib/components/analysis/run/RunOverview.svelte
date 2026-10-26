<script lang="ts">
	/**
	 * RunOverview — KPIs, severity (static counted vs AI consensus, kept
	 * apart), categories, metrics, research outcomes and the tool status
	 * table of one run. Every NA is "—" with its reason; counts from a run
	 * whose tools did not all succeed are marked as lower bounds.
	 *
	 * Props: `run`, `onretry(slug)` / `retrying` (tool retry, terminal runs
	 * only), `ontab(tab)` (jump to another tab).
	 */
	import type { AnalysisRunDetail, Severity } from '$lib/api/analysis';
	import { isMeasurement, isRunActive, isToolOk, SEVERITIES } from '$lib/api/analysis';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { severityColors } from '$lib/constants/colors';
	import { cn } from '$lib/utils';
	import Hint from '../Hint.svelte';
	import NaValue from '../NaValue.svelte';
	import ToolStatusTable from '../ToolStatusTable.svelte';
	import {
		categoryLabel,
		fmtInt,
		fmtNum,
		fmtPercent,
		fmtRate,
		fmtSeconds,
		fmtUsd,
		formatOutcome,
		OUTCOME_LABELS,
		outcomeNaReason,
		problemLabel,
		toolProblems,
		toolProgress,
		type OutcomeLabel
	} from '../run-utils';
	import type { RunTab } from './url';
	import ScanSearch from '@lucide/svelte/icons/scan-search';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import Hourglass from '@lucide/svelte/icons/hourglass';

	interface Props {
		run: AnalysisRunDetail;
		onretry?: (slug: string) => void;
		retrying?: string | null;
		ontab?: (tab: RunTab) => void;
	}

	let { run, onretry, retrying = null, ontab }: Props = $props();

	const m = $derived(isMeasurement(run.measurement) ? run.measurement : null);
	const active = $derived(isRunActive(run.status));
	const problems = $derived(toolProblems(run.results));
	const staticResults = $derived(run.results.filter((r) => r.tool_kind !== 'ai'));
	const staticProblems = $derived(problems.filter((p) => !p.isAi));
	const aiResult = $derived(run.results.find((r) => r.tool_kind === 'ai') ?? null);
	const hasAi = $derived(!!aiResult || !!run.plan?.reviewer);
	const staticNoData = $derived(
		staticResults.length > 0 && staticResults.every((r) => !isToolOk(r.status))
	);
	const lowerBoundNote = $derived(
		staticProblems.length > 0
			? `Lower bound: ${staticProblems.map(problemLabel).join(', ')} did not succeed`
			: ''
	);

	interface Kpi {
		label: string;
		value: string | null;
		na: string;
		sub: string;
		note?: string;
	}

	const kpis = $derived.by((): Kpi[] => {
		const progress = toolProgress(run);
		const ok = run.results.filter((r) => isToolOk(r.status)).length;
		const notApplicable = run.results.filter((r) => r.status === 'not_applicable').length;
		const failed = problems.length;
		const liveCounted = staticResults.reduce((sum, r) => sum + (r.findings?.counted ?? 0), 0);
		const totals = run.manifest?.totals ?? {};

		let staticValue: string | null = null;
		let staticNa = '';
		let staticSub = '';
		if (m) {
			if (staticNoData) staticNa = 'No static tool produced data';
			else staticValue = fmtInt(m.static.findings.total);
			staticSub = staticNoData ? '' : `${fmtInt(m.static.issues.total)} deduplicated issues`;
		} else if (active) {
			staticValue = fmtInt(liveCounted);
			staticSub = 'so far — the run is in progress';
		} else {
			staticNa = 'Nothing was measured';
		}

		let aiValue: string | null = null;
		let aiNa = '';
		let aiSub = '';
		if (!hasAi) aiNa = 'No AI reviewer in this plan';
		else if (m?.ai) {
			if (m.ai.available) {
				aiValue = fmtInt(m.ai.consensus.total);
				aiSub = `grounding ${fmtRate(m.ai.grounding_rate)} · ${m.ai.samples_ok}/${m.ai.samples} samples ok`;
			} else {
				aiNa = outcomeNaReason('ai_consensus_per_kloc', m);
				aiSub = `${m.ai.samples_ok}/${m.ai.samples} samples ok`;
			}
		} else if (active) aiNa = 'The AI review has not finished';
		else aiNa = 'The AI review did not run';

		const measured = m?.sloc.measured ?? totals.measured_sloc ?? null;
		const total = m?.sloc.total ?? totals.sloc ?? null;

		return [
			{
				label: 'Static findings',
				value: staticValue,
				na: staticNa,
				sub: staticSub,
				note: m && !staticNoData ? lowerBoundNote : ''
			},
			{
				label: 'Static issues / KLOC',
				value:
					m && run.outcomes?.static_issues_per_kloc != null
						? fmtNum(run.outcomes.static_issues_per_kloc, 1)
						: null,
				na: outcomeNaReason('static_issues_per_kloc', run.measurement),
				sub:
					m && run.outcomes?.static_findings_per_kloc != null
						? `${fmtNum(run.outcomes.static_findings_per_kloc, 1)} findings / KLOC`
						: ''
			},
			{
				label: 'AI consensus',
				value: aiValue,
				na: aiNa,
				sub: aiSub
			},
			{
				label: 'Measured SLOC',
				value: measured != null ? fmtInt(measured) : null,
				na: 'The sample has not been resolved yet',
				sub:
					total != null
						? `of ${fmtInt(total)} SLOC · ${m?.files.measured ?? totals.measured ?? '—'}/${m?.files.included ?? totals.included ?? '—'} files measured`
						: ''
			},
			{
				label: 'Tools OK',
				value: progress.total ? `${ok}/${progress.total}` : null,
				na: 'No tools in this plan',
				sub: active
					? `${progress.done} finished so far`
					: [
							failed ? `${failed} without data` : '',
							notApplicable ? `${notApplicable} not applicable` : ''
						]
							.filter(Boolean)
							.join(' · ') || 'all succeeded'
			},
			{
				label: 'Duration',
				value: run.duration_seconds != null ? fmtSeconds(run.duration_seconds) : null,
				na: active ? 'The run is still in progress' : 'The run did not start',
				sub: hasAi ? aiCostText() : 'no AI cost'
			}
		];
	});

	/** AI spend: unknown is "unknown" (never $0), a partial spend a lower bound. */
	function aiCostText(): string {
		const value = m?.ai ? m.ai.cost_usd : (run.summary?.cost_usd ?? null);
		if (value == null) return 'AI cost unknown';
		const complete = m?.ai ? m.ai.cost_complete !== false : run.summary?.cost_complete !== false;
		return `AI cost ${complete ? '' : '≥ '}${fmtUsd(value)}`;
	}

	/** Why the not-counted static findings are excluded (they overlap: "of which"). */
	const uncountedSentence = $derived.by(() => {
		if (!m) return '';
		const st = m.static;
		const parts = [
			st.out_of_scope_findings ? `${st.out_of_scope_findings} outside the measured scope` : '',
			st.scaffold_findings ? `${st.scaffold_findings} in scaffold code` : '',
			st.style_findings ? `${st.style_findings} style` : ''
		].filter(Boolean);
		return `${st.uncounted_findings} more finding${st.uncounted_findings === 1 ? ' is' : 's are'} not counted${
			parts.length
				? ` (including ${parts.join(', ')}; metric findings are never counted)`
				: ' (metric or uncounted categories)'
		}.`;
	});

	// ── Severity ───────────────────────────────────────────────────────
	function sevCount(counts: Partial<Record<Severity, number>> | undefined, s: Severity): number {
		return counts?.[s] ?? 0;
	}

	// ── Categories ─────────────────────────────────────────────────────
	const categoryRows = $derived.by(() => {
		if (!m) return [];
		const names = new Set<string>([
			...Object.keys(m.static.findings.by_category ?? {}),
			...Object.keys(m.static.issues.by_category ?? {}),
			...Object.keys(m.ai?.consensus.by_category ?? {})
		]);
		return [...names]
			.map((name) => ({
				name,
				findings: m.static.findings.by_category?.[name] ?? 0,
				issues: m.static.issues.by_category?.[name] ?? 0,
				ai: m.ai?.available ? (m.ai.consensus.by_category?.[name] ?? 0) : null,
				available: m.static.category_available?.[name] !== false
			}))
			.sort(
				(a, b) => b.issues - a.issues || b.findings - a.findings || a.name.localeCompare(b.name)
			);
	});

	const unmeasuredCategories = $derived(
		m
			? Object.entries(m.static.category_available ?? {})
					.filter(([name, ok]) => !ok && !categoryRows.some((r) => r.name === name))
					.map(([name]) => categoryLabel(name))
			: []
	);

	// ── Outcomes ───────────────────────────────────────────────────────
	const outcomeGroups: { key: OutcomeLabel['group']; label: string }[] = [
		{ key: 'static', label: 'Static analysis' },
		{ key: 'ai', label: 'AI review' },
		{ key: 'metrics', label: 'Code metrics' },
		{ key: 'sample', label: 'Sample' }
	];

	function outcomesOf(group: OutcomeLabel['group']): [string, OutcomeLabel][] {
		return Object.entries(OUTCOME_LABELS).filter(([, meta]) => meta.group === group);
	}

	const complexity = $derived(m?.metrics.complexity ?? null);
	const duplication = $derived(m?.metrics.duplication ?? null);
</script>

<div class="space-y-6">
	<!-- KPIs -->
	<div class="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
		{#each kpis as kpi (kpi.label)}
			<div class="kpi-card">
				<span class="kpi-label">{kpi.label}</span>
				<div class="flex items-baseline gap-1.5">
					<span class="kpi-value text-2xl sm:text-3xl">
						{#if kpi.value === null}
							<NaValue reason={kpi.na} />
						{:else}
							{kpi.value}
						{/if}
					</span>
					{#if kpi.note}
						<Hint text={kpi.note} underline={false}>
							<TriangleAlert class="size-4 text-warning-fg" aria-hidden="true" />
						</Hint>
					{/if}
				</div>
				{#if kpi.sub}
					<p class="text-[11px] text-muted-foreground sm:text-xs">{kpi.sub}</p>
				{/if}
			</div>
		{/each}
	</div>

	<!-- Severity: static and AI side by side, never merged -->
	<div class="grid gap-4 lg:grid-cols-2">
		<Card.Root>
			<Card.Header>
				<Card.Title class="flex items-center gap-2">
					<ScanSearch class="size-4 text-accent-2" aria-hidden="true" />Static analysis
				</Card.Title>
				<Card.Description>
					Counted findings and deduplicated issues by severity (measured scope only).
				</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-2">
				{#if !m}
					<EmptyState
						compact
						icon={Hourglass}
						title={active ? 'Counts appear when the run finishes' : 'Nothing was measured'}
						description={active
							? 'Findings stream in per tool on the Findings tab.'
							: run.error_message || undefined}
					/>
				{:else if staticNoData}
					<p class="text-sm text-muted-foreground">
						<NaValue reason="No static tool produced data" /> No static tool succeeded, so there are
						no static counts.
					</p>
				{:else}
					{#if lowerBoundNote}
						<p class="flex items-start gap-1.5 text-xs text-warning-fg">
							<TriangleAlert class="mt-px size-3.5 shrink-0" aria-hidden="true" />{staticProblems
								.map(problemLabel)
								.join(', ')} did not succeed, so these counts are a lower bound.
						</p>
					{/if}
					<table class="text-sm">
						<thead>
							<tr>
								<th>Severity</th>
								<th class="text-right">Findings</th>
								<th class="text-right">Issues</th>
							</tr>
						</thead>
						<tbody>
							{#each SEVERITIES as s (s)}
								<tr>
									<td><Badge class={severityColors[s]}>{s}</Badge></td>
									<td class="text-right tabular-nums"
										>{sevCount(m.static.findings.by_severity, s)}</td
									>
									<td class="text-right tabular-nums">{sevCount(m.static.issues.by_severity, s)}</td
									>
								</tr>
							{/each}
							<tr class="font-medium">
								<td>Total</td>
								<td class="text-right tabular-nums">{m.static.findings.total}</td>
								<td class="text-right tabular-nums">{m.static.issues.total}</td>
							</tr>
						</tbody>
					</table>
					{#if m.static.uncounted_findings > 0}
						<p class="text-[11px] text-muted-foreground">
							{uncountedSentence}
						</p>
					{/if}
				{/if}
			</Card.Content>
		</Card.Root>

		<Card.Root>
			<Card.Header>
				<Card.Title class="flex items-center gap-2">
					<Sparkles class="size-4 text-accent-3" aria-hidden="true" />AI review
				</Card.Title>
				<Card.Description>
					Consensus clusters: grounded findings reported by enough review samples. Opinions, not
					measurements.
				</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-2">
				{#if !hasAi}
					<EmptyState
						compact
						icon={Sparkles}
						title="No AI reviewer in this plan"
						description="The profile this run used has the AI reviewer turned off."
					/>
				{:else if !m}
					<EmptyState
						compact
						icon={Hourglass}
						title={active
							? 'Consensus appears when the review finishes'
							: 'The AI review did not run'}
					/>
				{:else if !m.ai || !m.ai.available}
					<p class="text-sm text-muted-foreground">
						<NaValue reason={outcomeNaReason('ai_consensus_per_kloc', m)} />
						{outcomeNaReason('ai_consensus_per_kloc', m)}: there is no AI consensus for this run.
					</p>
				{:else}
					{@const ai = m.ai}
					<table class="text-sm">
						<thead>
							<tr>
								<th>Severity</th>
								<th class="text-right">Consensus</th>
							</tr>
						</thead>
						<tbody>
							{#each SEVERITIES as s (s)}
								<tr>
									<td><Badge class={severityColors[s]}>{s}</Badge></td>
									<td class="text-right tabular-nums">{sevCount(ai.consensus.by_severity, s)}</td>
								</tr>
							{/each}
							<tr class="font-medium">
								<td>Total</td>
								<td class="text-right tabular-nums">{ai.consensus.total}</td>
							</tr>
						</tbody>
					</table>
					<p class="text-[11px] text-muted-foreground">
						{ai.samples_ok} of {ai.samples} samples succeeded · threshold {ai.threshold ?? '—'} · grounding
						{fmtRate(ai.grounding_rate)} · agreement (mean Jaccard)
						{fmtNum(ai.agreement.mean_pairwise_jaccard, 2)} ·
						<button
							type="button"
							class="cursor-pointer font-medium text-primary underline-offset-2 hover:underline"
							onclick={() => ontab?.('ai')}>Details</button
						>
					</p>
				{/if}
			</Card.Content>
		</Card.Root>
	</div>

	<!-- Categories + metrics -->
	<div class="grid gap-4 lg:grid-cols-2">
		<Card.Root>
			<Card.Header>
				<Card.Title>Categories</Card.Title>
				<Card.Description
					>Counted static findings and issues, and AI consensus, per category.</Card.Description
				>
			</Card.Header>
			<Card.Content class="space-y-2">
				{#if !m}
					<p class="text-sm text-muted-foreground">
						{active ? 'Available when the run finishes.' : 'Nothing was measured.'}
					</p>
				{:else if categoryRows.length === 0}
					<p class="text-sm text-muted-foreground">
						{#if staticNoData}
							No static tool succeeded, so there is nothing to break down by category.
						{:else if staticProblems.length > 0 || (hasAi && !m.ai?.available)}
							No counted findings from the tools that succeeded. Some tools did not succeed, so this
							is not a clean result.
						{:else}
							No counted findings in any category: every tool succeeded or was not applicable.
						{/if}
					</p>
				{:else}
					<div class="table-scroll-wrapper">
						<table class="text-sm">
							<thead>
								<tr>
									<th>Category</th>
									<th class="text-right">Findings</th>
									<th class="text-right">Issues</th>
									<th class="text-right">AI</th>
								</tr>
							</thead>
							<tbody>
								{#each categoryRows as row (row.name)}
									<tr>
										<td>{categoryLabel(row.name)}</td>
										<td class="text-right tabular-nums">
											{#if row.available}{row.findings}{:else}
												<Hint text="Lower bound: a tool that reports this category did not succeed">
													≥ {row.findings}
												</Hint>
											{/if}
										</td>
										<td class="text-right tabular-nums">
											{#if row.available}{row.issues}{:else}
												<Hint text="Lower bound: a tool that reports this category did not succeed">
													≥ {row.issues}
												</Hint>
											{/if}
										</td>
										<td class="text-right tabular-nums">
											{#if row.ai === null}
												<NaValue
													reason={hasAi
														? outcomeNaReason('ai_consensus_per_kloc', m)
														: 'No AI reviewer in this plan'}
												/>
											{:else}
												{row.ai}
											{/if}
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
				{#if unmeasuredCategories.length > 0}
					<p class="text-[11px] text-muted-foreground">
						Not measured (no successful tool reports them): {unmeasuredCategories.join(', ')}.
					</p>
				{/if}
			</Card.Content>
		</Card.Root>

		<Card.Root>
			<Card.Header>
				<Card.Title>Code metrics</Card.Title>
				<Card.Description>Complexity and duplication over the measured files.</Card.Description>
			</Card.Header>
			<Card.Content>
				{#if !m}
					<p class="text-sm text-muted-foreground">
						{active ? 'Available when the run finishes.' : 'Nothing was measured.'}
					</p>
				{:else}
					<dl class="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1.5 text-sm">
						<dt class="text-muted-foreground">
							Cyclomatic complexity (max / mean / p90)
							{#if complexity}<span class="text-[11px]"
									>· {complexity.source}, {complexity.functions ?? '—'} functions</span
								>{/if}
						</dt>
						<dd class="text-right tabular-nums">
							{#if complexity}
								{fmtNum(complexity.max, 0)} / {fmtNum(complexity.mean, 2)} / {fmtNum(
									complexity.p90,
									1
								)}
							{:else}
								<NaValue reason={outcomeNaReason('complexity_max', m)} />
							{/if}
						</dd>
						{#if complexity?.rank_distribution}
							<dt class="text-muted-foreground">Rank distribution (A best … F worst)</dt>
							<dd class="flex flex-wrap justify-end gap-1">
								{#each Object.entries(complexity.rank_distribution) as [rank, n] (rank)}
									<span
										class={cn(
											'rounded border px-1.5 font-mono text-[11px] tabular-nums',
											n > 0 && ['E', 'F'].includes(rank)
												? 'border-warning/40 text-warning-fg'
												: 'border-border'
										)}>{rank} {n}</span
									>
								{/each}
							</dd>
						{/if}
						<dt class="text-muted-foreground">
							Duplication
							{#if duplication}<span class="text-[11px]">· {duplication.source}</span>{/if}
						</dt>
						<dd class="text-right tabular-nums">
							{#if duplication && duplication.percentage != null}
								{fmtPercent(duplication.percentage)}
								<span class="text-[11px] text-muted-foreground">
									({duplication.duplicated_lines ?? '—'}/{duplication.total_lines ?? '—'} lines, {duplication.clones ??
										'—'} clones)
								</span>
							{:else}
								<NaValue reason={outcomeNaReason('duplication_pct', m)} />
							{/if}
						</dd>
						<dt class="text-muted-foreground">Suppression markers</dt>
						<dd class="text-right tabular-nums">
							{m.suppression_markers ?? '—'}
							<span class="text-[11px] text-muted-foreground">
								({m.suppression_markers_per_kloc != null
									? `${fmtNum(m.suppression_markers_per_kloc, 2)} / KLOC`
									: 'density n/a'})
							</span>
						</dd>
						<dt class="text-muted-foreground">Functional pass rate</dt>
						<dd class="text-right tabular-nums">
							{#if m.functional?.pass_rate != null}
								{fmtRate(m.functional.pass_rate)}
								<span class="text-[11px] text-muted-foreground">({m.functional.source ?? '—'})</span
								>
							{:else}
								<NaValue reason={outcomeNaReason('functional_pass_rate', m)} />
							{/if}
						</dd>
					</dl>
				{/if}
			</Card.Content>
		</Card.Root>
	</div>

	<!-- Research outcomes -->
	{#if m && run.outcomes}
		<Card.Root>
			<Card.Header>
				<Card.Title>Research outcomes</Card.Title>
				<Card.Description>
					The values a study reads from this run. Densities need at least
					{m.outcome_config?.min_sloc_for_density ?? '—'} measured SLOC.
				</Card.Description>
			</Card.Header>
			<Card.Content>
				<div class="grid gap-x-8 gap-y-4 sm:grid-cols-2 xl:grid-cols-4">
					{#each outcomeGroups as group (group.key)}
						<div>
							<p class="section-label mb-1.5">{group.label}</p>
							<dl class="space-y-1 text-sm">
								{#each outcomesOf(group.key) as [key, meta] (key)}
									{@const value = run.outcomes[key]}
									<div class="flex items-baseline justify-between gap-3">
										<dt class="text-muted-foreground">{meta.label}</dt>
										<dd class="shrink-0 tabular-nums">
											{#if value == null}
												<NaValue reason={outcomeNaReason(key, m)} />
											{:else}
												{formatOutcome(key, value)}
											{/if}
										</dd>
									</div>
								{/each}
							</dl>
						</div>
					{/each}
				</div>
			</Card.Content>
		</Card.Root>
	{/if}

	<!-- Tools -->
	<Card.Root id="run-tools">
		<Card.Header>
			<Card.Title>Tools</Card.Title>
			<Card.Description>
				Status, failure reason, exit code, duration, verified version and coverage per tool. Expand
				a row for diagnostics and raw outputs.
			</Card.Description>
		</Card.Header>
		<Card.Content class="p-0">
			<ToolStatusTable
				results={run.results}
				onretry={active ? undefined : onretry}
				{retrying}
				onai={hasAi ? () => ontab?.('ai') : undefined}
			/>
		</Card.Content>
	</Card.Root>
</div>
