<script lang="ts">
	/**
	 * RatingForm — a rating block: one scale per criterion (with anchors and an
	 * optional "Can't judge"), then the comment per the block's CommentMode.
	 *
	 * Props: `block` (RatingBlock), `answers` (RatingAnswers), `readOnly`,
	 * `onchange(next)` (a new answers object on every edit).
	 */
	import type { RatingAnswers, RatingBlock } from '$lib/api/types/human-eval';
	import { Textarea } from '$lib/components/ui/textarea';
	import { onScale } from './completeness';
	import ScaleInput from './ScaleInput.svelte';

	interface Props {
		block: RatingBlock;
		answers: RatingAnswers;
		readOnly?: boolean;
		onchange: (next: RatingAnswers) => void;
	}

	let { block, answers, readOnly = false, onchange }: Props = $props();
	const uid = $props.id();

	const criteria = $derived(answers.criteria ?? {});

	function setCriterion(id: string, value: number | 'na') {
		onchange({ ...answers, criteria: { ...criteria, [id]: value } });
	}

	function clearCriterion(id: string) {
		const next = { ...criteria };
		delete next[id];
		onchange({ ...answers, criteria: next });
	}

	function setComment(value: string) {
		onchange({ ...answers, comment: value });
	}
</script>

<div class="space-y-5">
	{#each block.criteria as criterion, i (criterion.id)}
		{@const value = criteria[criterion.id]}
		{@const invalid = value !== undefined && value !== 'na' && !onScale(value, criterion.scale)}
		<div class="space-y-2">
			<div>
				<p id="{uid}-c{i}" class="text-sm font-medium leading-snug">
					{criterion.label}
					{#if !criterion.required}<span class="ml-1 text-xs font-normal text-muted-foreground">(optional)</span>{/if}
				</p>
				{#if criterion.help}
					<p id="{uid}-h{i}" class="mt-0.5 whitespace-pre-line text-xs text-muted-foreground">{criterion.help}</p>
				{/if}
			</div>
			<ScaleInput
				scale={criterion.scale}
				{value}
				allowNa={criterion.allow_na}
				{readOnly}
				aria-labelledby="{uid}-c{i}"
				aria-describedby={criterion.help ? `${uid}-h${i}` : undefined}
				onchange={(v) => setCriterion(criterion.id, v)}
			/>
			{#if invalid}
				<p class="text-[11px] font-medium text-danger-fg">The saved value is not on this scale; pick again.</p>
			{/if}
			{#if !readOnly && !criterion.required && value !== undefined}
				<button
					type="button"
					class="cursor-pointer text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
					onclick={() => clearCriterion(criterion.id)}
				>
					Clear rating
				</button>
			{/if}
		</div>
	{/each}

	{#if block.comment !== 'off'}
		<div class="space-y-1.5">
			<label for="{uid}-comment" class="text-sm font-medium">
				Comment
				{#if block.comment === 'optional'}<span class="ml-1 text-xs font-normal text-muted-foreground">(optional)</span>{/if}
			</label>
			<Textarea
				id="{uid}-comment"
				rows={3}
				value={answers.comment ?? ''}
				disabled={readOnly}
				maxlength={5000}
				aria-required={block.comment === 'required'}
				placeholder="What drove your ratings?"
				oninput={(e: Event) => setComment((e.currentTarget as HTMLTextAreaElement).value)}
			/>
		</div>
	{/if}
</div>
