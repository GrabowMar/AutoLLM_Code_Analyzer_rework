<script lang="ts" module>
	/** One in-flight cell of this experiment on the endpoint. */
	export interface LaneJob {
		jobId: string;
		label: string;
		app: string;
		repeat: number;
		phase: string;
	}
</script>

<script lang="ts">
	/**
	 * EndpointLane — one endpoint this experiment uses, live: status, request
	 * slots (other experiments' share hatched), job cap, resident / loaded
	 * models (this user's by name, others as a count), this user's active jobs,
	 * queue depth, throughput and why queued runs are waiting.
	 *
	 * Props:
	 * - `endpoint`: `ExperimentStatusV2.endpoints[i]` (tick-patched).
	 * - `experimentId`.
	 * - `activeJobs`: this experiment's in-flight cells on the endpoint
	 *   (model · app · repeat · phase · since), from the live grid.
	 */
	import { cn } from '$lib/utils';
	import { toneClasses } from '$lib/constants/colors';
	import * as Card from '$lib/components/ui/card';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { SlotMeter, kindLabel } from '$lib/components/inference';
	import type { StatusEndpoint } from '$lib/api/types/experiments';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import Cpu from '@lucide/svelte/icons/cpu';
	import { blockedReasonText } from '../format';

	interface Props {
		endpoint: StatusEndpoint;
		experimentId: string;
		activeJobs?: LaneJob[];
	}

	let { endpoint, experimentId, activeJobs = [] }: Props = $props();

	let errorOpen = $state(false);

	const health = $derived(endpoint.online ? endpoint.health : 'offline');
	const healthTone = $derived(health === 'ok' ? 'success' : health === 'degraded' ? 'warning' : 'danger');
	const isOpenRouter = $derived(endpoint.kind === 'openrouter');
	const ownModels = $derived(new Set(endpoint.resident.models));
	const SHOWN_JOBS = 6;
</script>

<Card.Root class="min-w-0 gap-3 py-4" aria-labelledby="lane-{experimentId}-{endpoint.id}">
	<Card.Header class="gap-1 px-4">
		<div class="flex items-start justify-between gap-2">
			<div class="min-w-0">
				<Card.Title class="flex items-center gap-1.5 text-sm">
					<span id="lane-{experimentId}-{endpoint.id}" class="truncate font-mono">{endpoint.name || endpoint.slug}</span>
				</Card.Title>
				<p class="text-xs text-muted-foreground">
					{[kindLabel(endpoint.kind), isOpenRouter ? '' : endpoint.connection, endpoint.sessions > 1 ? `${endpoint.sessions} relays` : '']
						.filter(Boolean)
						.join(' · ')}
				</p>
			</div>
			<StatusBadge
				status={health}
				tone={healthTone}
				label={health === 'ok' ? 'online' : health}
			/>
		</div>
	</Card.Header>
	<Card.Content class="space-y-3 px-4 text-xs">
		<div class="space-y-1.5">
			<div class="flex flex-wrap items-center gap-x-4 gap-y-1">
				{#if !isOpenRouter}
					<SlotMeter used={endpoint.slots.used} total={endpoint.slots.total} label="Requests" />
				{/if}
				<SlotMeter
				used={endpoint.jobs.active}
				total={endpoint.jobs.cap}
				heldByOthers={endpoint.jobs.held_by_others}
				label="Jobs"
				/>
			</div>
			{#if endpoint.jobs.held_by_others > 0}
				<p class="text-xs text-muted-foreground tabular-nums">
					{endpoint.jobs.held_by_others} held by other experiments (hatched)
				</p>
			{/if}
		</div>

		{#if !isOpenRouter && (endpoint.resident.limit != null || endpoint.loaded.length > 0 || endpoint.loaded_others > 0)}
			<div class="space-y-1">
				<p class="section-label">{endpoint.resident.limit != null ? `Loaded · max ${endpoint.resident.limit}` : 'Loaded'}</p>
				<div class="flex flex-wrap gap-1">
					{#each endpoint.loaded as model (model.name)}
						<span
							class={cn(
								'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-xs',
								model.cpu_spill ? toneClasses.warning : 'border-border',
								ownModels.has(model.name) && 'font-semibold'
							)}
							title={model.cpu_spill
								? 'Part of the model spilled to CPU memory: expect slow generation'
								: ownModels.has(model.name)
									? 'Used by this experiment'
									: 'Loaded'}
						>
							{model.name}
							{#if model.size_vram_gb != null}<span class="font-sans font-normal text-muted-foreground">{model.size_vram_gb.toFixed(1)} GB</span>{/if}
							{#if model.cpu_spill}<Cpu class="size-3" aria-label="CPU spill" />{/if}
						</span>
					{:else}
						{#if endpoint.loaded_others === 0}
							<span class="text-xs text-muted-foreground">No model loaded</span>
						{/if}
					{/each}
					{#if endpoint.loaded_others > 0}
						<span class="rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground" title="Loaded for other users">
							+{endpoint.loaded_others}
						</span>
					{/if}
				</div>
			</div>
		{/if}

		<div class="space-y-1">
			<p class="section-label">Running for this experiment · {activeJobs.length}</p>
			{#if activeJobs.length === 0}
				<p class="text-xs text-muted-foreground">Nothing running here right now.</p>
			{:else}
				<ul class="space-y-0.5">
					{#each activeJobs.slice(0, SHOWN_JOBS) as job (job.jobId)}
						<li class="flex min-w-0 items-center gap-1.5">
							<a href="/applications/{job.jobId}" class="min-w-0 flex-1 truncate hover:underline" title="{job.label} · {job.app} · repeat {job.repeat + 1}">
								{job.label} <span class="text-muted-foreground">· {job.app} · #{job.repeat + 1}</span>
							</a>
							<span class="shrink-0 text-xs text-muted-foreground">{job.phase}</span>
						</li>
					{/each}
				</ul>
				{#if activeJobs.length > SHOWN_JOBS}
					<p class="text-xs text-muted-foreground">+{activeJobs.length - SHOWN_JOBS} more</p>
				{/if}
			{/if}
		</div>

		<dl class="grid grid-cols-3 gap-2 border-t border-border/60 pt-2 text-xs">
			<div>
				<dt class="text-muted-foreground">Queued here</dt>
				<dd class="font-mono tabular-nums">{endpoint.queued_jobs_this_experiment}</dd>
			</div>
			<div>
				<dt class="text-muted-foreground">Tokens/s</dt>
				<dd class="font-mono tabular-nums">{endpoint.tokens_per_s_5m != null ? endpoint.tokens_per_s_5m.toFixed(0) : '—'}</dd>
			</div>
			<div>
				<dt class="text-muted-foreground" title="Median request time, last 15 minutes">p50 request</dt>
				<dd class="font-mono tabular-nums">{endpoint.p50_request_s_15m != null ? `${endpoint.p50_request_s_15m.toFixed(1)} s` : '—'}</dd>
			</div>
		</dl>

		{#if endpoint.circuit === 'open'}
			<p class="rounded-md border px-2 py-1 text-xs {toneClasses.danger}">
				Paused after repeated errors; the executor retries shortly.
			</p>
		{/if}
		{#if endpoint.blocked_reason && endpoint.queued_jobs_this_experiment > 0}
			<p class="rounded-md border px-2 py-1 text-xs {toneClasses.warning}">
				Queued runs: {blockedReasonText(endpoint.blocked_reason)}
			</p>
		{/if}
		{#if endpoint.last_error}
			<div>
				<button
					type="button"
					class="flex cursor-pointer items-center gap-1 text-xs font-medium text-danger-fg"
					aria-expanded={errorOpen}
					onclick={() => (errorOpen = !errorOpen)}
				>
					<ChevronRight class={cn('size-3 transition-transform duration-150', errorOpen && 'rotate-90')} aria-hidden="true" />
					Last error
				</button>
				{#if errorOpen}
					<p class="mt-1 break-words font-mono text-xs text-muted-foreground">{endpoint.last_error}</p>
				{/if}
			</div>
		{/if}
	</Card.Content>
</Card.Root>
