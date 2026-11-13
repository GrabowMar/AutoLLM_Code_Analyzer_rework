<script lang="ts">
	/**
	 * Results tab: everything this platform measured for the model (the caller's runs).
	 * Order follows what a reader asks first: the verdict (one sentence + headline figures), then
	 * conformance by task (bars with Wilson 95% whiskers), the ranking, resource use, code findings
	 * and the experiments it ran in.
	 * Findings counts by level are the normalized (heuristic) levels, labelled so, with findings of
	 * unrated tools apart; a rule shows the level its tool printed.
	 */
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import ScanSearch from '@lucide/svelte/icons/scan-search';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { severityColors } from '$lib/constants/colors';
	import LevelBadge from '$lib/components/analysis/LevelBadge.svelte';
	import type { NormalizedCounts } from '$lib/api/models';
	import { formatDuration } from '$lib/utils/formatters';
	import type { ModelInsights } from '$lib/api/client';
	import { compactNumber, fmtCI, fmtRate, fmtUsd } from './helpers';
	import { MSS_WEIGHTS } from '$lib/api/rankings';
	import { COMPONENTS, NO_DATA, exclusionLabel, weightLabel } from '$lib/components/rankings/mss';

	interface Props {
		insights: ModelInsights | null;
		error: string;
		onRetry: () => void;
		slug: string;
	}

	let { insights, error, onRetry, slug }: Props = $props();

	const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info'] as const;
	const KIND_LABEL: Record<string, string> = {
		app_agentic: 'agentic',
		slot_single_shot: 'slot single-shot',
		slot_agent: 'slot agent',
	};

	const gen = $derived(insights?.generation);
	const res = $derived(gen?.resources);
	const ranking = $derived(insights?.ranking);
	const analysis = $derived(insights?.analysis);

	/** The one-sentence verdict: only figures the API returned, nothing inferred. */
	const verdict = $derived.by(() => {
		if (!gen) return '';
		const tasks = insights?.tasks.length ?? 0;
		const parts = [`${gen.runs_total.toLocaleString()} apps${tasks ? ` on ${tasks} task${tasks === 1 ? '' : 's'}` : ''}`];
		if (gen.success.n) parts.push(`${fmtRate(gen.success.rate)} built and started`);
		if (gen.functional.n) parts.push(`${fmtRate(gen.functional.rate)} passed the hidden conformance suite (${fmtCI(gen.functional.ci_low, gen.functional.ci_high)}, n=${gen.functional.n})`);
		return parts.join(' · ');
	});

	const kinds = $derived(gen ? Object.entries(gen.by_kind).map(([k, n]) => `${n} ${KIND_LABEL[k] ?? k}`).join(' · ') : '');

	/**
	 * Ranking rows: the composite, measured quality, the MSS v2 and its four components, coverage.
	 * Every score is null when its input is missing and shows as "no data", never as 0; a model
	 * outside the eligible frame has no MSS (and so no composite) and says why.
	 */
	const exclusion = $derived(ranking?.exclusion ? exclusionLabel(ranking.exclusion.code) : null);
	const scoreRows = $derived.by(() => {
		if (!ranking) return [];
		const s = ranking.scores;
		const p = ranking.positions;
		const noMss = exclusion ? `excluded: ${exclusion.label.toLowerCase()}` : 'no popularity or price data';
		return [
			{ key: 'composite', label: 'Composite', help: 'MSS 60% + measured quality 40% where measured; equal to MSS otherwise; none without an MSS', value: s.composite, pos: p.composite, strong: true, sub: false, missing: noMss },
			{
				key: 'empirical',
				label: 'Measured quality',
				help: 'Code-issue density per KLOC of source (dependency advisories, dynamic-tool findings and test code left out; repeats averaged per app) + conformance pass rate',
				value: s.empirical,
				pos: p.empirical,
				strong: false,
				sub: false,
				missing: 'needs analyzed apps',
			},
			{
				key: 'mss',
				label: 'Model Selection Score',
				help: `MSS v2: ${COMPONENTS.map((c) => `${c.label.toLowerCase()} ${weightLabel(MSS_WEIGHTS[c.key])}`).join(', ')} on fixed bounds; a missing component is left out and the weights renormalised. Position among eligible models.`,
				value: s.mss,
				pos: p.mss,
				strong: false,
				sub: false,
				missing: noMss,
			},
			...COMPONENTS.map((c) => ({
				key: c.key,
				label: `${c.label} · ${weightLabel(MSS_WEIGHTS[c.key])}`,
				help: c.help,
				value: s[c.key],
				pos: c.key === 'capability' ? p.capability : null,
				strong: false,
				sub: true,
				missing: NO_DATA,
			})),
			{ key: 'coverage', label: 'Coverage', help: 'Share of the MSS weight whose input is present', value: s.coverage, pos: null, strong: false, sub: true, missing: '—' },
		];
	});

	const resourceFacts = $derived.by(() => {
		if (!gen || !res) return [];
		return [
			{ label: 'Prompt tokens', value: compactNumber(res.mean_prompt_tokens) },
			{ label: 'Completion tokens', value: compactNumber(res.mean_completion_tokens) },
			{ label: 'Reasoning tokens', value: compactNumber(res.mean_reasoning_tokens) },
			{ label: 'Prompt cache hits', value: fmtRate(res.cache_hit_ratio) },
			{ label: 'LLM calls', value: res.mean_llm_calls != null ? res.mean_llm_calls.toFixed(1) : '—' },
			{ label: 'Output speed', value: res.median_output_tps != null ? `${res.median_output_tps.toFixed(0)} tok/s` : '—', title: 'Median completion tokens per second of wall time (includes tool and build time)' },
			{ label: 'Source code', value: res.mean_lines_of_code != null ? `${compactNumber(res.mean_lines_of_code)} LOC` : '—' },
			{ label: 'Cost per KLOC', value: res.cost_per_kloc != null ? fmtUsd(res.cost_per_kloc) : '—' },
		];
	});

	const countOf = (c: NormalizedCounts | undefined) => (c ? SEVERITIES.reduce((a, s) => a + (c[s] ?? 0), 0) + (c.unrated ?? 0) : 0);
	/** Code findings, AI-reviewer opinions and dependency advisories (packages, not the model's code) apart. */
	const findingGroups = $derived(
		analysis
			? [
					{ label: 'Analyzers', counts: analysis.findings_static, total: countOf(analysis.findings_static) },
					{ label: 'AI reviewer', counts: analysis.findings_ai, total: countOf(analysis.findings_ai) },
					...(analysis.findings_dependency
						? [{ label: 'Dependency advisories', counts: analysis.findings_dependency, total: countOf(analysis.findings_dependency) }]
						: []),
				]
			: [],
	);
	const hasUnrated = $derived(!!analysis?.by_tool.some((t) => t.unrated !== undefined));
	const density = $derived(ranking?.variance?.density_per_kloc_mean);

	const STUDIES_PREVIEW = 6;
	let showAllStudies = $state(false);
	const studies = $derived(insights?.studies ?? []);
	const shownStudies = $derived(showAllStudies ? studies : studies.slice(0, STUDIES_PREVIEW));

	const pct = (v: number | null | undefined) => Math.max(0, Math.min(1, v ?? 0)) * 100;
</script>

{#snippet rate(r: { rate: number | null; ci_low: number | null; ci_high: number | null; n: number })}
	<span class="md-rate" aria-hidden="true">
		<span class="md-rate-fill" style:width="{pct(r.rate)}%"></span>
		{#if r.ci_low != null && r.ci_high != null}
			<span class="md-rate-ci" style:left="{pct(r.ci_low)}%" style:width="{Math.max(0.5, pct(r.ci_high) - pct(r.ci_low))}%"></span>
		{/if}
	</span>
{/snippet}

{#snippet breakdown(title: string, data: Record<string, number>, unit: string)}
	{@const entries = Object.entries(data).sort((a, b) => b[1] - a[1])}
	{@const max = Math.max(1, ...entries.map(([, n]) => n))}
	<div class="min-w-0">
		<div class="md-label mb-2">{title}</div>
		{#if entries.length === 0}
			<p class="text-xs text-muted-foreground">—</p>
		{:else}
			<ul class="space-y-1.5">
				{#each entries as [name, n] (name)}
					<li class="space-y-0.5 text-xs">
						<div class="flex items-baseline justify-between gap-2">
							<span class="truncate font-mono" title={name}>{name}</span>
							<span class="font-mono tabular-nums text-muted-foreground" title="{n} {unit}">{n.toLocaleString()}</span>
						</div>
						<span class="md-bar" aria-hidden="true"><span style:width="{(n / max) * 100}%"></span></span>
					</li>
				{/each}
			</ul>
		{/if}
	</div>
{/snippet}

{#snippet rankingSection()}
	<section class="md-sec" aria-labelledby="res-rank">
		<div class="md-sec-head">
			<h2 id="res-rank" class="md-sec-title">Ranking</h2>
			<span class="md-sec-desc">Same scoring as <a href="/rankings" class="text-primary hover:underline">Rankings</a>, 0–100; missing inputs say so, never 0</span>
		</div>
		{#if !ranking}
			<p class="md-sec-body text-sm text-muted-foreground">Not in the rankings.</p>
		{:else}
			<ul class="divide-y divide-border">
				{#each scoreRows as row (row.key)}
					<li class="rank-row" title={row.help}>
						<span class="min-w-0 truncate {row.sub ? 'pl-3 text-xs text-muted-foreground' : 'text-sm'} {row.strong ? 'font-medium' : ''}">{row.label}</span>
						{#if row.value == null}
							<span class="col-span-2 text-right text-[11px] text-muted-foreground">{row.missing || '—'}</span>
						{:else}
							<span class="flex items-center gap-2">
								<span class="md-bar w-16" aria-hidden="true"><span style:width="{pct(row.value)}%" class={row.strong ? 'rank-strong' : ''}></span></span>
								<span class="w-9 text-right font-mono text-xs tabular-nums {row.strong ? 'font-semibold' : ''}">{(row.value * 100).toFixed(1)}</span>
							</span>
							<span class="w-16 text-right font-mono text-[11px] tabular-nums">
								{#if row.pos}<span class="text-foreground">#{row.pos.rank}</span><span class="text-muted-foreground"> / {row.pos.of}</span>{:else}<span class="text-muted-foreground">—</span>{/if}
							</span>
						{/if}
					</li>
				{/each}
			</ul>
			<p class="md-note">
				{#if ranking.inherited_from}MSS of the canonical model <span class="font-mono">{ranking.inherited_from}</span> (this row is a route copy or dated member).{/if}
				{#if ranking.exclusion && exclusion}Outside the eligible frame at <span class="font-mono">{ranking.exclusion.step}</span>: {exclusion.label}{exclusion.target ? ` ${exclusion.target}` : ''}{ranking.exclusion.detail ? ` (${ranking.exclusion.detail})` : ''}.{/if}
				Scored over <b class="font-mono text-foreground">{ranking.n_trials}</b> completed apps;
				{#if ranking.variance?.n_apps}
					code density from <b class="font-mono text-foreground">{ranking.variance.n_apps}</b> analyzed apps ({compactNumber(ranking.local_loc)} LOC){#if density != null}, {density.toFixed(1)}{#if ranking.variance?.density_per_kloc_stdev != null} ± {ranking.variance.density_per_kloc_stdev.toFixed(1)}{/if} weighted findings/KLOC{/if}.
				{:else}
					no apps analyzed yet, so no measured quality.
				{/if}
				{#if ranking.variance?.smoke_pass_rate_stdev != null}Pass-rate sd over apps {ranking.variance.smoke_pass_rate_stdev.toFixed(2)}.{/if}
				{ranking.models_exercised} model{ranking.models_exercised === 1 ? '' : 's'} measured on this platform.
			</p>
		{/if}
	</section>
{/snippet}

{#if error}
	<ErrorAlert message={error} onretry={onRetry} />
{:else if !insights || !gen || !res}
	<div class="space-y-4" aria-busy="true" aria-label="Loading results">
		<section class="md-sec">
			<div class="md-sec-head"><span class="md-skel h-4 w-28"></span><span class="md-skel h-3 w-64"></span></div>
			<div class="md-figs">
				{#each Array(6) as _, i (i)}
					<div class="md-fig"><span class="md-skel h-2.5 w-16"></span><span class="md-skel mt-1 h-6 w-14"></span><span class="md-skel h-2.5 w-20"></span></div>
				{/each}
			</div>
		</section>
		<div class="grid gap-4 lg:grid-cols-2">
			{#each [0, 1] as i (i)}
				<section class="md-sec"><div class="md-sec-body space-y-3">{#each Array(4) as _, j (j)}<span class="md-skel h-3 w-full"></span>{/each}</div></section>
			{/each}
		</div>
	</div>
{:else if gen.runs_total === 0}
	<EmptyState icon={FlaskConical} title="No measurements yet" description="Generate apps with this model — or add it to an experiment — to see build success, conformance, code findings and measured quality here.">
		{#snippet action()}
			<div class="flex flex-wrap justify-center gap-2">
				<Button size="sm" variant="outline" href="/experiments/new">New experiment</Button>
				<Button size="sm" href="/generate?model={encodeURIComponent(slug)}"><Sparkles class="size-3.5" />Generate an app</Button>
			</div>
		{/snippet}
	</EmptyState>
	{#if ranking}<div class="mt-4 max-w-2xl">{@render rankingSection()}</div>{/if}
{:else}
	<div class="space-y-4">
		<!-- 1. Verdict -->
		<section class="md-sec" aria-labelledby="res-verdict">
			<div class="md-sec-head">
				<h2 id="res-verdict" class="md-sec-title">Verdict</h2>
				<span class="md-sec-desc">Your runs{gen.first_run_at ? ` · ${gen.first_run_at.slice(0, 10)} – ${gen.last_run_at?.slice(0, 10)}` : ''}</span>
			</div>
			<p class="verdict">{verdict}.</p>
			<dl class="md-figs border-t border-border">
				<div class="md-fig">
					<dt class="md-label">Apps</dt>
					<dd class="md-val">{gen.runs_total.toLocaleString()}</dd>
					<dd class="md-sub" title={kinds}>{kinds || `${gen.completed} completed`}</dd>
				</div>
				<div class="md-fig" title="Built and started (Wilson 95% interval)">
					<dt class="md-label">Build success</dt>
					<dd class="md-val">{fmtRate(gen.success.rate)}</dd>
					<dd class="md-sub">{gen.success.n ? `${fmtCI(gen.success.ci_low, gen.success.ci_high)} · ${gen.success.successes}/${gen.success.n}` : 'nothing finished'}</dd>
				</div>
				<div class="md-fig" title="Passed the hidden API conformance suite (Wilson 95% interval)">
					<dt class="md-label">Conformance</dt>
					<dd class="md-val">{fmtRate(gen.functional.rate)}</dd>
					<dd class="md-sub">{gen.functional.n ? `${fmtCI(gen.functional.ci_low, gen.functional.ci_high)} · ${gen.functional.successes}/${gen.functional.n}` : 'not evaluated'}</dd>
				</div>
				<div class="md-fig">
					<dt class="md-label">Cost / app</dt>
					<dd class="md-val">{fmtUsd(res.mean_cost)}</dd>
					<dd class="md-sub">{fmtUsd(res.total_cost)} total billed</dd>
				</div>
				<div class="md-fig">
					<dt class="md-label">Duration</dt>
					<dd class="md-val">{formatDuration(res.median_duration, 0)}</dd>
					<dd class="md-sub">median · mean {formatDuration(res.mean_duration, 0)}</dd>
				</div>
				<div class="md-fig">
					<dt class="md-label">Code findings</dt>
					<dd class="md-val {density == null ? 'is-muted' : ''}">{density != null ? density.toFixed(1) : '—'}</dd>
					<dd class="md-sub">{density != null ? 'weighted / KLOC' : `${analysis?.analyzed_runs ?? 0} apps analyzed`}</dd>
				</div>
			</dl>
			<p class="md-note">Rates show Wilson 95% intervals (bar = estimate, whisker = interval). Descriptive: your runs only, not a designed comparison.</p>
		</section>

		<!-- 2. By task + ranking -->
		<div class="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
			<section class="md-sec" aria-labelledby="res-tasks">
				<div class="md-sec-head">
					<h2 id="res-tasks" class="md-sec-title">Conformance by task</h2>
					<span class="md-sec-desc">Share of evaluated apps that passed the hidden API suite</span>
				</div>
				{#if insights.tasks.length === 0}
					<p class="md-sec-body text-sm text-muted-foreground">No task runs yet.</p>
				{:else}
					<ul class="divide-y divide-border">
						{#each insights.tasks as t (t.slug)}
							<li class="task-row">
								<div class="min-w-0">
									<div class="truncate text-sm font-medium">{t.name}</div>
									<div class="truncate font-mono text-[11px] text-muted-foreground">{t.slug} · {t.runs} apps · {t.completed} completed</div>
								</div>
								<div class="task-rate">
									{#if t.functional.n}
										{@render rate(t.functional)}
									{:else}
										<span class="text-xs text-muted-foreground">not evaluated</span>
									{/if}
								</div>
								<div class="task-num">
									<span class="font-mono text-sm font-medium tabular-nums">{t.functional.n ? fmtRate(t.functional.rate) : '—'}</span>
									{#if t.functional.n}<span class="block font-mono text-[11px] text-muted-foreground tabular-nums">{fmtCI(t.functional.ci_low, t.functional.ci_high)} · n={t.functional.n}</span>{/if}
								</div>
							</li>
						{/each}
					</ul>
				{/if}
			</section>

			{@render rankingSection()}
		</div>

		<!-- 3. Resource use -->
		<section class="md-sec" aria-labelledby="res-resources">
			<div class="md-sec-head">
				<h2 id="res-resources" class="md-sec-title">Resource use</h2>
				<span class="md-sec-desc">Means per completed app</span>
			</div>
			<dl class="md-figs res-figs">
				{#each resourceFacts as f (f.label)}
					<div class="md-fig" title={f.title}>
						<dt class="md-label">{f.label}</dt>
						<dd class="font-mono text-base font-medium tabular-nums">{f.value}</dd>
					</div>
				{/each}
			</dl>
			<div class="grid gap-6 border-t border-border p-5 sm:grid-cols-3">
				{@render breakdown('Harness', gen.harnesses, 'apps')}
				{@render breakdown('Served by', gen.providers, 'LLM calls')}
				{@render breakdown('Termination', gen.terminations, 'apps')}
			</div>
		</section>

		<!-- 4. Code findings -->
		<section class="md-sec" aria-labelledby="res-findings">
			<div class="md-sec-head">
				<h2 id="res-findings" class="md-sec-title">Code findings</h2>
				<span class="md-sec-desc">Latest analysis run of each app · {analysis?.analyzed_runs ?? 0} of {gen.runs_total} apps analyzed</span>
			</div>
			{#if !analysis || analysis.analyzed_runs === 0}
				<div class="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
					<div class="flex items-center gap-3">
						<ScanSearch class="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
						<p class="text-sm text-muted-foreground">No analyzed apps yet. Run the analyzers on this model's apps to compare findings density with other models.</p>
					</div>
					<Button size="sm" variant="outline" href="/analysis/create">Analyze apps</Button>
				</div>
			{:else}
				<div class="border-b border-border p-4">
					<p class="mb-3 text-xs text-muted-foreground" title={analysis.severity_note}>
						By normalized level (heuristic): a mapping across tools, not the levels the tools printed; findings of tools that print no level are counted as
						unrated, never given one.
					</p>
					<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
						{#each findingGroups as group (group.label)}
							<div>
								<div class="md-label mb-2">{group.label} · {group.total}</div>
								<div class="flex flex-wrap gap-1.5">
									{#each SEVERITIES as sev (sev)}
										<Badge class="{severityColors[sev]} gap-1 font-mono tabular-nums {group.counts[sev] ? '' : 'opacity-40'}">{group.counts[sev] ?? 0} {sev}</Badge>
									{/each}
									{#if group.counts.unrated !== undefined}
										<Badge variant="outline" class="gap-1 font-mono tabular-nums text-muted-foreground {group.counts.unrated ? '' : 'opacity-40'}">{group.counts.unrated} unrated</Badge>
									{/if}
								</div>
							</div>
						{/each}
					</div>
				</div>
				<div class="grid lg:grid-cols-2 lg:divide-x lg:divide-border">
					<div class="overflow-x-auto">
						<table>
							<thead>
								<tr>
									<th rowspan="2">Tool</th>
									<th rowspan="2" class="num">Total</th>
									<th colspan={hasUnrated ? 5 : 4} class="text-center" title="A heuristic mapping across tools, not the levels the tools printed">Normalized level (heuristic)</th>
								</tr>
								<tr>
									<th class="num">Crit</th><th class="num">High</th><th class="num">Med</th><th class="num">Low</th>
									{#if hasUnrated}<th class="num" title="The tool printed no level">Unrated</th>{/if}
								</tr>
							</thead>
							<tbody>
								{#each analysis.by_tool as t (t.tool)}
									{@const printed = Object.entries(t.tool_severities ?? {}).map(([l, n]) => `${l}: ${n}`).join(', ')}
									<tr>
										<td class="font-mono text-xs" title={printed ? `Levels the tool printed — ${printed}` : undefined}>{t.tool}{#if t.ai}<Badge variant="outline" class="ml-1.5 h-4 px-1 text-[11px]">AI</Badge>{/if}</td>
										<td class="num">{t.total}</td>
										<td class="num {t.critical ? '' : 'text-muted-foreground'}">{t.critical}</td>
										<td class="num {t.high ? '' : 'text-muted-foreground'}">{t.high}</td>
										<td class="num {t.medium ? '' : 'text-muted-foreground'}">{t.medium}</td>
										<td class="num {t.low ? '' : 'text-muted-foreground'}">{t.low}</td>
										{#if hasUnrated}<td class="num {t.unrated ? '' : 'text-muted-foreground'}">{t.unrated ?? 0}</td>{/if}
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
					<div class="overflow-x-auto border-t border-border lg:border-t-0">
						<table>
							<thead><tr><th>Most frequent rule</th><th title="As the tool printed it">Level (tool)</th><th class="num">Count</th></tr></thead>
							<tbody>
								{#each analysis.top_rules as r, i (`${r.tool}:${r.rule_id}:${i}`)}
									<tr>
										<td class="max-w-[22rem]">
											<span class="block truncate text-xs" title={r.title}>{r.title}</span>
											<span class="cell-sub block font-mono text-[11px] text-muted-foreground">{r.tool} · {r.rule_id}</span>
										</td>
										<td>
											{#if r.tool_severity && r.tool_severity !== 'unrated'}
												{#each r.tool_severity.split(' / ') as label (label)}<LevelBadge {label} class="mr-1" />{/each}
											{:else}
												<span class="text-[11px] text-muted-foreground" title={r.tool_severity ? 'The tool prints no level' : 'Recorded before tools’ own levels were reported here'}>{r.tool_severity ? 'unrated' : '—'}</span>
											{/if}
										</td>
										<td class="num">{r.count}</td>
									</tr>
								{:else}
									<tr><td colspan="3" class="text-xs text-muted-foreground">No rule ids reported.</td></tr>
								{/each}
							</tbody>
						</table>
					</div>
				</div>
			{/if}
		</section>

		<!-- 5. Experiments -->
		<section class="md-sec" aria-labelledby="res-studies">
			<div class="md-sec-head">
				<h2 id="res-studies" class="md-sec-title">Experiments</h2>
				<span class="md-sec-desc">Experiments this model ran in</span>
				{#if studies.length}<span class="md-sec-aside font-mono">{studies.length}</span>{/if}
			</div>
			{#if studies.length === 0}
				<p class="md-sec-body text-sm text-muted-foreground">Not part of an experiment yet.</p>
			{:else}
				<ul class="study-grid">
					{#each shownStudies as e (e.id)}
						<li>
							<a href="/experiments/{e.id}" class="study-row">
								<span class="min-w-0 truncate text-sm" title={e.name}>{e.name}</span>
								<span class="flex shrink-0 items-center gap-2">
									<span class="font-mono text-[11px] tabular-nums text-muted-foreground">{e.runs} apps</span>
									<StatusBadge status={e.status} />
								</span>
							</a>
						</li>
					{/each}
				</ul>
				{#if studies.length > STUDIES_PREVIEW}
					<button type="button" class="md-more" aria-expanded={showAllStudies} onclick={() => (showAllStudies = !showAllStudies)}>
						{showAllStudies ? 'Show fewer' : `Show all ${studies.length}`}
					</button>
				{/if}
			{/if}
		</section>
	</div>
{/if}

<style>
	.verdict {
		padding: 0.875rem 1.25rem 1rem;
		font-family: var(--font-display);
		font-size: 1.0625rem;
		line-height: 1.5;
		text-wrap: pretty;
	}
	.task-row {
		display: grid;
		grid-template-columns: minmax(0, 1.2fr) minmax(6rem, 1fr) 6.5rem;
		align-items: center;
		gap: 1rem;
		padding: 0.75rem 1.25rem;
	}
	.task-num {
		text-align: right;
	}
	.rank-row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto auto;
		align-items: center;
		gap: 0.75rem;
		padding: 0.5rem 1.25rem;
	}
	:global(.md-bar > span.rank-strong) {
		background: var(--primary);
	}
	.res-figs {
		grid-template-columns: repeat(4, minmax(0, 1fr));
	}
	.res-figs > :global(.md-fig:nth-child(4n)) {
		border-right: 0;
	}
	.res-figs > :global(.md-fig:nth-child(n + 5)) {
		border-top: 1px solid var(--border);
	}
	.res-figs :global(.md-label) {
		white-space: nowrap;
	}
	.study-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(min(100%, 32rem), 1fr));
	}
	.study-grid > li {
		border-bottom: 1px solid var(--border);
	}
	.study-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		padding: 0.5rem 1.25rem;
		transition: background-color 150ms;
	}
	.study-row:hover {
		background: var(--surface-2, var(--muted));
	}
	@media (max-width: 640px) {
		.task-row {
			grid-template-columns: minmax(0, 1fr) 5.5rem;
			padding-inline: 1rem;
		}
		.task-rate {
			grid-column: 1 / -1;
			grid-row: 2;
		}
		.rank-row,
		.study-row {
			padding-inline: 1rem;
		}
		.res-figs {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
		.res-figs > :global(.md-fig:nth-child(2n)) {
			border-right: 0;
		}
		.res-figs > :global(.md-fig:nth-child(n + 3)) {
			border-top: 1px solid var(--border);
		}
	}
</style>
