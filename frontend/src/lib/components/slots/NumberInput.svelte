<script lang="ts">
	/**
	 * NumberInput — a number field that binds a number (`null` when empty), styled like `ui/input`. Svelte's
	 * `bind:value` on `type="number"` keeps partial input such as `-` or `1.` intact while typing (DESIGN_SYSTEM:
	 * number inputs bind numbers, never `String(n)`).
	 *
	 * Props: `value` (bindable `number | null`), `onvaluechange(v)` (after each edit), `id`, `min`, `max`,
	 * `step`, `placeholder`, `invalid`, `class`, `aria-label`.
	 */
	import { cn } from '$lib/utils';

	interface Props {
		value?: number | null;
		onvaluechange?: (value: number | null) => void;
		id?: string;
		min?: number;
		max?: number;
		step?: number | 'any';
		placeholder?: string;
		invalid?: boolean;
		disabled?: boolean;
		class?: string;
		'aria-label'?: string;
	}

	let {
		value = $bindable(null),
		onvaluechange,
		id,
		min,
		max,
		step,
		placeholder,
		invalid = false,
		disabled = false,
		class: className,
		'aria-label': ariaLabel,
	}: Props = $props();
</script>

<input
	{id}
	type="number"
	inputmode="decimal"
	{min}
	{max}
	{step}
	{placeholder}
	{disabled}
	aria-label={ariaLabel}
	aria-invalid={invalid || undefined}
	class={cn(
		'flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs transition-all placeholder:text-muted-foreground/70 hover:border-primary/40 focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive',
		className,
	)}
	bind:value
	oninput={() => onvaluechange?.(typeof value === 'number' && Number.isFinite(value) ? value : null)}
/>
