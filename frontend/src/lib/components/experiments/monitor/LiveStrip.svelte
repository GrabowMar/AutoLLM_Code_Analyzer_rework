<script lang="ts">
	/**
	 * LiveStrip — overall progress, slot lanes and the live-connection state,
	 * one block for the experiment page header strip (shown on every tab).
	 * Once `finished`, only the bar stays (with its legend when the outcome is
	 * mixed): idle lanes and "not live" add nothing to a completed run.
	 */
	import { cn } from '$lib/utils';
	import BatchProgress from '$lib/components/generator/batches/BatchProgress.svelte';
	import type { ExperimentLive } from '../live.svelte';
	import { toBatchCounts } from '../format';

	interface Props {
		live: ExperimentLive;
		finished?: boolean;
	}

	let { live, finished = false }: Props = $props();

	const status = $derived(live.status);

	const connectionText = $derived(
		live.connection === 'open' && !live.polling
			? { text: 'Live', tone: 'text-success-fg' }
			: live.connection === 'closed'
				? { text: 'Finished — not live', tone: 'text-muted-foreground' }
				: live.polling
					? { text: 'Polling every 5 s', tone: 'text-warning-fg' }
					: { text: 'Connecting…', tone: 'text-muted-foreground' }
	);

	const batchCounts = $derived(status ? toBatchCounts(status.counts) : null);
	const mixedOutcome = $derived(
		batchCounts ? Object.values(batchCounts).filter((n) => typeof n === 'number' && n > 0).length > 1 : false
	);

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

{#if status}
	<div class="space-y-1">
		<BatchProgress counts={batchCounts!} total={status.cells.total} legend={!finished || mixedOutcome} />
		{#if !finished}
			<div class="flex flex-wrap items-center justify-between gap-2 text-xs">
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
		{/if}
	</div>
{/if}
