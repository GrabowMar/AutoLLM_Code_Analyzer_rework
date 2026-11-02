<script lang="ts">
	/**
	 * QualityMetricsCard — complexity and duplication of one app, from the
	 * latest result per tool (lizard, radon, jscpd, plus any other tool that
	 * stored numeric metrics), each with its tool version, parser hash and the
	 * analysis run it came from. A tool that measured nothing says so.
	 *
	 * Props: `jobId`, `refreshKey` (changes re-fetch), `canAnalyze` (offer the
	 * Analyze button; false for jobs that produced no code). Self-fetching.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import Gauge from '@lucide/svelte/icons/gauge';
	import Plus from '@lucide/svelte/icons/plus';
	import { formatApiError } from '$lib/api/core';
	import { getJobQualityMetrics, type JobQualityMetrics, type QualityToolRow } from '$lib/api/job-insights';
	import { fmtNum, humanizeKey, isPlainObject, scalarText, shortHash } from './format';

	interface Props {
		jobId: string;
		refreshKey?: string;
		canAnalyze?: boolean;
	}

	let { jobId, refreshKey = '', canAnalyze = true }: Props = $props();

	let data = $state<JobQualityMetrics | null>(null);
	let loading = $state(true);
	let error = $state('');

	// Headline figures per tool, in reading order; everything else stays under "more".
	const HEADLINE: Record<string, { key: string; label: string; suffix?: string; derived?: boolean }[]> = {
		lizard: [
			{ key: 'avg_ccn', label: 'Avg CCN' },
			{ key: 'max_ccn', label: 'Max CCN' },
			{ key: 'functions', label: 'Functions' },
			{ key: 'functions_over_15', label: 'CCN > 15' },
			{ key: 'total_nloc', label: 'NLOC' },
			{ key: 'avg_function_nloc', label: 'Avg fn NLOC', derived: true },
		],
		radon: [
			{ key: 'average_complexity', label: 'Avg complexity' },
			{ key: 'max_complexity', label: 'Max complexity' },
			{ key: 'total_blocks', label: 'Blocks' },
		],
		jscpd: [
			{ key: 'duplication_percentage', label: 'Dup. lines', suffix: '%' },
			{ key: 'token_duplication_percentage', label: 'Dup. tokens', suffix: '%' },
			{ key: 'clones', label: 'Clones' },
			{ key: 'duplicated_lines', label: 'Dup. lines (n)' },
		],
	};

	async function load(id: string) {
		loading = true;
		try {
			const res = await getJobQualityMetrics(id);
			if (id !== jobId) return;
			data = res;
			error = '';
		} catch (e) {
			if (id === jobId) error = formatApiError(e, 'Could not load quality metrics.');
		} finally {
			if (id === jobId) loading = false;
		}
	}

	$effect(() => {
		void refreshKey;
		const id = jobId;
		data = null;
		void load(id);
	});

	function figure(tool: QualityToolRow, key: string, derived = false): unknown {
		return derived ? tool.derived?.[key] : tool.metrics?.[key];
	}

	function extraScalars(tool: QualityToolRow): [string, unknown][] {
		// max_ccn_function gets its own line below the figures.
		const shown = new Set([...(HEADLINE[tool.tool_slug] ?? []).map((h) => h.key), 'max_ccn_function']);
		return Object.entries(tool.metrics ?? {}).filter(
			([k, v]) => !shown.has(k) && (typeof v === 'number' || (typeof v === 'string' && v.length < 80))
		);
	}

	function notMeasuredText(tool: QualityToolRow): string {
		if (tool.status === 'not_run') return 'Not measured: no analysis run of this app used this tool.';
		if (tool.status === 'skipped') return `Not measured: skipped${tool.skip_reason ? ` (${tool.skip_reason.replace(/_/g, ' ')})` : ''}.`;
		if (tool.status === 'failed') return `Not measured: the tool failed${tool.error ? ` — ${tool.error}` : ''}.`;
		return 'Not measured.';
	}

	const measuredTools = $derived((data?.tools ?? []).filter((t) => t.measured));
</script>

<Card.Root>
	<Card.Header class="flex flex-row flex-wrap items-start justify-between gap-2 pb-2">
		<div class="space-y-1.5">
			<Card.Title class="flex items-center gap-1.5 text-sm"><Gauge class="size-4 text-muted-foreground" aria-hidden="true" />Complexity &amp; duplication</Card.Title>
			<Card.Description>Latest result per tool for this app; metrics from different tool versions are not comparable.</Card.Description>
		</div>
		{#if data && measuredTools.length === 0 && canAnalyze}
			<Button size="sm" variant="outline" href="/analysis/create?generation_job_id={jobId}"><Plus />Analyze</Button>
		{/if}
	</Card.Header>
	<Card.Content class="space-y-3 text-sm">
		{#if loading && !data}
			<LoadingState label="loading metrics" class="py-6" />
		{:else if error && !data}
			<ErrorAlert message={error} onretry={() => load(jobId)} />
		{:else if !data || data.tools.length === 0}
			<EmptyState compact icon={Gauge} title="Not measured" description="No analysis run has measured this app yet." />
		{:else}
			{#each data.tools as tool (tool.tool_slug)}
				<section class="rounded-md border border-border p-3" aria-label="{tool.tool_name} metrics">
					<div class="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1">
						<h3 class="text-sm font-medium">{tool.tool_name}</h3>
						<span class="text-[11px] text-muted-foreground">{tool.kind}</span>
						{#if !tool.measured}<StatusBadge status={tool.status === 'not_run' ? 'not measured' : tool.status} tone="neutral" />{/if}
						{#if tool.tool_version}<span class="ml-auto font-mono text-[11px] text-muted-foreground" title="Tool version">v{tool.tool_version.replace(/^.*version:\s*/i, '')}</span>{/if}
					</div>
					{#if tool.measured && tool.status === 'skipped'}
						<!-- Skipped as not_applicable: measured, with nothing to measure (e.g. radon on an app without Python). -->
						<p class="text-xs text-muted-foreground">Not applicable to this app: nothing for it to measure.</p>
					{:else if tool.measured}
						{@const headline = HEADLINE[tool.tool_slug] ?? []}
						{@const extras = extraScalars(tool)}
						<dl class="grid grid-cols-3 gap-x-3 gap-y-2 sm:grid-cols-4">
							{#each headline as h (h.key)}
								{@const v = figure(tool, h.key, h.derived)}
								<div class="min-w-0">
									<dt class="truncate text-[11px] text-muted-foreground" title={h.derived ? `${h.label} (derived: total NLOC / functions)` : h.label}>{h.label}{h.derived ? ' ≈' : ''}</dt>
									<dd class="font-mono text-sm font-medium tabular-nums">{typeof v === 'number' ? `${fmtNum(v)}${h.suffix ?? ''}` : '—'}</dd>
								</div>
							{/each}
							{#each extras as [key, v] (key)}
								<div class="min-w-0">
									<dt class="truncate text-[11px] text-muted-foreground" title={key}>{humanizeKey(key)}</dt>
									<dd class="truncate font-mono text-sm tabular-nums" title={scalarText(v)}>{scalarText(v)}</dd>
								</div>
							{/each}
						</dl>
						{#if typeof tool.metrics.max_ccn_function === 'string'}
							<p class="mt-1.5 truncate text-[11px] text-muted-foreground" title={tool.metrics.max_ccn_function}>Most complex: <span class="font-mono">{tool.metrics.max_ccn_function.replace('/work/', '')}</span></p>
						{/if}
						{#if isPlainObject(tool.metrics.rank_distribution)}
							<p class="mt-1.5 flex flex-wrap gap-x-3 font-mono text-[11px] tabular-nums">
								<span class="text-muted-foreground">ranks</span>
								{#each Object.entries(tool.metrics.rank_distribution) as [rank, n] (rank)}<span><span class="text-muted-foreground">{rank}</span> {n}</span>{/each}
							</p>
						{/if}
					{:else}
						<p class="text-xs text-muted-foreground">{notMeasuredText(tool)}</p>
					{/if}
					{#if tool.run}
						<p class="mt-2 flex flex-wrap gap-x-2 font-mono text-[11px] text-muted-foreground">
							{#if tool.run.accessible}
								<a href="/analysis/{tool.run.id}" class="hover:text-primary hover:underline">run {tool.run.name || tool.run.id.slice(0, 8)}</a>
							{:else}
								<span title="Run of another user">run {tool.run.id.slice(0, 8)}</span>
							{/if}
							{#if tool.parser_sha}<span title="Parser hash">parser {shortHash(tool.parser_sha)}</span>{/if}
							{#if tool.command_sha}<span title="Command hash">cmd {shortHash(tool.command_sha)}</span>{/if}
						</p>
					{/if}
				</section>
			{/each}
			{#if data.notes.length}
				<ul class="space-y-0.5 text-[11px] text-muted-foreground">
					{#each data.notes as note (note)}<li>{note}</li>{/each}
				</ul>
			{/if}
		{/if}
	</Card.Content>
</Card.Root>
