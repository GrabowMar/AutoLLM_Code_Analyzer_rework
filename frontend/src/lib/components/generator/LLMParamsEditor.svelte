<script lang="ts" module>
	import type { LLMParams } from '$lib/api/generation';

	export type LLMParamsEditorMode = 'agent' | 'scaffolding' | 'custom' | 'profile' | 'preset';

	/** Lower layers the editor sits on: their merged values plus where each came from. */
	export interface InheritedLLMParams {
		values: LLMParams;
		/** key → source token ("mode_default", "profile", "preset", "inline", …). */
		sources?: Record<string, string>;
	}
</script>

<script lang="ts">
	/**
	 * LLMParamsEditor — sparse editor for OpenRouter sampling params.
	 *
	 * Props:
	 * - `params` (bindable `LLMParams`): only explicitly set keys. A blank field
	 *   means "inherit"; clearing a field removes its key. Never emits null or ''.
	 * - `inherited`: `{ values, sources }` of the layers below; shown as each
	 *   field's placeholder, e.g. "0.3 · mode default".
	 * - `mode`: which fields apply.
	 *     'agent'  — no stop / response_format (the gateway drops them); provider routing allowed.
	 *     'preset' — sampling presets: also no provider (routing is never a preset property).
	 *     'scaffolding' | 'custom' | 'profile' — every field.
	 * - `idPrefix` (required): namespace for input ids.
	 * - `errors`: server field errors keyed by param name (e.g. from `apiFieldErrors`).
	 * - `invalid` (bindable, output): true while a field holds a value that was
	 *   not applied (out of range, bad JSON, …) — block submit on it.
	 * - `hint`: intro line ('' hides it). `disabled`. `onchange(params)` after each edit.
	 * - `hideProviderRouting`: no provider routing field (e.g. no OpenRouter model
	 *   is selected: routing only applies to OpenRouter). A routing value that is
	 *   already set stays visible so it can be cleared.
	 * - `localModels`: some selected models run on local endpoints; adds a note on
	 *   which parameters those servers receive.
	 */
	import { untrack } from 'svelte';
	import { cn } from '$lib/utils';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import * as Select from '$lib/components/ui/select';
	import {
		LLM_NUMBER_FIELDS,
		REASONING_EFFORTS,
		cleanLLMParams,
		formatLLMValue,
		llmSourceLabel,
		numberInputValue,
		type LLMNumberField,
	} from './llm-params';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';

	interface Props {
		params: LLMParams;
		inherited?: InheritedLLMParams;
		mode?: LLMParamsEditorMode;
		idPrefix: string;
		errors?: Record<string, string>;
		invalid?: boolean;
		hint?: string;
		disabled?: boolean;
		onchange?: (params: LLMParams) => void;
		hideProviderRouting?: boolean;
		localModels?: boolean;
	}

	let {
		params = $bindable({}),
		inherited,
		mode = 'custom',
		idPrefix,
		errors = {},
		invalid = $bindable(false),
		hint,
		disabled = false,
		onchange,
		hideProviderRouting = false,
		localModels = false,
	}: Props = $props();

	const MAX_STOP = 8;
	const MAX_STOP_LENGTH = 64;
	const REASONING_BUDGET: LLMNumberField = {
		key: 'max_tokens',
		label: 'Reasoning budget (tokens)',
		min: 1,
		max: 2_000_000,
		step: 1,
		integer: true,
		group: 'more',
	};

	const showStopAndFormat = $derived(mode === 'scaffolding' || mode === 'custom' || mode === 'profile');
	const showProvider = $derived(mode !== 'preset' && (!hideProviderRouting || params.provider != null));
	const defaultText = $derived(mode === 'agent' || mode === 'preset' ? 'provider default' : 'default');
	const intro = $derived(
		hint ??
			(mode === 'preset'
				? 'Only the values you set are stored in the preset; blank fields keep the provider default.'
				: 'Blank fields inherit the value shown in grey. Set values are frozen into each run’s snapshot.')
	);

	const basicFields = LLM_NUMBER_FIELDS.filter((f) => f.group === 'basic');
	const moreFields = LLM_NUMBER_FIELDS.filter((f) => f.group === 'more');
	const reasoning = $derived((params.reasoning ?? {}) as Record<string, unknown>);

	const moreSetCount = $derived(
		moreFields.filter((f) => params[f.key] != null).length + (reasoning.max_tokens != null ? 1 : 0)
	);
	const advancedSetCount = $derived(
		(showStopAndFormat && params.stop?.length ? 1 : 0) +
			(showStopAndFormat && params.response_format ? 1 : 0) +
			(showProvider && params.provider ? 1 : 0)
	);
	const hasAdvanced = $derived(showStopAndFormat || showProvider);
	const setCount = $derived(Object.keys(cleanLLMParams(params)).length);

	let moreOpen = $state(untrack(() => moreSetCount > 0));
	let advancedOpen = $state(untrack(() => advancedSetCount > 0));

	// ── emitting ────────────────────────────────────────────────────────
	function emit(next: LLMParams) {
		params = cleanLLMParams(next);
		onchange?.(params);
	}

	function setKey(key: keyof LLMParams, value: unknown) {
		const next: Record<string, unknown> = { ...params };
		if (value === undefined) delete next[key];
		else next[key] = value;
		emit(next as LLMParams);
	}

	function setReasoningKey(key: string, value: unknown) {
		const next: Record<string, unknown> = { ...reasoning };
		if (value === undefined) delete next[key];
		else next[key] = value;
		setKey('reasoning', Object.keys(next).length ? next : undefined);
	}

	function resetAll() {
		numberErrors = {};
		stopDraft = null;
		providerDraft = null;
		emit({});
	}

	// ── placeholders ────────────────────────────────────────────────────
	function inheritedText(key: string, value: unknown): string {
		if (value === null || value === undefined || value === '') return defaultText;
		const source = llmSourceLabel(inherited?.sources?.[key]);
		const shown = formatLLMValue(key, value);
		return source ? `${shown} · ${source}` : shown;
	}

	function placeholderFor(key: keyof LLMParams): string {
		return inheritedText(key, inherited?.values?.[key]);
	}

	function reasoningPlaceholder(subKey: 'effort' | 'max_tokens'): string {
		const inheritedReasoning = (inherited?.values?.reasoning ?? {}) as Record<string, unknown>;
		const value = inheritedReasoning[subKey];
		if (value === null || value === undefined || value === '') return defaultText;
		const source = llmSourceLabel(inherited?.sources?.reasoning);
		return source ? `${value} · ${source}` : String(value);
	}

	// ── number fields ───────────────────────────────────────────────────
	// An error is shown only while the param still holds the value it was
	// raised against; a parent reset (new preset, cleared draft) hides it.
	let numberErrors = $state<Record<string, { message: string; basis: unknown }>>({});

	function numberValue(field: LLMNumberField, reasoningField: boolean): number | null | undefined {
		return reasoningField
			? (reasoning[field.key] as number | null | undefined)
			: (params[field.key] as number | null | undefined);
	}

	function errorKey(field: LLMNumberField, reasoningField: boolean) {
		return reasoningField ? `reasoning.${field.key}` : field.key;
	}

	function numberError(field: LLMNumberField, reasoningField: boolean): string {
		const key = errorKey(field, reasoningField);
		const entry = numberErrors[key];
		if (entry && entry.basis === numberValue(field, reasoningField)) return entry.message;
		return errors[key] ?? '';
	}

	function setNumberError(field: LLMNumberField, reasoningField: boolean, message: string) {
		const key = errorKey(field, reasoningField);
		if (!message) {
			if (key in numberErrors) {
				const { [key]: _removed, ...rest } = numberErrors;
				numberErrors = rest;
			}
			return;
		}
		numberErrors = { ...numberErrors, [key]: { message, basis: numberValue(field, reasoningField) } };
	}

	function onNumberInput(field: LLMNumberField, reasoningField: boolean, event: Event) {
		const el = event.currentTarget as HTMLInputElement;
		const raw = el.value.trim();
		const apply = (value: number | undefined) =>
			reasoningField ? setReasoningKey(field.key, value) : setKey(field.key, value);
		if (raw === '') {
			// badInput: a partial entry such as "-" — keep it on screen, apply nothing yet.
			if (el.validity.badInput) {
				setNumberError(field, reasoningField, 'Incomplete number');
				return;
			}
			setNumberError(field, reasoningField, '');
			apply(undefined);
			return;
		}
		const value = Number(raw);
		let message = '';
		if (!Number.isFinite(value)) message = 'Enter a number';
		else if (field.integer && !Number.isInteger(value)) message = 'Whole numbers only';
		else if (value < field.min || value > field.max)
			message = `Allowed range ${field.min.toLocaleString()}–${field.max.toLocaleString()}`;
		if (message) {
			setNumberError(field, reasoningField, message);
			return;
		}
		setNumberError(field, reasoningField, '');
		apply(value);
	}

	// ── stop sequences (one per line; "\n" / "\t" escapes) ─────────────
	let stopDraft = $state<{ text: string; basis: string; error: string } | null>(null);
	const stopBasis = $derived(JSON.stringify(params.stop ?? null));
	const stopDraftActive = $derived(stopDraft !== null && stopDraft.basis === stopBasis);

	function escapeStop(value: string): string {
		return value.replace(/\\/g, '\\\\').replace(/\n/g, '\\n').replace(/\t/g, '\\t');
	}

	function unescapeStop(value: string): string {
		return value.replace(/\\(\\|n|t)/g, (_m, ch: string) => (ch === 'n' ? '\n' : ch === 't' ? '\t' : '\\'));
	}

	const stopText = $derived(
		stopDraftActive && stopDraft ? stopDraft.text : (params.stop ?? []).map(escapeStop).join('\n')
	);
	const stopError = $derived(stopDraftActive && stopDraft ? stopDraft.error : (errors.stop ?? ''));

	function onStopInput(raw: string) {
		const items = raw
			.split('\n')
			.map((line) => line.trim())
			.filter(Boolean)
			.map(unescapeStop);
		let error = '';
		if (items.length > MAX_STOP) error = `At most ${MAX_STOP} stop sequences`;
		else if (items.some((s) => s.length > MAX_STOP_LENGTH))
			error = `Each stop sequence is at most ${MAX_STOP_LENGTH} characters`;
		if (error) {
			stopDraft = { text: raw, basis: stopBasis, error };
			return;
		}
		const value = items.length ? items : undefined;
		stopDraft = { text: raw, basis: JSON.stringify(value ?? null), error: '' };
		setKey('stop', value);
	}

	// ── provider routing (JSON object) ──────────────────────────────────
	let providerDraft = $state<{ text: string; basis: string; error: string; note?: string } | null>(null);
	const providerBasis = $derived(JSON.stringify(params.provider ?? null));
	const providerDraftActive = $derived(providerDraft !== null && providerDraft.basis === providerBasis);
	const providerText = $derived(
		providerDraftActive && providerDraft
			? providerDraft.text
			: params.provider
				? JSON.stringify(params.provider, null, 2)
				: ''
	);
	const providerError = $derived(
		providerDraftActive && providerDraft ? providerDraft.error : (errors.provider ?? '')
	);
	const providerNote = $derived(providerDraftActive && providerDraft ? (providerDraft.note ?? '') : '');

	function onProviderInput(raw: string) {
		if (raw.trim() === '') {
			providerDraft = { text: raw, basis: 'null', error: '' };
			setKey('provider', undefined);
			return;
		}
		let parsed: unknown;
		try {
			parsed = JSON.parse(raw);
		} catch {
			providerDraft = { text: raw, basis: providerBasis, error: 'Invalid JSON' };
			return;
		}
		if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
			providerDraft = { text: raw, basis: providerBasis, error: 'Must be a JSON object' };
			return;
		}
		const value = Object.keys(parsed).length ? (parsed as Record<string, unknown>) : undefined;
		providerDraft = {
			text: raw,
			basis: JSON.stringify(value ?? null),
			error: '',
			note: value ? '' : 'An empty object is ignored: the default routing applies.',
		};
		setKey('provider', value);
	}

	// ── validity output ─────────────────────────────────────────────────
	const hasLocalErrors = $derived(
		[...basicFields, ...moreFields].some((f) => !!numberErrorFor(f, false)) ||
			!!numberErrorFor(REASONING_BUDGET, true) ||
			(showStopAndFormat && stopDraftActive && !!stopDraft?.error) ||
			(showProvider && providerDraftActive && !!providerDraft?.error)
	);

	function numberErrorFor(field: LLMNumberField, reasoningField: boolean): string {
		const entry = numberErrors[errorKey(field, reasoningField)];
		return entry && entry.basis === numberValue(field, reasoningField) ? entry.message : '';
	}

	$effect(() => {
		invalid = hasLocalErrors;
	});
	// Unmounted (e.g. inside a collapsed panel), the editor holds no unapplied
	// value: release the parent's flag rather than leave it blocking a submit.
	$effect(() => () => {
		invalid = false;
	});

	// A collapsed section never hides an invalid value: it stays open (and its
	// toggle says so) until the value is fixed or cleared, so a blocked submit
	// always has a visible reason. Collapsing is refused meanwhile, so fixing
	// the value never makes the field vanish mid-edit.
	const moreHasError = $derived(
		moreFields.some((f) => !!numberErrorFor(f, false)) || !!numberErrorFor(REASONING_BUDGET, true)
	);
	const advancedHasError = $derived(
		(showStopAndFormat && stopDraftActive && !!stopDraft?.error) ||
			(showProvider && providerDraftActive && !!providerDraft?.error)
	);
	const moreShown = $derived(moreOpen || moreHasError);
	const advancedShown = $derived(advancedOpen || advancedHasError);

	const effortItems = REASONING_EFFORTS.map((e) => ({ value: e, label: e }));
	const responseFormatItems = [{ value: 'json_object', label: 'JSON object' }];
</script>

{#snippet numberInput(field: LLMNumberField, reasoningField: boolean)}
	{@const id = `${idPrefix}-${reasoningField ? 'reasoning-' : ''}${field.key}`}
	{@const value = numberValue(field, reasoningField)}
	{@const fieldError = numberError(field, reasoningField)}
	<div class="min-w-0 space-y-1.5">
		<Label class="text-xs" for={id}>{field.label}</Label>
		<Input
			{id}
			type="number"
			inputmode={field.integer ? 'numeric' : 'decimal'}
			min={field.min}
			max={field.max}
			step={field.step}
			value={numberInputValue(value)}
			placeholder={reasoningField ? reasoningPlaceholder('max_tokens') : placeholderFor(field.key)}
			aria-invalid={fieldError ? true : undefined}
			aria-describedby={fieldError || field.hint ? `${id}-note` : undefined}
			class={cn('h-8 text-sm tabular-nums', value != null && 'border-primary/50')}
			{disabled}
			oninput={(e: Event) => onNumberInput(field, reasoningField, e)}
		/>
		{#if fieldError}
			<p id="{id}-note" class="text-[11px] text-danger-fg">{fieldError}</p>
		{:else if field.hint}
			<p id="{id}-note" class="text-[11px] text-muted-foreground">{field.hint}</p>
		{/if}
	</div>
{/snippet}

{#snippet sectionToggle(
	open: boolean,
	label: string,
	count: number,
	toggle: () => void,
	controls: string,
	hasError: boolean
)}
	<button
		type="button"
		class="flex cursor-pointer items-start gap-1 rounded text-left text-xs font-medium text-muted-foreground transition-colors duration-150 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
		aria-expanded={open}
		aria-controls={controls}
		onclick={toggle}
	>
		<ChevronRight class={cn('mt-px size-3.5 shrink-0 transition-transform duration-150', open && 'rotate-90')} aria-hidden="true" />
		{label}
		{#if hasError}
			<span class="rounded-full bg-destructive/10 px-1.5 text-[11px] text-danger-fg">invalid value</span>
		{:else if count > 0}
			<span class="rounded-full bg-primary/15 px-1.5 text-[11px] text-success-fg">{count} set</span>
		{/if}
	</button>
{/snippet}

<div class="@container/llm space-y-3" data-slot="llm-params-editor">
	<div class="flex flex-wrap items-start justify-between gap-2">
		{#if intro}
			<p class="min-w-0 flex-1 text-xs text-muted-foreground">{intro}</p>
		{/if}
		{#if setCount > 0}
			<Button variant="ghost" size="sm" class="ml-auto h-7 px-2" onclick={resetAll} {disabled}>
				<RotateCcw />Reset {setCount}
			</Button>
		{/if}
	</div>

	{#if localModels}
		<p class="text-[11px] text-muted-foreground" data-slot="llm-local-note">
			Local endpoints receive only what their API supports: provider routing is never sent, reasoning reaches
			only models that report thinking, and Ollama's OpenAI flavor ignores top-k, min-p and repetition penalty.
		</p>
	{/if}

	<div class="grid gap-3 @md/llm:grid-cols-2 @3xl/llm:grid-cols-4">
		{#each basicFields as field (field.key)}
			{@render numberInput(field, false)}
		{/each}
		<div class="min-w-0 space-y-1.5">
			<Label class="text-xs" for="{idPrefix}-reasoning-effort">Reasoning effort</Label>
			<Select.Simple
				id="{idPrefix}-reasoning-effort"
				size="sm"
				value={(reasoning.effort as string | undefined) ?? ''}
				items={effortItems}
				placeholder={reasoningPlaceholder('effort')}
				clearLabel="Inherit ({reasoningPlaceholder('effort')})"
				class={reasoning.effort ? 'border-primary/50' : ''}
				{disabled}
				onValueChange={(v) => setReasoningKey('effort', v || undefined)}
			/>
		</div>
	</div>

	<div class="space-y-3">
		{@render sectionToggle(
			moreShown,
			'More sampling (top-k, min-p, penalties, reasoning budget)',
			moreSetCount,
			() => (moreOpen = moreHasError || !moreOpen),
			`${idPrefix}-more`,
			moreHasError
		)}
		{#if moreShown}
			<div id="{idPrefix}-more" class="grid gap-3 @md/llm:grid-cols-2 @3xl/llm:grid-cols-4">
				{#each moreFields as field (field.key)}
					{@render numberInput(field, false)}
				{/each}
				{@render numberInput(REASONING_BUDGET, true)}
			</div>
		{/if}
	</div>

	{#if hasAdvanced}
		<div class="space-y-3">
			{@render sectionToggle(
				advancedShown,
				showStopAndFormat ? 'Advanced (stop, response format, provider routing)' : 'Advanced (provider routing)',
				advancedSetCount,
				() => (advancedOpen = advancedHasError || !advancedOpen),
				`${idPrefix}-advanced`,
				advancedHasError
			)}
			{#if advancedShown}
				<div id="{idPrefix}-advanced" class="grid gap-3 @md/llm:grid-cols-2">
					{#if showStopAndFormat}
						<div class="min-w-0 space-y-1.5">
							<Label class="text-xs" for="{idPrefix}-stop">Stop sequences</Label>
							<Textarea
								id="{idPrefix}-stop"
								rows={3}
								value={stopText}
								placeholder={params.stop ? '' : placeholderFor('stop')}
								class={cn('min-h-0 font-mono text-xs', params.stop?.length && 'border-primary/50')}
								aria-invalid={stopError ? true : undefined}
								aria-describedby="{idPrefix}-stop-note"
								{disabled}
								oninput={(e: Event) => onStopInput((e.currentTarget as HTMLTextAreaElement).value)}
							/>
							<p
								id="{idPrefix}-stop-note"
								class={cn('text-[11px]', stopError ? 'text-danger-fg' : 'text-muted-foreground')}
							>
								{stopError || `One per line, at most ${MAX_STOP}. Write \\n for a newline.`}
							</p>
						</div>
						<div class="min-w-0 space-y-1.5">
							<Label class="text-xs" for="{idPrefix}-response-format">Response format</Label>
							<Select.Simple
								id="{idPrefix}-response-format"
								size="sm"
								value={(params.response_format?.type as string | undefined) ?? ''}
								items={responseFormatItems}
								placeholder={placeholderFor('response_format') === defaultText
									? 'Free text'
									: placeholderFor('response_format')}
								clearLabel="Inherit (free text)"
								{disabled}
								onValueChange={(v) => setKey('response_format', v ? { type: v } : undefined)}
							/>
						</div>
					{/if}
					{#if showProvider}
						<div class={cn('min-w-0 space-y-1.5', showStopAndFormat && '@md/llm:col-span-2')}>
							<Label class="text-xs" for="{idPrefix}-provider">Provider routing (JSON)</Label>
							<Textarea
								id="{idPrefix}-provider"
								rows={3}
								value={providerText}
								placeholder={'{"order": ["anthropic"], "allow_fallbacks": false}'}
								class={cn('min-h-0 font-mono text-xs', params.provider && 'border-primary/50')}
								aria-invalid={providerError ? true : undefined}
								aria-describedby="{idPrefix}-provider-note"
								{disabled}
								oninput={(e: Event) => onProviderInput((e.currentTarget as HTMLTextAreaElement).value)}
							/>
							<p
								id="{idPrefix}-provider-note"
								class={cn('text-[11px]', providerError ? 'text-danger-fg' : 'text-muted-foreground')}
							>
								{providerError ||
									providerNote ||
									(mode === 'agent'
										? 'Replaces the platform default routing (parameter-honouring providers, fp8+ quantization) for every model.'
										: 'OpenRouter provider preferences, e.g. order, only, ignore, allow_fallbacks, quantizations.')}
							</p>
						</div>
					{/if}
				</div>
			{/if}
		</div>
	{/if}
</div>
