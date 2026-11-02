<script lang="ts">
	/**
	 * ScenariosResult — usability scenarios: pooled outcome counts, success
	 * rate (success / attempted; partial is not success), median time and mean
	 * ease (SEQ 1–7) per scenario, then per group the success rate as the mean
	 * over apps of each app's success share with a bootstrap CI over apps.
	 *
	 * Props: `result` (ScenariosBlockResult), `section`, `ctx`.
	 */
	import StudyFigure from '$lib/components/experiments/study/StudyFigure.svelte';
	import type { ScenariosBlockResult } from '$lib/api/human-eval';
	import { formatScore, formatSeconds, formatShare, plural } from '../../format';
	import DotCiPlot from './DotCiPlot.svelte';
	import type { ResultsContext } from './types';

	let { result, section, ctx }: { result: ScenariosBlockResult; section: string; ctx: ResultsContext } = $props();

	const isPrimary = $derived(ctx.primary?.block_id === result.block_id && ctx.primary.metric === 'success_rate');
	const share1 = (v: number) => formatShare(v, 1);
</script>

<div class="space-y-4">
	<StudyFigure id="tab-{result.block_id}-scenarios" kind="table" number="{section}.1" title="Scenario outcomes (pooled, descriptive)" flush>
		{#snippet controls()}
			{#if isPrimary}<span class="rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">Primary outcome</span>{/if}
		{/snippet}
		<div class="overflow-x-auto">
			<table class="text-sm">
				<thead>
					<tr>
						<th>Scenario</th>
						<th class="num">n</th>
						<th class="num" title="success · partial · failure · not attempted">Outcomes</th>
						<th class="num" title="Success / attempted (pooled over raters)">Success rate</th>
						<th class="num">Median time</th>
						<th class="num" title="Single Ease Question, 1–7">Ease</th>
					</tr>
				</thead>
				<tbody>
					{#each result.scenarios as s (s.scenario_id)}
						<tr>
							<td class="max-w-72 break-words">{s.title}</td>
							<td class="num text-xs">{s.n}</td>
							<td class="num text-xs text-muted-foreground">{s.success} · {s.partial} · {s.failure} · {s.not_attempted}</td>
							<td class="num text-xs font-medium">{formatShare(s.success_rate)}</td>
							<td class="num text-xs">{formatSeconds(s.median_seconds)}</td>
							<td class="num text-xs">{formatScore(s.ease_mean, 1)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</StudyFigure>

	{#each result.scenarios as s, i (s.scenario_id)}
		<StudyFigure id="fig-{result.block_id}-{s.scenario_id}" number="{section}.{i + 2}" title="{s.title}: success rate by {ctx.groupNoun.toLowerCase()}" flush>
			{#if s.per_group.length === 0}
				<p class="px-4 py-3 text-sm text-muted-foreground">No submitted data yet.</p>
			{:else}
				<DotCiPlot
					rows={s.per_group.map((g) => ({
						key: g.group,
						label: g.label,
						value: g.success_rate,
						ci: g.ci,
						n: `${plural(g.n_apps, 'app')} · ${g.n}`,
						sub: g.median_seconds != null ? `median ${formatSeconds(g.median_seconds)}` : undefined,
					}))}
					domain={[0, 1]}
					tickKind="share"
					format={share1}
					labelHeader={ctx.groupNoun}
					nHeader="Apps · attempts"
				/>
			{/if}
			{#snippet caption()}Mean over apps of each app’s success share; 95% bootstrap interval over apps (from 3 apps).{/snippet}
		</StudyFigure>
	{/each}
</div>
