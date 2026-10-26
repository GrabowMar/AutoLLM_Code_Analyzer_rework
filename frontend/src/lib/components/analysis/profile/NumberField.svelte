<script lang="ts">
	/**
	 * NumberField — a typed number input for config editors.
	 *
	 * Props:
	 * - `id` (required, also the invalid-field key), `value` (`number | null`).
	 * - `onchange(value)`: called with a number (or `null` when `nullable` and
	 *   emptied) only when the input holds an applicable value.
	 * - `min` / `max` / `step`, `integer`: checked locally before applying.
	 * - `nullable`: an empty field means `null` (e.g. "provider default").
	 * - `error`: server message; local problems (incomplete number, out of
	 *   range) take precedence while the field still holds that input.
	 * - `placeholder`, `unit` (suffix), `disabled`, `class`, `describedBy`, `aria-label`.
	 * Unapplied input registers with the editor's invalid-field tracker.
	 */
	import { cn } from '$lib/utils';
	import { Input } from '$lib/components/ui/input';
	import { numberInputValue } from '$lib/components/generator/llm-params';
	import { invalidFieldsContext } from './invalid-fields.svelte';

	interface Props {
		id: string;
		value: number | null | undefined;
		onchange?: (value: number | null) => void;
		min?: number;
		max?: number;
		step?: number | 'any';
		integer?: boolean;
		nullable?: boolean;
		error?: string;
		placeholder?: string;
		unit?: string;
		disabled?: boolean;
		class?: string;
		describedBy?: string;
		'aria-label'?: string;
	}

	let {
		id,
		value,
		onchange,
		min,
		max,
		step,
		integer = false,
		nullable = false,
		error = '',
		placeholder,
		unit,
		disabled = false,
		class: className,
		describedBy,
		'aria-label': ariaLabel
	}: Props = $props();

	const tracker = invalidFieldsContext();

	// A local problem stays visible only while the bound value is still the one
	// it was raised against (a parent reset clears it).
	let local = $state<{ message: string; basis: number | null | undefined } | null>(null);
	const localMessage = $derived(local && local.basis === value ? local.message : '');
	const message = $derived(localMessage || error);

	$effect(() => {
		tracker?.set(id, !!localMessage);
		return () => tracker?.set(id, false);
	});

	function fail(text: string) {
		local = { message: text, basis: value };
	}

	function onInput(event: Event) {
		const el = event.currentTarget as HTMLInputElement;
		const raw = el.value.trim();
		if (raw === '') {
			if (el.validity.badInput) return fail('Incomplete number');
			if (!nullable) return fail('Required');
			local = null;
			onchange?.(null);
			return;
		}
		const next = Number(raw);
		if (!Number.isFinite(next)) return fail('Not a number');
		if (integer && !Number.isInteger(next)) return fail('Whole numbers only');
		if (min != null && next < min) return fail(`Minimum ${min}`);
		if (max != null && next > max) return fail(`Maximum ${max}`);
		local = null;
		onchange?.(next);
	}
</script>

<div class={cn('space-y-1', className)}>
	<div class="relative">
		<Input
			{id}
			type="number"
			inputmode={integer ? 'numeric' : 'decimal'}
			{min}
			{max}
			step={step ?? (integer ? 1 : 'any')}
			value={numberInputValue(value)}
			{placeholder}
			{disabled}
			class={cn('h-8 tabular-nums', unit && 'pr-14', message && 'border-destructive')}
			aria-invalid={message ? true : undefined}
			aria-describedby={[describedBy, message ? `${id}-error` : ''].filter(Boolean).join(' ') ||
				undefined}
			aria-label={ariaLabel}
			oninput={onInput}
		/>
		{#if unit}
			<span
				class="pointer-events-none absolute inset-y-0 right-2.5 flex items-center text-[11px] text-muted-foreground"
				aria-hidden="true">{unit}</span
			>
		{/if}
	</div>
	{#if message}
		<p id="{id}-error" class="text-[11px] text-danger-fg">{message}</p>
	{/if}
</div>
