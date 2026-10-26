<script lang="ts">
	/**
	 * SummaryTab — §1 Abstract: what the experiment tests and where it stands
	 * in prose, the key findings of the pre-registered analysis (leader,
	 * effects against the baseline, limitations), Figure 1 (pass rate with
	 * 95% intervals), Figure 2 (the data-collection matrix, once launched),
	 * why runs failed, and the study record. Drafts show the launch estimate
	 * and what a launch freezes instead of results.
	 *
	 * Props: `experiment`, `live` (null for drafts), `finished`, `costUsd`,
	 * `refreshSignal` / `tickSignal` (bumped by the page on `state` / `tick`).
	 */
	import { onDestroy, untrack } from 'svelte';
	import { Label } from '$lib/components/ui/label';
	import { Segmented } from '$lib/components/ui/segmented';
	import * as Select from '$lib/components/ui/select';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import { getPreview, type ExperimentDetail, type ExperimentPreviewV2 } from '$lib/api/experiments';
	import { formatApiError } from '$lib/api/core';
	import { formatUsd } from '$lib/components/generator/batches/batch-utils';
	import Grid3x3 from '@lucide/svelte/icons/grid-3x3';
	import Trophy from '@lucide/svelte/icons/trophy';
	import ConditionGrid from '../monitor/ConditionGrid.svelte';
	import FailureSummary from '../monitor/FailureSummary.svelte';
	import type { ExperimentLive } from '../live.svelte';
	import type { GridGroupBy, GridSort } from '../cells';
	import { conditionName, formatEta, formatHours, pauseReasonLabel, plural, progressCounts } from '../format';
	import PassRatePlot from '../study/PassRatePlot.svelte';
	import StudyFigure from '../study/StudyFigure.svelte';
	import StudyRecord from '../study/StudyRecord.svelte';
	import StudySection from '../study/StudySection.svelte';
	import { conditionLabels, designShape, keyFindings } from '../study/findings';
	import { DEFAULT_RESULTS_QUERY, ResultsFeed } from '../study/results.svelte';

	interface Props {
		experiment: ExperimentDetail;
		live: ExperimentLive | null;
		finished?: boolean;
		costUsd?: number;
		refreshSignal?: number;
		tickSignal?: number;
	}

	let { experiment, live, finished = false, costUsd, refreshSignal = 0, tickSignal = 0 }: Props = $props();

	const PLOT_LIMIT = 12;
	const isDraft = $derived(experiment.status === 'draft');
	const shape = $derived(designShape(experiment));
	const labels = $derived(conditionLabels(experiment));
	const labelOf = (id: number) => labels.get(id) ?? `Condition #${id}`;
	const baseline = $derived(experiment.conditions.find((c) => c.id === experiment.baseline_condition_id) ?? null);
	const status = $derived(live?.status ?? null);

	// ── results (the pre-registered analysis, not the Results tab's options) ──
	const feed = new ResultsFeed(untrack(() => experiment.id));
	onDestroy(() => feed.destroy());

	$effect(() => {
		void refreshSignal;
		if (isDraft) return;
		untrack(() => void feed.load(DEFAULT_RESULTS_QUERY));
	});
	$effect(() => {
		void tickSignal;
		if (finished) return;
		untrack(() => feed.tick());
	});

	const results = $derived(feed.results);
	const graded = $derived((results?.leaderboard ?? []).some((r) => r.n_graded > 0));
	const onlyOutsideCommon = $derived(!graded && (results?.leaderboard ?? []).some((r) => (r.n_missing?.not_common ?? 0) > 0));
	const findings = $derived(results && graded ? keyFindings(results, labelOf) : []);
	const claims = $derived(findings.filter((f) => f.kind !== 'caveat'));
	const caveats = $derived(findings.filter((f) => f.kind === 'caveat'));

	// ── data collection ─────────────────────────────────────────────
	const counts = $derived(progressCounts(status?.counts ?? experiment.progress));
	const done = $derived(status ? status.cells.done + status.cells.failed_final : counts.completed + counts.failed + counts.cancelled);
	const failedFinal = $derived(status ? status.cells.failed_final : counts.failed + counts.cancelled);

	let groupBy = $state<GridGroupBy>('endpoint');
	let sort = $state<GridSort>('sequence');
	const groupItems = [
		{ value: 'endpoint', label: 'By endpoint' },
		{ value: 'none', label: 'Flat' },
	];
	const sortItems = [
		{ value: 'sequence', label: 'Launch order' },
		{ value: 'label', label: 'Name' },
		{ value: 'progress', label: 'Progress' },
		{ value: 'pass', label: 'Pass rate' },
	];

	// ── drafts: the launch estimate ─────────────────────────────────
	let preview = $state.raw<ExperimentPreviewV2 | null>(null);
	let previewError = $state('');
	$effect(() => {
		if (!isDraft) return;
		void experiment.updated_at;
		untrack(async () => {
			try {
				preview = await getPreview(experiment.id);
				previewError = '';
			} catch (e) {
				previewError = formatApiError(e, 'Could not estimate the launch.');
			}
		});
	});
	const blocking = $derived((preview?.issues ?? []).filter((i) => i.blocking));
	const variantNoun = $derived(experiment.mode === 'agent' ? 'harness preset' : 'prompt profile');
</script>

{#snippet findingText(parts: import('../study/findings').FindingPart[])}
	{#each parts as part, i (i)}{#if typeof part === 'string'}{part}{:else}<strong class="font-semibold text-foreground">{part.strong}</strong>{/if}{/each}
{/snippet}

<StudySection id="abstract" number="1" title="Abstract">
<div class="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_21rem]">
	<div class="min-w-0 space-y-4">
		<div class="overflow-clip rounded-md border border-border bg-card shadow-[var(--shadow-card)]">
			<div class="space-y-3 px-4 py-4 sm:px-5">
				{#if experiment.description}
					<p class="max-w-3xl whitespace-pre-line font-display text-[15px] leading-relaxed text-foreground/90">{experiment.description}</p>
				{/if}
				<p class="max-w-3xl font-display text-[15px] leading-relaxed text-foreground/90">
					This {experiment.mode === 'agent' ? 'agent' : 'template-scaffolding'} experiment compares
					<strong class="font-semibold text-foreground">{plural(shape.conditions, 'condition')}</strong>
					{#if shape.conditions > 1}
						({plural(shape.models, 'model')} × {plural(shape.variants, variantNoun)}{shape.ablations ? `, plus ${plural(shape.ablations, 'ablation')}` : ''})
					{/if}
					on <strong class="font-semibold text-foreground">{plural(shape.apps, 'app spec')}</strong>, each run {plural(shape.repeats, 'time')}
					({plural(shape.cells, 'cell')}).
					{#if baseline && shape.conditions > 1}
						Every condition is compared with the pre-registered baseline, <strong class="font-semibold text-foreground">{conditionName(baseline)}</strong>, paired by app,
					{:else}
						Runs are scored
					{/if}
					on the share of {experiment.mode === 'agent' ? 'hidden conformance checks' : 'evaluation checks'} a generated app passes, averaged per app
					with cluster-bootstrap 95% intervals.
				</p>
				<p class="max-w-3xl text-sm leading-relaxed text-muted-foreground">
					{#if isDraft}
						Not launched. The design can still change; launching freezes it together with the baseline and the seeds.
					{:else if experiment.status === 'running'}
						Data collection in progress: {done.toLocaleString()} of {plural(shape.cells, 'cell')} finished{failedFinal ? `, ${failedFinal} failed` : ''}{status?.eta_seconds != null ? `, about ${formatEta(status.eta_seconds)} left` : ''}. Figures are interim.
					{:else if experiment.status === 'paused'}
						Data collection paused ({pauseReasonLabel(experiment.pause_reason) || 'paused'}) at {done.toLocaleString()} of {plural(shape.cells, 'cell')}.
					{:else if experiment.status === 'cancelled'}
						Cancelled at {done.toLocaleString()} of {plural(shape.cells, 'cell')}{failedFinal ? ` (${failedFinal} failed or cancelled)` : ''}.
					{:else}
						Data collection finished: {done.toLocaleString()} of {plural(shape.cells, 'cell')}{failedFinal ? `, ${failedFinal} failed` : ', none failed'}{(costUsd ?? 0) > 0 ? `, ${formatUsd(costUsd ?? 0)} spent` : ''}.
					{/if}
				</p>

				{#if !isDraft}
					<div class="pt-2">
						<SectionHeading size="sm" level={3} title="Key findings" />
						{#if feed.loading && !results}
							<LoadingState label="computing results" class="py-4" />
						{:else if feed.error && !results}
							<ErrorAlert message={feed.error} onretry={() => feed.load(DEFAULT_RESULTS_QUERY)} />
						{:else if claims.length > 0}
							<ol class="mt-2 max-w-3xl space-y-1.5">
								{#each claims as finding, i (i)}
									<li class="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-1 text-sm leading-relaxed text-foreground/90">
										<span class="font-mono text-xs leading-6 text-muted-foreground">{i + 1}.</span>
										<span>{@render findingText(finding.parts)}</span>
									</li>
								{/each}
							</ol>
							{#if caveats.length > 0}
								<div class="mt-3">
									<h4 class="section-label">Limitations</h4>
									<ul class="mt-1.5 max-w-3xl space-y-1">
										{#each caveats as finding, i (i)}
											<li class="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-1 text-xs leading-relaxed text-muted-foreground">
												<span aria-hidden="true">–</span>
												<span>{@render findingText(finding.parts)}</span>
											</li>
										{/each}
									</ul>
								</div>
							{/if}
						{:else if onlyOutsideCommon}
							<p class="mt-1.5 text-sm text-muted-foreground">
								Runs are graded, but no app is graded for every condition yet, so the paired comparison has nothing to stand on.
								<a class="settings-link" href="?tab=results&common=0">Compare all graded cells</a>.
							</p>
						{:else}
							<p class="mt-1.5 text-sm text-muted-foreground">
								{experiment.mode === 'scaffolding' && !experiment.post_run?.evaluate
									? 'Nothing is graded: turn on Evaluate under Operations → Automation.'
									: 'Nothing graded yet — findings appear as runs are evaluated.'}
							</p>
						{/if}
					</div>
				{/if}
			</div>
		</div>

		{#if graded && results}
			<StudyFigure id="figure-1" number={1} title="Pass rate by condition" flush>
				<PassRatePlot {results} limit={PLOT_LIMIT} moreHref="?tab=results" />
				{#snippet caption()}
					<strong class="font-medium text-foreground">Figure 1.</strong>
					Mean of app-level pass rates per condition (dot), ranked, with the cluster-bootstrap 95% interval over apps (whiskers; shown from 3 apps).
					{#if baseline}The hollow diamond and the vertical hairline mark the baseline.{/if}
					k/n: cells that passed every check / graded cells. Common cells only, truncated runs excluded{results.interim ? '; interim' : ''}.
					<a class="settings-link" href="?tab=results">Full results</a>
				{/snippet}
			</StudyFigure>
		{:else if !isDraft && !feed.loading && !feed.error && !onlyOutsideCommon && experiment.status !== 'running'}
			<EmptyState compact icon={Trophy} title="No graded results" description="This experiment has no graded runs to plot." />
		{/if}

		{#if live}
			<FailureSummary experimentId={experiment.id} failed={failedFinal} />
			<StudyFigure id="figure-2" number={2} title="Data collection" flush>
				{#snippet controls()}
					<Segmented aria-label="Group conditions" size="sm" value={groupBy} items={groupItems} onValueChange={(v) => (groupBy = v as GridGroupBy)} />
					<div class="w-36">
						<Label class="sr-only" for="grid-sort">Sort conditions</Label>
						<Select.Simple id="grid-sort" size="sm" value={sort} items={sortItems} onValueChange={(v) => (sort = v as GridSort)} />
					</div>
				{/snippet}
				{#if live.grid && live.grid.conditions.length > 0}
					<ConditionGrid grid={live.grid} {groupBy} {sort} baselineId={experiment.baseline_condition_id} framed={false} />
				{:else if live.loading}
					<LoadingState label="loading cells" class="py-6" />
				{:else}
					<EmptyState compact icon={Grid3x3} title="No cells yet" />
				{/if}
				{#snippet caption()}
					<strong class="font-medium text-foreground">Figure 2.</strong>
					Every cell of the design: one row per condition, one column per app, one square per repeat. A solid square has finished; a
					ring is waiting (dashed: for a model to load). Open a square for its run, code and evaluation.
				{/snippet}
			</StudyFigure>
		{/if}
	</div>

	<div class="min-w-0 space-y-4 xl:sticky xl:top-16">
		<StudyRecord {experiment} {costUsd} policyEffective={status?.policy_effective ?? null} />

		{#if isDraft}
			<section aria-labelledby="launch-estimate-title" class="overflow-clip rounded-md border border-border bg-card shadow-[var(--shadow-card)]">
				<div class="border-b border-border/60 px-4 py-2.5">
					<p class="section-label">Before launch</p>
					<h3 id="launch-estimate-title" class="font-display text-[17px] font-semibold">Launch estimate</h3>
				</div>
				<div class="space-y-2 px-4 py-3 text-sm">
					{#if previewError}
						<ErrorAlert message={previewError} />
					{:else if !preview}
						<LoadingState label="estimating" class="py-4" />
					{:else}
						<dl class="grid grid-cols-[7.5rem_minmax(0,1fr)] gap-x-3 gap-y-1">
							<dt class="text-xs leading-5 text-muted-foreground">Cells</dt>
							<dd class="font-mono text-xs leading-5">{preview.total_jobs.toLocaleString()}</dd>
							<dt class="text-xs leading-5 text-muted-foreground">Wall clock</dt>
							<dd class="font-mono text-xs leading-5">{formatHours(preview.estimate.total_hours)}</dd>
							<dt class="text-xs leading-5 text-muted-foreground">Spend</dt>
							<dd class="font-mono text-xs leading-5" title={preview.cost.kind === 'budget_upper_bound' ? 'Upper bound: the budget cap × runs' : undefined}>
								{preview.cost.openrouter_upper_bound_usd > 0 ? `≤ ${formatUsd(preview.cost.openrouter_upper_bound_usd)}` : '$0 (local)'}
							</dd>
							<dt class="text-xs leading-5 text-muted-foreground">Endpoints</dt>
							<dd class="text-xs leading-5">
								{preview.endpoints.map((e) => `${e.name || e.slug}${e.online ? '' : ' (offline)'}`).join(', ') || '—'}
							</dd>
						</dl>
						{#if blocking.length > 0}
							<ul class="space-y-1 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-danger-fg">
								{#each blocking as issue (issue.code + issue.detail)}<li>{issue.detail}</li>{/each}
							</ul>
						{/if}
					{/if}
					<p class="text-xs leading-relaxed text-muted-foreground">
						A launch freezes the conditions, the baseline, the apps and the seeds, so the comparison stays as registered. Clone it to change any of them later.
					</p>
				</div>
			</section>
		{/if}
	</div>
</div>
</StudySection>
