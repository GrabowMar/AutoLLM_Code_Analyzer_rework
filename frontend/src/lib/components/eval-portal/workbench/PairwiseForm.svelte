<script lang="ts">
	/**
	 * PairwiseForm — A/B preference per criterion (A / tie / B, or a five-point
	 * scale from "A much better" to "B much better"), the overall preference and
	 * the rationale. Values are stored as displayed: A is the app shown as A.
	 *
	 * Props: `block` (PairwiseBlock), `answers`, `apps` (A and B with their
	 * labels), `readOnly`, `onchange(next)`.
	 */
	import type { PairwiseAnswers, PairwiseBlock, PairwiseValue, PortalApp } from '$lib/api/types/human-eval';
	import { Textarea } from '$lib/components/ui/textarea';
	import ChoiceGroup, { type ChoiceOption } from './ChoiceGroup.svelte';
	import { pairwiseKey, pairwiseOptions, parsePairwiseKey } from './pairwise';

	interface Props {
		block: PairwiseBlock;
		answers: PairwiseAnswers;
		apps: PortalApp[];
		readOnly?: boolean;
		onchange: (next: PairwiseAnswers) => void;
	}

	let { block, answers, apps, readOnly = false, onchange }: Props = $props();
	const uid = $props.id();

	const labelA = $derived(apps.find((a) => a.slot === 'A')?.label ?? 'App A');
	const labelB = $derived(apps.find((a) => a.slot === 'B')?.label ?? 'App B');
	const options = $derived<ChoiceOption[]>(
		pairwiseOptions(block.scale).map((o) => ({ value: o.key, label: o.short, title: o.label, tone: o.side === 'tie' ? 'neutral' : 'info' }))
	);
	const criteria = $derived(answers.criteria ?? {});

	function setCriterion(id: string, key: string) {
		const value = parsePairwiseKey(key, block.scale);
		if (value === undefined) return;
		onchange({ ...answers, criteria: { ...criteria, [id]: value } });
	}

	function setOverall(key: string) {
		const value = parsePairwiseKey(key, block.scale);
		if (value === undefined) return;
		onchange({ ...answers, overall: value });
	}

	function keyOf(value: PairwiseValue | undefined): string {
		return pairwiseKey(value, block.scale);
	}
</script>

{#snippet scaleRow(labelId: string, value: PairwiseValue | undefined, onpick: (key: string) => void)}
	<div class="flex items-center gap-2">
		<span class="w-14 shrink-0 text-right text-xs leading-tight">
			<b class="block font-mono text-sm">A</b><span class="text-muted-foreground">{labelA}</span>
		</span>
		<ChoiceGroup
			value={keyOf(value)}
			{options}
			fullWidth
			{readOnly}
			aria-labelledby={labelId}
			class="min-w-0 flex-1 flex-nowrap"
			onchange={onpick}
		/>
		<span class="w-14 shrink-0 text-xs leading-tight">
			<b class="block font-mono text-sm">B</b><span class="text-muted-foreground">{labelB}</span>
		</span>
	</div>
{/snippet}

<div class="space-y-5">
	<p class="text-xs text-muted-foreground">
		Compare the two apps. <b class="font-medium text-foreground">A</b> is {labelA}, <b class="font-medium text-foreground">B</b> is {labelB}.
		{block.scale === 'five_point' ? 'Pick how strongly one is better, or "=" if they are about the same.' : 'Pick the better one, or "Tie".'}
	</p>
	{#each block.criteria as criterion, i (criterion.id)}
		<div class="space-y-1.5">
			<div>
				<p id="{uid}-c{i}" class="text-sm font-medium leading-snug">{criterion.label}</p>
				{#if criterion.help}<p class="whitespace-pre-line text-xs text-muted-foreground">{criterion.help}</p>{/if}
			</div>
			{@render scaleRow(`${uid}-c${i}`, criteria[criterion.id], (key) => setCriterion(criterion.id, key))}
		</div>
	{/each}

	{#if block.overall}
		<div class="space-y-1.5 rounded-md border border-border bg-surface-2 px-3 py-3">
			<p id="{uid}-overall" class="text-sm font-semibold">Overall, which app is better?</p>
			{@render scaleRow(`${uid}-overall`, answers.overall, setOverall)}
		</div>
	{/if}

	{#if block.rationale !== 'off'}
		<div class="space-y-1.5">
			<label for="{uid}-why" class="text-sm font-medium">
				Why?
				{#if block.rationale === 'optional'}<span class="ml-1 text-xs font-normal text-muted-foreground">(optional)</span>{/if}
			</label>
			<Textarea
				id="{uid}-why"
				rows={3}
				value={answers.rationale ?? ''}
				disabled={readOnly}
				maxlength={5000}
				aria-required={block.rationale === 'required'}
				placeholder="What made the difference?"
				oninput={(e: Event) => onchange({ ...answers, rationale: (e.currentTarget as HTMLTextAreaElement).value })}
			/>
		</div>
	{/if}
</div>
