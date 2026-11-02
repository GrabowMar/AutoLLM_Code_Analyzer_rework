<script lang="ts">
	/**
	 * QuestionField — one participant question of any `QuestionKind`:
	 * - `text` / `textarea`: free text (≤ 5000 characters);
	 * - `single` / `attention`: one option (radio list);
	 * - `multi`: any number of options (checkbox list);
	 * - `scale`: one point of an integer scale (min..max on its step grid), with
	 *   the min/max anchors and per-point labels (shown under the points on short
	 *   labelled scales, otherwise read to screen readers);
	 * - `number`: a number within min/max.
	 * Single-choice and scale questions are a `<fieldset role="radiogroup">` of
	 * native radios (arrow keys move), multi-choice a `<fieldset>` of checkboxes,
	 * text and number questions an input with a `<label>`. Optional questions say
	 * so; required ones set `aria-required`; errors set `aria-invalid` and are
	 * linked with `aria-describedby`.
	 *
	 * Props:
	 * - `question` (`Question`; attention questions arrive without `expected`).
	 * - `value` (bindable `QuestionAnswer`; undefined counts as unanswered).
	 * - `readOnly`: show the answer without letting it change.
	 * - `error`: message to show (e.g. from `questionError` or the server).
	 * - `number`: optional prefix for the label ("3" → "3. Label").
	 * - `onchange(value)`: after each edit by the participant.
	 */
	import type { Question, QuestionAnswer } from '$lib/api/types/human-eval';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import { Textarea } from '$lib/components/ui/textarea';
	import { cn } from '$lib/utils';
	import { TEXT_ANSWER_MAX, scalePointLabel, scaleValues, toggleOption } from './questions';

	interface Props {
		question: Question;
		value?: QuestionAnswer;
		readOnly?: boolean;
		error?: string;
		number?: number | string;
		onchange?: (value: QuestionAnswer) => void;
	}

	let { question, value = $bindable(), readOnly = false, error = '', number, onchange }: Props = $props();

	const uid = $props.id();
	const inputId = `q-${uid}`;
	const helpId = `q-${uid}-help`;
	const errorId = `q-${uid}-error`;

	let localError = $state('');
	const shownError = $derived(error || localError);
	const describedBy = $derived(
		[question.help ? helpId : '', shownError ? errorId : ''].filter(Boolean).join(' ') || undefined
	);

	const options = $derived(question.options ?? []);
	const points = $derived(question.scale ? scaleValues(question.scale) : []);
	const pointLabels = $derived(
		question.scale ? points.map((v) => scalePointLabel(question.scale!, v)) : []
	);
	// Short scales with a label on every point show the labels under the points.
	const labelledScale = $derived(
		points.length > 0 && points.length <= 7 && !!question.scale?.labels && pointLabels.every(Boolean)
	);
	const isRadioGroup = $derived(
		question.kind === 'single' || question.kind === 'attention' || question.kind === 'scale'
	);
	const legendId = `q-${uid}-legend`;
	const textValue = $derived(typeof value === 'string' ? value : '');
	const selected = $derived(Array.isArray(value) ? value : []);
	const hasValue = $derived(
		value !== null && value !== undefined && !(Array.isArray(value) && value.length === 0) && value !== ''
	);

	function set(next: QuestionAnswer) {
		if (readOnly) return;
		value = next;
		onchange?.(next);
	}

	// Type-cast helper: <input type=number> takes the number itself (see DESIGN_SYSTEM.md).
	function numberInputValue(v: unknown): string {
		return (typeof v === 'number' ? v : '') as unknown as string;
	}

	function onNumberInput(event: Event) {
		const el = event.currentTarget as HTMLInputElement;
		const raw = el.value.trim();
		if (raw === '') {
			if (el.validity.badInput) {
				localError = 'Incomplete number';
				return;
			}
			localError = '';
			set(null);
			return;
		}
		const n = Number(raw);
		if (!Number.isFinite(n)) {
			localError = 'Enter a number';
			return;
		}
		localError = '';
		set(n);
	}

	const numberHint = $derived(
		question.min != null && question.max != null
			? `${question.min} to ${question.max}`
			: question.min != null
				? `${question.min} or more`
				: question.max != null
					? `up to ${question.max}`
					: ''
	);
</script>

{#snippet labelText()}
	{#if number != null}<span class="mr-1 font-mono text-xs text-muted-foreground tabular-nums">{number}.</span>{/if}<span
		class="text-sm font-medium leading-snug">{question.label}</span
	>{#if !question.required}<span class="ml-1.5 text-xs font-normal text-muted-foreground">(optional)</span>{/if}
{/snippet}

{#snippet notes()}
	{#if question.help}
		<p id={helpId} class="whitespace-pre-line text-xs text-muted-foreground">{question.help}</p>
	{/if}
{/snippet}

{#snippet errorLine()}
	{#if shownError}
		<p id={errorId} class="text-[11px] font-medium text-danger-fg">{shownError}</p>
	{/if}
{/snippet}

{#snippet clearButton()}
	{#if !readOnly && !question.required && hasValue}
		<button
			type="button"
			class="cursor-pointer text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline focus-visible:rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
			onclick={() => set(question.kind === 'multi' ? [] : null)}
		>
			Clear answer
		</button>
	{/if}
{/snippet}

<div class="space-y-2" data-question-id={question.id}>
	{#if isRadioGroup}
		<fieldset
			class="min-w-0 space-y-2"
			role="radiogroup"
			aria-labelledby={legendId}
			aria-describedby={describedBy}
			aria-invalid={shownError ? true : undefined}
			aria-required={question.required ? true : undefined}
		>
			<legend id={legendId} class="mb-1 block">{@render labelText()}</legend>
			{@render notes()}

			{#if question.kind === 'scale'}
				{#if question.scale && points.length}
					<div
						class={cn(
							'grid gap-1.5',
							labelledScale
								? 'grid-cols-1 sm:grid-cols-[repeat(var(--points),minmax(0,1fr))]'
								: points.length > 7
									? 'grid-cols-7 sm:grid-cols-[repeat(var(--points),minmax(0,1fr))]'
									: 'grid-cols-[repeat(var(--points),minmax(0,1fr))]'
						)}
						style:--points={points.length}
					>
						{#each points as point, i (point)}
							<label
								class={cn(
									'flex min-w-0 items-center gap-2',
									labelledScale ? 'sm:flex-col sm:items-stretch sm:gap-1' : 'flex-col',
									readOnly ? 'cursor-default' : 'cursor-pointer'
								)}
							>
								<input
									type="radio"
									class="peer sr-only"
									name={inputId}
									value={point}
									checked={value === point}
									disabled={readOnly}
									onchange={() => set(point)}
								/>
								<span
									class={cn(
										'flex h-9 shrink-0 items-center justify-center rounded-md border border-border bg-card font-mono tabular-nums transition-colors duration-150',
										points.length > 11 ? 'text-xs' : 'text-sm',
										labelledScale ? 'w-10 sm:w-full' : 'w-full',
										'peer-checked:border-primary peer-checked:bg-primary peer-checked:text-primary-foreground',
										'peer-focus-visible:ring-2 peer-focus-visible:ring-ring/50 peer-focus-visible:ring-offset-1 peer-focus-visible:ring-offset-background',
										!readOnly && 'hover:border-primary/50'
									)}>{point}</span
								>
								{#if labelledScale}
									<span class="text-xs leading-tight text-muted-foreground sm:text-center">{pointLabels[i]}</span>
								{:else if pointLabels[i]}
									<span class="sr-only">{pointLabels[i]}</span>
								{/if}
							</label>
						{/each}
					</div>
					{#if !labelledScale && (question.scale.min_label || question.scale.max_label)}
						<div class="flex justify-between gap-4 text-xs text-muted-foreground" aria-hidden="true">
							<span>{question.scale.min} = {question.scale.min_label || '—'}</span>
							<span class="text-right">{question.scale.max} = {question.scale.max_label || '—'}</span>
						</div>
					{/if}
				{:else}
					<p class="text-xs text-muted-foreground">This question has no scale.</p>
				{/if}
			{:else if options.length}
				<div class="grid gap-1.5">
					{#each options as option (option.value)}
						<label
							class={cn(
								'flex items-start gap-2.5 rounded-md border border-border bg-card px-3 py-2 text-sm transition-colors duration-150',
								'has-[:checked]:border-primary has-[:checked]:bg-primary/5 has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring/40',
								readOnly ? 'cursor-default' : 'cursor-pointer hover:border-primary/40'
							)}
						>
							<input
								type="radio"
								class="mt-0.5 size-4 shrink-0 accent-primary focus-visible:outline-none"
								name={inputId}
								value={option.value}
								checked={value === option.value}
								disabled={readOnly}
								onchange={() => set(option.value)}
							/>
							<span class="min-w-0 break-words">{option.label}</span>
						</label>
					{/each}
				</div>
			{:else}
				<p class="text-xs text-muted-foreground">This question has no answer options.</p>
			{/if}
		</fieldset>
		{@render errorLine()}
		{@render clearButton()}
	{:else if question.kind === 'multi'}
		<fieldset class="min-w-0 space-y-2" aria-labelledby={legendId} aria-describedby={describedBy}>
			<legend id={legendId} class="mb-1 block">{@render labelText()}</legend>
			{@render notes()}
			{#if options.length}
				<div class="grid gap-1.5">
					{#each options as option (option.value)}
						<label
							class={cn(
								'flex items-start gap-2.5 rounded-md border border-border bg-card px-3 py-2 text-sm transition-colors duration-150',
								'has-[[data-state=checked]]:border-primary has-[[data-state=checked]]:bg-primary/5',
								readOnly ? 'cursor-default' : 'cursor-pointer hover:border-primary/40'
							)}
						>
							<Checkbox
								class="mt-0.5"
								checked={selected.includes(option.value)}
								disabled={readOnly}
								aria-invalid={shownError ? true : undefined}
								onCheckedChange={(on) => set(toggleOption(question, value, option.value, on === true))}
							/>
							<span class="min-w-0 break-words">{option.label}</span>
						</label>
					{/each}
				</div>
			{:else}
				<p class="text-xs text-muted-foreground">This question has no answer options.</p>
			{/if}
		</fieldset>
		{@render errorLine()}
		{@render clearButton()}
	{:else}
		<label for={inputId} class="block">{@render labelText()}</label>
		{@render notes()}
		{#if question.kind === 'textarea'}
			<Textarea
				id={inputId}
				rows={4}
				value={textValue}
				maxlength={TEXT_ANSWER_MAX}
				readonly={readOnly}
				aria-required={question.required ? true : undefined}
				aria-invalid={shownError ? true : undefined}
				aria-describedby={describedBy}
				oninput={(e: Event) => set((e.currentTarget as HTMLTextAreaElement).value)}
			/>
		{:else if question.kind === 'number'}
			<div class="flex items-center gap-2">
				<Input
					id={inputId}
					type="number"
					inputmode="decimal"
					step="any"
					min={question.min ?? undefined}
					max={question.max ?? undefined}
					class="w-40 tabular-nums"
					value={numberInputValue(value)}
					readonly={readOnly}
					aria-required={question.required ? true : undefined}
					aria-invalid={shownError ? true : undefined}
					aria-describedby={describedBy}
					oninput={onNumberInput}
				/>
				{#if numberHint}<span class="text-xs text-muted-foreground">{numberHint}</span>{/if}
			</div>
		{:else}
			<Input
				id={inputId}
				type="text"
				value={textValue}
				maxlength={TEXT_ANSWER_MAX}
				autocomplete="off"
				readonly={readOnly}
				aria-required={question.required ? true : undefined}
				aria-invalid={shownError ? true : undefined}
				aria-describedby={describedBy}
				oninput={(e: Event) => set((e.currentTarget as HTMLInputElement).value)}
			/>
		{/if}
		{@render errorLine()}
	{/if}
</div>
