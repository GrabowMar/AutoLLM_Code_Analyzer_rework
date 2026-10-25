<script lang="ts">
	/**
	 * EndpointPlanRow — one endpoint in the composer's plan: models and cells
	 * placed on it, slots / job cap / resident limit, estimated model swaps for
	 * the chosen policy, and an est-hours bar scaled to the slowest endpoint.
	 *
	 * Props: `endpoint` (preview row), `maxHours` (bar scale), `swaps`, `policy`
	 * (effective policy label), `bottleneck` (this endpoint is the bottleneck).
	 */
	import { cn } from '$lib/utils';
	import { toneClasses } from '$lib/constants/colors';
	import type { PreviewEndpoint } from '$lib/api/types/experiments';
	import { kindLabel } from '$lib/components/inference';
	import { formatHours } from './format';

	interface Props {
		endpoint: PreviewEndpoint;
		maxHours: number;
		swaps?: number | null;
		policy?: string;
		bottleneck?: boolean;
	}

	let { endpoint, maxHours, swaps = null, policy = '', bottleneck = false }: Props = $props();

	const isOpenRouter = $derived(endpoint.kind === 'openrouter');
	const width = $derived(endpoint.est_hours != null && maxHours > 0 ? Math.max(3, (endpoint.est_hours / maxHours) * 100) : 0);
	const issues = $derived(endpoint.issues.filter((i) => i.code !== 'endpoint_offline'));
</script>

<li class="space-y-1.5 py-2 first:pt-0 last:pb-0">
	<div class="flex items-baseline justify-between gap-2 text-xs">
		<span class="flex min-w-0 items-center gap-1.5">
			<span
				class={cn('status-dot h-1.5 w-1.5 shrink-0', endpoint.online ? 'bg-success' : 'bg-destructive')}
				aria-hidden="true"
			></span>
			<span class="truncate font-mono font-medium" title="{endpoint.name} · {kindLabel(endpoint.kind)}">{endpoint.name || endpoint.slug}</span>
			{#if bottleneck}<span class="shrink-0 text-[11px] text-warning-fg">bottleneck</span>{/if}
		</span>
		<span class="shrink-0 font-mono tabular-nums">{formatHours(endpoint.est_hours)}</span>
	</div>
	<div class="h-1.5 w-full overflow-hidden rounded-full bg-muted" aria-hidden="true">
		{#if width > 0}
			<div class={cn('h-full rounded-full', bottleneck ? 'bg-warning' : 'bg-info')} style="width: {width}%"></div>
		{/if}
	</div>
	<p class="flex flex-wrap gap-x-2 text-[11px] text-muted-foreground tabular-nums">
		<span>{endpoint.models.length} model{endpoint.models.length === 1 ? '' : 's'}</span>
		<span>{endpoint.cells} cells</span>
		{#if !isOpenRouter}
			<span title="Request slots (max_concurrency) · job cap">{endpoint.max_concurrency} slot{endpoint.max_concurrency === 1 ? '' : 's'} · cap {endpoint.job_cap}</span>
			{#if endpoint.resident_limit != null}<span title="Models loaded at once">K {endpoint.resident_limit}</span>{/if}
			{#if swaps != null}<span title="Estimated model swaps for this order">{swaps} swap{swaps === 1 ? '' : 's'}</span>{/if}
		{/if}
		{#if policy}<span>{policy}</span>{/if}
		{#if endpoint.est_run_s_p50 != null}<span title="Median run time from recent history">p50 {Math.round(endpoint.est_run_s_p50)} s/run</span>{/if}
	</p>
	{#if !endpoint.online && !isOpenRouter}
		<p class="rounded-md border px-2 py-1 text-[11px] {toneClasses.warning}">
			Offline: runs wait until its relay connects{endpoint.sessions === 0 ? ' (no relay session)' : ''}.
		</p>
	{/if}
	{#each issues as issue (issue.code + (issue.model_id ?? ''))}
		<p class={cn('rounded-md border px-2 py-1 text-[11px]', issue.blocking ? toneClasses.danger : toneClasses.warning)}>{issue.detail}</p>
	{/each}
</li>
