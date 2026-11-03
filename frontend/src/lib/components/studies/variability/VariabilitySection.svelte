<script lang="ts">
	/**
	 * VariabilitySection — Results "Run-to-run variation": how much repeats of
	 * one condition on one app differ (`GET /variability/`, the same kept cells
	 * and query options as `/results/`). Descriptive only: no p-values.
	 *
	 * Self-fetching. Contents, numbered after the Results tab's own figures:
	 * KPI tiles (cells with repeats, pass agreement, repeat share of variance,
	 * code similarity), Table 3 the within-cell spread of the selected metric
	 * per condition (pooled SD, median CV, ICC with a cluster-bootstrap CI,
	 * design effect), Figure 4 every cell's repeats (strip plot), Figure 5 the
	 * variance decomposition of every metric, Table 4 pass@k / pass^k and
	 * check agreement, Table 5 code similarity within cells against the
	 * between-condition reference and finding overlap, Table 6 bootstrap rank
	 * intervals, Table 7 the precision planner (finished studies only),
	 * then the server's caveats. A CSV of per-cell rows downloads from the
	 * heading.
	 *
	 * Props:
	 * - `studyId`, `status` (the study's; refetches when it changes).
	 * - `query`: the Results tab's analysis options.
	 * - `number`: the section number ("3.4").
	 * - `labelOf(conditionId)`: condition display label (else the server's).
	 * - `refreshSignal`: bump to refetch.
	 */
	import { untrack } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import * as Select from '$lib/components/ui/select';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { tableFrame } from '$lib/components/table';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import {
		downloadStudyVariabilityCsv,
		getStudyVariability,
		type StudyVariability,
		type VariabilityMetric,
		type VariabilityQuery,
	} from '$lib/api/variability';
	import Download from '@lucide/svelte/icons/download';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Repeat from '@lucide/svelte/icons/repeat';
	import StudyFigure from '../study/StudyFigure.svelte';
	import StudySection from '../study/StudySection.svelte';
	import VarianceComponentsChart from './VarianceComponentsChart.svelte';
	import VariabilityStripPlot from './VariabilityStripPlot.svelte';
	import { downloadBlob } from '../format';
	import {
		DASH,
		formatAgreement,
		formatMetricValue,
		formatRatio,
		formatShare,
		plannerGrid,
		repeatsLabel,
	} from './variability';

	interface Props {
		studyId: string;
		status?: string;
		query?: VariabilityQuery;
		number?: string;
		labelOf?: (conditionId: number) => string;
		refreshSignal?: number;
	}

	let { studyId, status = '', query = {}, number = '3.4', labelOf, refreshSignal = 0 }: Props = $props();

	let data = $state<StudyVariability | null>(null);
	let loading = $state(false);
	let error = $state('');
	let csvBusy = $state(false);
	let metricKey = $state('');
	let controller: AbortController | null = null;

	async function load() {
		controller?.abort();
		const ctrl = new AbortController();
		controller = ctrl;
		loading = true;
		error = '';
		try {
			const next = await getStudyVariability(studyId, $state.snapshot(query) as VariabilityQuery, { signal: ctrl.signal });
			if (controller === ctrl) data = next;
		} catch (e) {
			if (!isAbortError(e) && controller === ctrl) error = formatApiError(e, 'Could not load run-to-run variation');
		} finally {
			if (controller === ctrl) loading = false;
		}
	}

	const queryKey = $derived(JSON.stringify(query));
	$effect(() => {
		void queryKey;
		void refreshSignal;
		void status;
		void studyId;
		untrack(() => void load());
		return () => controller?.abort();
	});

	async function downloadCsv() {
		csvBusy = true;
		try {
			const blob = await downloadStudyVariabilityCsv(studyId, $state.snapshot(query) as VariabilityQuery);
			downloadBlob(blob, `study-${studyId.slice(0, 8)}-variability.csv`);
		} catch (e) {
			error = formatApiError(e, 'Could not download the CSV');
		} finally {
			csvBusy = false;
		}
	}

	const serverLabel = $derived(new Map((data?.conditions ?? []).map((c) => [c.condition_id, c.label])));
	const nameOf = (id: number) => labelOf?.(id) ?? serverLabel.get(id) ?? `Condition #${id}`;

	const measured = $derived((data?.metrics ?? []).filter((m) => m.n_runs > 0));
	const metric = $derived<VariabilityMetric | undefined>(
		measured.find((m) => m.key === metricKey) ?? measured.find((m) => m.key === 'cost') ?? measured[0]
	);
	const metricItems = $derived(
		measured.map((m) => ({ value: m.key, label: m.label, description: `${m.source} · n ${m.n_runs}` }))
	);
	const interimTag = $derived(data?.interim ? ' Interim: the study is still running.' : '');

	/** The server's `billing` marker (cost only): every run was subscription-billed, so the billed $0 is no ceiling. */
	function billingOf(row: unknown): string[] | null {
		const sources = (row as { billing?: string[] | null } | null | undefined)?.billing;
		return Array.isArray(sources) && sources.length ? sources : null;
	}
	const passAgree = $derived.by(() => {
		let agree = 0;
		let cells = 0;
		for (const c of data?.conditions ?? []) {
			agree += c.pass_agreement.agree;
			cells += c.pass_agreement.cells;
		}
		return { agree, cells };
	});
	const repeatShare = $derived.by(() => {
		if (!data || !metric) return null;
		const comp = data.components[metric.key];
		if (!comp?.estimable || comp.ceiling || billingOf(comp)) return null;
		return comp.components.find((c) => c.source === 'repeat')?.share ?? null;
	});
	const iccByCondition = $derived(new Map((data && metric ? (data.icc[metric.key] ?? []) : []).map((r) => [r.condition_id, r])));
	const passK = $derived(new Map((data?.pass_at_k.conditions ?? []).map((r) => [r.condition_id, r])));
	const checksBy = $derived(new Map((data?.checks.conditions ?? []).map((r) => [r.condition_id, r])));
	const simBy = $derived(new Map((data?.similarity.conditions ?? []).map((r) => [r.condition_id, r])));
	const overlapBy = $derived(new Map((data?.finding_overlap.conditions ?? []).map((r) => [r.condition_id, r])));
	const kMax = $derived(data?.pass_at_k.k_max ?? 0);
	const rankBlocks = $derived(Object.values(data?.rank_stability ?? {}));
	const metricLabel = (key: string) => data?.metrics.find((m) => m.key === key)?.label ?? key;
	const metricUnit = (key: string) => data?.metrics.find((m) => m.key === key)?.unit ?? '';

	const plannerMetric = $derived.by(() => {
		const metrics = data?.planner?.metrics ?? {};
		if (metric && metrics[metric.key]) return metric.key;
		return Object.keys(metrics).find((k) => metrics[k].available) ?? Object.keys(metrics)[0] ?? '';
	});
	const plan = $derived(data?.planner?.metrics[plannerMetric] ?? null);
	const exactTestApps = $derived(data?.planner?.min_apps_exact_test ?? null);
	const grid = $derived(plan ? plannerGrid(plan.rows) : null);
</script>

<StudySection
	id="results-variation"
	{number}
	title="Run-to-run variation"
	lead="How much repeats of one condition on one app differ, and how much of the spread is the condition, the app or chance. Descriptive: no p-values."
>
	{#snippet aside()}
		{#if data && data.design.n_runs > 0}
			<Button variant="outline" size="sm" onclick={downloadCsv} disabled={csvBusy}>
				{#if csvBusy}<LoaderCircle class="animate-spin" />{:else}<Download />{/if}
				Per-cell CSV
			</Button>
		{/if}
	{/snippet}

	{#if error}
		<ErrorAlert message={error} onretry={load} />
	{/if}

	{#if loading && !data}
		<LoadingState label="computing run-to-run variation" />
	{:else if data}
		{#if data.design.n_runs === 0}
			<EmptyState compact icon={Repeat} title="No kept runs yet" description="Run-to-run variation appears once graded runs count in the analysis." />
		{:else if data.design.cells_with_repeats === 0}
			<EmptyState
				compact
				icon={Repeat}
				title="One repeat per cell"
				description="Run-to-run variation needs 2 or more repeats of a condition on an app. Add replicates (Operations) to estimate it."
			/>
		{:else}
			<div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
				<div class="kpi-card">
					<span class="kpi-label">Cells with repeats</span>
					<span class="font-mono text-xl font-medium tabular-nums">{data.design.cells_with_repeats}/{data.design.n_cells}</span>
					<span class="text-[11px] text-muted-foreground">{repeatsLabel(data.design.repeats_min, data.design.repeats_max)} · {data.design.n_runs} runs</span>
				</div>
				<div class="kpi-card">
					<span class="kpi-label">Pass agreement</span>
					<span class="font-mono text-xl font-medium tabular-nums">{passAgree.cells ? `${passAgree.agree}/${passAgree.cells}` : DASH}</span>
					<span class="text-[11px] text-muted-foreground">cells whose repeats all pass or all fail</span>
				</div>
				<div class="kpi-card">
					<span class="kpi-label">Repeat share of variance</span>
					<span class="font-mono text-xl font-medium tabular-nums">{formatShare(repeatShare)}</span>
					<span class="truncate text-[11px] text-muted-foreground" title={metric?.source}>{metric?.label ?? DASH} · run-to-run part of the total</span>
				</div>
				<div class="kpi-card">
					<span class="kpi-label">Code similarity</span>
					<span class="font-mono text-xl font-medium tabular-nums">
						{formatRatio(data.similarity.within.median_shingle_jaccard)}
						<span class="text-sm text-muted-foreground">vs {formatRatio(data.similarity.reference.median_shingle_jaccard)}</span>
					</span>
					<span class="text-[11px] text-muted-foreground">repeats vs other conditions (shingle Jaccard)</span>
				</div>
			</div>

			<StudyFigure id="table-3" kind="table" number={3} title="Within-cell spread by condition" flush>
				{#snippet controls()}
					<div class="flex items-center gap-2">
						<Label class="text-xs" for="variation-metric">Metric</Label>
						<Select.Simple
							id="variation-metric"
							size="sm"
							class="w-48"
							value={metric?.key ?? ''}
							items={metricItems}
							onValueChange={(v) => (metricKey = v)}
						/>
					</div>
				{/snippet}
				{#if metric}
					<div class="table-frame" {@attach tableFrame}>
						<table class="min-w-[44rem]">
							<thead>
								<tr>
									<th scope="col" class="col-sticky">Condition</th>
									<th scope="col" class="num" title="Cells with 2 or more measured repeats / runs measured">Cells · runs</th>
									<th scope="col" class="num">Mean</th>
									<th scope="col" class="num" title="sqrt(Σ (r−1) s² / Σ (r−1)) over the condition's cells; df = Σ (r−1)">Pooled SD (df)</th>
									<th scope="col" class="num" title="Median over cells of SD / mean (ratio-scale metrics only)">Median CV</th>
									<th scope="col" class="num" title="One-way ICC(1) over apps, two-stage cluster-bootstrap 95% CI (from 3 apps)">ICC [95% CI]</th>
									<th scope="col" class="num" title="Design effect 1 + (r0 − 1) ICC: how much repeats inflate the variance of a mean">DE</th>
								</tr>
							</thead>
							<tbody>
								{#each data.conditions as c (c.condition_id)}
									{@const d = c.dispersion[metric.key]}
									{@const icc = iccByCondition.get(c.condition_id)}
									{@const billing = billingOf(d)}
									<tr>
										<th scope="row" class="col-sticky bg-card text-left font-sans text-sm font-normal normal-case tracking-normal text-foreground">
											<span class="line-clamp-2 break-words font-medium" title={nameOf(c.condition_id)}>{nameOf(c.condition_id)}</span>
										</th>
										<td class="num text-xs">{d ? `${d.n_cells} · ${d.n_runs}` : DASH}</td>
										<td class="num text-xs">{formatMetricValue(d?.mean, metric.unit)}</td>
										<td class="num text-xs">
											{#if billing}
												<span class="text-muted-foreground" title={`Subscription-billed (${billing.join(', ')}): every run's billed cost is $0, so it describes billing, not usage (see List-price cost)`}>{d && d.df > 0 ? '0' : DASH} (billing)</span>
											{:else if d?.ceiling && d.df > 0}
												<span class="text-muted-foreground" title="Every measured run scored the same">0 (ceiling)</span>
											{:else if d?.pooled_sd != null}
												{formatMetricValue(d.pooled_sd, metric.unit)} <span class="text-muted-foreground">({d.df})</span>
											{:else}
												<span class="text-muted-foreground">{DASH}</span>
											{/if}
										</td>
										<td class="num text-xs">{metric.ratio_scale ? formatRatio(d?.median_cv) : DASH}</td>
										<td class="num text-xs">
											{#if icc?.icc != null}
												{formatRatio(icc.icc)}{icc.truncated ? '*' : ''}
												{#if icc.ci95}<span class="block text-muted-foreground">[{formatRatio(icc.ci95[0])}, {formatRatio(icc.ci95[1])}]</span>{/if}
											{:else}
												<span class="text-muted-foreground" title={icc?.reason ?? undefined}>{DASH}</span>
											{/if}
										</td>
										<td class="num text-xs">{formatRatio(icc?.design_effect)}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
				{#snippet caption()}
					<strong class="font-medium text-foreground">Table 3.</strong>
					{metric?.label ?? 'Metric'} ({metric?.source ?? DASH}) per condition over the kept cells: mean of runs; pooled within-cell SD with its
					degrees of freedom; median cell CV (ratio-scale metrics only, never a pass rate); ICC(1) of repeats over apps with a two-stage
					cluster-bootstrap 95% interval (from 3 apps; * a negative estimate shown as 0); design effect. With r = 2 each cell's SD has 1 degree
					of freedom.{interimTag}
				{/snippet}
			</StudyFigure>

			{#if metric}
				<StudyFigure id="figure-4" number={4} title="{metric.label}: every repeat of every cell" flush>
					<VariabilityStripPlot cells={data.cells} {metric} labelOf={nameOf} />
					{#snippet caption()}
						<strong class="font-medium text-foreground">Figure 4.</strong>
						One row per condition × app; dots: repeats, bar: their range, tick: the cell mean; right: mean ± SD (n repeats measured). Unmeasured
						repeats are not drawn.
					{/snippet}
				</StudyFigure>
			{/if}

			<StudyFigure id="figure-5" number={5} title="Where the variance comes from" flush>
				<VarianceComponentsChart components={data.components} metrics={data.metrics} selected={metric?.key} />
				{#snippet caption()}
					<strong class="font-medium text-foreground">Figure 5.</strong>
					Share of each metric's variance attributed to the condition, the app, their interaction and the repeat (run-to-run noise), by method of
					moments on cell means (harmonic r) over apps every condition has; * a negative estimate shown as 0. Condition is a designed factor: its
					share is descriptive. ICC: correlation of two repeats of one condition on one app. n: runs and apps used.{interimTag}
				{/snippet}
			</StudyFigure>

			<StudyFigure id="table-4" kind="table" number={4} title="Solving it at least once vs every time" flush>
				<div class="table-frame" {@attach tableFrame}>
					<table class="min-w-[44rem]">
						<thead>
							<tr>
								<th scope="col" class="col-sticky">Condition</th>
								<th scope="col" class="num" title="Cells with 2 or more repeats whose repeats all pass or all fail">Pass agreement</th>
								<th scope="col" class="num" title="Probability that one repeat passes (mean over apps)">pass@1</th>
								{#if kMax >= 2}
									<th scope="col" class="num" title="Probability that at least one of {kMax} repeats passes: 1 − C(n−c,k)/C(n,k)">pass@{kMax}</th>
									<th scope="col" class="num" title="Probability that all {kMax} repeats pass: C(c,k)/C(n,k)">pass^{kMax}</th>
								{/if}
								<th scope="col" class="num" title="Cells whose repeats all deploy or none does">Deploy agreement</th>
								<th scope="col" class="num" title="Cells whose repeats agree on every conformance check (one tester version)">Checks agree</th>
							</tr>
						</thead>
						<tbody>
							{#each data.conditions as c (c.condition_id)}
								{@const pk = passK.get(c.condition_id)}
								{@const k1 = pk?.by_k.find((k) => k.k === 1)}
								{@const kk = pk?.by_k.find((k) => k.k === kMax)}
								{@const ch = checksBy.get(c.condition_id)}
								<tr>
									<th scope="row" class="col-sticky bg-card text-left font-sans text-sm font-normal normal-case tracking-normal text-foreground">
										<span class="line-clamp-2 break-words font-medium">{nameOf(c.condition_id)}</span>
									</th>
									<td class="num text-xs">{formatAgreement(c.pass_agreement.agree, c.pass_agreement.cells)}</td>
									<td class="num text-xs">
										{formatShare(k1?.pass_at_k)}
										{#if k1?.pass_at_k_ci95}<span class="block text-muted-foreground">[{formatShare(k1.pass_at_k_ci95[0])}, {formatShare(k1.pass_at_k_ci95[1])}]</span>{/if}
									</td>
									{#if kMax >= 2}
										<td class="num text-xs">
											{formatShare(kk?.pass_at_k)}{#if kk && kk.n_cells < c.n_cells}<span class="block text-muted-foreground">{kk.n_cells} cells</span>{/if}
										</td>
										<td class="num text-xs">{formatShare(kk?.pass_hat_k)}</td>
									{/if}
									<td class="num text-xs">{formatAgreement(c.deploy_agreement.agree, c.deploy_agreement.cells)}</td>
									<td class="num text-xs">{ch ? formatAgreement(ch.cells_all_agree, ch.cells) : DASH}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				{#if data.checks.flaky.length > 0}
					<div class="border-t border-border/60 px-4 py-2.5">
						<p class="section-label">Checks that differ between repeats</p>
						<ul class="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs">
							{#each data.checks.flaky.slice(0, 12) as f (f.name)}
								<li><span class="font-medium">{f.name}</span> <span class="text-muted-foreground">{f.category ? `${f.category} · ` : ''}{f.cells} cell{f.cells === 1 ? '' : 's'}</span></li>
							{/each}
						</ul>
					</div>
				{/if}
				{#snippet caption()}
					<strong class="font-medium text-foreground">Table 4.</strong>
					Unbiased pass@k = 1 − C(n−c, k)/C(n, k) (at least one of k repeats passes) and pass^k = C(c, k)/C(n, k) (all k pass), per cell with n
					repeats of which c passed, averaged over apps (95% bootstrap interval over apps from 3 apps). Agreement counts cells with 2 or more
					repeats; checks are compared only within one tester version.{interimTag}
				{/snippet}
			</StudyFigure>

			<StudyFigure id="table-5" kind="table" number={5} title="How alike are the repeats' code and findings" flush>
				<div class="table-frame" {@attach tableFrame}>
					<table class="min-w-[48rem]">
						<thead>
							<tr>
								<th scope="col" class="col-sticky">Condition</th>
								<th scope="col" class="num" title="Median 5-token shingle Jaccard of repeats of one cell">Shingles: repeats</th>
								<th scope="col" class="num" title="Median shingle Jaccard against another condition on the same app and repeat index (boilerplate floor)">vs other condition</th>
								<th scope="col" class="num" title="Normalized-line Jaccard of repeats">Lines</th>
								<th scope="col" class="num" title="File-path Jaccard of the agent-written source files">Paths</th>
								<th scope="col" class="num" title="Files identical between repeats (median)">Identical files</th>
								<th scope="col" class="num" title="Jaccard of (tool, rule, file) code-issue signatures over tools that measured both repeats">Code issues</th>
								<th scope="col" class="num" title="Jaccard of dependency-advisory signatures (pip-audit, npm-audit, osv-scanner, trivy, retire)">Advisories</th>
							</tr>
						</thead>
						<tbody>
							{#each data.conditions as c (c.condition_id)}
								{@const s = simBy.get(c.condition_id)}
								{@const o = overlapBy.get(c.condition_id)}
								<tr>
									<th scope="row" class="col-sticky bg-card text-left font-sans text-sm font-normal normal-case tracking-normal text-foreground">
										<span class="line-clamp-2 break-words font-medium">{nameOf(c.condition_id)}</span>
									</th>
									<td class="num text-xs">
										{formatRatio(s?.within.median_shingle_jaccard)}
										{#if s}<span class="block text-muted-foreground">{s.within.n_pairs} pairs</span>{/if}
									</td>
									<td class="num text-xs">
										{formatRatio(s?.reference.median_shingle_jaccard)}
										{#if s && s.reference.n_pairs}<span class="block text-muted-foreground">{s.reference.n_pairs} pairs</span>{/if}
									</td>
									<td class="num text-xs">{formatRatio(s?.within.median_line_jaccard)}</td>
									<td class="num text-xs">{formatRatio(s?.within.median_path_jaccard)}</td>
									<td class="num text-xs">{s?.within.median_identical_files != null ? formatMetricValue(s.within.median_identical_files, '') : DASH}</td>
									<td class="num text-xs">
										{formatRatio(o?.code.median_jaccard)}
										{#if o}<span class="block text-muted-foreground">{o.code.n}/{o.n_pairs} pairs</span>{/if}
									</td>
									<td class="num text-xs">{formatRatio(o?.dependency.median_jaccard)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				{#if data.finding_overlap.by_tool.length > 0}
					<details class="border-t border-border/60 px-4 py-2.5 text-xs">
						<summary class="cursor-pointer text-muted-foreground">Finding overlap by tool ({data.finding_overlap.by_tool.length})</summary>
						<ul class="mt-2 grid gap-x-6 gap-y-1 sm:grid-cols-2 lg:grid-cols-3">
							{#each data.finding_overlap.by_tool as t (`${t.tool}:${t.kind}`)}
								<li class="flex justify-between gap-3 font-mono">
									<span class="truncate">{t.tool} <span class="text-muted-foreground">{t.kind === 'dependency' ? 'advisories' : 'code'}</span></span>
									<span>{formatRatio(t.median_jaccard)} <span class="text-muted-foreground">({t.n_pairs})</span></span>
								</li>
							{/each}
						</ul>
					</details>
				{/if}
				{#snippet caption()}
					<strong class="font-medium text-foreground">Table 5.</strong>
					Medians over pairs of repeats of one cell. Code: the agent-written, non-test source files (scaffold files left out); Jaccard of token
					5-gram shingles, normalized lines and file paths — similarity is diversity, not quality. The reference pairs this condition with another
					one on the same app and repeat index, so the shared boilerplate floor is visible. Findings: latest result per tool, only tools that
					measured both repeats; code issues and dependency advisories apart; — where no common tool measured a pair (unmeasured, not 0).
					{interimTag}
				{/snippet}
			</StudyFigure>

			<StudyFigure id="table-6" kind="table" number={6} title="How stable is the ranking" flush>
				<div class="grid gap-0 lg:grid-cols-2 lg:divide-x lg:divide-border/60">
					{#each rankBlocks as block (block.metric)}
						<div class="min-w-0">
							<p class="border-b border-border/60 bg-muted/40 px-4 py-1.5 text-xs">
								<span class="font-medium">{metricLabel(block.metric)}</span>
								<span class="text-muted-foreground">· {block.direction === 'higher' ? 'higher is better' : 'lower is better'} · {block.n_apps} apps</span>
							</p>
							{#if block.rows.length === 0}
								<p class="px-4 py-3 text-sm text-muted-foreground">No interval: {block.reason ?? 'not enough data'}.</p>
							{:else}
								<div class="overflow-x-auto">
									<table>
										<thead>
											<tr>
												<th scope="col">Condition</th>
												<th scope="col" class="num">Mean</th>
												<th scope="col" class="num" title="Observed competition rank (ties share the best rank)">Rank</th>
												<th scope="col" class="num" title="95% interval of the bootstrap ranks">95% rank interval</th>
												<th scope="col" class="num">P(rank 1)</th>
											</tr>
										</thead>
										<tbody>
											{#each block.rows as r (r.condition_id)}
												<tr>
													<td class="max-w-56 text-sm"><span class="line-clamp-2 break-words">{nameOf(r.condition_id)}</span></td>
													<td class="num text-xs">{formatMetricValue(r.mean, metricUnit(block.metric))}</td>
													<td class="num text-xs font-semibold">{r.rank}</td>
													<td class="num text-xs">{r.rank_ci95[0] === r.rank_ci95[1] ? r.rank_ci95[0] : `${r.rank_ci95[0]}–${r.rank_ci95[1]}`}</td>
													<td class="num text-xs">{formatShare(r.p_rank_1)}</td>
												</tr>
											{/each}
										</tbody>
									</table>
								</div>
							{/if}
						</div>
					{/each}
				</div>
				{#snippet caption()}
					<strong class="font-medium text-foreground">Table 6.</strong>
					Joint app-blocked bootstrap ({rankBlocks[0]?.draws ?? 1000} draws, seeded by the study): the apps every condition has are resampled
					once per draw for all conditions, then each app's repeats; conditions are ranked on the mean of app means (competition ranking). From 3
					shared apps. Exploratory.{interimTag}
				{/snippet}
			</StudyFigure>

			<StudyFigure id="table-7" kind="table" number={7} title="Precision of a next study" flush>
				{#if !data.planner}
					<p class="px-4 py-3 text-sm text-muted-foreground">{data.planner_reason ?? 'Not available.'}</p>
				{:else if !plan}
					<p class="px-4 py-3 text-sm text-muted-foreground">No metric to plan from.</p>
				{:else}
					<p class="border-b border-border/60 bg-muted/40 px-4 py-1.5 text-xs">
						<span class="font-medium">{metricLabel(plannerMetric)}</span>
						{#if plannerMetric !== metric?.key}<span class="text-muted-foreground">· the selected metric has no plan; showing this one</span>{/if}
					</p>
					{#if !plan.available || !grid}
						<p class="px-4 py-3 text-sm text-muted-foreground">Not planned: {plan.reason ?? 'not estimable'}.</p>
					{:else}
						<div class="overflow-x-auto">
							<table>
								<thead>
									<tr>
										<th scope="col">Apps</th>
										{#each grid.repeats as r (r)}
											<th scope="col" class="num">r = {r}</th>
										{/each}
									</tr>
								</thead>
								<tbody>
									{#each grid.apps as a (a)}
										<tr>
											<th scope="row" class="text-left font-mono text-xs font-normal">{a}</th>
											{#each grid.repeats as r (r)}
												{@const cell = grid.at(a, r)}
												<td class="num text-xs">
													±{formatMetricValue(cell?.mean_half_width, metricUnit(plannerMetric))}
													<span class="block text-muted-foreground">Δ ±{formatMetricValue(cell?.delta_half_width, metricUnit(plannerMetric))}</span>
												</td>
											{/each}
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					{/if}
				{/if}
				{#snippet caption()}
					<strong class="font-medium text-foreground">Table 7.</strong>
					95% CI half-width of a condition mean (top) and of a paired difference between two conditions (Δ, below) for a new design of A apps × r
					repeats, from this study's variance components (normal approximation; planning guidance only). Shown for finished studies only,
					so it cannot steer when to stop this one.{#if exactTestApps != null}
						An exact sign or Wilcoxon test needs at least {exactTestApps} apps to reach p &lt; 0.05 two-sided.{/if}
				{/snippet}
			</StudyFigure>

			{#if data.notes.length > 0}
				<ol class="max-w-4xl space-y-1 text-xs leading-relaxed text-muted-foreground" aria-label="Run-to-run variation notes">
					{#each data.notes as note, i (note)}
						<li class="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-1">
							<span class="font-mono">{String.fromCharCode(97 + (i % 26))}</span>
							<span>{note}</span>
						</li>
					{/each}
				</ol>
			{/if}
		{/if}
	{/if}
</StudySection>
