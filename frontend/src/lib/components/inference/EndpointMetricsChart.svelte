<script lang="ts">
	/**
	 * EndpointMetricsChart — last-hour activity of an endpoint, one metric at a
	 * time (no dual axes): slots in use (average in-flight requests per minute)
	 * or tokens per minute, from `GET /inference/endpoints/{id}/metrics/`.
	 *
	 * Props:
	 * - `endpointId`, `minutes` (default 60).
	 * - `refreshKey`: change it to refetch silently (keeps the previous render).
	 * - `capacity`: max_concurrency, shown in the slots summary.
	 * - `height` (default 180).
	 */
	import { AreaChart } from 'layerchart';
	import { getEndpointMetrics } from '$lib/api/inference';
	import type { EndpointMetricsPoint } from '$lib/api/types/inference';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { formatNumber, formatTokens } from '$lib/utils/formatters';
	import { Segmented } from '$lib/components/ui/segmented';
	import ChartEmpty from '$lib/components/charts/ChartEmpty.svelte';
	import ChartSkeleton from '$lib/components/charts/ChartSkeleton.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { seriesColor } from '$lib/components/charts/theme';

	interface Props {
		endpointId: number;
		minutes?: number;
		refreshKey?: unknown;
		capacity?: number;
		height?: number;
	}

	let { endpointId, minutes = 60, refreshKey, capacity, height = 180 }: Props = $props();

	let points = $state<EndpointMetricsPoint[] | null>(null);
	let error = $state('');
	let metric = $state<'slots' | 'tokens'>('slots');

	async function load(signal?: AbortSignal) {
		try {
			const result = await getEndpointMetrics(endpointId, minutes, { signal });
			points = result.points;
			error = '';
		} catch (e) {
			if (isAbortError(e)) return;
			// A failed silent refresh keeps the chart on screen.
			if (points === null) error = formatApiError(e, 'Could not load metrics.');
		}
	}

	$effect(() => {
		void refreshKey;
		void endpointId;
		void minutes;
		const controller = new AbortController();
		void load(controller.signal);
		return () => controller.abort();
	});

	const data = $derived(
		(points ?? []).map((p) => ({
			date: new Date(p.minute),
			slots: p.inflight_avg,
			tokens: p.tokens,
		}))
	);
	const totals = $derived.by(() => {
		const list = points ?? [];
		return {
			completed: list.reduce((n, p) => n + p.completed, 0),
			errors: list.reduce((n, p) => n + p.errors, 0),
			tokens: list.reduce((n, p) => n + p.tokens, 0),
			peak: list.reduce((n, p) => Math.max(n, p.inflight_avg), 0),
		};
	});
	const hasActivity = $derived(totals.completed > 0 || totals.errors > 0 || totals.peak > 0);
	const series = $derived(
		metric === 'slots'
			? [{ key: 'slots', label: 'Slots in use (avg)', color: seriesColor(1) }]
			: [{ key: 'tokens', label: 'Tokens per minute', color: seriesColor(2) }]
	);
	const timeFormat = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' });
</script>

<div class="space-y-3">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<Segmented
			aria-label="Metric"
			size="sm"
			bind:value={metric}
			items={[
				{ value: 'slots', label: 'Slots in use' },
				{ value: 'tokens', label: 'Tokens' },
			]}
		/>
		{#if points}
			<p class="text-xs tabular-nums text-muted-foreground">
				{totals.completed.toLocaleString()} requests · {formatTokens(totals.tokens)} tokens ·
				<span class={totals.errors > 0 ? 'text-danger-fg' : ''}>{totals.errors} errors</span>
				· peak {formatNumber(totals.peak)}{#if capacity}/{capacity}{/if} slots
			</p>
		{/if}
	</div>
	{#if error}
		<ErrorAlert message={error} onretry={() => load()} />
	{:else if points === null}
		<ChartSkeleton {height} />
	{:else if !hasActivity}
		<ChartEmpty {height} message="No requests in the last {minutes} minutes." />
	{:else}
		<div class="w-full" style="height: {height}px">
			<AreaChart
				{data}
				x="date"
				{series}
				padding={{ left: 28, bottom: 20, top: 8, right: 8 }}
				props={{
					area: { fillOpacity: 0.12, line: { strokeWidth: 2 } },
					grid: { class: 'stroke-[color:var(--border)]' },
					xAxis: { format: (d: Date) => timeFormat.format(d), tickLength: 0 },
					yAxis: { tickLength: 0 },
					highlight: { points: { r: 4, strokeWidth: 2, stroke: 'var(--card)' } },
				}}
			/>
		</div>
		<div class="flex items-center gap-4 text-xs text-muted-foreground">
			{#each series as s (s.key)}
				<span class="inline-flex items-center gap-1.5">
					<span class="h-0.5 w-4 rounded-full" style="background: {s.color}"></span>
					{s.label}
				</span>
			{/each}
		</div>
	{/if}
</div>
