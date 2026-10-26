<script lang="ts">
	/**
	 * RunOverview — a run at a glance: what it covered (tools measured / failed /
	 * skipped, code size, time), what needs attention (failed tools, dynamic tools
	 * skipped because the app was down, truncated generated code), findings by the
	 * level each tool reported and by tool, the most affected files and rules, and
	 * the metric tools' figures. Every count links into the Findings tab.
	 *
	 * Props: `run`, `tools`, `results` (by slug), `facets` (null while loading),
	 * `onfilter(partial filters)`, `ontab(tab)`, `onrerununmeasured()`, `rerunning`.
	 */
	import type { AnalyzerTool } from '$lib/api/analyzers';
	import type { AnalysisRunDetail, FindingFacets, RunToolResult } from '$lib/api/runs';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import CircleX from '@lucide/svelte/icons/circle-x';
	import Globe from '@lucide/svelte/icons/globe';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import ToolIcon from '$lib/components/analyzers/ToolIcon.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import LevelBadge from './LevelBadge.svelte';
	import MetricsPanel from './MetricsPanel.svelte';
	import type { FindingFilters } from './FindingsExplorer.svelte';
	import { formatSpan, isActive, resultFindingCount, resultState } from './format';

	interface Props {
		run: AnalysisRunDetail;
		tools: Map<string, AnalyzerTool>;
		results: Map<string, RunToolResult>;
		facets: FindingFacets | null;
		onfilter: (filters: Partial<FindingFilters>) => void;
		ontab: (tab: string) => void;
		onrerununmeasured: () => void;
		rerunning?: boolean;
	}

	let { run, tools, results, facets, onfilter, ontab, onrerununmeasured, rerunning = false }: Props = $props();

	const states = $derived(run.tool_slugs.map((slug) => ({ slug, result: results.get(slug), state: resultState(results.get(slug)) })));
	const measured = $derived(states.filter((s) => s.state.measured).length);
	const failed = $derived(states.filter((s) => s.result?.status === 'failed'));
	const appDown = $derived(states.filter((s) => s.result?.summary?.skip_reason === 'app_not_running'));
	const notApplicable = $derived(states.filter((s) => s.result?.summary?.skip_reason === 'not_applicable'));
	const unmeasured = $derived(states.filter((s) => s.result && !s.state.measured && !s.state.live));
	const active = $derived(isActive(run.status));

	const total = $derived(facets?.total ?? (run.summary?.total_findings as number | undefined) ?? 0);
	const levelMax = $derived(Math.max(1, ...(facets?.levels.map((l) => l.count) ?? [1]), facets?.unrated ?? 0));
	const byTool = $derived(
		states
			.map((s) => ({ slug: s.slug, count: resultFindingCount(s.result) }))
			.filter((t) => t.count > 0)
			.sort((a, b) => b.count - a.count)
	);
	const toolMax = $derived(Math.max(1, ...byTool.map((t) => t.count)));
	const codeLines = $derived(run.source_files.reduce((n, f) => n + f.lines, 0));
	const truncated = $derived((run.summary?.truncated_inputs ?? []) as string[]);
	const metrics = $derived((run.summary?.metrics_by_tool ?? {}) as Record<string, Record<string, any>>);
	const hasMetrics = $derived(Object.values(metrics).some((m) => m && Object.keys(m).length > 0));

	function toolName(slug: string) {
		return tools.get(slug)?.name ?? slug;
	}
</script>

<div class="space-y-4">
	{#if truncated.length > 0 || failed.length > 0 || appDown.length > 0}
		<div class="space-y-2">
			{#if truncated.length > 0}
				<div class="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning-fg" role="note">
					<TriangleAlert class="mt-0.5 size-4 shrink-0" />
					<p>The generated {truncated.join(' and ')} code was cut off at the model's token limit: these findings describe an incomplete app.</p>
				</div>
			{/if}
			{#if failed.length > 0}
				<div class="flex flex-wrap items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-danger-fg" role="note">
					<CircleX class="mt-0.5 size-4 shrink-0" />
					<p class="min-w-0 flex-1">
						{failed.length} tool{failed.length === 1 ? '' : 's'} failed and measured nothing: {failed.map((f) => toolName(f.slug)).join(', ')}.
						Their absence is not a clean result.
					</p>
					<Button variant="outline" size="sm" class="h-7 text-xs" onclick={() => ontab('tools')}>See why</Button>
				</div>
			{/if}
			{#if appDown.length > 0}
				<div class="flex flex-wrap items-start gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning-fg" role="note">
					<Globe class="mt-0.5 size-4 shrink-0" />
					<p class="min-w-0 flex-1">
						{appDown.length} dynamic tool{appDown.length === 1 ? ' was' : 's were'} skipped because the app wasn't running ({appDown.map((f) => toolName(f.slug)).join(', ')}).
						{#if run.target.job_id}Start it on the <a class="underline underline-offset-2" href="/applications/{run.target.job_id}">app page</a>, then re-run them.{/if}
					</p>
				</div>
			{/if}
		</div>
	{/if}

	<div class="grid grid-cols-2 gap-3 md:grid-cols-4">
		<button type="button" class="kpi-card cursor-pointer text-left hover:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none" style="--kpi-accent: var(--chart-1)" onclick={() => onfilter({})}>
			<span class="kpi-label">Findings{active ? ' so far' : ''}</span>
			<span class="kpi-value">{total}</span>
			<span class="text-[11px] text-muted-foreground">{facets?.unrated ? `${facets.unrated} not rated by their tool` : 'open the list'}</span>
		</button>
		<button type="button" class="kpi-card cursor-pointer text-left hover:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none" style="--kpi-accent: var(--chart-2)" onclick={() => ontab('tools')}>
			<span class="kpi-label">Tools measured</span>
			<span class="kpi-value">{measured}<span class="text-base text-muted-foreground">/{run.tool_slugs.length}</span></span>
			<span class="text-[11px] text-muted-foreground">
				{#if failed.length}{failed.length} failed{:else if notApplicable.length}{notApplicable.length} had nothing to read{:else}all ran{/if}
			</span>
		</button>
		<div class="kpi-card" style="--kpi-accent: var(--chart-3)">
			<span class="kpi-label">Code</span>
			{#if run.source_files.length > 0}
				<span class="kpi-value">{run.source_files.length}<span class="text-base text-muted-foreground"> files</span></span>
				<span class="truncate text-[11px] text-muted-foreground">{codeLines.toLocaleString()} lines{run.target.stack_slug ? ` · ${run.target.stack_slug}` : ''}</span>
			{:else}
				<span class="kpi-value text-base">{run.target.kind === 'deleted' ? 'App deleted' : '—'}</span>
				<span class="truncate text-[11px] text-muted-foreground">{run.target.stack_slug ?? ''}</span>
			{/if}
		</div>
		<div class="kpi-card" style="--kpi-accent: var(--chart-5)">
			<span class="kpi-label">Duration</span>
			<span class="kpi-value">{formatSpan(run.duration_seconds)}</span>
			<span class="text-[11px] text-muted-foreground">{run.tool_slugs.length} tool{run.tool_slugs.length === 1 ? '' : 's'} in sequence</span>
		</div>
	</div>

	<div class="grid items-start gap-4 lg:grid-cols-2">
		<Card.Root class="gap-3">
			<Card.Header>
				<Card.Title class="text-base">Levels as the tools reported them</Card.Title>
				<Card.Description class="text-xs">Each tool's own words; tools that print no level are counted apart, never rated for them.</Card.Description>
			</Card.Header>
			<Card.Content>
				{#if !facets}
					<LoadingState label="loading levels" class="py-4" />
				{:else if facets.levels.length === 0 && facets.unrated === 0}
					<p class="text-sm text-muted-foreground">No findings.</p>
				{:else}
					{#if facets.levels.length === 0 && run.summary?.reported_severity_counts === undefined && !active}
						<p class="mb-2 text-xs text-muted-foreground">This run was recorded before tools' own levels were stored; re-run it to see them.</p>
					{/if}
					<ul class="space-y-1.5">
						{#each facets.levels as l (l.value)}
							<li>
								<button type="button" class="group grid w-full cursor-pointer grid-cols-[7rem_minmax(0,1fr)_3rem] items-center gap-2 text-left" onclick={() => onfilter({ level: l.value })}>
									<LevelBadge label={l.value} class="justify-self-start" />
									<span class="h-2 rounded-sm bg-muted"><span class="block h-2 rounded-sm bg-foreground/50 transition-colors group-hover:bg-primary" style="width: {(l.count / levelMax) * 100}%"></span></span>
									<span class="text-right font-mono text-xs tabular-nums">{l.count}</span>
								</button>
							</li>
						{/each}
						{#if facets.unrated > 0}
							<li>
								<button type="button" class="group grid w-full cursor-pointer grid-cols-[7rem_minmax(0,1fr)_3rem] items-center gap-2 text-left" onclick={() => onfilter({ level: 'unrated' })}>
									<span class="text-xs text-muted-foreground">Not rated</span>
									<span class="h-2 rounded-sm bg-muted"><span class="block h-2 rounded-sm bg-muted-foreground/40 transition-colors group-hover:bg-primary" style="width: {(facets.unrated / levelMax) * 100}%"></span></span>
									<span class="text-right font-mono text-xs tabular-nums">{facets.unrated}</span>
								</button>
							</li>
						{/if}
					</ul>
				{/if}
			</Card.Content>
		</Card.Root>

		<Card.Root class="gap-3">
			<Card.Header>
				<Card.Title class="text-base">Findings by tool</Card.Title>
				<Card.Description class="text-xs">Tools overlap (e.g. three dependency auditors report the same CVE); compare tools, don't sum them.</Card.Description>
			</Card.Header>
			<Card.Content>
				{#if byTool.length === 0}
					<p class="text-sm text-muted-foreground">{active ? 'No findings yet.' : 'No tool reported findings.'}</p>
				{:else}
					<ul class="space-y-1.5">
						{#each byTool as t (t.slug)}
							<li>
								<button type="button" class="group grid w-full cursor-pointer grid-cols-[9rem_minmax(0,1fr)_3rem] items-center gap-2 text-left" onclick={() => onfilter({ tool: t.slug })}>
									<span class="flex min-w-0 items-center gap-1.5 text-xs"><ToolIcon tool={tools.get(t.slug) ?? { slug: t.slug }} size="xs" /><span class="truncate">{toolName(t.slug)}</span></span>
									<span class="h-2 rounded-sm bg-muted"><span class="block h-2 rounded-sm bg-[var(--chart-1)] transition-opacity group-hover:opacity-80" style="width: {(t.count / toolMax) * 100}%"></span></span>
									<span class="text-right font-mono text-xs tabular-nums">{t.count}</span>
								</button>
							</li>
						{/each}
					</ul>
				{/if}
			</Card.Content>
		</Card.Root>

		{#if facets && facets.files.length > 0}
			<Card.Root class="gap-0 overflow-clip py-0">
				<Card.Header class="border-b border-border py-3">
					<Card.Title class="text-base">Most affected files</Card.Title>
				</Card.Header>
				<Card.Content class="p-0">
					<ul class="divide-y divide-border/60">
						{#each facets.files.slice(0, 8) as file (file.value)}
							<li>
								<button type="button" class="flex w-full cursor-pointer items-center gap-3 px-4 py-1.5 text-left text-xs hover:bg-muted/50" onclick={() => onfilter({ file: file.value })}>
									<span class="min-w-0 flex-1 truncate font-mono" title={file.value}>{file.value}</span>
									<span class="font-mono tabular-nums text-muted-foreground">{file.count}</span>
								</button>
							</li>
						{/each}
					</ul>
				</Card.Content>
			</Card.Root>
		{/if}

		{#if facets && facets.rules.length > 0}
			<Card.Root class="gap-0 overflow-clip py-0">
				<Card.Header class="border-b border-border py-3">
					<Card.Title class="text-base">Most frequent rules</Card.Title>
				</Card.Header>
				<Card.Content class="p-0">
					<ul class="divide-y divide-border/60">
						{#each facets.rules.slice(0, 8) as rule (rule.value)}
							<li>
								<button type="button" class="flex w-full cursor-pointer items-center gap-3 px-4 py-1.5 text-left text-xs hover:bg-muted/50" onclick={() => onfilter({ rule: rule.value })}>
									<span class="min-w-0 flex-1">
										<span class="block truncate font-mono">{rule.value}</span>
										{#if rule.label}<span class="block truncate text-muted-foreground">{rule.label}</span>{/if}
									</span>
									<span class="font-mono tabular-nums text-muted-foreground">{rule.count}</span>
								</button>
							</li>
						{/each}
					</ul>
				</Card.Content>
			</Card.Root>
		{/if}
	</div>

	{#if hasMetrics}
		<Card.Root class="gap-3">
			<Card.Header>
				<Card.Title class="text-base">Metrics</Card.Title>
				<Card.Description class="text-xs">Aggregate figures from complexity and duplication tools.</Card.Description>
			</Card.Header>
			<Card.Content class="@container">
				<MetricsPanel {metrics} {tools} />
			</Card.Content>
		</Card.Root>
	{/if}

	{#if unmeasured.length > 0 && !active}
		<div class="flex flex-wrap items-center gap-3 rounded-md border border-border bg-card px-4 py-3 text-sm">
			<p class="min-w-0 flex-1 text-muted-foreground">
				{unmeasured.length} tool{unmeasured.length === 1 ? '' : 's'} measured nothing ({unmeasured.map((u) => u.state.label.toLowerCase()).filter((v, i, a) => a.indexOf(v) === i).join(', ')}).
				Re-running only those keeps this run's other results.
			</p>
			<Button size="sm" variant="outline" disabled={rerunning} onclick={onrerununmeasured}>
				{#if rerunning}<LoaderCircle class="size-3.5 animate-spin motion-reduce:animate-none" />{:else}<RotateCcw class="size-3.5" />{/if}
				Re-run {unmeasured.length} tool{unmeasured.length === 1 ? '' : 's'}
			</Button>
		</div>
	{/if}
</div>
