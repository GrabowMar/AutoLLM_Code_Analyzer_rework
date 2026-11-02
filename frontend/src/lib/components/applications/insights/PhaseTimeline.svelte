<script lang="ts">
	/**
	 * PhaseTimeline — where one job's time went: queue → generation (LLM vs
	 * tools) → waiting → evaluation (build vs tests), as one proportional bar
	 * plus a step list with durations and their sources, then the executor's
	 * execution record (attempts, lane, run order, retries).
	 *
	 * Props: `timeline` (from GET /jobs/{id}/timeline/), `loading`, `error`,
	 * `onretry`, `compact` (bar + steps only, for the failure page).
	 */
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import Clock from '@lucide/svelte/icons/clock';
	import type { JobTimeline, TimelineSegment } from '$lib/api/job-insights';
	import { formatDateCompact } from '$lib/utils/formatters';
	import { fmtSeconds, fmtShare, fmtUsd, fmtInt, humanizeKey } from './format';

	interface Props {
		timeline: JobTimeline | null;
		loading?: boolean;
		error?: string;
		onretry?: () => void;
		compact?: boolean;
	}

	let { timeline, loading = false, error = '', onretry, compact = false }: Props = $props();

	// Wait phases are neutral; work phases take categorical chart slots (never status colours).
	const FILL: Record<string, string> = {
		queue: 'var(--muted-foreground)',
		sandbox_wait: 'var(--muted-foreground)',
		other: 'var(--muted-foreground)',
		generation: 'var(--chart-1)',
		run: 'var(--chart-1)',
		evaluation: 'var(--chart-2)',
	};
	const PART_FILL: Record<string, string> = {
		llm: 'var(--chart-1)',
		tools_other: 'var(--chart-3)',
		backend_stage: 'var(--chart-1)',
		frontend_stage: 'var(--chart-5)',
		build: 'var(--chart-2)',
		tests: 'var(--chart-3)',
		scheduler_queue: 'var(--muted-foreground)',
		llm_slot_wait: 'var(--chart-4)',
	};

	function fill(seg: TimelineSegment): string {
		return FILL[seg.key] ?? 'var(--chart-5)';
	}

	// Trajectory time split (seconds keys end in _s, ratios in _share); unknown keys pass through.
	const TRAJ_LABEL: Record<string, string> = {
		head_s: 'before first call',
		llm_s: 'LLM',
		between_calls_s: 'between calls',
		tool_exec_s: 'tool execution',
		tail_s: 'after last call',
		llm_share: 'LLM share',
	};
	function trajectoryText(time: Record<string, number>): string {
		return Object.entries(time)
			.map(([k, v]) => {
				const label = TRAJ_LABEL[k] ?? k.replace(/_(s|share)$/, '').replace(/_/g, ' ');
				const value = k.endsWith('_s') ? fmtSeconds(v) : k.endsWith('_share') ? `${Math.round(v * 100)}%` : String(v);
				return `${label} ${value}`;
			})
			.join(' · ');
	}

	const barSegments = $derived(
		(timeline?.segments ?? []).filter((s) => !s.separate && s.seconds != null && s.seconds > 0)
	);
	const barTotal = $derived(barSegments.reduce((n, s) => n + (s.seconds ?? 0), 0));
	const exec = $derived(timeline?.execution ?? null);
	const priorCost = $derived(exec?.prior_attempts?.cost ?? null);
</script>

<Card.Root>
	<Card.Header class="pb-2">
		<Card.Title class="flex items-center gap-1.5 text-sm"><Clock class="size-4 text-muted-foreground" aria-hidden="true" />Phase timeline</Card.Title>
		<Card.Description>
			Where this job's time went, from creation to the last result. Waiting is shown apart from work, so queue time
			never counts as generation time.
		</Card.Description>
	</Card.Header>
	<Card.Content class="space-y-4 text-sm">
		{#if loading && !timeline}
			<LoadingState label="loading timeline" class="py-6" />
		{:else if error && !timeline}
			<ErrorAlert message={error} {onretry} />
		{:else if !timeline || timeline.segments.length === 0}
			<EmptyState
				compact
				icon={Clock}
				title="No timing recorded"
				description={timeline && !timeline.live ? 'No start or end time was stored for this job.' : 'This job has not started yet.'}
			/>
		{:else}
			{#if barTotal > 0}
				<div>
					<div
						class="flex h-3 w-full gap-px overflow-hidden rounded-sm bg-muted"
						role="img"
						aria-label="Phases: {barSegments.map((s) => `${s.label} ${fmtSeconds(s.seconds)}`).join(', ')}"
					>
						{#each barSegments as seg (seg.key)}
							<span
								class="h-full min-w-[2px] {seg.kind === 'wait' ? 'opacity-35' : ''}"
								style:width="{((seg.seconds ?? 0) / barTotal) * 100}%"
								style:background-color={fill(seg)}
								title="{seg.label}: {fmtSeconds(seg.seconds)}"
							></span>
						{/each}
					</div>
					<p class="mt-1 flex flex-wrap justify-between gap-2 font-mono text-[11px] tabular-nums text-muted-foreground">
						<span>{formatDateCompact(timeline.milestones[0]?.at)}</span>
						<span>
							{#if timeline.total_seconds != null}{fmtSeconds(timeline.total_seconds)} created → finished{/if}
							{#if timeline.run_seconds != null} · {fmtSeconds(timeline.run_seconds)} started → finished{/if}
						</span>
					</p>
				</div>
			{/if}

			<ol class="space-y-2.5">
				{#each timeline.segments as seg (seg.key)}
					<li class="min-w-0">
						<div class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
							<span class="size-2 shrink-0 self-center rounded-sm {seg.kind === 'wait' ? 'opacity-35' : ''}" style:background-color={fill(seg)} aria-hidden="true"></span>
							<span class="font-medium">{seg.label}</span>
							{#if seg.kind === 'wait'}<Badge variant="outline" class="text-[11px]">waiting</Badge>{/if}
							{#if seg.approximate}<span class="text-[11px] text-muted-foreground" title="Derived, not directly measured: {seg.source}">≈</span>{/if}
							<span class="ml-auto font-mono tabular-nums">{fmtSeconds(seg.seconds)}</span>
						</div>
						<p class="pl-4 text-[11px] text-muted-foreground">
							{#if seg.start}<span class="font-mono tabular-nums">{formatDateCompact(seg.start)}{seg.end ? ` → ${formatDateCompact(seg.end)}` : ''}</span>{' · '}{/if}<span class="font-mono" title="Source">{seg.source}</span>
							{#if seg.note}<span class="block {seg.separate ? 'text-warning-fg' : ''}">{seg.note}</span>{/if}
						</p>
						{#if seg.parts.length && !compact}
							<ul class="mt-1 space-y-0.5 pl-4 text-xs">
								{#each seg.parts as part (part.key)}
									<li class="flex items-center gap-2 {part.key === 'llm_slot_wait' ? 'pl-3' : ''}">
										<span class="size-1.5 shrink-0 rounded-full" style:background-color={PART_FILL[part.key] ?? 'var(--chart-5)'} aria-hidden="true"></span>
										<span class="min-w-0 truncate text-muted-foreground" title={part.source}>{part.label}{part.approximate ? ' ≈' : ''}</span>
										<span class="ml-auto font-mono tabular-nums">{fmtSeconds(part.seconds)}</span>
										<span class="w-10 text-right font-mono tabular-nums text-muted-foreground">{fmtShare(part.seconds, seg.seconds)}</span>
									</li>
								{/each}
							</ul>
						{/if}
						{#if seg.trajectory_time && !compact}
							<p class="mt-1 pl-4 text-[11px] text-muted-foreground" title="From the agent trajectory (result_data.agent.trajectory.time)">
								<span class="section-label">Trajectory</span>
								{trajectoryText(seg.trajectory_time)}
							</p>
						{/if}
					</li>
				{/each}
			</ol>

			{#if timeline.notes.length}
				<ul class="space-y-0.5 text-[11px] text-muted-foreground">
					{#each timeline.notes as note (note)}<li>{note}</li>{/each}
				</ul>
			{/if}

			{#if exec && !compact}
				<div class="space-y-2">
					<SectionHeading size="sm" level={3} title="Execution record" />
					{#if !exec.recorded}
						<p class="text-xs text-muted-foreground">
							The executor stored no runtime record (queue timings, attempts) for this job · lane <span class="font-mono">{exec.lane}</span> · attempt {exec.attempt + 1}
						</p>
					{:else}
						<dl class="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-4">
							<div><dt class="text-[11px] text-muted-foreground">Lane</dt><dd class="font-mono">{exec.lane}{exec.endpoint_slug ? ` · ${exec.endpoint_slug}` : ''}</dd></div>
							<div><dt class="text-[11px] text-muted-foreground">Attempt</dt><dd class="font-mono tabular-nums">{exec.attempt + 1}</dd></div>
							<div><dt class="text-[11px] text-muted-foreground" title="Global start order of the executor (started_seq)">Run order</dt><dd class="font-mono tabular-nums">{exec.run_order ?? exec.started_seq ?? '—'}</dd></div>
							<div><dt class="text-[11px] text-muted-foreground">Scheduling policy</dt><dd class="font-mono">{exec.policy ?? '—'}</dd></div>
							<div><dt class="text-[11px] text-muted-foreground">Queue (this attempt)</dt><dd class="font-mono tabular-nums">{fmtSeconds(exec.queue_seconds)}</dd></div>
							<div><dt class="text-[11px] text-muted-foreground" title="Time requests waited for a free slot of the endpoint">LLM slot wait</dt><dd class="font-mono tabular-nums">{fmtSeconds(exec.llm_queue_wait_s)}</dd></div>
							<div><dt class="text-[11px] text-muted-foreground">Sequence</dt><dd class="font-mono tabular-nums">{exec.sequence ?? '—'}</dd></div>
							<div><dt class="text-[11px] text-muted-foreground">Infra errors (this attempt)</dt><dd class="font-mono tabular-nums">{fmtInt(exec.infra_error_count)}</dd></div>
						</dl>
						{#if exec.waiting_reason}
							<p class="text-xs"><StatusBadge status={exec.waiting_reason} /> until <span class="font-mono">{formatDateCompact(exec.run_after)}</span></p>
						{/if}
						{#if exec.reevaluated}
							<p class="text-[11px] text-muted-foreground">A re-evaluation is queued or running; the evaluation shown is the previous one until it finishes.</p>
						{/if}
						{#if exec.attempts.length}
							<div class="space-y-1">
								<p class="section-label">Earlier attempts{#if priorCost != null} · {fmtUsd(priorCost)} spent{/if}</p>
								<ul class="space-y-1 text-xs">
									{#each exec.attempts as a, i (i)}
										<li class="rounded border px-2 py-1">
											<span class="font-mono">#{(a.attempt ?? i) + 1}</span>
											<span class="text-muted-foreground">· {humanizeKey(a.reason ?? 'unknown')}</span>
											{#if a.cost != null}<span class="font-mono tabular-nums"> · {fmtUsd(a.cost)}</span>{/if}
											{#if a.infra_errors}<span class="text-muted-foreground"> · {a.infra_errors} infra errors</span>{/if}
											{#if a.at}<span class="font-mono text-[11px] text-muted-foreground"> · {formatDateCompact(a.at)}</span>{/if}
											{#if a.error}<span class="block break-words text-[11px] text-muted-foreground">{a.error}</span>{/if}
										</li>
									{/each}
								</ul>
							</div>
						{/if}
					{/if}
				</div>
			{/if}
		{/if}
	</Card.Content>
</Card.Root>
