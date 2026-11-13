<script lang="ts">
	/**
	 * ResultsSection — section 3 of the study record (spec 10.3): the analysis specification line, the
	 * confirmatory gate, the leaderboard with cluster-bootstrap CIs, paired comparisons against the
	 * baseline (method shown: paired by CRN or independent), the ITT-vs-excluding-truncated sensitivity,
	 * the attrition table, the condition × unit matrix, by-factor EMMs, stated vs unstated pass rates per
	 * prose variant, the common-check pass rate, test-retest agreement, issues and lettered notes.
	 * p-values are withheld while results are interim; anything not measured is "—".
	 *
	 * Props: `studyId`, `refreshSignal` (bump to refetch, throttled by the page).
	 */
	import { untrack } from 'svelte';
	import { getStudyResults } from '$lib/api/v3/studies';
	import type { StudyResultsOut, StudyResultsQuery } from '$lib/api/v3/types';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import * as Select from '$lib/components/ui/select';
	import { Label } from '$lib/components/ui/label';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { cn } from '$lib/utils';
	import StudySection from '../study/StudySection.svelte';
	import StudyFigure from '../study/StudyFigure.svelte';
	import { formatPct, formatUsd, plural } from '../format';
	import AttritionTable from './AttritionTable.svelte';
	import ByFactorPanel from './ByFactorPanel.svelte';
	import { FACTOR_BY_KEY } from '../builder/design';
	import type { FactorKey } from '$lib/api/v3/types';

	interface Props {
		studyId: string;
		refreshSignal?: number;
		number?: string;
	}

	let { studyId, refreshSignal = 0, number = '3' }: Props = $props();

	let data = $state<StudyResultsOut | null>(null);
	let error = $state('');
	let loading = $state(false);
	let query = $state<StudyResultsQuery>({});
	let controller: AbortController | null = null;

	async function load() {
		controller?.abort();
		const ctrl = new AbortController();
		controller = ctrl;
		loading = true;
		try {
			const out = await getStudyResults(studyId, $state.snapshot(query) as StudyResultsQuery, { signal: ctrl.signal });
			if (ctrl.signal.aborted) return;
			data = out;
			error = '';
		} catch (e) {
			if (!isAbortError(e)) error = formatApiError(e, 'Could not load the results.');
		} finally {
			if (controller === ctrl) loading = false;
		}
	}

	$effect(() => {
		void studyId;
		void refreshSignal;
		void query.sensitivity;
		void query.fingerprint;
		untrack(() => load());
		return () => controller?.abort();
	});

	const labels = $derived(new Map((data?.leaderboard ?? []).map((r) => [r.condition_id, r.label])));
	const label = (id: number | null) => (id === null ? '—' : (labels.get(id) ?? `condition ${id}`));
	const p = (v: number | null | undefined) => (data?.interim ? 'withheld' : v === null || v === undefined ? '—' : v < 0.001 ? '< .001' : v.toFixed(3).replace(/^0/, ''));
	const ci = (c: [number, number] | null) => (c ? `${formatPct(c[0], 1)} – ${formatPct(c[1], 1)}` : '—');
	const pp = (v: number | null) => (v === null ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v * 100).toFixed(1)} pp`);
	const exploratoryView = $derived(!!query.sensitivity || query.fingerprint === 'all');
	const units = $derived(data?.matrix.units ?? []);
	const cellOf = (conditionId: number, unitId: number) => data?.matrix.cells.find((c) => c.condition_id === conditionId && c.unit_id === unitId);
	const letters = 'abcdefghijklmnopqrstuvwxyz';
</script>

<StudySection id="results" {number} title="Results" lead="Intention-to-treat: truncated and budget-stopped runs are included; excluding them is a sensitivity analysis.">
	{#snippet aside()}
		<div class="flex flex-wrap items-end gap-2">
			<div class="space-y-1">
				<Label for="results-sensitivity" class="text-xs">Analysis</Label>
				<Select.Simple
					id="results-sensitivity"
					size="sm"
					value={query.sensitivity ?? ''}
					items={[
						{ value: '', label: 'Pre-registered (ITT)' },
						{ value: 'exclude_truncated', label: 'Sensitivity: exclude truncated' },
					]}
					onValueChange={(v) => (query = { ...query, sensitivity: v || undefined })}
				/>
			</div>
			<div class="space-y-1">
				<Label for="results-fingerprint" class="text-xs">Fingerprints</Label>
				<Select.Simple
					id="results-fingerprint"
					size="sm"
					value={query.fingerprint ?? 'majority'}
					items={[
						{ value: 'majority', label: 'Majority per (condition, unit)' },
						{ value: 'all', label: 'All (mixed allowed)' },
					]}
					onValueChange={(v) => (query = { ...query, fingerprint: v as 'all' | 'majority' })}
				/>
			</div>
		</div>
	{/snippet}

	{#if error}
		<ErrorAlert message={error} onretry={load} />
	{:else if !data}
		<LoadingState label="loading results" />
	{:else}
		<div class={cn('space-y-6', loading && 'opacity-70')}>
			<p class="text-sm">
				<b>{exploratoryView ? 'Exploratory view' : data.gate.locked ? 'Pre-registered analysis' : 'Exploratory experiment'}</b>
				— {data.common_cells ? 'common cells only' : 'all cells'} · {data.fingerprint_filter} fingerprint · {data.itt ? 'ITT' : 'truncated excluded'} ·
				baseline {label(data.baseline_condition_id)} ({data.baseline_source.replace(/_/g, ' ')}){#if data.interim} · <span class="text-warning-fg">interim: p-values withheld</span>{/if}
				{#if exploratoryView}<button type="button" class="ml-2 cursor-pointer text-primary underline" onclick={() => (query = {})}>back to the pre-registered analysis</button>{/if}
			</p>

			{#if data.gate.locked}
				<StudyFigure id="table-gate" kind="table" number="G" title="Confirmatory gate">
					<dl class="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
						<dt class="text-muted-foreground">Status</dt><dd><StatusBadge status={data.gate.status} /></dd>
						<dt class="text-muted-foreground">Planned cells</dt><dd class="font-mono">{data.gate.planned_cells}</dd>
						<dt class="text-muted-foreground">Stopped early</dt><dd>{data.gate.stopped_early ? 'yes (deviation)' : 'no'}</dd>
						<dt class="text-muted-foreground">Multiplicity</dt><dd>{data.gate.multiplicity.correction} over {plural(data.gate.multiplicity.family_size, 'test')} ({data.gate.multiplicity.family ?? 'no family'})</dd>
						<dt class="text-muted-foreground">Exclusion rate</dt><dd>{data.gate.exclusion_rate.over.length ? `${data.gate.exclusion_rate.over.length} condition(s) above ${formatPct(data.gate.exclusion_rate.threshold)}` : `all below ${formatPct(data.gate.exclusion_rate.threshold)}`}</dd>
						<dt class="text-muted-foreground">Differential attrition</dt><dd>{data.gate.differential_attrition.conditions.length ? `above ${formatPct(data.gate.differential_attrition.threshold)}` : 'within the threshold'}</dd>
						{#if data.gate.significance_unreachable}
							<dt class="text-muted-foreground">Significance</dt><dd class="text-warning-fg">unreachable with this design (needs ≥ {data.gate.significance_unreachable.units_needed} units)</dd>
						{/if}
						{#if data.gate.deviations.length}
							<dt class="text-muted-foreground">Deviations</dt><dd class="text-warning-fg">{data.gate.deviations.join(', ').replace(/_/g, ' ')}</dd>
						{/if}
					</dl>
				</StudyFigure>
			{/if}

			<StudyFigure id="table-1" kind="table" number={1} title="Conditions ranked by mean pass rate" flush>
				<div class="overflow-x-auto">
					<table>
						<thead>
							<tr>
								<th class="num">#</th>
								<th class="col-sticky">Condition</th>
								<th class="num">Mean</th>
								<th class="num">95% CI</th>
								<th class="num">Units</th>
								<th class="num">Graded</th>
								<th class="num" title="Pending · infra · excluded · not measured">Missing</th>
								<th class="num">Common checks</th>
								<th class="num">Cost</th>
							</tr>
						</thead>
						<tbody>
							{#each data.leaderboard as r (r.condition_id)}
								{@const missing = (r.n_missing.pending ?? 0) + (r.n_missing.infra ?? 0) + (r.n_missing.excluded ?? 0) + (r.n_missing.not_measured ?? 0)}
								<tr>
									<td class="num">{r.rank}</td>
									<td class="col-sticky max-w-64 truncate" title={r.label}>{r.label}{#if r.is_baseline} <span class="text-xs text-muted-foreground">(baseline)</span>{/if}</td>
									<td class="num">{formatPct(r.pass_rate_mean, 1)}</td>
									<td class="num">{ci(r.pass_rate_ci95)}</td>
									<td class="num">{r.n_units}</td>
									<td class="num">{r.n_graded}</td>
									<td class="num" title="pending {r.n_missing.pending ?? 0} · infra {r.n_missing.infra ?? 0} · excluded {r.n_missing.excluded ?? 0} · not measured {r.n_missing.not_measured ?? 0}">{missing}</td>
									<td class="num">{formatPct(data.common_pass_rate[String(r.condition_id)] ?? null, 1)}</td>
									<td class="num">{formatUsd(r.cost_usd)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				{#snippet caption()}
					Mean of per-unit mean pass rates ({data?.planned_repeats} planned repeats); 95% CIs by {data?.leaderboard[0]?.ci_method?.replace(/_/g, ' ') ?? 'cluster bootstrap'} over units.
					Model failures (invalid output, build or start failure) score 0; infra errors and unmeasured runs are missing, never 0.
				{/snippet}
			</StudyFigure>

			{#if data.paired.rows.length > 0}
				<StudyFigure id="table-2" kind="table" number={2} title="Differences against the baseline, paired by unit" flush>
					<div class="overflow-x-auto">
						<table>
							<thead><tr><th class="col-sticky">Condition</th><th>Method</th><th class="num">Pairs</th><th class="num">Mean Δ</th><th class="num">95% CI</th><th class="num">W / L / T</th><th class="num">p</th><th class="num">p adj.</th></tr></thead>
							<tbody>
								{#each data.paired.rows as r (r.condition_id)}
									<tr>
										<td class="col-sticky max-w-64 truncate">{label(r.condition_id)}</td>
										<td class="text-xs">{r.method === 'paired_crn' ? 'paired (CRN)' : 'independent'} · {r.primary_test}</td>
										<td class="num">{r.n_pairs}</td>
										<td class="num">{pp(r.mean_delta)}</td>
										<td class="num">{r.ci95 ? `${pp(r.ci95[0])} – ${pp(r.ci95[1])}` : '—'}</td>
										<td class="num">{r.wins} / {r.losses} / {r.ties}</td>
										<td class="num">{r.untested_reason ? r.untested_reason.replace(/_/g, ' ') : p(r.p_value)}</td>
										<td class="num">{p(r.p_adjusted)}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
					{#snippet caption()}
						{data?.paired.correction} correction, α = {data?.paired.alpha}; {plural(data?.paired.n_units ?? 0, 'unit')}. Paired methods need the seed forwarded for both conditions; otherwise independent methods are used (shown per row).
						{#if data?.paired.sensitivity?.changed.length}<b class="text-warning-fg">The sensitivity analysis changes {plural(data.paired.sensitivity.changed.length, 'conclusion')}.</b>{/if}
					{/snippet}
				</StudyFigure>
			{/if}

			{#if units.length > 0}
				<StudyFigure id="table-3" kind="table" number={3} title="Mean pass rate per condition and unit" flush>
					<div class="overflow-x-auto">
						<table class="text-xs">
							<thead><tr><th class="col-sticky">Condition</th>{#each units as u (u.id)}<th class="num max-w-32 truncate" title={u.unit_key}>{u.unit_key.replace(/^(task|slot):/, '')}</th>{/each}</tr></thead>
							<tbody>
								{#each data.leaderboard as r (r.condition_id)}
									<tr>
										<td class="col-sticky max-w-48 truncate">{r.label}</td>
										{#each units as u (u.id)}
											{@const c = cellOf(r.condition_id, u.id)}
											<td class="num" title={c ? `${c.k}/${c.n} passed all checks` : ''}>{c && c.n ? formatPct(c.mean, 0) : '—'}</td>
										{/each}
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
					{#snippet caption()}Mean over the kept repeats of each cell; "—" = nothing measured yet.{/snippet}
				</StudyFigure>
			{/if}

			<StudyFigure id="figure-factors" kind="figure" number={4} title="Estimated marginal means by factor level">
				<ByFactorPanel byFactor={data.by_factor} factorLabel={(k) => FACTOR_BY_KEY[k as FactorKey]?.label ?? k} />
			</StudyFigure>

			{#if Object.keys(data.by_prose_variant).length > 0}
				<StudyFigure id="table-prose" kind="table" number={5} title="Stated and unstated checks per prose variant">
					<table class="text-xs">
						<thead><tr><th>Variant</th><th class="num">Stated</th><th class="num">Unstated</th><th class="num">n</th></tr></thead>
						<tbody>
							{#each Object.entries(data.by_prose_variant) as [variant, v] (variant)}
								<tr><td>{variant}</td><td class="num">{formatPct(v.stated, 1)}</td><td class="num">{formatPct(v.unstated, 1)}</td><td class="num">{v.n}</td></tr>
							{/each}
						</tbody>
					</table>
					{#snippet caption()}Pass rate on the checks the brief states vs. leaves unstated, per prose variant.{/snippet}
				</StudyFigure>
			{/if}

			<StudyFigure id="table-attrition" kind="table" number={6} title="Attrition per condition" flush>
				<AttritionTable rows={data.attrition} over={data.gate.exclusion_rate.over} />
				{#snippet caption()}Truncated runs are kept (ITT). Infra errors are retried with the same manifest and seed; a usable run is never superseded.{/snippet}
			</StudyFigure>

			<p class="text-xs text-muted-foreground">
				Test-retest agreement:
				{#if data.retest_agreement.status === 'not_measured' || data.retest_agreement.n === 0}
					not measured
				{:else}
					{formatPct(data.retest_agreement.check_agreement, 1)} of checks, {formatPct(data.retest_agreement.run_agreement, 1)} of runs agree (κ {data.retest_agreement.kappa?.toFixed(2) ?? '—'}, n = {data.retest_agreement.n})
				{/if}
			</p>

			{#if data.issues.length > 0}
				<ul class="space-y-1 text-xs">
					{#each data.issues as issue, i (issue.code + i)}
						<li class={issue.severity === 'error' ? 'text-danger-fg' : issue.severity === 'warn' ? 'text-warning-fg' : 'text-muted-foreground'}>
							<span class="font-mono">{issue.code}</span>: {issue.message}
						</li>
					{/each}
				</ul>
			{/if}
			{#if data.notes.length > 0}
				<ol class="space-y-0.5 text-xs text-muted-foreground">
					{#each data.notes as note, i (i)}<li><sup>{letters[i] ?? i}</sup> {note}</li>{/each}
				</ol>
			{/if}
		</div>
	{/if}
</StudySection>
