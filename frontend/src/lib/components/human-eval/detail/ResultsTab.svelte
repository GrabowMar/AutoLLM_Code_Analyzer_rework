<script lang="ts">
	/**
	 * ResultsTab — the analysis, computed by the server (`GET results/`):
	 *
	 * 1 Status: provisional (still collecting; p-values withheld), confirmatory
	 *   (plan followed) or exploratory with the deviations; counts, rule-based
	 *   exclusions, warnings and the groups (pool / rated / unevaluable apps).
	 * 2 Contrasts against the baseline (group by condition only; p only when the
	 *   server sends one; Holm-adjusted).
	 * 3… one section per design block (figures: dot + 95% CI per group, tables
	 *   per app, agreement, precision, Bradley–Terry rankings).
	 * Then human vs automated measures, data quality per participant and the
	 * de-blinding key of the apps.
	 *
	 * Options in the URL: `group` (condition | model | app) and `excluded=1`
	 * (include excluded data: exploratory).
	 *
	 * Props: `task`.
	 */
	import { onDestroy, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import StudyFigure from '$lib/components/studies/study/StudyFigure.svelte';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';
	import { Button } from '$lib/components/ui/button';
	import { Segmented } from '$lib/components/ui/segmented';
	import { Switch } from '$lib/components/ui/switch';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { getResults, type GroupBy, type TaskDetail, type TaskResults } from '$lib/api/human-eval';
	import { formatDateTime } from '$lib/utils/formatters';
	import { cn } from '$lib/utils';
	import {
		BLOCK_TYPE_LABELS,
		GROUP_BY_LABELS,
		PARTICIPANT_KIND_LABELS,
		RULE_LABELS,
		deviationText,
		formatCi,
		formatCoef,
		formatContrastDiff,
		formatP,
		formatSeconds,
		humanize,
		metricIsShare,
		plural,
	} from '../format';
	import FlagList from './FlagList.svelte';
	import BugsResult from './results/BugsResult.svelte';
	import PairwiseResult from './results/PairwiseResult.svelte';
	import QuestionnaireResult from './results/QuestionnaireResult.svelte';
	import RatingResult from './results/RatingResult.svelte';
	import RequirementsResult from './results/RequirementsResult.svelte';
	import ScenariosResult from './results/ScenariosResult.svelte';
	import SecurityResult from './results/SecurityResult.svelte';
	import type { ResultsContext } from './results/types';
	import ChartColumn from '@lucide/svelte/icons/chart-column';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import Hourglass from '@lucide/svelte/icons/hourglass';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	let { task }: { task: TaskDetail } = $props();

	const params = $derived(page.url.searchParams);
	const groupBy = $derived<GroupBy>(
		params.get('group') === 'model' ? 'model' : params.get('group') === 'app' ? 'app' : 'condition'
	);
	const includeExcluded = $derived(params.get('excluded') === '1');

	function setParams(next: Record<string, string | null>) {
		const url = new URL(page.url);
		for (const [key, value] of Object.entries(next)) {
			if (value == null) url.searchParams.delete(key);
			else url.searchParams.set(key, value);
		}
		try {
			goto(`${url.pathname}${url.search}`, { replaceState: true, keepFocus: true, noScroll: true });
		} catch {
			// Router not ready yet.
		}
	}

	let results = $state.raw<TaskResults | null>(null);
	let loading = $state(true);
	let loadError = $state('');
	let ctl: AbortController | null = null;

	async function load() {
		ctl?.abort();
		const mine = new AbortController();
		ctl = mine;
		loading = true;
		try {
			results = await getResults(task.id, { group_by: groupBy, include_excluded: includeExcluded || undefined }, { signal: mine.signal });
			loadError = '';
		} catch (e) {
			if (isAbortError(e) || mine.signal.aborted) return;
			loadError = formatApiError(e, 'Could not compute the results.');
		} finally {
			if (ctl === mine) loading = false;
		}
	}

	const queryKey = $derived(JSON.stringify([task.id, groupBy, includeExcluded]));
	$effect(() => {
		void queryKey;
		untrack(() => void load());
	});
	onDestroy(() => ctl?.abort());

	let groupValue = $state(untrack(() => groupBy as string));
	$effect(() => {
		groupValue = groupBy;
	});
	let excludedValue = $state(untrack(() => includeExcluded));
	$effect(() => {
		excludedValue = includeExcluded;
	});

	const GROUP_ITEMS = [
		{ value: 'condition', label: 'Condition', title: 'Study condition (the factor levels of the cell); apps without one fall back to their model' },
		{ value: 'model', label: 'Model' },
		{ value: 'app', label: 'App', title: 'Every app on its own' },
	];

	const groupLabels = $derived(new Map((results?.groups ?? []).map((g) => [g.key, g.label])));
	const ctx = $derived<ResultsContext>({
		groupNoun: GROUP_BY_LABELS[results?.group_by ?? groupBy],
		groupLabel: (key: string) => groupLabels.get(key) ?? key,
		pWithheld: results?.p_values_withheld ?? true,
		primary: task.design.analysis?.primary ?? null,
	});
	const blockTitle = (blockId: string | undefined) => {
		if (!blockId) return '';
		return task.design.blocks.find((b) => b.id === blockId)?.title ?? blockId;
	};

	// Section numbers follow what is shown (no gaps).
	const showContrasts = $derived(!!results && results.group_by === 'condition');
	const sectionNo = $derived.by(() => {
		let k = 1;
		const blocks: Record<string, string> = {};
		const contrasts = showContrasts ? String(++k) : '';
		for (const b of results?.blocks ?? []) blocks[b.block_id] = String(++k);
		return {
			contrasts,
			blocks,
			hva: results?.human_vs_automated.length ? String(++k) : '',
			quality: String(++k),
			apps: String(++k),
		};
	});
	const anyP = $derived((results?.contrasts ?? []).some((c) => c.p_value != null || c.p_adjusted != null));
	const ruleEntries = $derived(Object.entries(results?.rule_exclusions ?? {}));
	const deviations = $derived((results?.deviations ?? []).filter((d) => d !== 'provisional'));
	const primaryLabel = $derived.by(() => {
		const p = ctx.primary;
		if (!p) return '';
		return `${blockTitle(p.block_id)} · ${humanize(p.metric)}`;
	});
</script>

<div class="space-y-6">
	<div class="flex flex-wrap items-center justify-between gap-3">
		<div class="flex flex-wrap items-center gap-3">
			<Segmented aria-label="Group results by" size="sm" items={GROUP_ITEMS} bind:value={groupValue} onValueChange={(v) => setParams({ group: v === 'condition' ? null : v })} />
			<label class="flex items-center gap-2 text-sm">
				<Switch size="sm" bind:checked={excludedValue} label="Include excluded data" onCheckedChange={(v) => setParams({ excluded: v ? '1' : null })} />
				Include excluded data
			</label>
		</div>
		<div class="flex items-center gap-2 text-xs text-muted-foreground">
			{#if results}<span>Computed {formatDateTime(results.generated_at)}</span>{/if}
			<Button variant="ghost" size="sm" onclick={load} disabled={loading} aria-label="Recompute results">
				<RefreshCw class={cn(loading && 'animate-spin motion-reduce:animate-none')} />
			</Button>
		</div>
	</div>

	{#if loadError}
		<ErrorAlert message={loadError} onretry={load} />
	{:else if loading && !results}
		<LoadingState label="computing results" />
	{:else if results}
		<!-- 1 Status -->
		<section class="space-y-3">
			<SectionHeading number="1" title="Status of the analysis" />
			{#if results.provisional}
				<Alert class="border-info/40 bg-info/5">
					<Hourglass class="text-info-fg" />
					<AlertDescription>
						<span class="font-medium text-foreground">Provisional.</span> The task is still collecting data; estimates will change and p-values are
						withheld until it is closed.
					</AlertDescription>
				</Alert>
			{:else if results.confirmatory && !results.include_excluded}
				<Alert class="border-success/40 bg-success/5">
					<CircleCheck class="text-success-fg" />
					<AlertDescription>
						<span class="font-medium text-foreground">Confirmatory.</span> Closed once, planned sample reached, no post-hoc exclusions, primary
						outcome pre-registered{primaryLabel ? ` (${primaryLabel})` : ''}.
					</AlertDescription>
				</Alert>
			{:else}
				<Alert class="border-warning/40 bg-warning/5">
					<TriangleAlert class="text-warning-fg" />
					<div class="space-y-1">
						<p class="font-medium">Exploratory</p>
						{#if results.include_excluded}<p class="text-sm text-muted-foreground">Excluded participants and assignments are included in this view.</p>{/if}
						{#if deviations.length}
							<ul class="list-disc space-y-0.5 pl-4 text-sm text-muted-foreground">
								{#each deviations as d (d)}<li>{deviationText(d)}</li>{/each}
							</ul>
						{/if}
					</div>
				</Alert>
			{/if}
			{#if results.provisional && deviations.length}
				<p class="text-xs text-muted-foreground">Already deviating from the plan: {deviations.map((d) => deviationText(d)).join(' ')}</p>
			{/if}

			<div class="header-stats rounded-md border border-border bg-card px-4 py-2.5">
				<span class="header-stat"><b>{results.counts.participants_included}/{results.counts.participants}</b> participants included</span>
				<span class="header-stat"><b>{results.counts.assignments_included}/{results.counts.assignments_submitted}</b> submitted assignments included</span>
				<span class="header-stat"><b>{results.counts.assignments_skipped}</b> skipped</span>
				<span class="header-stat"><b>{results.counts.assignments_excluded}</b> excluded</span>
				{#if ctx.primary}<span class="header-stat">primary <b class="font-sans">{primaryLabel}</b></span>{:else}<span class="header-stat">no primary outcome registered</span>{/if}
			</div>

			{#if ruleEntries.length > 0}
				<p class="text-xs text-muted-foreground">
					Pre-registered exclusion rules applied:
					{#each ruleEntries as [rule, count], i (rule)}
						{i > 0 ? ' · ' : ''}<span class="text-foreground">{RULE_LABELS[rule] ?? humanize(rule)}</span> removed {plural(count, 'assignment')}
					{/each}
				</p>
			{/if}

			{#if results.warnings.length > 0}
				<ul class="space-y-1 rounded-md border border-warning/40 bg-warning/5 px-4 py-2.5 text-sm">
					{#each results.warnings as w, i (i)}
						<li class="flex gap-2">
							<TriangleAlert class="mt-0.5 size-3.5 shrink-0 text-warning-fg" aria-hidden="true" />
							<span>
								{w.message || humanize(w.code)}
								{#if w.block_id}<span class="text-muted-foreground"> — {blockTitle(w.block_id)}</span>{/if}
								<span class="font-mono text-[11px] text-muted-foreground">{w.code}</span>
							</span>
						</li>
					{/each}
				</ul>
			{/if}

			<StudyFigure id="tab-groups" kind="table" number="1" title="{ctx.groupNoun}s in the analysis" flush>
				<div class="overflow-x-auto">
					<table class="text-sm">
						<thead>
							<tr>
								<th>{ctx.groupNoun}</th>
								<th class="num" title="Apps in the pool">Pool</th>
								<th class="num" title="Apps with at least one included submission">Rated</th>
								<th class="num" title="No included submission and at least one 'app broken' skip">Unevaluable</th>
							</tr>
						</thead>
						<tbody>
							{#each results.groups as g (g.key)}
								<tr>
									<td class="max-w-80 break-words">{g.label}</td>
									<td class="num text-xs">{g.n_apps_pool}</td>
									<td class="num text-xs">{g.n_apps_rated}</td>
									<td class={cn('num text-xs', g.n_apps_unevaluable > 0 && 'text-warning-fg')}>{g.n_apps_unevaluable}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				{#snippet caption()}
					Unevaluable apps are handled by the pre-registered policy ({task.design.analysis?.unevaluable_policy === 'floor' ? 'counted at the floor' : 'left out'});
					figures also show the floor sensitivity where it applies.
				{/snippet}
			</StudyFigure>
		</section>

		<!-- 2 Contrasts -->
		{#if showContrasts}
			<section class="space-y-3">
				<SectionHeading number={sectionNo.contrasts} title="Contrasts against the baseline" lead="Each condition minus the baseline condition, per pre-registered metric." />
				{#if results.contrasts.length === 0}
					<p class="text-sm text-muted-foreground">No contrasts: they need a baseline condition (pairing baseline or the study’s baseline condition) and data in both groups.</p>
				{:else}
					<StudyFigure id="tab-contrasts" kind="table" number="{sectionNo.contrasts}.1" title="Difference to the baseline per metric" flush>
						<div class="overflow-x-auto">
							<table class="text-sm">
								<thead>
									<tr>
										<th>Metric</th>
										<th>Condition vs baseline</th>
										<th class="num">Difference [95% CI]</th>
										<th class="num" title="Paired on common (app spec, repeat) cells when ≥ 3 exist">Design</th>
										{#if anyP}
											<th class="num" title="Wilcoxon signed-rank (paired)">p</th>
											<th class="num" title="Holm-adjusted across all contrasts">p (Holm)</th>
										{/if}
									</tr>
								</thead>
								<tbody>
									{#each results.contrasts as c, i (i)}
										<tr class={cn(c.primary && 'bg-primary/5')}>
											<td>
												{c.label}
												{#if c.primary}<span class="ml-1 rounded-full border border-primary/40 px-1.5 text-[11px] text-primary">primary</span>{/if}
												<span class="block text-[11px] text-muted-foreground">{blockTitle(c.block_id)}</span>
											</td>
											<td class="max-w-72 text-xs">{c.group_label} <span class="text-muted-foreground">vs {c.baseline_label}</span></td>
											<td class="num text-xs">
												<span class="font-medium">{formatContrastDiff(c.diff, c.metric)}</span>
												<span class="text-muted-foreground">{formatCi(c.ci, (v) => (metricIsShare(c.metric) ? `${(v * 100).toFixed(1)}` : v.toFixed(2)))}</span>
											</td>
											<td class="num text-xs text-muted-foreground">{c.paired ? `paired, ${plural(c.n_cells, 'cell')}` : 'unpaired'}</td>
											{#if anyP}
												<td class="num text-xs">{c.p_value != null ? formatP(c.p_value) : ''}</td>
												<td class="num text-xs">{c.p_adjusted != null ? formatP(c.p_adjusted) : ''}</td>
											{/if}
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
						{#snippet caption()}
							Group mean minus baseline mean (higher = more of the metric; shares in percentage points). Paired: bootstrap CI of the mean
							difference over common cells, Wilcoxon signed-rank p; unpaired: bootstrap CI of the difference of group means, no p.
							{#if !anyP && ctx.pWithheld} p-values are withheld while the results are provisional.{/if}
						{/snippet}
					</StudyFigure>
				{/if}
			</section>
		{/if}

		<!-- Blocks -->
		{#if results.blocks.length === 0}
			<EmptyState icon={ChartColumn} title="No block results yet" description="Results appear once participants submit assignments." />
		{/if}
		{#each results.blocks as block (block.block_id)}
			{@const no = sectionNo.blocks[block.block_id]}
			<section class="space-y-3">
				<SectionHeading number={no} title={block.title || BLOCK_TYPE_LABELS[block.type]} count={BLOCK_TYPE_LABELS[block.type]} />
				{#if block.type === 'rating'}
					<RatingResult result={block} section={no} {ctx} />
				{:else if block.type === 'requirements'}
					<RequirementsResult result={block} section={no} {ctx} />
				{:else if block.type === 'security'}
					<SecurityResult result={block} section={no} {ctx} />
				{:else if block.type === 'scenarios'}
					<ScenariosResult result={block} section={no} {ctx} />
				{:else if block.type === 'questionnaire'}
					<QuestionnaireResult result={block} section={no} {ctx} />
				{:else if block.type === 'bugs'}
					<BugsResult result={block} section={no} {ctx} />
				{:else if block.type === 'pairwise'}
					<PairwiseResult result={block} section={no} {ctx} />
				{/if}
			</section>
		{/each}

		<!-- Human vs automated -->
		{#if results.human_vs_automated.length > 0}
			<section class="space-y-3">
				<SectionHeading number={sectionNo.hva} title="Human vs automated measures" lead="Rank correlations between the human ratings and the platform’s automated measures, per app." />
				<StudyFigure id="tab-hva" kind="table" number="{sectionNo.hva}.1" title="Spearman correlations over apps" flush>
					<div class="overflow-x-auto">
						<table class="text-sm">
							<thead><tr><th>Measure</th><th class="num normal-case">Spearman ρ [95% CI]</th><th class="num">Apps</th></tr></thead>
							<tbody>
								{#each results.human_vs_automated as h (h.metric)}
									<tr>
										<td>
											{h.label}
											{#if h.note}<span class="block text-[11px] text-muted-foreground">{h.note}</span>{/if}
										</td>
										<td class="num text-xs"><span class="font-medium">{formatCoef(h.rho)}</span> <span class="text-muted-foreground">{formatCi(h.ci, (v) => formatCoef(v))}</span></td>
										<td class="num text-xs">{h.n}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
					{#snippet caption()}Bootstrap 95% intervals. Static findings per kLOC use only tools present for every app and are not deduplicated.{/snippet}
				</StudyFigure>
			</section>
		{/if}

		<!-- Data quality -->
		<section class="space-y-3">
			<SectionHeading number={sectionNo.quality} title="Data quality" lead="Per participant: work, timing and the quality flags behind the exclusion rules." />
			<StudyFigure id="tab-quality" kind="table" number="{sectionNo.quality}.1" title="Participants" flush>
				<div class="max-h-[32rem] overflow-auto">
					<table class="text-sm">
						<thead>
							<tr>
								<th>Participant</th>
								<th class="num">Submitted</th>
								<th class="num">Skipped</th>
								<th class="num">Median time</th>
								<th class="num" title="Assignments flagged as speeding">Speeding</th>
								<th class="num" title="Rating blocks answered with one value throughout">Straight-lining</th>
								<th class="num" title="Attention questions answered wrongly">Attention</th>
								<th>Flags</th>
							</tr>
						</thead>
						<tbody>
							{#each results.quality as q (q.participant_id)}
								<tr class={cn(q.excluded && 'bg-muted/30')}>
									<td>
										<span class="font-mono text-xs">{q.label}</span>
										<span class="ml-1 text-[11px] text-muted-foreground">{PARTICIPANT_KIND_LABELS[q.kind] ?? q.kind}</span>
										{#if q.excluded}<span class="ml-1 text-[11px] font-medium text-danger-fg">excluded</span>{/if}
									</td>
									<td class="num text-xs">{q.submitted}</td>
									<td class="num text-xs">{q.skipped}</td>
									<td class="num text-xs">{formatSeconds(q.median_seconds)}</td>
									<td class={cn('num text-xs', q.speeding > 0 && 'text-warning-fg')}>{q.speeding}</td>
									<td class={cn('num text-xs', q.straightlining > 0 && 'text-warning-fg')}>{q.straightlining}</td>
									<td class={cn('num text-xs', q.attention_failed > 0 && 'text-warning-fg')}>{q.attention_failed}</td>
									<td class="max-w-56"><FlagList flags={q.flags} /></td>
								</tr>
							{:else}
								<tr><td colspan="8" class="text-xs text-muted-foreground">No participants yet.</td></tr>
							{/each}
						</tbody>
					</table>
				</div>
			</StudyFigure>
		</section>

		<!-- Apps (de-blinding key) -->
		<section class="space-y-3">
			<SectionHeading number={sectionNo.apps} title="Apps" lead="The de-blinding key: blinded label → model, condition and run (researchers only)." />
			<StudyFigure id="tab-apps" kind="table" number="{sectionNo.apps}.1" title="Blinded labels and what they stand for" flush>
				<div class="max-h-[32rem] overflow-auto">
					<table class="text-sm">
						<thead>
							<tr><th>App</th><th>Model</th><th>Condition</th><th>Spec</th><th class="num">Submitted</th><th class="num" title="Skips because the platform could not provide the app">Unavailable</th></tr>
						</thead>
						<tbody>
							{#each results.apps as a (a.app_id)}
								<tr>
									<td class="font-mono text-xs">
										{#if a.run_id}<a class="hover:text-primary" href="/samples/{a.run_id}">{a.label}</a>{:else}{a.label}{/if}
									</td>
									<td class="max-w-48 truncate text-xs" title={a.model_name}>{a.model_name || '—'}</td>
									<td class="max-w-48 truncate text-xs" title={a.condition_label}>{a.condition_label || '—'}</td>
									<td class="font-mono text-[11px] text-muted-foreground">{a.app_slug || '—'}</td>
									<td class="num text-xs">{a.n_submitted}</td>
									<td class={cn('num text-xs', a.n_unavailable > 0 && 'text-warning-fg')}>{a.n_unavailable}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</StudyFigure>
		</section>
	{/if}
</div>
