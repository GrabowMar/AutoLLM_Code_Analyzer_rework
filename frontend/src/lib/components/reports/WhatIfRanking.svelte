<script lang="ts">
	/**
	 * WhatIfRanking — the study report's TOPSIS ranking recomputed live with the reader's weights
	 * (`ranking.ts`, a port of the backend's `mcdm.topsis_scores`). One slider per criterion
	 * (benefit ↑ / cost ↓), weights renormalised to 100%; the ranking re-sorts as they move, each
	 * row showing its score as a bar, its rank in the report and how far it moved (neutral ink, an
	 * arrow + number — never red/green). "Report weights" restores the report's own; with them the
	 * scores equal the report's.
	 *
	 * Props: `data` (`rankingData(report_data)`), `number` (figure number), `id`.
	 */
	import ArrowDown from '@lucide/svelte/icons/arrow-down';
	import ArrowUp from '@lucide/svelte/icons/arrow-up';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import StudyFigure from '$lib/components/studies/study/StudyFigure.svelte';
	import { Button } from '$lib/components/ui/button';
	import { cn } from '$lib/utils';
	import { competitionRanks, normalise, topsis, type RankingData } from './ranking';
	import { shortName } from './format';

	interface Props {
		data: RankingData;
		number: string | number;
		id: string;
	}

	let { data, number, id }: Props = $props();

	const reportWeights = () => data.criteria.map((c) => Math.round(c.weight * 100));
	let weights = $state<number[]>(reportWeights());

	const shares = $derived(normalise(weights));
	const result = $derived(topsis(data.ranking, data.criteria, weights));
	const ranks = $derived(competitionRanks(result.scores));
	const reportRank = $derived(new Map(data.ranking.map((r) => [r.model, r.topsis_rank ?? null])));
	const rows = $derived(
		[...result.scores.entries()]
			.map(([model, score]) => ({ model, score, rank: ranks.get(model)!, before: reportRank.get(model) ?? null }))
			.sort((a, b) => a.rank - b.rank || a.model.localeCompare(b.model))
	);
	const isReport = $derived(weights.every((w, i) => w === reportWeights()[i]));
	const moved = $derived(rows.filter((r) => r.before != null && r.before !== r.rank).length);
</script>

{#snippet caption()}
	TOPSIS closeness to the ideal model (vector normalisation; 1 = best on every weighted criterion), recomputed in your
	browser from the report's per-model values. Weights are renormalised to 100%. {#if result.dropped.length}Left out (a
	criterion not measured): {result.dropped.map(shortName).join(', ')}.{/if} Exploratory: a ranking only orders models by the
	weights you choose.
{/snippet}

<StudyFigure {id} {number} title="What if: re-rank with your own weights" flush {caption}>
	{#snippet controls()}
		<span class="text-xs text-muted-foreground" role="status" aria-live="polite">
			{isReport ? 'As in the report' : moved === 0 ? 'Same order as the report' : `${moved} ${moved === 1 ? 'model moves' : 'models move'}`}
		</span>
		<Button variant="outline" size="sm" class="h-7 text-xs" disabled={isReport} onclick={() => (weights = reportWeights())}>
			<RotateCcw class="size-3.5" />Report weights
		</Button>
		<Button variant="outline" size="sm" class="h-7 text-xs" onclick={() => (weights = data.criteria.map(() => 20))}>Equal</Button>
	{/snippet}

	<div class="grid gap-0 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
		<fieldset class="space-y-3 border-b border-border/60 p-4 lg:border-b-0 lg:border-r">
			<legend class="sr-only">Criterion weights</legend>
			{#each data.criteria as c, i (c.key)}
				<div class="space-y-1">
					<div class="flex items-baseline justify-between gap-2 text-sm">
						<label for="{id}-w-{c.key}" class="min-w-0 truncate">{c.label}</label>
						<span class="shrink-0 font-mono text-xs tabular-nums">{(shares[i] * 100).toFixed(0)}%</span>
					</div>
					<div class="flex items-center gap-2">
						<span
							class="inline-flex w-14 shrink-0 items-center gap-0.5 text-[11px] text-muted-foreground"
							title={c.direction === 'benefit' ? 'Higher is better' : 'Lower is better'}
						>
							{#if c.direction === 'benefit'}<ArrowUp class="size-3" aria-hidden="true" />benefit{:else}<ArrowDown class="size-3" aria-hidden="true" />cost{/if}
						</span>
						<input
							id="{id}-w-{c.key}"
							type="range"
							min="0"
							max="100"
							step="5"
							bind:value={weights[i]}
							class="wi-range h-1.5 w-full cursor-pointer"
							aria-valuetext="{(shares[i] * 100).toFixed(0)} percent of the weight"
						/>
					</div>
				</div>
			{/each}
		</fieldset>

		<ol class="divide-y divide-border/60" aria-label="Ranking with these weights">
			{#each rows as r (r.model)}
				{@const delta = r.before != null ? r.before - r.rank : 0}
				<li class="grid grid-cols-[2rem_minmax(0,1fr)_3rem] items-center gap-x-3 gap-y-1.5 px-4 py-2.5 sm:grid-cols-[2rem_minmax(0,1fr)_minmax(5rem,10rem)_4.5rem]">
					<span class={cn('font-mono text-lg font-medium tabular-nums', r.rank === 1 && 'text-primary')}>{r.rank}</span>
					<span class="min-w-0">
						<span class="block truncate text-sm font-medium" title={r.model}>{shortName(r.model)}</span>
						<span class="block text-[11px] text-muted-foreground">report rank {r.before ?? '—'}</span>
					</span>
					<span class="flex items-center gap-2 max-sm:col-span-3 max-sm:row-start-2 max-sm:pl-[2.75rem]">
						<span class="block h-2 flex-1 overflow-hidden rounded-full bg-muted" aria-hidden="true">
							<span class="block h-full rounded-full bg-[var(--chart-1)] transition-[width] duration-200" style:width="{r.score * 100}%"></span>
						</span>
						<span class="w-11 text-right font-mono text-xs tabular-nums">{r.score.toFixed(3)}</span>
					</span>
					<span class="text-right font-mono text-xs tabular-nums text-muted-foreground max-sm:col-start-3 max-sm:row-start-1">
						{#if delta > 0}<span class="inline-flex items-center gap-0.5 text-foreground" title="Up {delta} from the report"><ArrowUp class="size-3" aria-hidden="true" />{delta}</span>
						{:else if delta < 0}<span class="inline-flex items-center gap-0.5 text-foreground" title="Down {-delta} from the report"><ArrowDown class="size-3" aria-hidden="true" />{-delta}</span>
						{:else}<span title="Same rank as in the report">=</span>{/if}
					</span>
				</li>
			{/each}
		</ol>
	</div>
</StudyFigure>

<style>
	.wi-range {
		accent-color: var(--primary);
	}
</style>
