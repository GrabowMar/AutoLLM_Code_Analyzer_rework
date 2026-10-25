<script lang="ts">
	/**
	 * ActivityFeed — the last `job` transitions of this experiment (newest
	 * first, at most 50 kept by `ExperimentLive`), filterable to failures and
	 * retries. Each row links to the run.
	 *
	 * Props:
	 * - `items`: `ExperimentLive.activity`.
	 * - `conditionLabel(id)`, `appName(id)`: display names.
	 */
	import { cn } from '$lib/utils';
	import { Segmented } from '$lib/components/ui/segmented';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import Activity from '@lucide/svelte/icons/activity';
	import { CELL_STATES } from '../cells';
	import type { ActivityItem } from '../live.svelte';

	interface Props {
		items: ActivityItem[];
		conditionLabel: (id: number | null) => string;
		appName: (id: number | null) => string;
	}

	let { items, conditionLabel, appName }: Props = $props();

	let filter = $state('all');

	const failures = (i: ActivityItem) => i.code === 'x' || i.code === 'f';
	const visible = $derived(
		filter === 'failures' ? items.filter(failures) : filter === 'retries' ? items.filter((i) => i.retry) : items
	);
	const filterItems = $derived([
		{ value: 'all', label: `All ${items.length}` },
		{ value: 'failures', label: `Failures ${items.filter(failures).length}` },
		{ value: 'retries', label: `Retries ${items.filter((i) => i.retry).length}` },
	]);

	function time(ts: string): string {
		const d = new Date(ts);
		return Number.isNaN(d.getTime())
			? ''
			: d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
	}
</script>

<div class="space-y-2">
	<Segmented aria-label="Filter activity" size="sm" items={filterItems} bind:value={filter} />
	{#if visible.length === 0}
		<EmptyState
			compact
			icon={Activity}
			title={items.length === 0 ? 'No activity yet' : 'Nothing matches this filter'}
			description={items.length === 0 ? 'Run transitions appear here as they happen.' : undefined}
		/>
	{:else}
		<ol class="max-h-80 divide-y divide-border/60 overflow-y-auto rounded-md border border-border" aria-live="polite">
			{#each visible as item (item.key)}
				<li class="flex min-w-0 items-center gap-2 px-2.5 py-1.5 text-xs">
					<span class="w-16 shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground">{time(item.ts)}</span>
					<span class={cn('size-2.5 shrink-0 rounded-[2px]', CELL_STATES[item.code].square, 'animate-none')} aria-hidden="true"></span>
					<a href="/applications/{item.jobId}" class="min-w-0 flex-1 truncate hover:underline">
						<span class="font-medium">{conditionLabel(item.conditionId)}</span>
						<span class="text-muted-foreground"> · {appName(item.appId)}{item.repeat != null ? ` · #${item.repeat + 1}` : ''}</span>
					</a>
					<span class="shrink-0 text-[11px] text-muted-foreground">
						{CELL_STATES[item.code].label}{item.attempt > 1 ? ` · attempt ${item.attempt}` : ''}
					</span>
				</li>
			{/each}
		</ol>
	{/if}
</div>
