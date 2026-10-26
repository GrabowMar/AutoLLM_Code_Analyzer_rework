<script lang="ts">
	/**
	 * RunDataBadge — what a run's data is worth, for list rows: "pending"
	 * while it runs, "Complete" (every required tool succeeded or was not
	 * applicable), "Incomplete" (a lower bound; the tooltip names the tools),
	 * or "No data" (no tool produced anything). Never color alone.
	 *
	 * Props: `run` (list item or detail).
	 */
	import type { AnalysisRunListItem } from '$lib/api/analysis';
	import { isRunActive } from '$lib/api/analysis';
	import Hint from './Hint.svelte';
	import { problemLabel, summaryToolProblems } from './run-utils';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import CircleX from '@lucide/svelte/icons/circle-x';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	interface Props {
		run: AnalysisRunListItem;
	}

	let { run }: Props = $props();

	const active = $derived(isRunActive(run.status));
	const noData = $derived(
		!active &&
			(run.tools?.total ?? 0) > 0 &&
			(run.tools?.succeeded ?? 0) + (run.tools?.not_applicable ?? 0) === 0
	);

	const note = $derived.by(() => {
		const problems = summaryToolProblems(run);
		if (problems.length) return `Did not succeed: ${problems.map(problemLabel).join(', ')}`;
		return run.error_message || 'A required tool did not succeed';
	});
</script>

{#if active}
	<span class="text-xs text-muted-foreground">pending</span>
{:else if run.is_complete}
	<span class="inline-flex items-center gap-1 text-xs text-success-fg">
		<CircleCheck class="size-3.5" aria-hidden="true" />Complete
	</span>
{:else if noData}
	<Hint
		text={`${run.status === 'cancelled' ? 'Cancelled before any tool produced data' : 'No tool produced data'}. ${note}`}
		underline={false}
	>
		<span class="inline-flex items-center gap-1 text-xs text-danger-fg">
			<CircleX class="size-3.5" aria-hidden="true" />No data
		</span>
	</Hint>
{:else}
	<Hint text={note} underline={false}>
		<span class="inline-flex items-center gap-1 text-xs text-warning-fg">
			<TriangleAlert class="size-3.5" aria-hidden="true" />Incomplete
		</span>
	</Hint>
{/if}
