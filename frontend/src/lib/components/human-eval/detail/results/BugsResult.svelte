<script lang="ts">
	/**
	 * BugsResult — the bug log: bugs per rater per group (mean over apps,
	 * bootstrap CI over apps) and per app totals by the testers' own severity,
	 * with "none found" confirmations.
	 *
	 * Props: `result` (BugsBlockResult), `section`, `ctx`.
	 */
	import StudyFigure from '$lib/components/experiments/study/StudyFigure.svelte';
	import type { BugsBlockResult } from '$lib/api/human-eval';
	import { formatScore, humanize, zeroBasedDomain } from '../../format';
	import GroupScoreFigure from './GroupScoreFigure.svelte';
	import type { ResultsContext } from './types';

	let { result, section, ctx }: { result: BugsBlockResult; section: string; ctx: ResultsContext } = $props();

	const isPrimary = $derived(ctx.primary?.block_id === result.block_id && ctx.primary.metric === 'bugs_per_rater');
	const two = (v: number) => formatScore(v, 2);
	const domain = $derived(zeroBasedDomain(result.per_group.flatMap((g) => [g.mean, g.ci?.high, g.floor?.mean])));
</script>

<div class="space-y-4">
	<GroupScoreFigure
		id="fig-{result.block_id}-bugs"
		number="{section}.1"
		title="Bugs reported per rater by {ctx.groupNoun.toLowerCase()}"
		scores={result.per_group}
		{domain}
		format={two}
		groupNoun={ctx.groupNoun}
		primary={isPrimary}
		note="Per app: bug reports / submitted assignments."
	/>
	<StudyFigure id="tab-{result.block_id}-bug-apps" kind="table" number="{section}.2" title="Bug reports per app" flush>
		<div class="max-h-96 overflow-auto">
			<table class="text-sm">
				<thead>
					<tr><th>App</th><th>{ctx.groupNoun}</th><th class="num">Raters</th><th class="num">Bugs</th><th class="num">Per rater</th><th class="num" title="Raters who confirmed finding none">None found</th><th>By tester severity</th></tr>
				</thead>
				<tbody>
					{#each result.per_app as a (a.app_id)}
						<tr>
							<td class="font-mono text-xs">{a.label}</td>
							<td class="max-w-48 truncate text-xs">{ctx.groupLabel(a.group)}</td>
							<td class="num text-xs">{a.n_raters}</td>
							<td class="num text-xs">{a.total}</td>
							<td class="num text-xs font-medium">{formatScore(a.per_rater_mean, 2)}</td>
							<td class="num text-xs text-muted-foreground">{a.none_found}</td>
							<td class="text-[11px] text-muted-foreground">
								{Object.entries(a.by_severity ?? {}).filter(([, n]) => n > 0).map(([k, n]) => `${humanize(k)} ${n}`).join(' · ') || '—'}
							</td>
						</tr>
					{:else}
						<tr><td colspan="7" class="text-xs text-muted-foreground">No rated apps.</td></tr>
					{/each}
				</tbody>
			</table>
		</div>
	</StudyFigure>
</div>
