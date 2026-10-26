<script lang="ts">
	/**
	 * RunLiveStrip — an active run's progress: one segment per tool in execution
	 * order (filled when done, tinted by outcome, pulsing while running), what the
	 * run is doing right now (queued, waiting for the workspace, preparing, which
	 * tool and for how long), counts so far and the live-connection state.
	 *
	 * Props: `run`, `results` (by slug), `tools` (names), `now` (ms), `connection`.
	 */
	import type { AnalyzerTool } from '$lib/api/analyzers';
	import type { AnalysisRunDetail, RunToolResult } from '$lib/api/runs';
	import type { Tone } from '$lib/constants/colors';
	import { toneFillClasses } from '$lib/constants/colors';
	import { cn } from '$lib/utils';
	import type { WatchConnection } from './live.svelte';
	import { elapsedSince, formatClock, phaseLabel, resultState } from './format';

	interface Props {
		run: AnalysisRunDetail;
		results: Map<string, RunToolResult>;
		tools: Map<string, AnalyzerTool>;
		now: number;
		connection: WatchConnection;
	}

	let { run, results, tools, now, connection }: Props = $props();

	const segments = $derived(
		run.tool_slugs.map((slug) => ({ slug, result: results.get(slug), state: resultState(results.get(slug)) }))
	);
	const done = $derived(segments.filter((s) => s.result && !s.state.live && s.result.status !== 'pending').length);
	const running = $derived(segments.find((s) => s.state.live));
	const findings = $derived(run.progress?.findings ?? 0);

	const headline = $derived.by(() => {
		if (run.status === 'pending') return 'Queued — runs of your workspace execute one at a time';
		if (running) return `Running ${tools.get(running.slug)?.name ?? running.slug}`;
		if (run.phase && run.phase !== 'running_tools') return phaseLabel(run.phase);
		return done === run.tool_slugs.length ? 'Finishing up' : 'Starting the next tool';
	});

	const connectionText = $derived(
		connection === 'open'
			? { text: 'Live', cls: 'text-success-fg' }
			: connection === 'reconnecting'
				? { text: 'Reconnecting — polling every 3 s', cls: 'text-warning-fg' }
				: { text: 'Connecting…', cls: 'text-muted-foreground' }
	);

	function fill(tone: Tone, live: boolean, has: boolean): string {
		if (!has) return 'bg-transparent';
		if (live) return 'bg-info animate-pulse motion-reduce:animate-none';
		return toneFillClasses[tone];
	}
</script>

<section class="rounded-md border border-info/30 bg-card px-4 py-3 shadow-[var(--shadow-card)]" aria-label="Run progress">
	<div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
		<p class="text-sm font-medium" aria-live="polite">
			{headline}{#if running?.result?.started_at}<span class="ml-2 font-mono text-info-fg tabular-nums">{formatClock(elapsedSince(running.result.started_at, now))}</span>{/if}
		</p>
		<p class="flex flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
			<span><b class="font-mono font-medium text-foreground tabular-nums">{done}</b>/{run.tool_slugs.length} tools</span>
			<span><b class="font-mono font-medium text-foreground tabular-nums">{findings}</b> findings so far</span>
			{#if run.started_at}<span>elapsed <span class="font-mono tabular-nums">{formatClock(elapsedSince(run.started_at, now))}</span></span>{/if}
			<span class={cn('inline-flex items-center gap-1', connectionText.cls)}>
				<span class={cn('status-dot h-1.5 w-1.5', connection === 'open' && 'status-dot-live')} aria-hidden="true"></span>{connectionText.text}
			</span>
		</p>
	</div>
	<div class="mt-2 flex h-2 gap-0.5" role="progressbar" aria-valuemin={0} aria-valuemax={run.tool_slugs.length} aria-valuenow={done} aria-label="Tools finished">
		{#each segments as s (s.slug)}
			<span
				class={cn('h-full min-w-1 flex-1 rounded-[1px] border', s.result && s.result.status !== 'pending' ? 'border-transparent' : 'border-border', fill(s.state.tone, s.state.live, !!s.result && s.result.status !== 'pending'))}
				title="{tools.get(s.slug)?.name ?? s.slug}: {s.state.label}"
			></span>
		{/each}
	</div>
</section>
