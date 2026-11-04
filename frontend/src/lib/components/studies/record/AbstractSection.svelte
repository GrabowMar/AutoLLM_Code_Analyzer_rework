<script lang="ts">
	/**
	 * AbstractSection — section 1 of the study record (spec 10.3): the description, a generated paragraph
	 * (design, outcome, estimator), the lock badge and `prereg_hash`, a status sentence and the headline
	 * results (best condition with its CI; p-values withheld while interim).
	 *
	 * Props: `study`, `results` (null until loaded), `cellsDone`, `cellsTotal`.
	 */
	import type { StudyOut, StudyResultsOut } from '$lib/api/v3/types';
	import StudySection from '../study/StudySection.svelte';
	import { KIND_LABELS, MODE_LABELS, formatPct, pauseLabel, plural, shortHash } from '../format';

	interface Props {
		study: StudyOut;
		results: StudyResultsOut | null;
		cellsDone: number;
		cellsTotal: number;
	}

	let { study, results, cellsDone, cellsTotal }: Props = $props();

	const units = $derived(study.mode === 'app' ? study.design.units.tasks.length : study.design.units.slot_targets.length);
	const varied = $derived(study.design.factors.filter((f) => f.levels.length > 1).map((f) => f.key.replace(/_/g, ' ')));
	const primary = $derived(String((study.analysis_plan as Record<string, unknown> | null)?.primary_metric ?? 'pass_rate').replace(/_/g, ' '));
	const best = $derived(results?.leaderboard.find((r) => r.pass_rate_mean !== null) ?? null);
	const status = $derived(
		study.status === 'draft' || study.status === 'planned'
			? 'Not started.'
			: study.status === 'paused'
				? `Paused (${pauseLabel(study.pause_reason)}): ${cellsDone} of ${cellsTotal} cells done.`
				: study.status === 'completed'
					? `Completed: ${plural(cellsDone, 'cell')} done.`
					: study.status === 'stopped'
						? `Stopped early (${study.stop_reason?.code.replace(/_/g, ' ') ?? 'unknown'}): ${cellsDone} of ${cellsTotal} cells done; budget stops are balanced across units.`
						: `${study.status === 'piloting' ? 'Piloting' : 'Running'}: ${cellsDone} of ${cellsTotal} cells done.`,
	);
</script>

<StudySection id="abstract" number="1" title="Abstract">
	<div class="max-w-3xl space-y-3 font-display text-[15px] leading-relaxed">
		{#if study.description}<p>{study.description}</p>{/if}
		<p>
			{study.kind === 'exploratory' ? 'An' : 'A'} {KIND_LABELS[study.kind].toLowerCase()} {MODE_LABELS[study.mode].toLowerCase()} experiment
			{#if varied.length}varying {varied.join(', ')}{/if}
			over {plural(units, study.mode === 'app' ? 'task' : 'slot target')} with {plural(study.repeats, 'repeat')} per condition and unit
			(common random numbers: every condition gets the same seed for the same unit and repeat).
			The outcome is {primary}, analysed by intention-to-treat; excluding truncated runs is a sensitivity analysis.
			{#if study.locked}The design and analysis plan were pre-registered at launch (<span class="font-mono text-sm">prereg {shortHash(study.prereg_hash)}</span>).{/if}
		</p>
		<p class="text-muted-foreground">{status}</p>
	</div>
	{#if best && results}
		<ol class="max-w-3xl list-decimal space-y-1 pl-5 text-sm">
			<li>
				Highest mean pass rate: <b>{best.label}</b> at {formatPct(best.pass_rate_mean, 1)}
				{#if best.pass_rate_ci95}(95% CI {formatPct(best.pass_rate_ci95[0], 1)} – {formatPct(best.pass_rate_ci95[1], 1)}){/if}
				over {plural(best.n_units, 'unit')}.
			</li>
			{#if results.interim}
				<li class="text-muted-foreground">Results are interim: p-values are withheld until the experiment completes.</li>
			{:else if results.paired.rows.some((r) => r.p_adjusted !== null && r.p_adjusted < results.paired.alpha)}
				<li>
					{plural(results.paired.rows.filter((r) => r.p_adjusted !== null && r.p_adjusted < results.paired.alpha).length, 'condition')} differ from the baseline after {results.paired.correction} correction.
				</li>
			{/if}
			{#if results.gate.deviations.length}<li class="text-warning-fg">Deviations from the plan: {results.gate.deviations.join(', ').replace(/_/g, ' ')}.</li>{/if}
		</ol>
	{/if}
</StudySection>
