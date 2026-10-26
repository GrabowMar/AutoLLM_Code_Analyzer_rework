<script lang="ts">
	/**
	 * ToolPipeline — every tool of a run in execution order: state (queued,
	 * running with a live clock, completed, failed, skipped and why), time,
	 * findings with the levels the tool reported, version, warnings and the
	 * error. Rows link to the tool's findings and its raw output.
	 *
	 * Props: `tools` (slug → catalog tool), `slugs` (the run's tool order),
	 * `results` (by slug), `now` (ms, ticks while live), `onfindings(slug)`,
	 * `onoutput(result)`.
	 */
	import type { AnalyzerTool } from '$lib/api/analyzers';
	import type { RunToolResult } from '$lib/api/runs';
	import ToolIcon from '$lib/components/analyzers/ToolIcon.svelte';
	import { Button } from '$lib/components/ui/button';
	import Globe from '@lucide/svelte/icons/globe';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import FileText from '@lucide/svelte/icons/file-text';
	import ListFilter from '@lucide/svelte/icons/list-filter';
	import { cn } from '$lib/utils';
	import { toneClasses } from '$lib/constants/colors';
	import LevelBadge from './LevelBadge.svelte';
	import { elapsedSince, formatClock, formatSpan, resultFindingCount, resultState, reportedLevels } from './format';

	interface Props {
		tools: Map<string, AnalyzerTool>;
		slugs: string[];
		results: Map<string, RunToolResult>;
		now: number;
		onfindings?: (slug: string) => void;
		onoutput?: (result: RunToolResult) => void;
	}

	let { tools, slugs, results, now, onfindings, onoutput }: Props = $props();

	let openErrors = $state(new Set<string>());

	function toggleError(slug: string) {
		const next = new Set(openErrors);
		if (next.has(slug)) next.delete(slug);
		else next.add(slug);
		openErrors = next;
	}
</script>

<ul class="divide-y divide-border/60" aria-label="Tools of this run">
	{#each slugs as slug, i (slug)}
		{@const r = results.get(slug)}
		{@const tool = tools.get(slug)}
		{@const st = resultState(r)}
		{@const count = resultFindingCount(r)}
		{@const levels = reportedLevels(r?.summary)}
		{@const warnings = (r?.summary?.warnings ?? []) as string[]}
		{@const version = (r?.summary?.tool_version ?? '') as string}
		{@const hasOutput = !!r && (r.status === 'completed' || r.status === 'failed')}
		<li class={cn('px-3 py-2.5 transition-colors duration-150', st.live && 'bg-info/5')}>
			<div class="flex flex-wrap items-center gap-x-3 gap-y-1.5 md:grid md:grid-cols-[1.25rem_1.75rem_minmax(0,1fr)_8rem_4rem_minmax(8rem,13rem)_10rem]">
				<span class="w-5 shrink-0 text-right font-mono text-[11px] text-muted-foreground tabular-nums">{i + 1}</span>
				<ToolIcon tool={tool ?? { slug }} size="sm" />
				<div class="min-w-[9rem] flex-1">
					<p class="flex flex-wrap items-center gap-1.5 text-sm font-medium">
						{tool?.name ?? slug}
						{#if tool?.analysis_mode === 'dynamic'}
							<span class="inline-flex items-center gap-0.5 rounded border border-border px-1 text-[11px] font-normal text-muted-foreground" title="Probes the running app"><Globe class="size-3" />Dynamic</span>
						{/if}
						{#if tool?.kind === 'ai'}
							<span class="inline-flex items-center gap-0.5 rounded border border-border px-1 text-[11px] font-normal text-muted-foreground" title="An LLM reviewer: findings are opinions, not measurements"><Sparkles class="size-3" />AI</span>
						{/if}
					</p>
					{#if version}<p class="cell-sub truncate font-mono text-[11px] text-muted-foreground" title={version}>{version}</p>{/if}
				</div>

				<span class={cn('inline-flex shrink-0 items-center gap-1.5 justify-self-start rounded-full border px-2 py-0.5 text-[11px] font-medium', toneClasses[st.tone])}>
					<span class={cn('status-dot h-1.5 w-1.5', st.live && 'status-dot-live')} aria-hidden="true"></span>
					{st.label}
				</span>

				<span class="w-16 shrink-0 text-right font-mono text-xs text-muted-foreground tabular-nums" title={r?.started_at ? `Started ${new Date(r.started_at).toLocaleTimeString()}` : undefined}>
					{#if st.live}
						<span class="text-info-fg">{formatClock(elapsedSince(r?.started_at, now))}</span>
					{:else if r?.duration_seconds != null}
						{formatSpan(r.duration_seconds)}
					{:else}
						—
					{/if}
				</span>

				<div class="flex min-w-0 flex-wrap items-center gap-1 md:justify-end">
					{#if r && (st.measured || count > 0)}
						<span class="mr-1 font-mono text-sm tabular-nums">{count}</span>
						<span class="mr-1 text-xs text-muted-foreground">finding{count === 1 ? '' : 's'}</span>
						{#each levels.slice(0, 3) as [label, n] (label)}
							<span class="inline-flex items-center gap-0.5"><LevelBadge {label} /><span class="font-mono text-[11px] text-muted-foreground tabular-nums">{n}</span></span>
						{/each}
					{/if}
				</div>

				<div class="ml-auto flex shrink-0 items-center justify-end gap-1 md:ml-0">
					{#if count > 0 && onfindings}
						<Button variant="ghost" size="sm" class="h-7 px-2 text-xs" onclick={() => onfindings(slug)} title="Show this tool's findings">
							<ListFilter class="size-3.5" />Findings
						</Button>
					{/if}
					{#if hasOutput && onoutput}
						<Button variant="ghost" size="sm" class="h-7 px-2 text-xs" onclick={() => onoutput(r!)} title="Command, exit code and raw output">
							<FileText class="size-3.5" />Output
						</Button>
					{/if}
				</div>
			</div>

			{#if r?.error_message && r.status !== 'running'}
				{@const long = r.error_message.length > 160}
				<div class={cn('mt-1.5 ml-8 text-xs', r.status === 'failed' ? 'text-danger-fg' : 'text-muted-foreground')}>
					<p class={cn('whitespace-pre-line [overflow-wrap:anywhere]', long && !openErrors.has(slug) && 'line-clamp-2')}>{r.error_message}</p>
					{#if long}
						<button type="button" class="cursor-pointer text-muted-foreground underline-offset-2 hover:text-foreground hover:underline" onclick={() => toggleError(slug)}>
							{openErrors.has(slug) ? 'Show less' : 'Show all'}
						</button>
					{/if}
				</div>
			{/if}
			{#if warnings.length > 0}
				<div class="mt-1.5 ml-8 flex items-start gap-1.5 text-xs text-warning-fg">
					<TriangleAlert class="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
					<div class="min-w-0 space-y-0.5">
						{#each warnings as w, wi (wi)}<p class="[overflow-wrap:anywhere]">{w}</p>{/each}
					</div>
				</div>
			{/if}
		</li>
	{/each}
</ul>
