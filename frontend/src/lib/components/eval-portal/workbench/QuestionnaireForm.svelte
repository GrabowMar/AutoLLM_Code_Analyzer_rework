<script lang="ts">
	/**
	 * QuestionnaireForm — a questionnaire block: the standard instrument's fixed
	 * questions (SUS, UMUX-Lite, NASA-TLX; sent by the server in the assignment
	 * context) or the researcher's custom questions, each a QuestionField.
	 *
	 * Props: `block`, `answers` (QuestionnaireAnswers), `questions` (resolved
	 * list), `readOnly`, `onchange(next)`.
	 */
	import type { Question, QuestionAnswer, QuestionnaireAnswers, QuestionnaireBlock } from '$lib/api/types/human-eval';
	import QuestionField from '$lib/components/eval-portal/QuestionField.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { questionIssue, isUnanswered } from './completeness';

	interface Props {
		block: QuestionnaireBlock;
		answers: QuestionnaireAnswers;
		questions: Question[];
		readOnly?: boolean;
		onchange: (next: QuestionnaireAnswers) => void;
	}

	let { block, answers, questions, readOnly = false, onchange }: Props = $props();

	const given = $derived(answers.answers ?? {});

	function set(id: string, value: QuestionAnswer) {
		onchange({ ...answers, answers: { ...given, [id]: value } });
	}

	// Show a problem only for answers that exist but are invalid; missing answers
	// are listed under "Still to do" instead of shouting at every question.
	function shownIssue(q: Question): string {
		const value = given[q.id];
		if (isUnanswered(value)) return '';
		return questionIssue(q, value) ?? '';
	}
</script>

{#if questions.length === 0}
	<EmptyState
		compact
		title="No questions"
		description={block.instrument === 'custom' ? 'This questionnaire has no questions.' : 'The questions could not be loaded. Reload the page.'}
	/>
{:else}
	<ol class="space-y-5">
		{#each questions as question, i (question.id)}
			<li>
				<QuestionField
					{question}
					value={given[question.id]}
					number={i + 1}
					{readOnly}
					error={shownIssue(question)}
					onchange={(v) => set(question.id, v)}
				/>
			</li>
		{/each}
	</ol>
{/if}
