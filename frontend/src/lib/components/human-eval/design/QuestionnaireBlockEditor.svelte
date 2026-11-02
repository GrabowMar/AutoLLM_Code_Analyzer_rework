<script lang="ts">
	/**
	 * QuestionnaireBlockEditor — a standard instrument (its fixed questions are
	 * supplied by the server and shown read-only) or custom questions.
	 *
	 * Props: `id`, `block` (bindable), `environment` (instrument wording),
	 * `errors` (relative to the block), `readOnly`.
	 */
	import * as Select from '$lib/components/ui/select';
	import { Label } from '$lib/components/ui/label';
	import type { HumanEvalEnvironment, QuestionnaireBlock, StandardInstrument } from '$lib/api/types/human-eval';
	import { messageAt, messagesAt, under, type FieldError } from './errors';
	import { scaleSummary } from './scale';
	import QuestionListEditor from './QuestionListEditor.svelte';

	interface Props {
		id: string;
		block: QuestionnaireBlock;
		environment: HumanEvalEnvironment | null;
		errors?: FieldError[];
		readOnly?: boolean;
	}

	let { id, block = $bindable(), environment, errors = [], readOnly = false }: Props = $props();

	const FALLBACK: Record<StandardInstrument, { name: string; note: string }> = {
		sus: { name: 'System Usability Scale (SUS)', note: '10 items, 1–5; scored 0–100.' },
		umux_lite: { name: 'UMUX-Lite', note: '2 items, 1–7; scored 0–100.' },
		nasa_tlx: { name: 'NASA-TLX (raw)', note: '6 workload dimensions, 0–100 in steps of 5; mean 0–100.' },
	};

	const instrumentItems = $derived([
		...(Object.keys(FALLBACK) as StandardInstrument[]).map((key) => ({
			value: key,
			label: environment?.instruments?.[key]?.name || FALLBACK[key].name,
			description: FALLBACK[key].note,
		})),
		{ value: 'custom', label: 'Custom questions', description: 'Your own questions, asked for every app' },
	]);

	const standard = $derived(block.instrument === 'custom' ? null : block.instrument);
	const fixedQuestions = $derived(standard ? (environment?.instruments?.[standard]?.questions ?? []) : []);

	function setInstrument(value: string) {
		if (value === block.instrument) return;
		if (value === 'custom') {
			block.instrument = 'custom';
			return;
		}
		if (value in FALLBACK) {
			block.instrument = value as StandardInstrument;
			// Standard instruments carry no questions of their own.
			block.questions = [];
		}
	}
</script>

<div class="space-y-4">
	<div class="space-y-1.5 sm:w-96">
		<Label class="text-xs" for="{id}-instrument">Instrument</Label>
		{#if readOnly}
			<p class="text-sm">{instrumentItems.find((i) => i.value === block.instrument)?.label ?? block.instrument}</p>
		{:else}
			<Select.Simple
				id="{id}-instrument"
				value={block.instrument}
				items={instrumentItems}
				onValueChange={setInstrument}
				aria-invalid={messageAt(errors, 'instrument') ? true : undefined}
			/>
		{/if}
		{#if messageAt(errors, 'instrument')}<p class="text-[11px] text-danger-fg">{messageAt(errors, 'instrument')}</p>{/if}
	</div>

	{#if standard}
		<div class="space-y-2">
			<p class="section-label">Fixed questions <span class="normal-case tracking-normal">(standard wording, not editable)</span></p>
			{#if fixedQuestions.length === 0}
				<p class="text-sm text-muted-foreground">{FALLBACK[standard].note} The server supplies the standard wording.</p>
			{:else}
				<ol class="space-y-1 rounded-md border border-border bg-surface-1 px-3 py-2">
					{#each fixedQuestions as question, i (question.id)}
						<li class="flex items-baseline gap-2 text-sm">
							<span class="w-6 shrink-0 text-right font-mono text-[11px] tabular-nums text-muted-foreground">{i + 1}</span>
							<span class="min-w-0 flex-1">
								{question.label}
								{#if question.scale}
									<span class="ml-1 font-mono text-[11px] text-muted-foreground">
										{scaleSummary(question.scale)}{question.scale.min_label || question.scale.max_label ? ` · ${question.scale.min_label ?? ''} → ${question.scale.max_label ?? ''}` : ''}
									</span>
								{/if}
							</span>
						</li>
					{/each}
				</ol>
			{/if}
		</div>
	{:else}
		<div class="space-y-2">
			<p class="section-label">Questions</p>
			<QuestionListEditor
				id="{id}-questions"
				bind:questions={block.questions}
				errors={under(errors, 'questions')}
				{readOnly}
				allowAttention={false}
				emptyText="No questions yet — a custom questionnaire needs at least one."
			/>
		</div>
	{/if}
	{#each messagesAt(errors) as message (message)}
		<p class="text-[11px] text-danger-fg">{message}</p>
	{/each}
</div>
