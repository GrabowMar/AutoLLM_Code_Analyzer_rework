<script lang="ts">
	/**
	 * ResultsTab — §3 Results (one server computation, `GET /results/`): the
	 * analysis specification (baseline and exclusions, with a way back to the
	 * pre-registered analysis), 3.1 Table 1 the ranked conditions with
	 * cluster-bootstrap CIs, 3.2 Figure 3 the paired differences against the
	 * baseline (forest plot), 3.3 Table 2 the condition × app matrix, and the
	 * server's notes as footnotes. Options live in the URL (`baseline`,
	 * `common`, `invalid`, `truncated`, `fingerprint`, `transpose`). Refetched
	 * on state changes, and on ticks at most every 30 s while running.
	 *
	 * Props: `experiment`, `refreshSignal` (bumped by the page on `state`),
	 * `tickSignal` (bumped on `tick`).
	 */
	import { onDestroy, untrack } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import { Switch } from '$lib/components/ui/switch';
	import * as Select from '$lib/components/ui/select';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import ResultsMatrix from '$lib/components/generator/batches/ResultsMatrix.svelte';
	import { fromResultsMatrix } from '$lib/components/generator/batches/matrix';
	import type { ExperimentDetail, ResultsQuery } from '$lib/api/experiments';
	import FileText from '@lucide/svelte/icons/file-text';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import Trophy from '@lucide/svelte/icons/trophy';
	import Leaderboard from '../Leaderboard.svelte';
	import PairedDeltas from '../PairedDeltas.svelte';
	import { MIN_APPS_FOR_CI, conditionName, isTerminal } from '../format';
	import StudyFigure from '../study/StudyFigure.svelte';
	import StudySection from '../study/StudySection.svelte';
	import { ResultsFeed } from '../study/results.svelte';

	interface Props {
		experiment: ExperimentDetail;
		refreshSignal?: number;
		tickSignal?: number;
	}

	let { experiment, refreshSignal = 0, tickSignal = 0 }: Props = $props();

	const OPTION_KEYS = ['baseline', 'common', 'invalid', 'truncated', 'fingerprint'];

	const params = $derived(page.url.searchParams);
	const flag = (key: string, fallback: boolean) => {
		const v = params.get(key);
		return v == null ? fallback : v === '1';
	};
	const baselineParam = $derived(params.get('baseline'));
	const query = $derived<ResultsQuery>({
		baseline: baselineParam ? Number(baselineParam) : undefined,
		common_cells: flag('common', true),
		exclude_invalid: flag('invalid', false),
		exclude_truncated: flag('truncated', true),
		fingerprint: params.get('fingerprint') === 'majority' ? 'majority' : 'all',
	});
	const transpose = $derived(flag('transpose', experiment.conditions.length > 15));
	const customised = $derived(OPTION_KEYS.some((k) => params.has(k)));

	const feed = new ResultsFeed(untrack(() => experiment.id));
	onDestroy(() => feed.destroy());

	// Keyed on the query's value: other URL changes (tab, transpose) do not refetch.
	const queryKey = $derived(JSON.stringify(query));
	$effect(() => {
		void queryKey;
		void refreshSignal;
		untrack(() => void feed.load($state.snapshot(query)));
	});
	$effect(() => {
		void tickSignal;
		untrack(() => {
			if (!isTerminal(experiment.status)) feed.tick();
		});
	});

	function setParams(next: Record<string, string | null>) {
		const url = new URL(page.url);
		for (const [key, value] of Object.entries(next)) {
			if (value == null) url.searchParams.delete(key);
			else url.searchParams.set(key, value);
		}
		goto(`${url.pathname}${url.search}`, { replaceState: true, keepFocus: true, noScroll: true });
	}
	const setParam = (key: string, value: string | null) => setParams({ [key]: value });
	const resetAnalysis = () => setParams(Object.fromEntries(OPTION_KEYS.map((k) => [k, null])));

	const results = $derived(feed.results);
	const conditionLabel = $derived(new Map(experiment.conditions.map((c) => [c.id, conditionName(c)])));
	const labelOf = (id: number) => conditionLabel.get(id) ?? `Condition #${id}`;
	const conditionInfo = $derived(
		experiment.conditions.map((c) => ({
			id: c.id,
			label: conditionName(c),
			model: c.model_name ?? `model #${c.model}`,
			variant: c.harness_preset_ref ? `${c.harness_preset_ref.name} v${c.harness_preset_ref.version}` : c.harness || '',
		}))
	);
	const matrix = $derived(results ? fromResultsMatrix(results, conditionInfo) : null);
	const graded = $derived((results?.leaderboard ?? []).some((r) => r.n_graded > 0));
	/** Graded cells exist, but none is shared by every condition (common cells on). */
	const onlyOutsideCommon = $derived(!graded && (results?.leaderboard ?? []).some((r) => (r.n_missing?.not_common ?? 0) > 0));
	const baselineItems = $derived(
		experiment.conditions.map((c) => ({
			value: String(c.id),
			label: conditionName(c),
			description: c.id === experiment.baseline_condition_id ? 'pre-registered' : undefined,
		}))
	);
	const currentBaseline = $derived(String(results?.baseline_condition_id ?? baselineParam ?? experiment.baseline_condition_id ?? ''));
	const toggles = $derived([
		{ key: 'common', label: 'Common cells only', on: query.common_cells !== false, def: true, title: 'Compare every condition on the same graded (app, repeat) cells' },
		{ key: 'truncated', label: 'Exclude truncated', on: query.exclude_truncated !== false, def: true, title: 'Drop runs that hit a budget or wall-clock limit (for every condition)' },
		{ key: 'invalid', label: 'Exclude invalid', on: !!query.exclude_invalid, def: false, title: 'Drop runs whose validity is “exclude” (for every condition)' },
		{ key: 'fingerprint', label: 'Majority fingerprint only', on: query.fingerprint === 'majority', def: false, title: 'Keep only runs with the condition’s most common configuration fingerprint' },
	]);

	/** The analysis as one sentence (what Table 1 and Figure 3 were computed on). */
	const specification = $derived.by(() => {
		const parts = [
			query.common_cells !== false ? 'common cells only' : 'all graded cells',
			query.exclude_truncated !== false ? 'truncated runs excluded' : 'truncated runs kept',
			query.exclude_invalid ? 'invalid runs excluded' : 'invalid runs kept',
		];
		if (query.fingerprint === 'majority') parts.push('majority fingerprint only');
		return parts.join(' · ');
	});

	// The interim banner already says this.
	const notes = $derived((results?.notes ?? []).filter((note) => !(results?.interim && /^interim\b/i.test(note))));

	function toggle(key: string, on: boolean, def: boolean) {
		if (key === 'fingerprint') setParam('fingerprint', on ? 'majority' : null);
		else setParam(key, on === def ? null : on ? '1' : '0');
	}
</script>

<div class="space-y-10">
	<section aria-labelledby="analysis-title" class="overflow-clip rounded-md border border-border bg-card shadow-[var(--shadow-card)]">
		<div class="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-border/60 px-4 py-2.5">
			<div class="min-w-0 space-y-0.5">
				<p class="section-label" id="analysis-title">Analysis</p>
				<p class="text-sm">
					{#if customised || results?.baseline_source === 'post_hoc'}
						<span class="inline-flex items-center gap-1 font-medium text-warning-fg"><TriangleAlert class="size-3.5" aria-hidden="true" />Exploratory</span>
						<span class="text-muted-foreground">— differs from the pre-registered analysis:</span>
					{:else}
						<span class="font-medium">Pre-registered</span>
						<span class="text-muted-foreground">—</span>
					{/if}
					<span class="text-muted-foreground">{specification}.</span>
				</p>
			</div>
			<div class="flex flex-wrap items-center gap-2">
				{#if customised}
					<Button variant="ghost" size="sm" onclick={resetAnalysis}><RotateCcw />Pre-registered analysis</Button>
				{/if}
				<Button variant="outline" size="sm" href="/reports/create?type=experiment_report&experiment_id={experiment.id}"><FileText />Generate report</Button>
			</div>
		</div>
		<div class="flex flex-col gap-4 px-4 py-3 lg:flex-row lg:items-end">
			<div class="min-w-0 space-y-1 lg:w-80">
				<Label class="text-xs" for="results-baseline">Baseline</Label>
				<Select.Simple
					id="results-baseline"
					size="sm"
					value={currentBaseline}
					items={baselineItems}
					onValueChange={(v) => setParam('baseline', v === String(experiment.baseline_condition_id) ? null : v)}
				/>
			</div>
			<div class="flex flex-wrap gap-x-5 gap-y-2 max-sm:gap-y-0">
				{#each toggles as t (t.key)}
					<label class="flex cursor-pointer items-center gap-2 text-xs max-sm:min-h-11" title={t.title}>
						<Switch size="sm" checked={t.on} label={t.label} onCheckedChange={(v) => toggle(t.key, v, t.def)} />
						{t.label}
					</label>
				{/each}
			</div>
		</div>
	</section>

	{#if feed.error}
		<ErrorAlert message={feed.error} onretry={() => feed.load()} />
	{/if}

	{#if feed.loading && !results}
		<LoadingState label="computing results" />
	{:else if results}
		{#if results.interim}
			<p class="flex items-start gap-2 rounded-md border border-info/30 bg-info/5 px-3 py-2 text-sm text-foreground" role="status">
				<span class="mt-0.5 font-mono text-xs font-medium uppercase tracking-[0.08em] text-info-fg">Interim</span>
				<span>Conditions are at unequal progress: estimates can still move and p-values are withheld until the experiment finishes.</span>
			</p>
		{/if}

		{#if onlyOutsideCommon}
			<EmptyState
				icon={Trophy}
				title="No cells graded for every condition yet"
				description="Some runs are graded, but “Common cells only” compares conditions on the (app, repeat) cells all of them have graded. Turn it off to see the graded runs now."
			>
				{#snippet action()}
					<Button size="sm" variant="outline" onclick={() => toggle('common', false, true)}>Show all graded cells</Button>
				{/snippet}
			</EmptyState>
		{:else if !graded}
			<EmptyState
				icon={Trophy}
				title="No graded cells yet"
				description={experiment.mode === 'scaffolding' && !experiment.post_run?.evaluate
					? 'Turn on Evaluate under Operations → Automation to grade runs.'
					: 'Results appear as runs are evaluated.'}
			/>
		{:else}
			<StudySection id="results-overall" number="3.1" title="Pass rate by condition">
				<StudyFigure id="table-1" kind="table" number={1} title="Conditions ranked by mean pass rate" flush>
					<Leaderboard {results} />
					{#snippet caption()}
						<strong class="font-medium text-foreground">Table 1.</strong>
						Pass rate: mean of app-level pass rates, with a cluster-bootstrap 95% interval over apps (from {MIN_APPS_FOR_CI} apps; bar: interval, tick:
						mean). Δ: paired difference from the baseline. Missing: cells without a usable grade (hover for why). Median time compares only
						within one endpoint. Validity and termination: share of runs per outcome.
					{/snippet}
				</StudyFigure>
			</StudySection>

			<StudySection id="results-baseline" number="3.2" title="Comparison with the baseline">
				<StudyFigure id="figure-3" number={3} title="Difference from {labelOf(results.paired.baseline_condition_id ?? -1)}" flush>
					<PairedDeltas {results} {labelOf} />
					{#snippet caption()}
						<strong class="font-medium text-foreground">Figure 3.</strong>
						Mean app-level difference in pass rate from the baseline (square), in percentage points, with the paired cluster-bootstrap 95%
						interval (whiskers; from {MIN_APPS_FOR_CI} paired apps). Filled: the interval excludes 0; hollow: it includes 0. W/L/T: apps won, lost
						and tied; n: apps graded for both. p: Wilcoxon signed-rank, Holm-adjusted across conditions{results.interim ? ' (withheld: interim)' : ''}.
					{/snippet}
				</StudyFigure>
			</StudySection>

			{#if matrix}
				<StudySection id="results-apps" number="3.3" title="Per app">
					<StudyFigure id="table-2" kind="table" number={2} title="Pass rate per condition and app" flush>
						{#snippet controls()}
							<label class="flex cursor-pointer items-center gap-2 text-xs max-sm:min-h-11">
								<Switch size="sm" checked={transpose} label="Conditions as rows" onCheckedChange={(v) => setParam('transpose', v ? '1' : '0')} />
								Conditions as rows
							</label>
						{/snippet}
						<div class="p-3">
							<ResultsMatrix {matrix} mode={experiment.mode} graded {transpose} />
						</div>
						{#snippet caption()}
							<strong class="font-medium text-foreground">Table 2.</strong>
							Each cell aggregates the repeats of one condition on one app: k/n cells passed, mean pass rate, one dot per repeat. Open a cell for its runs.
						{/snippet}
					</StudyFigure>
				</StudySection>
			{/if}
		{/if}

		{#if notes.length > 0}
			<section aria-labelledby="results-notes-title" class="space-y-2">
				<SectionHeading id="results-notes-title" size="sm" title="Notes" />
				<ol class="max-w-4xl space-y-1 text-xs leading-relaxed text-muted-foreground">
					{#each notes as note, i (note)}
						<li class="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-1">
							<span class="font-mono">{String.fromCharCode(97 + (i % 26))}</span>
							<span>{note}</span>
						</li>
					{/each}
				</ol>
			</section>
		{/if}
	{/if}
</div>
