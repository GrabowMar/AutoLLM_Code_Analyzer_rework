<script lang="ts">
	/**
	 * StudyProgressPanel — live execution state of a launched study: launch
	 * submission, run counts and progress, cost so far vs estimate, timing,
	 * stale runs, the global queue and daemon capacity, per-tool health and
	 * the targets × tools status matrix (click a cell to open the run).
	 *
	 * Props: `progress: StudyProgress`, `onresume()` (resume an interrupted
	 * launch; omitted = no action), `resuming`.
	 */
	import type { StudyProgress } from '$lib/api/analysis';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import { toneClasses, statusTone } from '$lib/constants/colors';
	import { cn } from '$lib/utils';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Play from '@lucide/svelte/icons/play';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import StatusMatrix from './StatusMatrix.svelte';
	import Hint from '../Hint.svelte';
	import { toolCellHref } from './report/provenance';
	import {
		LAUNCH_STATUS_LABELS,
		fmtBytes,
		fmtInt,
		fmtPct,
		fmtSeconds,
		fmtUsd,
		shortHash
	} from './format';

	interface Props {
		progress: StudyProgress;
		onresume?: () => void;
		resuming?: boolean;
	}

	let { progress, onresume, resuming = false }: Props = $props();

	const counts = $derived(progress.counts);
	const launch = $derived(progress.launch);
	const launchPending = $derived(launch.status === 'submitting' || launch.status === 'interrupted');

	const RUN_SEGMENTS: {
		key: keyof StudyProgress['counts']['runs'];
		label: string;
		color: string;
	}[] = [
		{ key: 'completed', label: 'Completed', color: 'var(--success)' },
		{ key: 'partial', label: 'Partial', color: 'var(--warning)' },
		{ key: 'failed', label: 'Failed', color: 'var(--destructive)' },
		{ key: 'cancelled', label: 'Cancelled', color: 'var(--muted-foreground)' },
		{ key: 'running', label: 'Running', color: 'var(--info)' },
		{ key: 'queued', label: 'Queued', color: 'var(--border)' }
	];
	const segmentTotal = $derived(Math.max(1, counts.planned || counts.submitted));

	const sortedRows = $derived(
		[...progress.rows].sort(
			(a, b) => (a.group || '').localeCompare(b.group || '') || a.label.localeCompare(b.label)
		)
	);
	const matrixRows = $derived(
		sortedRows.map((r) => ({
			key: r.run_id,
			label: r.label,
			group: r.group,
			href: `/analysis/${r.run_id}`,
			note: r.stale
				? 'stale'
				: r.cancel_requested && (r.status === 'running' || r.status === 'queued')
					? 'cancelling'
					: r.status === 'running' && r.phase
						? r.phase.replace(/^running:/, 'running ').replace(/_/g, ' ')
						: undefined
		}))
	);
	const matrixCells = $derived(
		sortedRows.map((r) => progress.tools.map((t) => r.tools[t]?.status ?? 'missing'))
	);
	const matrixReasons = $derived(
		sortedRows.map((r) => progress.tools.map((t) => r.tools[t]?.failure_reason ?? ''))
	);

	const TOOL_SEGMENTS = [
		'succeeded',
		'not_applicable',
		'failed',
		'timed_out',
		'skipped',
		'cancelled',
		'running',
		'pending'
	] as const;
	const toolBad = (c: Partial<Record<string, number>>) =>
		(c.failed ?? 0) + (c.timed_out ?? 0) + (c.skipped ?? 0) + (c.cancelled ?? 0);

	const capacity = $derived(
		progress.capacity && !('error' in progress.capacity) ? progress.capacity : null
	);
	const capacityError = $derived(
		progress.capacity && 'error' in progress.capacity ? progress.capacity.error : ''
	);
</script>

<div class="space-y-4">
	<!-- Launch -->
	{#if launch.status && launch.status !== 'submitted'}
		<div
			class={cn(
				'flex flex-wrap items-center justify-between gap-3 rounded-md border px-4 py-3 text-sm',
				toneClasses[statusTone(launch.status)]
			)}
			role="status"
		>
			<div class="flex items-center gap-2 text-foreground">
				{#if launch.status === 'submitting'}
					<LoaderCircle
						class="h-4 w-4 animate-spin motion-reduce:animate-none"
						aria-hidden="true"
					/>
				{:else}
					<TriangleAlert class="h-4 w-4" aria-hidden="true" />
				{/if}
				<span class="font-medium">{LAUNCH_STATUS_LABELS[launch.status]}</span>
				<span class="tabular-nums text-muted-foreground"
					>{launch.submitted}/{launch.planned ?? '—'} submitted</span
				>
				{#if launch.error}<span class="text-danger-fg">· {launch.error}</span>{/if}
			</div>
			{#if launchPending && launch.status !== 'submitting' && onresume}
				<Button size="sm" onclick={onresume} disabled={resuming}>
					{#if resuming}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Play
						/>{/if}
					Resume launch
				</Button>
			{/if}
		</div>
	{/if}

	<!-- KPIs -->
	<div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
		<div class="kpi-card">
			<span class="kpi-label">Progress</span>
			<span class="kpi-value"
				>{progress.progress === null ? '—' : fmtPct(progress.progress, 0)}</span
			>
			<span class="text-[11px] text-muted-foreground">
				{counts.terminal} of {counts.submitted} runs finished · {counts.active} active
			</span>
		</div>
		<div class="kpi-card">
			<span class="kpi-label">Complete runs</span>
			<span class="kpi-value">{counts.complete}</span>
			<span class="text-[11px] text-muted-foreground">
				all required tools OK · {counts.terminal - counts.complete} not complete
			</span>
		</div>
		<div class="kpi-card">
			<span class="kpi-label">AI cost so far</span>
			<span class="kpi-value"
				>{#if progress.cost.complete === false}<Hint
						text="Lower bound: the spend of {progress.cost.runs_with_unknown_cost ??
							'some'} run(s) is not fully known (a replicate or a replaced attempt without recorded cost)."
						>≥ {fmtUsd(progress.cost.so_far_usd)}</Hint
					>{:else}{fmtUsd(progress.cost.so_far_usd)}{/if}</span
			>
			<span class="text-[11px] text-muted-foreground">
				estimate {fmtUsd(progress.cost.estimate_usd)} · replaced attempts included
			</span>
		</div>
		<div class="kpi-card">
			<span class="kpi-label">Elapsed</span>
			<span class="kpi-value">{fmtSeconds(progress.timing.elapsed_seconds)}</span>
			<span class="text-[11px] text-muted-foreground">
				{#if counts.active > 0}ETA {progress.timing.eta_seconds === null
						? 'unknown'
						: fmtSeconds(progress.timing.eta_seconds)}{:else}no active run{/if}
			</span>
		</div>
	</div>

	<!-- Run status bar -->
	<Card.Root class="gap-3">
		<Card.Header class="flex flex-row flex-wrap items-baseline justify-between gap-2">
			<Card.Title>Runs</Card.Title>
			<span class="text-xs text-muted-foreground tabular-nums">
				{counts.eligible} eligible · {counts.planned} planned · {counts.submitted}
				submitted
				{#if counts.not_launched}· <span class="text-warning-fg"
						>{counts.not_launched} not launched</span
					>{/if}
			</span>
		</Card.Header>
		<Card.Content class="space-y-3">
			<div
				class="flex h-3 w-full overflow-hidden rounded-full bg-muted"
				role="img"
				aria-label="Run status distribution"
			>
				{#each RUN_SEGMENTS as seg (seg.key)}
					{#if counts.runs[seg.key] > 0}
						<span
							class="h-full"
							style="width: {(counts.runs[seg.key] / segmentTotal) * 100}%; background: {seg.color}"
							title="{seg.label}: {counts.runs[seg.key]}"
						></span>
					{/if}
				{/each}
			</div>
			<ul class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
				{#each RUN_SEGMENTS as seg (seg.key)}
					<li class="inline-flex items-center gap-1.5">
						<span class="h-2 w-2 rounded-full" style="background: {seg.color}"></span>
						<span class="text-foreground/90">{seg.label}</span>
						<span class="tabular-nums">{counts.runs[seg.key]}</span>
					</li>
				{/each}
			</ul>
			{#if progress.stale.count > 0}
				<div
					class="flex items-start gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs"
					role="alert"
				>
					<TriangleAlert class="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning-fg" aria-hidden="true" />
					<p>
						{progress.stale.count} run{progress.stale.count === 1 ? '' : 's'} stopped heartbeating for
						more than
						{progress.stale.threshold_seconds} s; the reconciler requeues them (or fails them after the
						retry limit).
						{#each progress.stale.run_ids as id, i (id)}
							{#if i > 0},{/if}
							<a class="font-mono text-foreground hover:underline" href="/analysis/{id}"
								>{shortHash(id, 8)}</a
							>
						{/each}
					</p>
				</div>
			{/if}
			{#if progress.not_launched.length > 0}
				<details class="rounded-md border border-border text-xs">
					<summary class="cursor-pointer px-3 py-2 font-medium text-warning-fg">
						{progress.not_launched.length} target(s) got no run
					</summary>
					<ul class="space-y-1 border-t border-border px-3 py-2">
						{#each progress.not_launched as skip (skip.job_id)}
							<li>
								<a class="hover:underline" href="/applications/{skip.job_id}">{skip.label}</a>
								<span class="text-muted-foreground"
									>— {skip.reason.replace(/_/g, ' ')}{skip.detail ? `: ${skip.detail}` : ''}</span
								>
							</li>
						{/each}
					</ul>
				</details>
			{/if}
		</Card.Content>
	</Card.Root>

	<div class="grid items-start gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
		<!-- Matrix -->
		<Card.Root class="min-w-0">
			<Card.Header>
				<Card.Title>Targets × tools</Card.Title>
				<Card.Description
					>Live tool status per run. Open a cell for the run's tool results and diagnostics.</Card.Description
				>
			</Card.Header>
			<Card.Content>
				<StatusMatrix
					rows={matrixRows}
					columns={progress.tools.map((t) => ({ key: t, label: t }))}
					cells={matrixCells}
					reasons={matrixReasons}
					cellHref={(i, j) => {
						const row = sortedRows[i];
						const tool = progress.tools[j];
						return row && tool
							? toolCellHref(row.run_id, tool, row.tools[tool]?.status ?? '')
							: null;
					}}
					rowHeader="Run"
					label="Tool status per study run"
				/>
			</Card.Content>
		</Card.Root>

		<div class="min-w-0 space-y-4">
			<!-- Tool health -->
			<Card.Root>
				<Card.Header>
					<Card.Title>Tool health</Card.Title>
					<Card.Description
						>A failed or timed-out tool leaves a gap, never a clean result.</Card.Description
					>
				</Card.Header>
				<Card.Content>
					<ul class="space-y-1.5 text-xs">
						{#each progress.tools as tool (tool)}
							{@const c = progress.tool_counts[tool] ?? {}}
							{@const total = Object.values(c).reduce((a, b) => a + (b ?? 0), 0)}
							{@const bad = toolBad(c)}
							<li class="grid grid-cols-[6.5rem_minmax(0,1fr)_auto] items-center gap-2">
								<span class="truncate font-mono" title={tool}>{tool}</span>
								<span class="flex h-2 overflow-hidden rounded-full bg-muted" aria-hidden="true">
									{#each TOOL_SEGMENTS as status (status)}
										{#if (c[status] ?? 0) > 0 && total > 0}
											<span
												class="h-full {status === 'succeeded'
													? 'bg-success'
													: status === 'not_applicable'
														? 'bg-muted-foreground/40'
														: status === 'running'
															? 'bg-info'
															: status === 'pending'
																? 'bg-border'
																: status === 'skipped'
																	? 'bg-warning'
																	: 'bg-destructive'}"
												style="width: {((c[status] ?? 0) / total) * 100}%"
											></span>
										{/if}
									{/each}
								</span>
								<span
									class="whitespace-nowrap tabular-nums"
									class:text-danger-fg={bad > 0}
									class:text-muted-foreground={bad === 0}
								>
									{#if bad > 0}{bad} not OK{:else}{c.succeeded ?? 0}/{total}{/if}
								</span>
							</li>
						{/each}
					</ul>
				</Card.Content>
			</Card.Root>

			<!-- Queue + capacity -->
			<Card.Root>
				<Card.Header><Card.Title>Queue and capacity</Card.Title></Card.Header>
				<Card.Content class="space-y-3 text-sm">
					<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1">
						<dt class="text-muted-foreground">Running (all users)</dt>
						<dd class="tabular-nums">
							{progress.queue.running_global} / {progress.queue.max_concurrent_runs} slots
						</dd>
						<dt class="text-muted-foreground">Queued (all users)</dt>
						<dd class="tabular-nums">{progress.queue.queued_global}</dd>
						<dt class="text-muted-foreground">Ahead of this study</dt>
						<dd class="tabular-nums">{progress.queue.queued_ahead}</dd>
					</dl>
					{#if capacity}
						<div class="space-y-1.5">
							<div class="flex justify-between text-xs text-muted-foreground">
								<span>Sandbox memory</span>
								<span class="tabular-nums"
									>{fmtBytes(capacity.used_bytes)} of {fmtBytes(capacity.budget_bytes)}</span
								>
							</div>
							<div
								class="h-2 overflow-hidden rounded-full bg-muted"
								role="img"
								aria-label="Sandbox memory used"
							>
								<span
									class="block h-full {capacity.free_bytes <= 0 ? 'bg-destructive' : 'bg-info'}"
									style="width: {Math.min(
										100,
										(capacity.used_bytes / Math.max(1, capacity.budget_bytes)) * 100
									)}%"
								></span>
							</div>
							<ul class="space-y-0.5 text-[11px] text-muted-foreground">
								{#each capacity.containers as c (c.name)}
									<li class="flex justify-between gap-2">
										<span class="truncate font-mono" title={c.name}>{c.name}</span>
										<span class="shrink-0"
											>{c.analysis ? 'analysis' : 'other'} · {c.unlimited
												? 'no limit'
												: fmtBytes(c.mem_bytes)}</span
										>
									</li>
								{/each}
							</ul>
						</div>
					{:else if capacityError}
						<p class="text-xs text-warning-fg">
							Capacity unknown: {capacityError}
						</p>
					{:else}
						<p class="text-xs text-muted-foreground">Capacity is reported while runs are active.</p>
					{/if}
				</Card.Content>
			</Card.Root>
		</div>
	</div>

	<p class="text-[11px] text-muted-foreground">
		Updated {new Date(progress.generated_at).toLocaleTimeString()} · {fmtInt(counts.targets)} targets
		({counts.excluded}
		excluded)
	</p>
</div>
