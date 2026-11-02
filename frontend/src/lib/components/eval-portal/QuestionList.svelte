<script lang="ts">
	/**
	 * QuestionList — numbered QuestionFields bound to one answers map (task
	 * pre/post questionnaires, custom questionnaire blocks).
	 *
	 * Props:
	 * - `questions` (`Question[]`), `answers` (bindable `Record<id, QuestionAnswer>`;
	 *   pass an object, missing ids count as unanswered).
	 * - `readOnly`, `errors` (question id → message), `numbered` (default true).
	 * - `onchange(id, value)`: after each edit.
	 */
	import type { Question, QuestionAnswer } from '$lib/api/types/human-eval';
	import QuestionField from './QuestionField.svelte';

	interface Props {
		questions: Question[];
		answers: Record<string, QuestionAnswer>;
		readOnly?: boolean;
		errors?: Record<string, string>;
		numbered?: boolean;
		onchange?: (id: string, value: QuestionAnswer) => void;
	}

	let {
		questions,
		answers = $bindable(),
		readOnly = false,
		errors = {},
		numbered = true,
		onchange,
	}: Props = $props();
</script>

<ol class="list-none space-y-6">
	{#each questions as question, i (question.id)}
		<li>
			<QuestionField
				{question}
				bind:value={answers[question.id]}
				{readOnly}
				error={errors[question.id] ?? ''}
				number={numbered ? i + 1 : undefined}
				onchange={(value) => onchange?.(question.id, value)}
			/>
		</li>
	{/each}
</ol>
