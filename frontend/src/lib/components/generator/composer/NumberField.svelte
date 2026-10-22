<script lang="ts">
	/**
	 * NumberField — labelled number input with range validation.
	 *
	 * Props:
	 * - `value` (bindable `number | null`): stored units; null = empty.
	 * - `scale`: display = value / scale (e.g. 60 shows seconds as minutes);
	 *   typed values are converted back and rounded to whole stored units.
	 * - `min` / `max` (stored units), `integer` (default true), `step` (display units).
	 * - `required`: an empty field is an error (otherwise empty = inherit).
	 * - `error`: server message; `invalid` (bindable output): a typed value was not applied.
	 * - `label`, `id`, `placeholder`, `hint`, `disabled`, `class`.
	 * - `onchange(value)` after each applied edit. `children`: trailing addon (e.g. a button).
	 */
	import type { Snippet } from 'svelte';
	import { cn } from '$lib/utils';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { numberInputValue } from '../llm-params';

	interface Props {
		id: string;
		label: string;
		value?: number | null;
		min?: number;
		max?: number;
		integer?: boolean;
		scale?: number;
		step?: number;
		required?: boolean;
		placeholder?: string;
		hint?: string;
		error?: string;
		invalid?: boolean;
		disabled?: boolean;
		class?: string;
		onchange?: (value: number | null) => void;
		children?: Snippet;
	}

	let {
		id,
		label,
		value = $bindable(null),
		min,
		max,
		integer = true,
		scale = 1,
		step,
		required = false,
		placeholder,
		hint,
		error = '',
		invalid = $bindable(false),
		disabled = false,
		class: className,
		onchange,
		children,
	}: Props = $props();

	// A local error stays visible only while the value is the one it was raised
	// against: a parent reset (new draft, cleared overrides) hides it.
	let local = $state<{ message: string; basis: number | null } | null>(null);

	const display = $derived(value == null ? null : value / scale);
	const localError = $derived(local && local.basis === (value ?? null) ? local.message : '');
	const shownError = $derived(localError || error);

	function fmt(n: number): string {
		return (n / scale).toLocaleString(undefined, { maximumFractionDigits: 3 });
	}

	const rangeText = $derived(
		min != null && max != null
			? `Allowed range ${fmt(min)}–${fmt(max)}`
			: min != null
				? `Minimum ${fmt(min)}`
				: max != null
					? `Maximum ${fmt(max)}`
					: 'Enter a number'
	);

	$effect(() => {
		invalid = !!localError;
	});

	function apply(next: number | null) {
		value = next;
		onchange?.(next);
	}

	function onInput(event: Event) {
		const el = event.currentTarget as HTMLInputElement;
		const raw = el.value.trim();
		if (raw === '') {
			// badInput: a partial entry such as "-" or "1e" — keep it, apply nothing.
			if (el.validity.badInput) {
				local = { message: 'Incomplete number', basis: value ?? null };
				return;
			}
			local = required ? { message: `Required. ${rangeText}`, basis: null } : null;
			apply(null);
			return;
		}
		const typed = Number(raw);
		if (!Number.isFinite(typed)) {
			local = { message: 'Enter a number', basis: value ?? null };
			return;
		}
		if (integer && scale === 1 && !Number.isInteger(typed)) {
			local = { message: 'Whole numbers only', basis: value ?? null };
			return;
		}
		const stored = scale === 1 ? typed : Math.round(typed * scale);
		if ((min != null && stored < min) || (max != null && stored > max)) {
			local = { message: rangeText, basis: value ?? null };
			return;
		}
		local = null;
		apply(stored);
	}
</script>

<div class={cn('min-w-0 space-y-1.5', className)}>
	<Label class="text-xs" for={id}>{label}</Label>
	<div class="flex items-center gap-1.5">
		<Input
			{id}
			type="number"
			inputmode={integer && scale === 1 ? 'numeric' : 'decimal'}
			min={min != null ? min / scale : undefined}
			max={max != null ? max / scale : undefined}
			step={step ?? (integer && scale === 1 ? 1 : 'any')}
			value={numberInputValue(display)}
			{placeholder}
			aria-invalid={shownError ? true : undefined}
			aria-describedby={shownError || hint ? `${id}-note` : undefined}
			class={cn('h-8 text-sm tabular-nums', value != null && !required && 'border-primary/50')}
			{disabled}
			oninput={onInput}
		/>
		{@render children?.()}
	</div>
	{#if shownError}
		<p id="{id}-note" class="text-[11px] text-danger-fg">{shownError}</p>
	{:else if hint}
		<p id="{id}-note" class="text-[11px] text-muted-foreground">{hint}</p>
	{/if}
</div>
