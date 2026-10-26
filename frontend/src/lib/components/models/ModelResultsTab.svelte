<script lang="ts">
	/**
	 * Results tab: everything this platform measured for the model (the caller's
	 * jobs) — outcomes with Wilson 95% intervals, the rankings scores and
	 * positions, resource use, code findings and per-template results.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import ScanSearch from '@lucide/svelte/icons/scan-search';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { ScoreCell } from '$lib/components/table';
	import { severityColors } from '$lib/constants/colors';
	import { formatDuration } from '$lib/utils/formatters';
	import type { ModelInsights } from '$lib/api/client';
	import { compactNumber, fmtCI, fmtRate, fmtUsd } from './helpers';

	interface Props {
		insights: ModelInsights | null;
		error: string;
		onRetry: () => void;
		slug: string;
	}

	let { insights, error, onRetry, slug }: Props = $props();

	const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info'] as const;

	const gen = $derived(insights?.generation);
	const res = $derived(gen?.resources);
	const ranking = $derived(insights?.ranking);
	const analysis = $derived(insights?.analysis);

	const kpis = $derived.by(() => {
		if (!gen || !res) return [];
		return [
			{ label: 'Apps', value: String(gen.jobs_total), sub: Object.entries(gen.by_mode).map(([m, n]) => `${n} ${m}`).join(' · ') },
			{ label: 'Build success', value: fmtRate(gen.success.rate), sub: gen.success.n ? `${fmtCI(gen.success.ci_low, gen.success.ci_high)} · ${gen.success.successes}/${gen.success.n}` : 'nothing finished' },
			{
				label: 'Conformance',
				value: fmtRate(gen.functional.rate),
				sub: gen.functional.n ? `${fmtCI(gen.functional.ci_low, gen.functional.ci_high)} · ${gen.functional.successes}/${gen.functional.n}` : 'not evaluated',
			},
			{ label: 'Cost / app', value: fmtUsd(res.mean_cost), sub: `${fmtUsd(res.total_cost)} total` },
			{ label: 'Duration', value: formatDuration(res.median_duration, 0), sub: `median · mean ${formatDuration(res.mean_duration, 0)}` },
			{ label: 'Code / app', value: res.mean_lines_of_code != null ? `${compactNumber(res.mean_lines_of_code)} LOC` : '—', sub: res.cost_per_kloc != null ? `${fmtUsd(res.cost_per_kloc)} per KLOC` : '' },
		];
	});

	const scoreRows = $derived.by(() => {
		if (!ranking) return [];
		const s = ranking.scores;
		const p = ranking.positions;
		return [
			{ key: 'composite', label: 'Composite', help: 'Metadata score blended with measured quality', value: s.composite_score, pos: p.composite, strong: true },
			{ key: 'empirical', label: 'Measured quality', help: 'Findings density per KLOC + conformance pass rate', value: s.empirical_quality_score, pos: p.empirical },
			{ key: 'mss', label: 'Metadata score (MSS)', help: 'Adoption, benchmarks, cost and access', value: s.mss_score, pos: p.mss },
			{ key: 'benchmark', label: 'Benchmarks', help: 'Seeded benchmark scores', value: s.benchmark_score, pos: p.benchmark },
			{ key: 'cost', label: 'Cost efficiency', help: '', value: s.cost_efficiency_score, pos: null },
			{ key: 'access', label: 'Accessibility', help: '', value: s.accessibility_score, pos: null },
			{ key: 'adoption', label: 'Adoption', help: '', value: s.adoption_score, pos: null },
		];
	});

	const resourceFacts = $derived.by(() => {
		if (!gen || !res) return [];
		return [
			{ label: 'Prompt tokens / app', value: compactNumber(res.mean_prompt_tokens) },
			{ label: 'Completion tokens / app', value: compactNumber(res.mean_completion_tokens) },
			{ label: 'Reasoning tokens / app', value: compactNumber(res.mean_reasoning_tokens) },
			{ label: 'Prompt cache hits', value: fmtRate(res.cache_hit_ratio) },
			{ label: 'LLM calls / app', value: res.mean_llm_calls != null ? res.mean_llm_calls.toFixed(1) : '—' },
			{ label: 'Output speed', value: res.median_output_tps != null ? `${res.median_output_tps.toFixed(0)} tok/s` : '—', title: 'Median completion tokens per second of wall time (includes tool and build time)' },
			{ label: 'Repair round used', value: fmtRate(gen.repair_rate) },
			{ label: 'Continuations', value: String(gen.continuations_used) },
		];
	});

	const totalStatic = $derived(analysis ? SEVERITIES.reduce((a, s) => a + analysis.findings_static[s], 0) : 0);
	const totalAi = $derived(analysis ? SEVERITIES.reduce((a, s) => a + analysis.findings_ai[s], 0) : 0);
	const density = $derived(ranking?.variance?.density_per_kloc_mean);
</script>

{#snippet breakdown(title: string, data: Record<string, number>, unit: string)}
	{@const entries = Object.entries(data)}
	{@const max = Math.max(1, ...entries.map(([, n]) => n))}
	<div class="min-w-0">
		<div class="section-label mb-2">{title}</div>
		{#if entries.length === 0}
			<p class="text-xs text-muted-foreground">—</p>
		{:else}
			<ul class="space-y-1.5">
				{#each entries as [name, n] (name)}
					<li class="space-y-0.5 text-xs">
						<div class="flex items-baseline justify-between gap-2">
							<span class="truncate font-mono" title={name}>{name}</span>
							<span class="font-mono tabular-nums text-muted-foreground" title="{n} {unit}">{n}</span>
						</div>
						<span class="block h-1 rounded-full bg-muted" aria-hidden="true">
							<span class="block h-full rounded-full bg-muted-foreground/45" style:width="{(n / max) * 100}%"></span>
						</span>
					</li>
				{/each}
			</ul>
		{/if}
	</div>
{/snippet}

{#if error}
	<ErrorAlert message={error} onretry={onRetry} />
{:else if !insights || !gen || !res}
	<LoadingState label="loading results" />
{:else if gen.jobs_total === 0}
	<EmptyState icon={FlaskConical} title="No measurements yet" description="Generate apps with this model — or add it to an experiment — to see build success, conformance, code findings and its ranking here.">
		{#snippet action()}
			<div class="flex flex-wrap justify-center gap-2">
				<Button size="sm" variant="outline" href="/experiments/new">New experiment</Button>
				<Button size="sm" href="/sample-generator?mode=agent&model={encodeURIComponent(slug)}"><Sparkles class="size-3.5" />Generate an app</Button>
			</div>
		{/snippet}
	</EmptyState>
{:else}
	<div class="space-y-4">
		<div class="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
			{#each kpis as kpi (kpi.label)}
				<div class="kpi-card">
					<span class="kpi-label">{kpi.label}</span>
					<span class="font-mono text-xl font-medium tabular-nums">{kpi.value}</span>
					{#if kpi.sub}<span class="truncate text-[11px] text-muted-foreground" title={kpi.sub}>{kpi.sub}</span>{/if}
				</div>
			{/each}
		</div>
		<p class="text-[11px] text-muted-foreground">
			Rates show Wilson 95% intervals. Your jobs only{gen.first_job_at ? `, ${gen.first_job_at.slice(0, 10)} – ${gen.last_job_at?.slice(0, 10)}` : ''}.
		</p>

		<div class="grid gap-4 lg:grid-cols-2">
			<Card.Root class="gap-0 overflow-clip py-0">
				<Card.Header class="border-b border-border py-3">
					<Card.Title class="text-sm font-medium">Ranking</Card.Title>
					<Card.Description class="text-xs">
						Same scoring as <a href="/rankings" class="text-primary hover:underline">Rankings</a>; 0–100.
						{#if ranking}{ranking.models_exercised} model{ranking.models_exercised === 1 ? '' : 's'} measured.{/if}
					</Card.Description>
				</Card.Header>
				<Card.Content class="p-0">
					{#if !ranking}
						<p class="p-4 text-sm text-muted-foreground">Not in the rankings.</p>
					{:else}
						<div class="overflow-x-auto">
							<table>
								<thead>
									<tr><th>Score</th><th class="num">Value</th><th class="num">Position</th></tr>
								</thead>
								<tbody>
									{#each scoreRows as row (row.key)}
										<tr>
											<td title={row.help}>
												<span class={row.strong ? 'font-medium' : ''}>{row.label}</span>
												{#if row.help}<span class="cell-sub block text-[11px] text-muted-foreground">{row.help}</span>{/if}
											</td>
											<ScoreCell value={row.value} strong={row.strong} />
											<td class="num text-xs">
												{#if row.pos}<span class="text-foreground">#{row.pos.rank}</span><span class="text-muted-foreground"> / {row.pos.of}</span>{:else}<span class="text-muted-foreground">—</span>{/if}
											</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
						<p class="header-stats border-t border-border px-3 py-2">
							<span class="header-stat"><b>{ranking.n_trials}</b> trials</span>
							<span class="header-stat"><b>{compactNumber(ranking.local_loc)}</b> LOC</span>
							{#if density != null}
								<span class="header-stat" title="Severity-weighted static findings per 1000 lines, mean ± sd over analyzed jobs">
									<b>{density.toFixed(1)}</b>{#if ranking.variance?.density_per_kloc_stdev != null}± {ranking.variance.density_per_kloc_stdev.toFixed(1)}{/if} findings/KLOC
								</span>
							{/if}
							{#if ranking.variance?.smoke_pass_rate_stdev != null}
								<span class="header-stat" title="Standard deviation of the per-job conformance pass rate"><b>{ranking.variance.smoke_pass_rate_stdev.toFixed(2)}</b> pass-rate sd</span>
							{/if}
						</p>
					{/if}
				</Card.Content>
			</Card.Root>

			<Card.Root>
				<Card.Header class="pb-0"><Card.Title class="text-sm font-medium">Resource use</Card.Title><Card.Description class="text-xs">Means over completed apps</Card.Description></Card.Header>
				<Card.Content class="space-y-4">
					<dl class="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
						{#each resourceFacts as f (f.label)}
							<div class="flex items-baseline justify-between gap-2 border-b border-dashed border-border/60 pb-1" title={f.title}>
								<dt class="text-xs text-muted-foreground">{f.label}</dt>
								<dd class="font-mono text-xs tabular-nums">{f.value}</dd>
							</div>
						{/each}
					</dl>
					<div class="grid gap-4 sm:grid-cols-3">
						{@render breakdown('Harness', gen.engines, 'apps')}
						{@render breakdown('Served by', gen.providers, 'LLM calls')}
						{@render breakdown('Termination', gen.terminations, 'apps')}
					</div>
				</Card.Content>
			</Card.Root>
		</div>

		<Card.Root class="gap-0 overflow-clip py-0">
			<Card.Header class="border-b border-border py-3">
				<Card.Title class="text-sm font-medium">Code findings</Card.Title>
				<Card.Description class="text-xs">
					Latest analysis run of each app · {analysis?.analyzed_jobs ?? 0} of {gen.jobs_total} apps analyzed
				</Card.Description>
			</Card.Header>
			<Card.Content class="p-0">
				{#if !analysis || analysis.analyzed_jobs === 0}
					<div class="p-4">
						<EmptyState compact icon={ScanSearch} title="No analyzed apps" description="Run the analyzers on this model's apps to compare findings density with other models.">
							{#snippet action()}<Button size="sm" variant="outline" href="/analysis/create">Analyze apps</Button>{/snippet}
						</EmptyState>
					</div>
				{:else}
					<div class="grid gap-4 border-b border-border p-4 sm:grid-cols-2">
						{#each [['Static tools', analysis.findings_static, totalStatic], ['AI reviewer', analysis.findings_ai, totalAi]] as const as [label, counts, total] (label)}
							<div>
								<div class="section-label mb-2">{label} · {total}</div>
								<div class="flex flex-wrap gap-1.5">
									{#each SEVERITIES as sev (sev)}
										<Badge class="{severityColors[sev]} gap-1 font-mono tabular-nums {counts[sev] ? '' : 'opacity-40'}">{counts[sev]} {sev}</Badge>
									{/each}
								</div>
							</div>
						{/each}
					</div>
					<div class="grid lg:grid-cols-2 lg:divide-x lg:divide-border">
						<div class="overflow-x-auto">
							<table>
								<thead><tr><th>Tool</th><th class="num">Total</th><th class="num">Crit</th><th class="num">High</th><th class="num">Med</th><th class="num">Low</th></tr></thead>
								<tbody>
									{#each analysis.by_tool as t (t.tool)}
										<tr>
											<td class="font-mono text-xs">{t.tool}{#if t.ai}<Badge variant="outline" class="ml-1.5 h-4 px-1 text-[11px]">AI</Badge>{/if}</td>
											<td class="num">{t.total}</td>
											<td class="num {t.critical ? '' : 'text-muted-foreground'}">{t.critical}</td>
											<td class="num {t.high ? '' : 'text-muted-foreground'}">{t.high}</td>
											<td class="num {t.medium ? '' : 'text-muted-foreground'}">{t.medium}</td>
											<td class="num {t.low ? '' : 'text-muted-foreground'}">{t.low}</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
						<div class="overflow-x-auto border-t border-border lg:border-t-0">
							<table>
								<thead><tr><th>Most frequent rule</th><th>Severity</th><th class="num">Count</th></tr></thead>
								<tbody>
									{#each analysis.top_rules as r (`${r.tool}:${r.rule_id}:${r.severity}`)}
										<tr>
											<td class="max-w-[22rem]">
												<span class="block truncate text-xs" title={r.title}>{r.title}</span>
												<span class="cell-sub block font-mono text-[11px] text-muted-foreground">{r.tool} · {r.rule_id}</span>
											</td>
											<td><Badge class="{severityColors[r.severity] ?? severityColors.info} text-[11px]">{r.severity}</Badge></td>
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
			</Card.Content>
		</Card.Root>

		<div class="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
			<Card.Root class="gap-0 overflow-clip py-0">
				<Card.Header class="border-b border-border py-3">
					<Card.Title class="text-sm font-medium">By app spec</Card.Title>
					<Card.Description class="text-xs">Conformance = the hidden API suite passed</Card.Description>
				</Card.Header>
				<Card.Content class="p-0">
					{#if insights.templates.length === 0}
						<p class="p-4 text-sm text-muted-foreground">No app-spec runs (custom prompts only).</p>
					{:else}
						<div class="overflow-x-auto">
							<table>
								<thead><tr><th>App spec</th><th class="num">Apps</th><th class="num">Completed</th><th class="num">Conformance</th></tr></thead>
								<tbody>
									{#each insights.templates as t (t.slug)}
										<tr>
											<td>{t.name}<span class="cell-sub block font-mono text-[11px] text-muted-foreground">{t.slug}</span></td>
											<td class="num">{t.jobs}</td>
											<td class="num">{t.completed}</td>
											<td class="num">
												{#if t.functional.n}
													{fmtRate(t.functional.rate)}
													<span class="cell-sub block text-[11px] text-muted-foreground">{fmtCI(t.functional.ci_low, t.functional.ci_high)} · n={t.functional.n}</span>
												{:else}<span class="text-muted-foreground">—</span>{/if}
											</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					{/if}
				</Card.Content>
			</Card.Root>

			<Card.Root class="gap-0 overflow-clip py-0">
				<Card.Header class="border-b border-border py-3">
					<Card.Title class="text-sm font-medium">Experiments</Card.Title>
					<Card.Description class="text-xs">Designed experiments this model ran in</Card.Description>
				</Card.Header>
				<Card.Content class="p-0">
					{#if insights.experiments.length === 0}
						<p class="p-4 text-sm text-muted-foreground">Not part of an experiment yet.</p>
					{:else}
						<ul class="divide-y divide-border">
							{#each insights.experiments as e (e.id)}
								<li>
									<a href="/experiments/{e.id}" class="flex items-center justify-between gap-3 px-3 py-2 text-sm hover:bg-muted/40">
										<span class="min-w-0 truncate">{e.name}</span>
										<span class="flex shrink-0 items-center gap-2">
											<span class="font-mono text-xs tabular-nums text-muted-foreground">{e.jobs} apps</span>
											<StatusBadge status={e.status} />
										</span>
									</a>
								</li>
							{/each}
						</ul>
					{/if}
				</Card.Content>
			</Card.Root>
		</div>
	</div>
{/if}
