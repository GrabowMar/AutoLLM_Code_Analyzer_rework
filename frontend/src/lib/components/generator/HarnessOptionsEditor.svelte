<script lang="ts">
	/**
	 * HarnessOptionsEditor — schema-driven form for one harness's tunable options
	 * (`AgentHarnessInfo.option_schema` from `GET /agent/config/`).
	 *
	 * Props:
	 * - `schema` (`HarnessOptionField[]`): fields to render (enum → select,
	 *   int → number input, bool → switch, multi_enum → checkboxes).
	 * - `options` (bindable `HarnessOptions`): sparse — only values that differ
	 *   from the defaults. Picking the default value removes the key, matching
	 *   the backend's normalization (so the content hash is stable).
	 * - `defaults`: the harness's `default_options` (falls back to each field's `default`).
	 * - `idPrefix` (default 'harness-options'), `disabled`.
	 * - `errors`: server messages keyed by option key.
	 * - `invalid` (bindable, output): true while a number field holds an unapplied value.
	 * - `onchange(options)` after each edit.
	 * An empty schema renders a short "no tunable options" note.
	 */
	import { cn } from '$lib/utils';
	import type { HarnessOptionField, HarnessOptions, HarnessOptionValue } from '$lib/api/generation';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Switch } from '$lib/components/ui/switch';
	import * as Select from '$lib/components/ui/select';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { numberInputValue } from './llm-params';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';

	interface Props {
		schema: HarnessOptionField[];
		options: HarnessOptions;
		defaults?: HarnessOptions;
		idPrefix?: string;
		disabled?: boolean;
		errors?: Record<string, string>;
		invalid?: boolean;
		onchange?: (options: HarnessOptions) => void;
	}

	let {
		schema,
		options = $bindable({}),
		defaults = {},
		idPrefix = 'harness-options',
		disabled = false,
		errors = {},
		invalid = $bindable(false),
		onchange,
	}: Props = $props();

	function defaultOf(field: HarnessOptionField): HarnessOptionValue {
		return defaults[field.key] ?? field.default;
	}

	function sameValue(a: unknown, b: unknown): boolean {
		if (Array.isArray(a) && Array.isArray(b)) {
			return a.length === b.length && [...a].map(String).sort().join('\u0000') === [...b].map(String).sort().join('\u0000');
		}
		return a === b;
	}

	function current(field: HarnessOptionField): HarnessOptionValue {
		return field.key in options ? options[field.key] : defaultOf(field);
	}

	function isModified(field: HarnessOptionField): boolean {
		return field.key in options && !sameValue(options[field.key], defaultOf(field));
	}

	const modifiedCount = $derived(schema.filter((f) => isModified(f)).length);

	function setOption(field: HarnessOptionField, value: HarnessOptionValue | undefined) {
		const next: HarnessOptions = { ...options };
		if (value === undefined || sameValue(value, defaultOf(field))) delete next[field.key];
		else next[field.key] = value;
		options = next;
		onchange?.(options);
	}

	function resetAll() {
		intErrors = {};
		options = {};
		onchange?.(options);
	}

	// Number-field errors stay visible only while the option still holds the
	// value they were raised against (a parent reset hides them).
	let intErrors = $state<Record<string, { message: string; basis: unknown }>>({});

	function intError(field: HarnessOptionField): string {
		const entry = intErrors[field.key];
		if (entry && entry.basis === options[field.key]) return entry.message;
		return errors[field.key] ?? '';
	}

	function onIntInput(field: HarnessOptionField, event: Event) {
		const el = event.currentTarget as HTMLInputElement;
		const raw = el.value.trim();
		const fail = (message: string) => {
			intErrors = { ...intErrors, [field.key]: { message, basis: options[field.key] } };
		};
		if (raw === '') {
			if (el.validity.badInput) return fail('Incomplete number');
			delete intErrors[field.key];
			setOption(field, undefined);
			return;
		}
		const value = Number(raw);
		if (!Number.isInteger(value)) return fail('Whole numbers only');
		if (field.min != null && value < field.min) return fail(`Minimum ${field.min}`);
		if (field.max != null && value > field.max) return fail(`Maximum ${field.max}`);
		delete intErrors[field.key];
		setOption(field, value);
	}

	const hasLocalErrors = $derived(
		schema.some((f) => {
			const entry = intErrors[f.key];
			return !!entry && entry.basis === options[f.key];
		})
	);

	$effect(() => {
		invalid = hasLocalErrors;
	});

	function enumItems(field: HarnessOptionField) {
		const fallback = defaultOf(field);
		return (field.choices ?? []).map((choice) => ({
			value: choice.value,
			label: choice.value === fallback ? `${choice.label} (default)` : choice.label,
		}));
	}

	function toggleChoice(field: HarnessOptionField, value: string, on: boolean) {
		const selected = new Set((current(field) as string[] | undefined) ?? []);
		if (on) selected.add(value);
		else selected.delete(value);
		// Keep the schema's choice order so equal sets serialize identically.
		setOption(
			field,
			(field.choices ?? []).map((c) => c.value).filter((v) => selected.has(v))
		);
	}

	function describeDefault(field: HarnessOptionField): string {
		const value = defaultOf(field);
		if (typeof value === 'boolean') return value ? 'on' : 'off';
		if (Array.isArray(value)) {
			return value.length
				? value.map((v) => field.choices?.find((c) => c.value === v)?.label ?? v).join(', ')
				: 'none';
		}
		if (field.type === 'enum') return field.choices?.find((c) => c.value === value)?.label ?? String(value);
		return String(value);
	}
</script>

{#if schema.length === 0}
	<EmptyState
		compact
		icon={SlidersHorizontal}
		title="No tunable options"
		description="This harness runs with fixed settings, so every run of it is configured identically."
	/>
{:else}
	<div class="space-y-4" data-slot="harness-options-editor">
		<div class="flex flex-wrap items-center justify-between gap-2">
			<p class="text-xs text-muted-foreground" aria-live="polite">
				{modifiedCount === 0
					? 'All options at the harness defaults.'
					: `${modifiedCount} option${modifiedCount === 1 ? ' differs' : 's differ'} from the harness defaults.`}
			</p>
			{#if modifiedCount > 0}
				<Button variant="ghost" size="sm" class="h-7 px-2" onclick={resetAll} {disabled}>
					<RotateCcw />Reset to defaults
				</Button>
			{/if}
		</div>

		{#each schema as field (field.key)}
			{@const id = `${idPrefix}-${field.key}`}
			{@const modified = isModified(field)}
			{@const fieldError = field.type === 'int' ? intError(field) : (errors[field.key] ?? '')}
			<div class="space-y-1.5">
				<div class="flex flex-wrap items-center gap-2">
					{#if field.type === 'multi_enum'}
						<span id="{id}-label" class="text-sm font-medium">{field.label}</span>
					{:else}
						<Label for={id}>{field.label}</Label>
					{/if}
					<span class="font-mono text-[11px] text-muted-foreground">{field.key}</span>
					{#if modified}
						<span class="rounded-full bg-primary/15 px-1.5 text-[11px] font-medium text-success-fg">modified</span>
						<button
							type="button"
							class="cursor-pointer text-[11px] text-muted-foreground underline-offset-2 transition-colors hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
							{disabled}
							onclick={() => {
								delete intErrors[field.key];
								setOption(field, undefined);
							}}
						>
							reset
						</button>
					{/if}
				</div>

				{#if field.type === 'enum'}
					<Select.Simple
						{id}
						size="sm"
						class={cn('sm:max-w-xs', modified && 'border-primary/50')}
						value={String(current(field))}
						items={enumItems(field)}
						aria-describedby="{id}-help"
						{disabled}
						onValueChange={(v) => setOption(field, v)}
					/>
				{:else if field.type === 'int'}
					<Input
						{id}
						type="number"
						inputmode="numeric"
						min={field.min}
						max={field.max}
						step={1}
						value={numberInputValue(options[field.key])}
						placeholder="default {describeDefault(field)}"
						class={cn('h-8 sm:max-w-xs', modified && 'border-primary/50')}
						aria-invalid={fieldError ? true : undefined}
						aria-describedby="{id}-help"
						{disabled}
						oninput={(e: Event) => onIntInput(field, e)}
					/>
				{:else if field.type === 'bool'}
					<Switch
						{id}
						checked={current(field) === true}
						label={field.label}
						describedBy="{id}-help"
						size="sm"
						{disabled}
						onCheckedChange={(checked) => setOption(field, checked)}
					/>
				{:else if field.type === 'multi_enum'}
					{@const values = new Set(((current(field) as string[] | undefined) ?? []).map(String))}
					<div
						class="flex flex-wrap gap-x-4 gap-y-2"
						role="group"
						aria-labelledby="{id}-label"
						aria-describedby="{id}-help"
					>
						{#each field.choices ?? [] as choice (choice.value)}
							<label class="flex cursor-pointer items-center gap-2 text-sm">
								<Checkbox
									checked={values.has(choice.value)}
									{disabled}
									onCheckedChange={(on) => toggleChoice(field, choice.value, on)}
								/>
								{choice.label}
							</label>
						{/each}
					</div>
				{/if}

				<p id="{id}-help" class={cn('text-[11px]', fieldError ? 'text-danger-fg' : 'text-muted-foreground')}>
					{#if fieldError}
						{fieldError}
					{:else}
						{field.help}{field.help ? ' ' : ''}Default: {describeDefault(field)}{field.type === 'int' &&
						(field.min != null || field.max != null)
							? ` · range ${field.min ?? '…'}–${field.max ?? '…'}`
							: ''}.
					{/if}
				</p>
			</div>
		{/each}
	</div>
{/if}
