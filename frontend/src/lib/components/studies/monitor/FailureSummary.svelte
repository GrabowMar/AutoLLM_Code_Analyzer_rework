<script lang="ts">
	/**
	 * FailureSummary — what went wrong, by outcome and reason (spec 10.3 §6): runs that are not `tested`
	 * grouped by outcome (model-caused: output invalid, build/start failed; infra: infra error, which is
	 * retried and never counts against a condition) and by reason, with a likely cause and one example
	 * run per group.
	 *
	 * Props: `cells` (`StudyCellsPayload`).
	 */
	import type { StudyCellsPayload } from '$lib/api/v3/types';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { outcomeLabel, plural, runTone } from '../format';

	interface Props {
		cells: StudyCellsPayload;
	}

	let { cells }: Props = $props();

	const HINTS: Record<string, string> = {
		output_invalid: 'The model ended without a usable workspace (empty, skeleton only or unparsable). A data point, not an error.',
		build_fail: 'The generated app does not build under the recorded lock. Model-caused.',
		start_fail: 'The app builds but does not answer its health check. Model-caused.',
		infra_error: 'Platform problem (sandbox, gateway, provider): retried with the same manifest and seed; not counted against the condition.',
		excluded: 'Excluded by the design (cell budget, a budget stop, a stop rule or cancellation).',
	};

	const groups = $derived.by(() => {
		const map = new Map<string, { outcome: string; reason: string; n: number; runId: string | null }>();
		for (const cell of cells.cells) {
			const run = cell.current_run;
			const outcome = cell.outcome || (cell.state === 'excluded' ? 'excluded' : cell.state === 'infra_pending' ? 'infra_error' : '');
			if (!outcome || outcome === 'tested') continue;
			const reason = run?.outcome_reason || cell.exclusion_reason || '';
			const key = `${outcome}|${reason}`;
			const g = map.get(key) ?? { outcome, reason, n: 0, runId: run?.id ?? null };
			g.n += 1;
			map.set(key, g);
		}
		return [...map.values()].sort((a, b) => b.n - a.n);
	});
</script>

{#if groups.length === 0}
	<p class="text-xs text-muted-foreground">No failed, invalid or excluded cell so far.</p>
{:else}
	<ul class="space-y-2">
		{#each groups as g (g.outcome + g.reason)}
			<li class="flex flex-wrap items-start gap-2 text-xs">
				<StatusBadge status={g.outcome} tone={runTone(g.outcome)} label={outcomeLabel(g.outcome)} />
				<span class="font-mono tabular-nums">{plural(g.n, 'cell')}</span>
				{#if g.reason}<span class="font-mono text-muted-foreground">{g.reason}</span>{/if}
				<span class="basis-full text-muted-foreground">{HINTS[g.outcome] ?? ''}{#if g.runId} <a class="text-primary underline" href="/samples/{g.runId}">Example run</a>{/if}</span>
			</li>
		{/each}
	</ul>
{/if}
