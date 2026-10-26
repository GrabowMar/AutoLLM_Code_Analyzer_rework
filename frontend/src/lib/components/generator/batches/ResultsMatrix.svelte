<script lang="ts">
	/**
	 * Batch results matrix: rows = task, columns = condition, each cell
	 * aggregates its trials (k/n passed, mean pass rate, validity markers,
	 * one dot per trial). Clicking a cell lists its trials with links.
	 * Scrolls sideways with a sticky task column on narrow screens.
	 *
	 * Props:
	 * - `jobs`: the batch's job list items (`/batches/{id}/jobs/`).
	 * - `matrix`: a prebuilt matrix instead of `jobs` (experiment results:
	 *   `fromResultsMatrix(results)`, so it applies the server's filters).
	 * - `mode`: batch mode ('agent' | 'scaffolding' | …) — picks the column axis.
	 * - `graded`: false for free-form tasks (cells show progress, not pass rates).
	 * - `transpose` (default false): conditions as rows and tasks as columns
	 *   (for many conditions); the per-condition aggregate becomes the last column.
	 */
	import { tick } from 'svelte';
	import type { GenerationJobList } from '$lib/api/generation';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import X from '@lucide/svelte/icons/x';
	import CircleX from '@lucide/svelte/icons/circle-x';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import Ban from '@lucide/svelte/icons/ban';
	import GitFork from '@lucide/svelte/icons/git-fork';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import ArrowUpRight from '@lucide/svelte/icons/arrow-up-right';
	import { cn } from '$lib/utils';
	import { tableFrame, shortModelName } from '$lib/components/table';
	import {
		TRIAL_DOT_CLASSES,
		TRIAL_OUTCOME_LABELS,
		formatPassRate,
		formatUsd,
		jobDisplayStatus,
		jobPhaseTones,
		shortId,
		type TrialOutcome,
	} from './batch-utils';
	import {
		buildResultsMatrix,
		cellKey,
		type CellStats,
		type MatrixCell,
		type MatrixColumn,
		type MatrixRow,
		type ResultsMatrix as ResultsMatrixData,
	} from './matrix';

	interface Props {
		jobs?: GenerationJobList[];
		matrix?: ResultsMatrixData | null;
		mode: string;
		graded: boolean;
		transpose?: boolean;
	}

	let { jobs = [], matrix: prebuilt = null, mode, graded, transpose = false }: Props = $props();

	const matrix = $derived(prebuilt ?? buildResultsMatrix(jobs, mode, graded));

	// Display axes: tasks × conditions, or conditions × tasks when transposed.
	type Axis = { kind: 'task'; item: MatrixRow } | { kind: 'condition'; item: MatrixColumn };
	const taskAxis = $derived(matrix.rows.map((item): Axis => ({ kind: 'task', item })));
	const conditionAxis = $derived(matrix.columns.map((item): Axis => ({ kind: 'condition', item })));
	const displayRows = $derived(transpose ? conditionAxis : taskAxis);
	const displayCols = $derived(transpose ? taskAxis : conditionAxis);
	const cornerLabel = $derived(transpose ? 'Condition' : 'Task');
	const showAggregate = $derived(matrix.rows.length > 1);

	function cellAt(row: Axis, col: Axis): MatrixCell | undefined {
		const task = row.kind === 'task' ? row.item : (col.item as MatrixRow);
		const condition = row.kind === 'condition' ? row.item : (col.item as MatrixColumn);
		return matrix.cells.get(cellKey(task.key, condition.key));
	}
	const columnLabel = $derived(
		mode === 'agent' ? 'Harness preset' : mode === 'scaffolding' ? 'Profile' : ''
	);

	let selectedKey = $state<string | null>(null);
	// Survives refreshes (keys are stable); disappears if the cell does.
	const selected = $derived(selectedKey ? (matrix.cells.get(selectedKey) ?? null) : null);

	const outcomesPresent = $derived.by(() => {
		const seen = new Set<TrialOutcome>();
		for (const cell of matrix.cells.values()) for (const t of cell.trials) seen.add(t.outcome);
		return (Object.keys(TRIAL_OUTCOME_LABELS) as TrialOutcome[]).filter((o) => seen.has(o));
	});

	async function toggle(cell: MatrixCell) {
		selectedKey = selectedKey === cell.key ? null : cell.key;
		if (!selectedKey) return;
		// The trial list renders below the matrix; bring it on screen.
		await tick();
		document.getElementById('matrix-cell-detail')?.scrollIntoView({ block: 'nearest' });
	}

	function headline(stats: CellStats): { value: string; unit: string } {
		if (graded && stats.graded > 0) return { value: `${stats.passed}/${stats.graded}`, unit: 'passed' };
		return { value: `${stats.finished}/${stats.total}`, unit: 'done' };
	}

	function cellSummary(cell: MatrixCell): string {
		const s = cell.stats;
		const parts = [`${cell.row.label}, ${cell.column.model}${cell.column.variant ? ` (${cell.column.variant})` : ''}`];
		if (graded && s.graded > 0) parts.push(`${s.passed} of ${s.graded} graded trials passed, mean ${formatPassRate(s.meanPassRate)}`);
		parts.push(`${s.finished} of ${s.total} trials finished`);
		if (s.active) parts.push(`${s.active} in progress`);
		if (s.failed) parts.push(`${s.failed} failed`);
		if (s.warn) parts.push(`${s.warn} with validity warnings`);
		if (s.excluded) parts.push(`${s.excluded} excluded`);
		if (s.mixedConfigs) parts.push('trials ran under different configurations');
		return parts.join('; ');
	}

	const evalTones = { not_applicable: 'neutral', infra_error: 'warning' } as const;
</script>

{#snippet dots(cell: MatrixCell)}
	<span class="flex flex-wrap gap-1" aria-hidden="true">
		{#each cell.trials as trial (trial.job.id)}
			<span
				class={cn(
					'status-dot h-2.5 w-2.5',
					TRIAL_DOT_CLASSES[trial.outcome],
					trial.outcome === 'running' && 'status-dot-live text-info',
					trial.superseded && 'opacity-35'
				)}
				title="{trial.label}: {TRIAL_OUTCOME_LABELS[trial.outcome]}{trial.superseded ? ' (replaced by a retry)' : ''}"
			></span>
		{/each}
	</span>
{/snippet}

{#snippet markers(stats: CellStats)}
	{#if stats.failed || stats.warn || stats.excluded || stats.mixedConfigs || stats.superseded}
		<span class="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px]" aria-hidden="true">
			{#if stats.failed}
				<span class="inline-flex items-center gap-0.5 text-danger-fg" title="{stats.failed} runs failed">
					<CircleX class="h-3 w-3" />{stats.failed}
				</span>
			{/if}
			{#if stats.warn}
				<span class="inline-flex items-center gap-0.5 text-warning-fg" title="{stats.warn} trials with validity warnings">
					<TriangleAlert class="h-3 w-3" />{stats.warn}
				</span>
			{/if}
			{#if stats.excluded}
				<span class="inline-flex items-center gap-0.5 text-danger-fg" title="{stats.excluded} trials excluded from analyses">
					<Ban class="h-3 w-3" />{stats.excluded}
				</span>
			{/if}
			{#if stats.mixedConfigs}
				<span class="inline-flex items-center gap-0.5 text-warning-fg" title="Trials ran under different configurations">
					<GitFork class="h-3 w-3" />mixed
				</span>
			{/if}
			{#if stats.superseded}
				<span class="inline-flex items-center gap-0.5 text-muted-foreground" title="{stats.superseded} attempts replaced by a retry">
					<RotateCcw class="h-3 w-3" />{stats.superseded}
				</span>
			{/if}
		</span>
	{/if}
{/snippet}

{#snippet axisLabel(axis: Axis, header: boolean)}
	{#if axis.kind === 'task'}
		<span class={cn('line-clamp-2 break-words hyphens-auto', header ? 'text-xs font-semibold text-foreground' : '')} title={axis.item.slug ?? axis.item.label}>{axis.item.label}</span>
		{#if axis.item.slug === null}
			<span class="block text-[11px] font-normal text-muted-foreground">free-form</span>
		{/if}
	{:else}
		{@const col = axis.item}
		<span class="line-clamp-2 break-words text-xs font-semibold text-foreground" title="{col.model}{col.modelId ? ` (${col.modelId})` : ''}">{shortModelName(col.model)}</span>
		{#if col.variant}
			<span class="block truncate font-mono text-[11px] font-normal text-muted-foreground" title="{columnLabel}: {col.variant}">{col.variant}</span>
		{/if}
		{#if col.conditionId != null}
			<span class="block text-[11px] font-normal text-muted-foreground">condition #{col.conditionId}</span>
		{/if}
	{/if}
{/snippet}

{#snippet aggregate(stats: CellStats | undefined, title: string)}
	{#if stats}
		{@const head = headline(stats)}
		<span class="flex items-baseline justify-between gap-2">
			<span class="font-mono text-sm font-semibold tabular-nums">
				{head.value}<span class="ml-1 font-sans text-[11px] font-normal text-muted-foreground">{head.unit}</span>
			</span>
			{#if graded && stats.meanPassRate != null}
				<span class="font-mono text-xs tabular-nums text-muted-foreground" {title}>{formatPassRate(stats.meanPassRate)}</span>
			{/if}
		</span>
	{/if}
{/snippet}

<div class="space-y-3">
	<div class="table-frame rounded-md border border-border bg-card" role="region" aria-label="Results matrix" {@attach tableFrame}>
		<table class="w-max min-w-full">
			<thead>
				<tr>
					<th scope="col" class="col-sticky w-28 min-w-28 max-w-28 align-bottom sm:w-auto sm:min-w-36 sm:max-w-56">{cornerLabel}</th>
					{#each displayCols as col (col.item.key)}
						<th scope="col" class="min-w-36 max-w-56 align-bottom font-sans normal-case tracking-normal">
							{@render axisLabel(col, true)}
						</th>
					{/each}
					{#if transpose && showAggregate}
						<th scope="col" class="min-w-32 align-bottom font-sans normal-case tracking-normal">
							<span class="block text-xs font-semibold text-foreground">All tasks</span>
						</th>
					{/if}
				</tr>
			</thead>
			<tbody>
				{#each displayRows as row (row.item.key)}
					<tr class="group">
						<th
							scope="row"
							class="col-sticky top-auto z-[1] w-28 min-w-28 max-w-28 border-b-border/60 bg-card font-sans normal-case tracking-normal backdrop-blur-none sm:w-auto sm:min-w-36 sm:max-w-56 group-hover:bg-[color-mix(in_oklab,var(--primary)_5%,var(--card))] align-top text-sm font-medium text-foreground"
						>
							{@render axisLabel(row, false)}
						</th>
						{#each displayCols as col (col.item.key)}
							{@const cell = cellAt(row, col)}
							<td class="p-1 align-top">
								{#if cell}
									{@const head = headline(cell.stats)}
									<button
										type="button"
										class={cn(
											'flex w-full min-w-32 cursor-pointer flex-col gap-1.5 rounded-md border px-2.5 py-2 text-left transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40',
											selectedKey === cell.key
												? 'border-primary/60 bg-primary/10'
												: 'border-transparent hover:border-border hover:bg-muted/50'
										)}
										aria-expanded={selectedKey === cell.key}
										aria-controls="matrix-cell-detail"
										onclick={() => toggle(cell)}
									>
										<span class="sr-only">{cellSummary(cell)}</span>
										<span class="flex items-baseline justify-between gap-2" aria-hidden="true">
											<span class="font-mono text-sm font-semibold tabular-nums">
												{head.value}<span class="ml-1 font-sans text-[11px] font-normal text-muted-foreground">{head.unit}</span>
											</span>
											{#if graded && cell.stats.meanPassRate != null}
												<span class="font-mono text-xs tabular-nums text-muted-foreground" title="Mean pass rate">{formatPassRate(cell.stats.meanPassRate)}</span>
											{/if}
										</span>
										{@render dots(cell)}
										{@render markers(cell.stats)}
									</button>
								{:else}
									<span class="block px-2.5 py-2 text-muted-foreground" aria-label="No runs">—</span>
								{/if}
							</td>
						{/each}
						{#if transpose && showAggregate}
							<td class="border-l border-border px-3.5 align-top">
								{@render aggregate(matrix.columnStats.get(row.item.key), 'Mean pass rate over every graded trial')}
							</td>
						{/if}
					</tr>
				{/each}
			</tbody>
			{#if !transpose && showAggregate}
				<tfoot>
					<tr>
						<th scope="row" class="col-sticky top-auto z-[1] w-28 min-w-28 max-w-28 border-b-border/60 bg-card font-sans normal-case tracking-normal backdrop-blur-none sm:w-auto sm:min-w-36 sm:max-w-56 border-t border-t-border align-top text-xs font-medium text-muted-foreground">
							All tasks
						</th>
						{#each matrix.columns as col (col.key)}
							<td class="border-t border-border px-3.5 align-top">
								{@render aggregate(matrix.columnStats.get(col.key), 'Mean pass rate over every graded trial')}
							</td>
						{/each}
					</tr>
				</tfoot>
			{/if}
		</table>
	</div>

	{#if outcomesPresent.length}
		<ul class="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground" aria-label="Trial dot legend">
			{#each outcomesPresent as outcome (outcome)}
				<li class="inline-flex items-center gap-1.5">
					<span class={cn('status-dot h-2.5 w-2.5', TRIAL_DOT_CLASSES[outcome])} aria-hidden="true"></span>
					{TRIAL_OUTCOME_LABELS[outcome]}
				</li>
			{/each}
		</ul>
	{/if}
	<p class="text-[11px] text-muted-foreground">
		{#if graded}
			"k/n passed" counts trials with a conformance result that passed every check; failed runs, excluded trials
			and attempts replaced by a retry are shown as markers and never counted.
		{:else}
			Free-form tasks have no app spec, so runs are not graded.
		{/if}
	</p>

	<div id="matrix-cell-detail" aria-live="polite">
		{#if selected}
			<Card.Root>
				<Card.Header class="pb-2">
					<div class="flex items-start justify-between gap-2">
						<div class="min-w-0">
							<Card.Title class="text-sm">{selected.row.label} × {selected.column.model}</Card.Title>
							<Card.Description class="font-mono text-[11px]">
								{[selected.column.variant, selected.column.conditionId != null ? `condition #${selected.column.conditionId}` : '']
									.filter(Boolean)
									.join(' · ') || selected.column.modelId}
							</Card.Description>
						</div>
						<Button variant="ghost" size="icon" class="h-8 w-8" onclick={() => (selectedKey = null)} aria-label="Close trial list">
							<X class="h-4 w-4" />
						</Button>
					</div>
				</Card.Header>
				<Card.Content class="space-y-2">
					{#if selected.stats.mixedConfigs}
						<p class="text-[11px] text-warning-fg">
							These trials do not share one configuration fingerprint, so they are not pure replicates. Compare
							the fingerprints below before pooling them.
						</p>
					{/if}
					<ul class="data-list shadow-none">
						{#each selected.trials as trial (trial.job.id)}
							{@const job = trial.job}
							<li class={cn('data-list-row flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-2 text-sm', trial.superseded && 'opacity-60')}>
								<span class="w-16 shrink-0 font-medium">{trial.label}</span>
								<StatusBadge status={jobDisplayStatus(job)} tones={jobPhaseTones} />
								{#if job.eval_status}
									<StatusBadge status={job.eval_status} tones={evalTones} />
								{/if}
								{#if job.pass_rate != null}
									<span class="font-mono text-xs tabular-nums">{formatPassRate(job.pass_rate)}</span>
								{/if}
								{#if job.validity === 'exclude'}
									<span title="Excluded from analyses (validity)"><StatusBadge status="excluded" tone="danger" /></span>
								{:else if job.validity === 'warn'}
									<span title="Valid, with validity warnings"><StatusBadge status="warn" tone="warning" label="warning" /></span>
								{/if}
								{#if job.termination && job.termination !== 'completed'}
									<span class="text-[11px] text-muted-foreground">{job.termination.replace(/_/g, ' ')}</span>
								{/if}
								{#if trial.superseded}
									<span class="text-[11px] text-muted-foreground">replaced by a retry</span>
								{/if}
								<span class="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
									{#if job.cost_usd != null}<span class="font-mono tabular-nums">{formatUsd(job.cost_usd)}</span>{/if}
									{#if job.experiment_seed != null}<span class="font-mono tabular-nums" title="Sampling seed">seed {job.experiment_seed}</span>{/if}
									{#if selected.stats.mixedConfigs && job.config_fingerprint}
										<span class="font-mono" title="Configuration fingerprint">{job.config_fingerprint.slice(0, 10)}</span>
									{/if}
									<a
										href="/applications/{job.id}"
										class="inline-flex items-center gap-0.5 font-medium text-success-fg hover:underline"
										aria-label="Open {trial.label} ({shortId(job.id)})"
									>
										Open<ArrowUpRight class="h-3 w-3" />
									</a>
								</span>
							</li>
						{/each}
					</ul>
				</Card.Content>
			</Card.Root>
		{/if}
	</div>
</div>
