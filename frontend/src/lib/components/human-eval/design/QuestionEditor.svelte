<script lang="ts">
	/**
	 * QuestionEditor — the body of one question (kind, label, help, required and
	 * the kind's own fields: options + expected answer, scale, number bounds).
	 * The list (QuestionListEditor) owns ordering and removal.
	 *
	 * Props: `id`, `question` (bindable), `errors` (relative to the question),
	 * `readOnly`, `allowAttention`.
	 */
	import * as Select from '$lib/components/ui/select';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import NumberField from '$lib/components/studies/shared/NumberField.svelte';
	import type { Question, QuestionKind } from '$lib/api/types/human-eval';
	import { messageAt, messagesAt, under, type FieldError } from './errors';
	import { QUESTION_KINDS, applyQuestionKind } from './factories';
	import { scaleSummary } from './scale';
	import OptionListEditor from './OptionListEditor.svelte';
	import ScaleEditor from './ScaleEditor.svelte';

	interface Props {
		id: string;
		question: Question;
		errors?: FieldError[];
		readOnly?: boolean;
		allowAttention?: boolean;
	}

	let { id, question = $bindable(), errors = [], readOnly = false, allowAttention = true }: Props = $props();

	const kindItems = $derived(
		QUESTION_KINDS.filter((k) => allowAttention || k.value !== 'attention' || question.kind === 'attention').map((k) => ({ value: k.value, label: k.label, description: k.description }))
	);
	const kindLabel = $derived(QUESTION_KINDS.find((k) => k.value === question.kind)?.label ?? question.kind);
	const hasOptions = $derived(question.kind === 'single' || question.kind === 'multi' || question.kind === 'attention');
	const optionErrors = $derived(under(errors, 'options'));
	const expectedLabel = $derived(question.options?.find((o) => o.value === question.expected)?.label ?? '');

	function setKind(kind: string) {
		if (!QUESTION_KINDS.some((k) => k.value === kind) || kind === question.kind) return;
		question = applyQuestionKind(question, kind as QuestionKind);
	}

	function optionRemoved(value: string) {
		if (question.kind === 'attention' && question.expected === value) {
			question.expected = question.options?.[0]?.value ?? '';
		}
	}
</script>

{#if readOnly}
	<div class="space-y-1 text-sm">
		<p class="text-xs text-muted-foreground">
			{kindLabel}{question.required ? ' · required' : ' · optional'}
			{#if question.kind === 'scale' && question.scale} · {scaleSummary(question.scale)}{/if}
			{#if question.kind === 'number' && (question.min != null || question.max != null)}
				· {question.min ?? '−∞'} to {question.max ?? '∞'}
			{/if}
		</p>
		{#if question.help}<p class="text-xs text-muted-foreground">{question.help}</p>{/if}
		{#if hasOptions && question.options}
			<OptionListEditor id="{id}-options" options={question.options} readOnly />
		{/if}
		{#if question.kind === 'attention'}
			<p class="text-xs"><span class="text-muted-foreground">Expected answer:</span> {expectedLabel || question.expected || '—'}</p>
		{/if}
	</div>
{:else}
	<div class="space-y-3">
		<div class="grid gap-2 sm:grid-cols-[11rem_minmax(0,1fr)]">
			<div class="space-y-1.5">
				<Label class="text-xs" for="{id}-kind">Kind</Label>
				<Select.Simple id="{id}-kind" size="sm" value={question.kind} items={kindItems} onValueChange={setKind} />
			</div>
			<div class="space-y-1.5">
				<Label class="text-xs" for="{id}-label">Question</Label>
				<Input
					id="{id}-label"
					class="h-8 text-sm"
					bind:value={question.label}
					maxlength={300}
					aria-invalid={messageAt(errors, 'label') ? true : undefined}
				/>
				{#if messageAt(errors, 'label')}<p class="text-[11px] text-danger-fg">{messageAt(errors, 'label')}</p>{/if}
			</div>
		</div>
		<div class="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
			<div class="space-y-1.5">
				<Label class="text-xs" for="{id}-help">Help text <span class="text-muted-foreground">(optional)</span></Label>
				<Input id="{id}-help" class="h-8 text-sm" bind:value={() => question.help ?? '', (v) => (question.help = v)} maxlength={5000} />
			</div>
			<label class="flex h-8 items-center gap-2 text-sm">
				<Checkbox
					checked={question.kind === 'attention' ? true : question.required}
					disabled={question.kind === 'attention'}
					onCheckedChange={(v) => (question.required = !!v)}
				/>
				Required
			</label>
		</div>

		{#if hasOptions}
			<div class="space-y-1.5">
				<p class="section-label">Options</p>
				<OptionListEditor
					id="{id}-options"
					bind:options={() => question.options ?? [], (v) => (question.options = v)}
					errors={optionErrors}
					onremoved={optionRemoved}
				/>
			</div>
		{/if}

		{#if question.kind === 'attention'}
			<div class="space-y-1.5 sm:w-72">
				<Label class="text-xs" for="{id}-expected">Expected answer</Label>
				<Select.Simple
					id="{id}-expected"
					size="sm"
					value={question.expected ?? ''}
					placeholder="Pick the correct option"
					items={(question.options ?? []).map((o) => ({ value: o.value, label: o.label || o.value }))}
					onValueChange={(v) => (question.expected = v)}
					aria-invalid={messageAt(errors, 'expected') ? true : undefined}
				/>
				<p class="text-[11px] {messageAt(errors, 'expected') ? 'text-danger-fg' : 'text-muted-foreground'}">
					{messageAt(errors, 'expected') || 'Never sent to participants; answers that differ are flagged as attention failures.'}
				</p>
			</div>
		{/if}

		{#if question.kind === 'scale'}
			<div class="space-y-1.5">
				<p class="section-label">Scale</p>
				<ScaleEditor
					id="{id}-scale"
					bind:scale={() => question.scale ?? { min: 1, max: 5, step: 1 }, (v) => (question.scale = v)}
					errors={under(errors, 'scale')}
				/>
			</div>
		{/if}

		{#if question.kind === 'number'}
			<div class="grid grid-cols-2 gap-2 sm:w-72">
				<NumberField
					id="{id}-min"
					label="Minimum (optional)"
					integer={false}
					bind:value={() => question.min ?? null, (v) => (question.min = v)}
					error={messageAt(errors, 'min')}
				/>
				<NumberField
					id="{id}-max"
					label="Maximum (optional)"
					integer={false}
					bind:value={() => question.max ?? null, (v) => (question.max = v)}
					error={messageAt(errors, 'max')}
				/>
			</div>
		{/if}

		{#each [...messagesAt(errors), ...messagesAt(errors, 'kind'), ...messagesAt(errors, 'id'), ...messagesAt(errors, 'required'), ...messagesAt(errors, 'help')] as message (message)}
			<p class="text-[11px] text-danger-fg">{message}</p>
		{/each}
	</div>
{/if}
