<script lang="ts">
	/**
	 * ActivityFeed — the latest run transitions of the study (`run` events, newest first; at most 50).
	 *
	 * Props: `items` (`ActivityItem[]` from `StudyLive`), `conditionLabel(conditionId)`.
	 */
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { outcomeLabel, runTone } from '../format';
	import type { ActivityItem } from './live.svelte';

	interface Props {
		items: ActivityItem[];
		conditionLabel: (conditionId: number) => string;
	}

	let { items, conditionLabel }: Props = $props();
</script>

{#if items.length === 0}
	<p class="text-xs text-muted-foreground">Run transitions appear here as they happen.</p>
{:else}
	<ol class="max-h-72 space-y-1 overflow-y-auto text-xs">
		{#each items as item (item.key)}
			<li class="flex flex-wrap items-center gap-2">
				<span class="font-mono text-muted-foreground">{new Date(item.ts).toLocaleTimeString()}</span>
				<StatusBadge status={item.outcome || item.status} tone={runTone(item.outcome || item.status)} label={item.outcome ? outcomeLabel(item.outcome) : item.status} />
				<a class="font-mono text-primary hover:underline" href="/samples/{item.runId}">{item.runId.slice(0, 8)}</a>
				<span class="truncate text-muted-foreground">
					{item.conditionId !== null ? conditionLabel(item.conditionId) : ''}{item.unitKey ? ` · ${item.unitKey}` : ''}{item.repeat !== null ? ` · r${item.repeat}` : ''}{item.attempt > 1 ? ` · attempt ${item.attempt}` : ''}
				</span>
			</li>
		{/each}
	</ol>
{/if}
