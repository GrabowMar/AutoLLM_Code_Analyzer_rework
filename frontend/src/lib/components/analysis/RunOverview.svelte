<script lang="ts">
	/**
	 * RunOverview — a run at a glance: what it covered (tools measured / failed /
	 * skipped, code size, time), what needs attention (failed tools, dynamic tools
	 * skipped because the app was down, a truncated generation run), findings by the
	 * level each tool reported and by tool, the most affected files and rules, and
	 * the metric tools' figures. Every count links into the Findings tab.
	 *
	 * With the run's context (`GET runs/{id}/context/`): code size is the study's
	 * source LOC of the model scope by layer, with the whole app tree's LOC beside
	 * it (stored files the tools never saw noted apart), code
	 * findings in test files are counted apart, each frequent rule shows how
	 * common it is across the corpus, and the replicate context card compares
	 * the app with the other runs of its setup.
	 *
	 * Props: `run`, `tools`, `results` (by slug), `facets` (null while loading),
	 * `context` (null while loading or for an active run), `contextLoading`,
	 * `contextError`, `oncontextscope(scope)`, `onretrycontext()`,
	 * `onfilter(partial filters)`, `ontab(tab)`, `onrerununmeasured()`, `rerunning`.
	 */
	import type { AnalyzerTool } from '$lib/api/analyzers';
	import type { AnalysisRunDetail, ContextScope, FindingFacets, RunContext, RunToolResult } from '$lib/api/runs';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import CircleX from '@lucide/svelte/icons/circle-x';
	import Globe from '@lucide/svelte/icons/globe';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import FolderMinus from '@lucide/svelte/icons/folder-minus';
	import ToolIcon from '$lib/components/analyzers/ToolIcon.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { Segmented } from '$lib/components/ui/segmented';
	import SameSetupPanel from '$lib/components/applications/SameSetupPanel.svelte';
	import LevelBadge from './LevelBadge.svelte';
	import MetricsPanel from './MetricsPanel.svelte';
	import type { FindingFilters } from './FindingsExplorer.svelte';
	import {
		excludedPathsNote,
		formatSpan,
		isActive,
		nonCodeText,
		prevalenceIndex,
		prevalenceTitle,
		resultFindingCount,
		resultState,
		rulePrevalenceLines,
		runScopeCounts,
		sortLevels,
	} from './format';

	interface Props {
		run: AnalysisRunDetail;
		tools: Map<string, AnalyzerTool>;
		results: Map<string, RunToolResult>;
		facets: FindingFacets | null;
		onfilter: (filters: Partial<FindingFilters>) => void;
		ontab: (tab: string) => void;
		onrerununmeasured: () => void;
		rerunning?: boolean;
		context?: RunContext | null;
		contextLoading?: boolean;
		contextError?: string;
		oncontextscope?: (scope: ContextScope) => void;
		onretrycontext?: () => void;
	}

	let {
		run,
		tools,
		results,
		facets,
		onfilter,
		ontab,
		onrerununmeasured,
		rerunning = false,
		context = null,
		contextLoading = false,
		contextError = '',
		oncontextscope,
		onretrycontext,
	}: Props = $props();

	/**
	 * A tool with nothing in scope to read (`skip_reason: not_applicable`, `tool_applicable: false`) is
	 * **not measured** (spec 8.8), never a zero; re-running it cannot change that (the tree is immutable).
	 */
	function isNotApplicable(result: RunToolResult | undefined): boolean {
		return !!result && (result.summary?.skip_reason === 'not_applicable' || result.summary?.tool_applicable === false);
	}
	/** A completed result that measured the code (not a not-measured summary). */
	function isMeasured(result: RunToolResult | undefined): boolean {
		return result?.status === 'completed' && result.summary?.measurement !== 'not_measured' && !isNotApplicable(result);
	}

	const states = $derived(run.tool_slugs.map((slug) => ({ slug, result: results.get(slug), state: resultState(results.get(slug)) })));
	const measured = $derived(states.filter((s) => isMeasured(s.result)).length);
	const failed = $derived(states.filter((s) => s.result?.status === 'failed'));
	const appDown = $derived(states.filter((s) => s.result?.summary?.skip_reason === 'app_not_running'));
	const notApplicable = $derived(states.filter((s) => isNotApplicable(s.result)));
	/** Re-runnable: finished without a measurement for a reason a re-run can fix (not "nothing to read"). */
	const unmeasured = $derived(
		states.filter((s) => s.result && !isMeasured(s.result) && !isNotApplicable(s.result) && !s.state.live && s.result.status !== 'pending')
	);
	const measuredNote = $derived(
		[
			failed.length ? `${failed.length} failed` : '',
			unmeasured.length - failed.length > 0 ? `${unmeasured.length - failed.length} not measured` : '',
			notApplicable.length ? `${notApplicable.length} had nothing to read (not measured)` : '',
		]
			.filter(Boolean)
			.join(' · ') || 'all measured'
	);
	const active = $derived(isActive(run.status));

	/** Finished, and no tool measured anything: zero findings is not a clean result. */
	const nothingMeasured = $derived(!active && run.tool_slugs.length > 0 && measured === 0);
	const total = $derived(facets?.total ?? (run.summary?.total_findings as number | undefined) ?? 0);
	/** Findings by scope (null = no tool of that scope measured the run); null before the split is known. */
	const scopes = $derived(facets?.scopes ?? (active ? null : runScopeCounts(run.summary)));
	/** A finished run's headline is its code findings: advisories and runtime findings are not code issues. */
	const codeHeadline = $derived(!active && !!scopes);
	/** "160 advisories · 3 runtime" when the run has more than code findings. */
	const nonCode = $derived(nonCodeText(scopes));
	// Ordered by the tool's own word (the run list's chips use the same rule), never by the normalized level.
	const levels = $derived(sortLevels(facets?.levels ?? []));
	const levelMax = $derived(Math.max(1, ...levels.map((l) => l.count), facets?.unrated ?? 0));
	const byTool = $derived(
		states
			.map((s) => ({ slug: s.slug, count: resultFindingCount(s.result), inTests: testsByTool.get(s.slug) ?? 0 }))
			.filter((t) => t.count > 0)
			.sort((a, b) => b.count - a.count)
	);
	const toolMax = $derived(Math.max(1, ...byTool.map((t) => t.count)));
	const codeLines = $derived(run.source_files.reduce((n, f) => n + f.lines, 0));
	/** Source LOC by the study's definition (null until the context loads, or for code without paths). */
	const loc = $derived(context?.source_loc ?? null);
	const excluded = $derived(loc?.excluded_paths ?? null);
	/** Code findings in test files: counted apart from the app's code issues, as the study does. */
	const testFindings = $derived(facets?.code_parts?.tests ?? 0);
	const sourceFindings = $derived(facets?.code_parts?.source ?? null);
	/** Pre-rule runs: code findings in excluded non-source dirs (counted neither as source nor tests). */
	const excludedFindings = $derived(facets?.code_parts?.excluded ?? 0);
	const codeValue = $derived((testFindings > 0 || excludedFindings > 0) && sourceFindings != null ? sourceFindings : scopes?.code);
	const codeExtra = $derived(
		[testFindings > 0 ? `${testFindings} in test files` : '', excludedFindings > 0 ? `${excludedFindings} in excluded dirs` : '', nonCode].filter(Boolean).join(' · ')
	);
	const testsByTool = $derived(new Map((context?.test_split ?? []).map((r) => [r.tool, r.findings_in_tests])));
	// The rule list pools every tool that reports a rule id (ruff and flake8 both say F401): one line per tool.
	const prevalenceByRule = $derived(prevalenceIndex(context?.rule_prevalence ?? []).byRuleAll);
	const scopeItems = $derived(
		(context?.scopes_available ?? []).map((sc) => ({
			value: sc,
			label: sc === 'study' ? 'Study' : 'All my apps',
			title:
				sc === 'study'
					? "The app's study: the current run of each main-stage cell"
					: 'Every app of yours with a finished analysis (the current run of each study cell, plus ad-hoc runs)',
		}))
	);
	/** The analyzed GenerationRun (null for pasted code or a deleted app). */
	const jobId = $derived(run.target.kind === 'run' ? (run.target.job_id ?? null) : null);
	/** `summary.truncated_inputs` = `[validity.termination]` when the generation run was truncated. */
	const truncatedBy = $derived(((run.summary?.truncated_inputs ?? []) as string[]).filter(Boolean));
	const truncated = $derived(
		truncatedBy.length
			? `The generation run was truncated (${truncatedBy.map((t) => t.replace(/_/g, ' ')).join(', ')}): these findings describe an app that may be unfinished. Truncated runs stay in the study sample (intention-to-treat).`
			: ''
	);
	const metrics = $derived((run.summary?.metrics_by_tool ?? {}) as Record<string, Record<string, any>>);
	const hasMetrics = $derived(Object.values(metrics).some((m) => m && Object.keys(m).length > 0));

	function toolName(slug: string) {
		return tools.get(slug)?.name ?? slug;
	}
</script>

<div class="space-y-4">
	{#if truncated || failed.length > 0 || appDown.length > 0}
		<div class="space-y-2">
			{#if truncated}
				<div class="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning-fg" role="note">
					<TriangleAlert class="mt-0.5 size-4 shrink-0" />
					<p>{truncated}</p>
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
						{#if run.target.job_id}Start it on the <a class="underline underline-offset-2" href="/samples/{run.target.job_id}">sample page</a>, then re-run them.{/if}
					</p>
				</div>
			{/if}
		</div>
	{/if}

	<div class="grid grid-cols-2 gap-3 md:grid-cols-4">
		<button
			type="button"
			class="kpi-card cursor-pointer text-left hover:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none"
			style="--kpi-accent: var(--chart-1)"
			title={codeHeadline && testFindings > 0 ? `The app's own source: ${testFindings} finding${testFindings === 1 ? '' : 's'} in test files are counted apart, as in the study` : undefined}
			onclick={() => onfilter(codeHeadline && testFindings > 0 ? { scope: 'source' } : codeHeadline && nonCode ? { scope: 'code' } : {})}
		>
			<span class="kpi-label">{codeHeadline ? 'Code findings' : `Findings${active ? ' so far' : ''}`}</span>
			{#if nothingMeasured && total === 0}
				<span class="kpi-value text-muted-foreground" title="No tool measured the code">—</span>
				<span class="text-[11px] text-warning-fg">nothing measured</span>
			{:else}
				{#if codeHeadline && scopes?.code == null}
					<span class="kpi-value text-muted-foreground" title="No code-analysis tool measured this run">—</span>
				{:else}
					<span class="kpi-value">{codeHeadline ? codeValue : total}</span>
				{/if}
				{#if codeHeadline && codeExtra}
					<span class="text-[11px] text-muted-foreground" title="Test-file findings, dependency advisories (package vulnerabilities) and runtime findings are not the app's code issues">+ {codeExtra}</span>
				{:else if nonCode}
					<span class="text-[11px] text-muted-foreground" title="Dependency advisories (package vulnerabilities) and runtime findings are not code issues">{codeHeadline ? `+ ${nonCode}` : `${scopes?.code ?? '—'} code · ${nonCode}`}</span>
				{:else}
					<span class="text-[11px] text-muted-foreground">{facets?.unrated ? `${facets.unrated} not rated by their tool` : 'open the list'}</span>
				{/if}
			{/if}
		</button>
		<button type="button" class="kpi-card cursor-pointer text-left hover:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none" style="--kpi-accent: var(--chart-2)" onclick={() => ontab('tools')}>
			<span class="kpi-label">Tools measured</span>
			<span class="kpi-value">{measured}<span class="text-base text-muted-foreground">/{run.tool_slugs.length}</span></span>
			<span class="text-[11px] text-muted-foreground">
				{#if active}running{:else}{measuredNote}{/if}
			</span>
		</button>
		<div class="kpi-card" style="--kpi-accent: var(--chart-3)">
			{#if loc}
				<span class="kpi-label">{loc.scope === 'model' ? 'Model code' : 'Code'} · {loc.files} source file{loc.files === 1 ? '' : 's'}</span>
				<span
					class="kpi-value"
					title="Non-empty, non-comment lines of source files, test files apart: the study's LOC (the per-kLOC denominator){loc.scope === 'model' ? ', counted over what the model wrote or changed' : ''}. Stored: {run.source_files.length} files, {codeLines.toLocaleString()} raw lines incl. manifests and lockfiles."
				>{loc.total.toLocaleString()}<span class="text-base text-muted-foreground"> LOC</span></span>
				{#if loc.scope === 'model' && loc.app_total != null}
					<span class="truncate text-[11px] text-muted-foreground" title="Source LOC of the whole app tree the tools read (starter included)">{loc.app_total.toLocaleString()} LOC in the whole app</span>
				{/if}
				<span
					class="truncate text-[11px] text-muted-foreground"
					title="Source lines: backend {loc.by_layer.backend?.toLocaleString() ?? '—'}, frontend {loc.by_layer.frontend?.toLocaleString() ?? '—'}; test code counted apart: {loc.by_layer.tests?.toLocaleString() ?? 'unknown (no file paths)'} lines in {loc.test_files ?? '—'} files"
				>
					{loc.by_layer.backend?.toLocaleString() ?? '—'} BE · {loc.by_layer.frontend?.toLocaleString() ?? '—'} FE · {loc.by_layer.tests?.toLocaleString() ?? '—'} tests
				</span>
			{:else if run.source_files.length > 0}
				<span class="kpi-label">Code</span>
				<span class="kpi-value">{run.source_files.length}<span class="text-base text-muted-foreground"> files</span></span>
				<span class="truncate text-[11px] text-muted-foreground">{codeLines.toLocaleString()} lines{run.target.stack_ref ? ` · ${run.target.stack_ref}` : ''}</span>
			{:else}
				<span class="kpi-label">Code</span>
				<span class="kpi-value text-base">{run.target.kind === 'deleted' ? 'App deleted' : '—'}</span>
				<span class="truncate text-[11px] text-muted-foreground">{run.target.stack_ref ?? ''}</span>
			{/if}
		</div>
		<div class="kpi-card" style="--kpi-accent: var(--chart-5)">
			<span class="kpi-label">Duration</span>
			<span class="kpi-value">{formatSpan(run.duration_seconds)}</span>
			<span class="text-[11px] text-muted-foreground">{run.tool_slugs.length} tool{run.tool_slugs.length === 1 ? '' : 's'} in sequence</span>
		</div>
	</div>

	{#if excluded && excluded.count > 0}
		<!-- A run from before the rule read these files: its findings may be about them (an attention item). -->
		<div
			class="flex items-start gap-2 rounded-md border px-3 py-2 text-sm {excluded.left_out === false
				? 'border-warning/30 bg-warning/10 text-warning-fg'
				: 'border-border bg-card text-muted-foreground'}"
			role="note"
		>
			{#if excluded.left_out === false}<TriangleAlert class="mt-0.5 size-4 shrink-0" />{:else}<FolderMinus class="mt-0.5 size-4 shrink-0" />{/if}
			<div class="min-w-0 flex-1">
				<p>{excludedPathsNote(excluded)}</p>
				{#if excluded.sample.length > 0}
					<details class="mt-1 text-xs">
						<summary class="cursor-pointer hover:text-foreground">Show {excluded.sample.length < excluded.count ? `the first ${excluded.sample.length}` : 'them'}</summary>
						<ul class="mt-1 max-h-40 space-y-0.5 overflow-auto font-mono">
							{#each excluded.sample as path (path)}<li class="break-all">{path}</li>{/each}
						</ul>
						{#if excluded.sample.length < excluded.count}<p class="mt-1">+{(excluded.count - excluded.sample.length).toLocaleString()} more</p>{/if}
					</details>
				{/if}
			</div>
		</div>
	{/if}

	<div class="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
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
						{#each levels as l (l.value)}
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
									<span class="text-right font-mono text-xs tabular-nums" title={t.inTests > 0 ? `${t.inTests} of them in test files (counted apart)` : undefined}>{t.count}{#if t.inTests > 0}<span class="block text-[11px] text-muted-foreground">{t.inTests} tests</span>{/if}</span>
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
					<div class="flex flex-wrap items-start justify-between gap-2">
						<div class="min-w-0">
							<Card.Title class="text-base">Most frequent rules</Card.Title>
							{#if context?.scope}
								<Card.Description class="text-xs">
									How common each rule is in {context.scope_label} (n = {context.n} analyzed app{context.n === 1 ? '' : 's'}{context.model_n != null ? `, ${context.model_n} of this model` : ''}): apps whose latest result of that tool reports it, over the apps the tool measured.{context.interim ? ' Interim: the study is still running.' : ''}
								</Card.Description>
							{/if}
						</div>
						{#if scopeItems.length > 1 && context?.scope}
							<Segmented size="sm" aria-label="Prevalence corpus" items={scopeItems} value={context.scope} onValueChange={(v) => oncontextscope?.(v as ContextScope)} />
						{/if}
					</div>
				</Card.Header>
				<Card.Content class="p-0">
					{#if contextError}
						<div class="border-b border-border p-3"><ErrorAlert message={contextError} onretry={onretrycontext} /></div>
					{/if}
					<ul class="divide-y divide-border/60">
						{#each facets.rules.slice(0, 8) as rule (rule.value)}
							{@const prevs = prevalenceByRule.get(rule.value) ?? []}
							<li>
								<button type="button" class="flex w-full cursor-pointer items-center gap-3 px-4 py-1.5 text-left text-xs hover:bg-muted/50" onclick={() => onfilter({ rule: rule.value })}>
									<span class="min-w-0 flex-1">
										<span class="block truncate font-mono">{rule.value}</span>
										{#if rule.label}<span class="block truncate text-muted-foreground">{rule.label}</span>{/if}
										{#if prevs.length > 0 && context}
											{#each rulePrevalenceLines(prevs) as line (line.row.tool)}
												<span class="block truncate font-mono text-[11px] text-muted-foreground tabular-nums" title={prevalenceTitle(line.row, context.scope_label)}>{line.text}</span>
											{/each}
										{:else if contextLoading}
											<span class="block text-[11px] text-muted-foreground">…</span>
										{/if}
									</span>
									<span class="font-mono tabular-nums text-muted-foreground">{rule.count}</span>
								</button>
							</li>
						{/each}
					</ul>
					{#if context?.notes.length}
						<p class="border-t border-border px-4 py-2 text-[11px] text-muted-foreground">{context.notes.join(' ')}</p>
					{/if}
				</Card.Content>
			</Card.Root>
		{/if}
	</div>

	{#if hasMetrics}
		<Card.Root class="gap-3">
			<Card.Header>
				<Card.Title class="text-base">Metrics</Card.Title>
				<Card.Description class="text-xs">Aggregate figures from complexity, duplication and other tools that report metrics.</Card.Description>
			</Card.Header>
			<Card.Content class="@container">
				<MetricsPanel {metrics} {tools} />
			</Card.Content>
		</Card.Root>
	{/if}

	{#if jobId && !active}
		<!-- Replicate context: the app's same-setup runs and their spread (a self-contained card). -->
		<SameSetupPanel {jobId} variant="analysis" />
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
