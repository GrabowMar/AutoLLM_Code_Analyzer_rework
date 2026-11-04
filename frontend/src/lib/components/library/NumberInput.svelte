<script lang="ts">
	/**
	 * NumberInput — a number `<input>` that binds a number (`null` when empty), styled like `ui/input`.
	 * The input binds the number itself (never `String(n)`), so partial input such as `-0` or `1.` is not
	 * rewritten while the user types. `onchange` fires with the parsed value after each edit.
	 *
	 * Props: `value`, `onchange`, `min`, `max`, `step`, `placeholder`, `id`, `disabled`, `readonly`,
	 * `invalid` (red border), `class`, `aria-label`, `aria-describedby`.
	 */
	import { untrack } from 'svelte';
	import { cn } from '$lib/utils';

	interface Props {
		value: number | null;
		onchange?: (value: number | null) => void;
		min?: number;
		max?: number;
		step?: number | 'any';
		placeholder?: string;
		id?: string;
		disabled?: boolean;
		readonly?: boolean;
		invalid?: boolean;
		class?: string;
		'aria-label'?: string;
		'aria-describedby'?: string;
	}

	let {
		value,
		onchange,
		min,
		max,
		step = 'any',
		placeholder,
		id,
		disabled = false,
		readonly = false,
		invalid = false,
		class: className,
		'aria-label': ariaLabel,
		'aria-describedby': ariaDescribedby,
	}: Props = $props();

	let local = $state<number | null>(null);
	$effect.pre(() => {
		// Follow outside changes (reset, preset switch) without fighting the user's own typing:
		// only `value` is tracked.
		const next = value ?? null;
		untrack(() => {
			if (next !== local) local = next;
		});
	});

	function input() {
		const next = typeof local === 'number' && Number.isFinite(local) ? local : null;
		onchange?.(next);
	}
</script>

<input
	type="number"
	{id}
	{min}
	{max}
	{step}
	{placeholder}
	{disabled}
	{readonly}
	aria-label={ariaLabel}
	aria-describedby={ariaDescribedby}
	aria-invalid={invalid || undefined}
	bind:value={local}
	oninput={input}
	class={cn(
		'flex h-8 w-full rounded-md border border-input bg-surface-1 px-2.5 py-1 font-mono text-sm tabular-nums shadow-xs transition-all placeholder:text-muted-foreground/70 hover:border-primary/40 focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-50 read-only:bg-muted/40',
		invalid && 'border-destructive',
		className,
	)}
/>
