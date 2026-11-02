<script lang="ts">
	/**
	 * QuestionListEditor — an ordered list of questions (pre/post
	 * questionnaires, custom questionnaire blocks): add by kind + label, reorder,
	 * duplicate, remove, and edit each with QuestionEditor. Ids come from the
	 * label when a question is added and stay stable afterwards.
	 *
	 * Props: `id` (prefix), `questions` (bindable), `errors` (relative to the
	 * list: `["0", "label"]` or `["<question id>", "label"]`), `readOnly`,
	 * `allowAttention` (default true), `emptyText`.
	 */
	import * as Select from '$lib/components/ui/select';
	import type { Question, QuestionKind } from '$lib/api/types/human-eval';
	import { messagesAt, under, type FieldError } from './errors';
	import { QUESTION_KINDS, clonePlain, newQuestion } from './factories';
	import { uniqueId } from './ids';
	import { insertedAfter, moved, removedAt } from './lists';
	import AddEntryForm from './AddEntryForm.svelte';
	import ListItemActions from './ListItemActions.svelte';
	import QuestionEditor from './QuestionEditor.svelte';

	interface Props {
		id: string;
		questions: Question[];
		errors?: FieldError[];
		readOnly?: boolean;
		allowAttention?: boolean;
		emptyText?: string;
	}

	let {
		id,
		questions = $bindable([]),
		errors = [],
		readOnly = false,
		allowAttention = true,
		emptyText = 'No questions yet.',
	}: Props = $props();

	let newKind = $state<QuestionKind>('single');
	const kindItems = $derived(
		QUESTION_KINDS.filter((k) => allowAttention || k.value !== 'attention').map((k) => ({ value: k.value, label: k.label }))
	);
	const listMessages = $derived(messagesAt(errors));

	function add(label: string) {
		questions = [...questions, newQuestion(newKind, label, questions)];
	}

	function duplicate(index: number) {
		const copy = clonePlain(questions[index]);
		copy.id = uniqueId(copy.id, questions.map((q) => q.id));
		copy.label = `${copy.label} (copy)`.slice(0, 300);
		questions = insertedAfter(questions, index, copy);
	}
</script>

<div class="space-y-2">
	{#if questions.length === 0}
		<p class="text-sm text-muted-foreground">{emptyText}</p>
	{/if}
	<ol class="space-y-2">
		{#each questions as question, i (question.id)}
			{@const errs = under(errors, [i, question.id])}
			<li class="rounded-md border bg-surface-1 {errs.length ? 'border-destructive/40' : 'border-border'}">
				<div class="flex items-center gap-2 border-b border-border px-3 py-1.5">
					<span class="font-mono text-[11px] tabular-nums text-muted-foreground">Q{i + 1}</span>
					<span class="min-w-0 flex-1 truncate text-sm font-medium">{question.label || 'Untitled question'}</span>
					<span class="hidden font-mono text-[11px] text-muted-foreground sm:inline" title="Stable id (exports, analysis)">{question.id}</span>
					{#if !readOnly}
						<ListItemActions
							name={question.label}
							index={i}
							count={questions.length}
							onmove={(d) => (questions = moved(questions, i, d))}
							onduplicate={() => duplicate(i)}
							onremove={() => (questions = removedAt(questions, i))}
						/>
					{/if}
				</div>
				<div class="px-3 py-2.5">
					<QuestionEditor id="{id}-{question.id}" bind:question={questions[i]} errors={errs} {readOnly} {allowAttention} />
				</div>
			</li>
		{/each}
	</ol>
	{#each listMessages as message (message)}
		<p class="text-[11px] text-danger-fg">{message}</p>
	{/each}
	{#if !readOnly}
		<AddEntryForm id="{id}-add" placeholder="New question" buttonLabel="Add question" onadd={add}>
			<Select.Simple
				size="sm"
				class="h-8 w-40"
				aria-label="Kind of the new question"
				value={newKind}
				items={kindItems}
				onValueChange={(v) => (newKind = v as QuestionKind)}
			/>
		</AddEntryForm>
	{/if}
</div>
