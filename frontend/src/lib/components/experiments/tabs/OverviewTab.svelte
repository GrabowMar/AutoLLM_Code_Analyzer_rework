<script lang="ts">
	/**
	 * OverviewTab — the live monitor: KPIs (done, throughput, ETA, tokens/s,
	 * cost, bottleneck), overall progress, slot lanes, one EndpointLane per
	 * endpoint, the condition × app grid and the activity feed.
	 *
	 * Props: `experiment`, `live` (ExperimentLive), `appName(id)`.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import { Segmented } from '$lib/components/ui/segmented';
	import * as Select from '$lib/components/ui/select';
	import { cn } from '$lib/utils';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import BatchProgress from '$lib/components/generator/batches/BatchProgress.svelte';
	import { formatUsd } from '$lib/components/generator/batches/batch-utils';
	import type { ExperimentDetail } from '$lib/api/types/experiments';
	import Grid3x3 from '@lucide/svelte/icons/grid-3x3';
	import ActivityFeed from '../monitor/ActivityFeed.svelte';
	import ConditionGrid from '../monitor/ConditionGrid.svelte';
	import EndpointLane, { type LaneJob } from '../monitor/EndpointLane.svelte';
	import { cellIndex, endpointGroupKey, type GridGroupBy, type GridSort } from '../cells';
	import type { ExperimentLive } from '../live.svelte';
	import { bottleneckLabel, formatEta, toBatchCounts } from '../format';

	interface Props {
		experiment: ExperimentDetail;
		live: ExperimentLive;
		appName: (id: number | null) => string;
	}

	let { experiment, live, appName }: Props = $props();

	let groupBy = $state<GridGroupBy>('endpoint');
	let sort = $state<GridSort>('sequence');

	const status = $derived(live.status);
	const grid = $derived(live.grid);
	const bottleneck = $derived(bottleneckLabel(status?.bottleneck));
	const tokensPerS = $derived(
		(status?.endpoints ?? []).reduce((sum, e) => sum + (e.tokens_per_s_5m ?? 0), 0)
	);
	const costUsd = $derived((status?.conditions ?? []).reduce((sum, c) => sum + (c.cost_usd ?? 0), 0));
	const conditionLabel = $derived(new Map((grid?.conditions ?? []).map((c) => [c.id, c.label])));

	const PHASES: Record<string, string> = { r: 'running', q: 'awaiting eval', e: 'evaluating' };

	/** This experiment's in-flight cells per endpoint slug (from the grid). */
	const laneJobs = $derived.by(() => {
		const out = new Map<string, LaneJob[]>();
		if (!grid) return out;
		grid.conditions.forEach((condition, ci) => {
			const key = endpointGroupKey(condition.endpoint_slug);
			grid.apps.forEach((app, ai) => {
				for (let r = 0; r < grid.repeats; r++) {
					const i = cellIndex(grid, ci, ai, r);
					const code = grid.codes[i];
					const jobId = grid.jobIds[i];
					if (!code || !jobId || !(code in PHASES)) continue;
					const list = out.get(key) ?? [];
					list.push({ jobId, label: condition.label, app: app.name, repeat: r, phase: PHASES[code] });
					out.set(key, list);
				}
			});
		});
		return out;
	});

	const connectionText = $derived(
		live.connection === 'open' && !live.polling
			? { text: 'Live', tone: 'text-success-fg' }
			: live.connection === 'closed'
				? { text: 'Finished — not live', tone: 'text-muted-foreground' }
				: live.polling
					? { text: 'Polling every 5 s', tone: 'text-warning-fg' }
					: { text: 'Connecting…', tone: 'text-muted-foreground' }
	);

	const groupItems = [
		{ value: 'endpoint', label: 'By endpoint' },
		{ value: 'none', label: 'Flat' },
	];
	const sortItems = [
		{ value: 'sequence', label: 'Launch order' },
		{ value: 'label', label: 'Name' },
		{ value: 'progress', label: 'Progress' },
		{ value: 'pass', label: 'Pass rate' },
	];
	const lanes = $derived(
		status
			? [
					{ label: 'Sandboxes', ...status.lanes.sandbox },
					{ label: 'Eval', ...status.lanes.eval },
					{ label: 'LLM jobs', ...status.lanes.llm },
					{ label: 'Post', ...status.lanes.post },
				]
			: []
	);
</script>

{#if !status}
	{#if live.loading}
		<LoadingState label="loading live status" />
	{:else}
		<EmptyState title="No live status" description={live.error || 'The status could not be loaded.'}>
			{#snippet action()}
				<Button size="sm" variant="outline" onclick={() => void live.resync()}>Retry</Button>
			{/snippet}
		</EmptyState>
	{/if}
{:else}
	<div class="space-y-6">
		<div class="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
			<div class="kpi-card">
				<span class="kpi-label">Done</span>
				<span class="kpi-value text-2xl">{status.cells.done}<span class="text-base font-normal text-muted-foreground">/{status.cells.total}</span></span>
				<span class="text-[11px] text-muted-foreground">
					{status.cells.in_flight} in flight{status.cells.failed_final ? ` · ${status.cells.failed_final} failed` : ''}{status.cells.retried
						? ` · ${status.cells.retried} retried`
						: ''}
				</span>
			</div>
			<div class="kpi-card">
				<span class="kpi-label">Throughput</span>
				<span class="kpi-value text-2xl">{status.throughput.per_hour_ewma.toFixed(1)}</span>
				<span class="text-[11px] text-muted-foreground">cells/h · {status.throughput.completed_last_15m} in 15 min</span>
			</div>
			<div class="kpi-card">
				<span class="kpi-label">ETA</span>
				<span class="kpi-value text-2xl">{formatEta(status.eta_seconds)}</span>
				<span class="text-[11px] text-muted-foreground">slowest endpoint + evaluation</span>
			</div>
			<div class="kpi-card">
				<span class="kpi-label">Tokens/s</span>
				<span class="kpi-value text-2xl">{tokensPerS ? tokensPerS.toFixed(0) : '—'}</span>
				<span class="text-[11px] text-muted-foreground">all endpoints, last 5 min</span>
			</div>
			<div class="kpi-card">
				<span class="kpi-label">Cost</span>
				<span class="kpi-value text-2xl">{costUsd > 0 ? formatUsd(costUsd) : '—'}</span>
				<span class="text-[11px] text-muted-foreground">OpenRouter only</span>
			</div>
			<div class="kpi-card" title={bottleneck?.title ?? ''}>
				<span class="kpi-label">Bottleneck</span>
				<span class="text-sm font-semibold">{bottleneck?.label ?? 'None'}</span>
				<span class="line-clamp-2 text-[11px] text-muted-foreground">{bottleneck?.title || 'Nothing is waiting on capacity.'}</span>
			</div>
		</div>

		<div class="space-y-2">
			<BatchProgress counts={toBatchCounts(status.counts)} total={status.cells.total} legend />
			<div class="flex flex-wrap items-center justify-between gap-2 text-[11px]">
				<p class="flex flex-wrap gap-x-3 font-mono tabular-nums text-muted-foreground">
					{#each lanes as lane (lane.label)}
						<span><span class="font-sans">{lane.label}</span> <span class={cn(lane.total > 0 && lane.used >= lane.total ? 'text-warning-fg' : 'text-foreground')}>{lane.used}/{lane.total}</span></span>
					{/each}
				</p>
				<p class={cn('inline-flex items-center gap-1.5', connectionText.tone)} role="status">
					<span class={cn('status-dot h-1.5 w-1.5', live.connection === 'open' && !live.polling && 'status-dot-live')} aria-hidden="true"></span>
					{connectionText.text}
				</p>
			</div>
		</div>

		{#if status.endpoints.length > 0}
			<section class="space-y-2" aria-label="Endpoints">
				<h2 class="section-label">Endpoints</h2>
				<div class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
					{#each status.endpoints as endpoint (endpoint.id)}
						<EndpointLane
							{endpoint}
							experimentId={experiment.id}
							activeJobs={laneJobs.get(endpoint.kind === 'openrouter' ? 'openrouter' : endpoint.slug) ?? []}
						/>
					{/each}
				</div>
			</section>
		{/if}

		<Card.Root>
			<Card.Header class="flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
				<div class="space-y-1">
					<Card.Title>Cells</Card.Title>
					<Card.Description>One square per run (condition × app × repeat). Open a square for its run.</Card.Description>
				</div>
				<div class="flex flex-wrap items-end gap-2">
					<Segmented aria-label="Group conditions" size="sm" value={groupBy} items={groupItems} onValueChange={(v) => (groupBy = v as GridGroupBy)} />
					<div class="w-36 space-y-1">
						<Label class="sr-only" for="grid-sort">Sort conditions</Label>
						<Select.Simple id="grid-sort" size="sm" value={sort} items={sortItems} onValueChange={(v) => (sort = v as GridSort)} />
					</div>
				</div>
			</Card.Header>
			<Card.Content>
				{#if grid && grid.conditions.length > 0}
					<ConditionGrid {grid} {groupBy} {sort} baselineId={experiment.baseline_condition_id} />
				{:else}
					<EmptyState compact icon={Grid3x3} title="No cells yet" />
				{/if}
			</Card.Content>
		</Card.Root>

		<Card.Root>
			<Card.Header>
				<Card.Title>Activity</Card.Title>
				<Card.Description>The latest run transitions while this page is open.</Card.Description>
			</Card.Header>
			<Card.Content>
				<ActivityFeed
					items={live.activity}
					conditionLabel={(id) => (id != null ? (conditionLabel.get(id) ?? `Condition #${id}`) : '—')}
					{appName}
				/>
			</Card.Content>
		</Card.Root>
	</div>
{/if}
