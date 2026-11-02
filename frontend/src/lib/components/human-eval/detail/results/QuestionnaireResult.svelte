<script lang="ts">
	/**
	 * QuestionnaireResult — a questionnaire block: the standard instrument's
	 * score (SUS / UMUX-Lite / raw NASA-TLX, all 0–100) per group and per app,
	 * then every question's answer distribution (and mean for numeric kinds).
	 *
	 * Props: `result` (QuestionnaireBlockResult), `section`, `ctx`.
	 */
	import StudyFigure from '$lib/components/experiments/study/StudyFigure.svelte';
	import type { QuestionnaireBlockResult } from '$lib/api/human-eval';
	import { INSTRUMENT_LABELS, formatScore } from '../../format';
	import AppScoreTable from './AppScoreTable.svelte';
	import GroupScoreFigure from './GroupScoreFigure.svelte';
	import type { ResultsContext } from './types';

	let { result, section, ctx }: { result: QuestionnaireBlockResult; section: string; ctx: ResultsContext } = $props();

	const isPrimary = $derived(ctx.primary?.block_id === result.block_id && ctx.primary.metric === 'score');
	const one = (v: number) => formatScore(v, 1);
	const NOTES: Record<string, string> = {
		sus: 'SUS 0–100 (Brooke 1996); 68 is the commonly cited average.',
		umux_lite: 'UMUX-Lite 0–100 from two 7-point items.',
		nasa_tlx: 'Raw NASA-TLX 0–100: mean of six subscales; higher means more workload.',
	};

	function distribution(d: Record<string, number>): string {
		return Object.entries(d ?? {})
			.map(([k, v]) => `${k}: ${v}`)
			.join(' · ');
	}
</script>

<div class="space-y-4">
	{#if result.score}
		<GroupScoreFigure
			id="fig-{result.block_id}-score"
			number="{section}.1"
			title="{INSTRUMENT_LABELS[result.instrument]} score by {ctx.groupNoun.toLowerCase()}"
			scores={result.score.per_group}
			domain={[0, 100]}
			format={one}
			groupNoun={ctx.groupNoun}
			primary={isPrimary}
			note={NOTES[result.instrument] ?? ''}
		>
			<details>
				<summary class="cursor-pointer text-xs font-medium text-muted-foreground hover:text-foreground">Per app ({result.score.per_app.length})</summary>
				<div class="mt-2">
					<AppScoreTable scores={result.score.per_app} groupLabel={ctx.groupLabel} groupNoun={ctx.groupNoun} format={one} naColumn={false} />
				</div>
			</details>
		</GroupScoreFigure>
	{/if}

	{#if result.questions.length > 0}
		<StudyFigure id="tab-{result.block_id}-questions" kind="table" number="{section}.{result.score ? 2 : 1}" title="Answers per question" flush>
			<div class="max-h-[28rem] overflow-auto">
				<table class="text-sm">
					<thead><tr><th>Question</th><th>Kind</th><th class="num">n</th><th class="num">Mean</th><th>Distribution</th></tr></thead>
					<tbody>
						{#each result.questions as q (q.question_id)}
							<tr>
								<td class="max-w-80 break-words">{q.label}</td>
								<td class="text-xs text-muted-foreground">{q.kind}</td>
								<td class="num text-xs">{q.n}</td>
								<td class="num text-xs">{formatScore(q.mean, 2)}</td>
								<td class="max-w-96 break-words text-xs text-muted-foreground">{distribution(q.distribution) || '—'}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			{#snippet caption()}Pooled over all included responses (descriptive).{/snippet}
		</StudyFigure>
	{/if}
</div>
