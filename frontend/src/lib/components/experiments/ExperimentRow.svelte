<script lang="ts" module>
	export type RowAction = 'pause' | 'resume' | 'cancel' | 'archive';
</script>

<script lang="ts">
	/**
	 * ExperimentRow — one experiment in the list: name, hypothesis, status,
	 * design size; progress (finished / cells), ETA and cost; the endpoints it
	 * runs on and its next scheduled run; inline lifecycle actions.
	 *
	 * Props:
	 * - `experiment`: list item (progress may be patched live from `tick`).
	 * - `busy`: an action on this row is in flight.
	 * - `onaction(action)`: Pause / Resume / Cancel / Archive (the page confirms Cancel).
	 */
	import { Button } from '$lib/components/ui/button';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import BatchProgress from '$lib/components/generator/batches/BatchProgress.svelte';
	import { EndpointChip } from '$lib/components/inference';
	import { formatUsd } from '$lib/components/generator/batches/batch-utils';
	import type { ExperimentListItem } from '$lib/api/types/experiments';
	import Archive from '@lucide/svelte/icons/archive';
	import CalendarClock from '@lucide/svelte/icons/calendar-clock';
	import Copy from '@lucide/svelte/icons/copy';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Pause from '@lucide/svelte/icons/pause';
	import Play from '@lucide/svelte/icons/play';
	import Square from '@lucide/svelte/icons/square';
	import {
		MODE_LABELS,
		experimentTones,
		finishedCells,
		formatEta,
		isTerminal,
		pauseReasonLabel,
		policyLabel,
		timeUntil,
		toBatchCounts,
	} from './format';

	interface Props {
		experiment: ExperimentListItem;
		busy?: boolean;
		onaction: (action: RowAction) => void;
	}

	let { experiment: exp, busy = false, onaction }: Props = $props();

	const done = $derived(finishedCells(exp.progress));
	const terminal = $derived(isTerminal(exp.status));
	const launched = $derived(exp.status !== 'draft');
	const design = $derived(
		`${exp.condition_count} condition${exp.condition_count === 1 ? '' : 's'} × ${exp.app_count} app${exp.app_count === 1 ? '' : 's'} × ${exp.repeats}`
	);
</script>

<!-- One `.data-list-row`; the page wraps the rows in a `.data-list`. -->
	<div class="data-list-row grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,24rem)_19rem] lg:items-center">
		<div class="min-w-0 space-y-1">
			<div class="flex min-w-0 flex-wrap items-center gap-2">
				<a href="/experiments/{exp.id}" class="min-w-0 truncate text-sm font-semibold hover:text-primary">{exp.name}</a>
				<StatusBadge status={exp.status} tones={experimentTones} />
				{#if exp.status === 'paused' && exp.pause_reason}
					<span class="text-[11px] text-warning-fg">{pauseReasonLabel(exp.pause_reason)}</span>
				{/if}
			</div>
			<p class="cell-sub flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted-foreground">
				<span>{MODE_LABELS[exp.mode] ?? exp.mode}</span>
				<span aria-hidden="true">·</span>
				<span class="rounded-full border border-border px-1.5" title="Scheduling policy">{policyLabel(exp.scheduling_policy)}</span>
				<span aria-hidden="true">·</span>
				<span class="font-mono tabular-nums" title="conditions × apps × repeats">{design}</span>
			</p>
		</div>

		<div class="min-w-0 space-y-1.5">
			{#if launched}
				<BatchProgress counts={toBatchCounts(exp.progress)} total={exp.total_cells} />
				<p class="flex flex-wrap gap-x-3 text-[11px] text-muted-foreground tabular-nums">
					<span><span class="font-mono text-foreground">{done}/{exp.total_cells}</span> done</span>
					{#if !terminal && exp.eta_seconds != null}<span>ETA {formatEta(exp.eta_seconds)}</span>{/if}
					{#if exp.cost_usd > 0}<span class="font-mono">{formatUsd(exp.cost_usd)}</span>{/if}
				</p>
			{:else}
				<p class="text-[11px] text-muted-foreground">Draft · {exp.total_cells} cells planned</p>
			{/if}
			{#if exp.endpoints.length > 0 || exp.has_schedule}
				<div class="flex flex-wrap items-center gap-1">
					{#each exp.endpoints as ep (ep.id)}
						<EndpointChip endpoint={ep} />
					{/each}
					{#if exp.has_schedule}
						<span
							class="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground"
							title={exp.next_run_at ? `Next scheduled run: ${new Date(exp.next_run_at).toLocaleString()}` : 'Scheduled'}
						>
							<CalendarClock class="size-3" aria-hidden="true" />
							{exp.next_run_at ? timeUntil(exp.next_run_at) : 'scheduled'}
						</span>
					{/if}
				</div>
			{/if}
		</div>

		<!-- One visible next step (Pause / Resume, else Clone); the rest on hover / focus. -->
		<div class="flex flex-wrap items-center gap-0.5 lg:justify-end">
			{#if busy}<LoaderCircle class="mr-1 size-4 animate-spin text-muted-foreground motion-reduce:animate-none" aria-label="Working" />{/if}
			<div class="row-actions-more flex items-center gap-0.5">
				{#if exp.status === 'running' || exp.status === 'paused'}
					{@render iconAction('Cancel experiment', Square, () => onaction('cancel'), true)}
				{/if}
				{#if exp.status === 'completed' || exp.status === 'cancelled'}
					{@render iconAction('Archive', Archive, () => onaction('archive'))}
				{/if}
				{#if exp.status === 'running' || exp.status === 'paused'}
					<Button
						variant="ghost"
						size="sm"
						class="size-8 p-0 text-muted-foreground hover:text-foreground"
						href="/experiments/new?from={exp.id}"
						title="Clone: new draft with this design"
						aria-label="Clone: new draft with this design"><Copy class="size-4" /></Button
					>
				{/if}
			</div>
			{#if exp.status === 'running'}
				<Button variant="ghost" size="sm" class="h-7 gap-1 px-2 text-xs" disabled={busy} onclick={() => onaction('pause')}><Pause class="size-3" />Pause</Button>
			{:else if exp.status === 'paused'}
				<Button variant="ghost" size="sm" class="h-7 gap-1 px-2 text-xs" disabled={busy} onclick={() => onaction('resume')}><Play class="size-3" />Resume</Button>
			{:else}
				<Button variant="ghost" size="sm" class="h-7 gap-1 px-2 text-xs" href="/experiments/new?from={exp.id}" title="New draft with this design"><Copy class="size-3" />Clone</Button>
			{/if}
		</div>
	</div>


{#snippet iconAction(label: string, Icon: typeof Copy, onclick: () => void, danger = false)}
	<Button
		variant="ghost"
		size="sm"
		class="size-8 p-0 text-muted-foreground {danger ? 'hover:bg-destructive/10 hover:text-danger-fg' : 'hover:text-foreground'}"
		title={label}
		aria-label={label}
		disabled={busy}
		{onclick}
	>
		<Icon class="size-4" />
	</Button>
{/snippet}
