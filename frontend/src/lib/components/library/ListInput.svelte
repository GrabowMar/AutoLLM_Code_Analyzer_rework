<script lang="ts">
	/**
	 * ListInput — a list of short strings edited as comma-separated text ("fp8, bf16"). The text is kept
	 * while typing (a trailing comma survives); `onchange` gets the trimmed, non-empty items.
	 *
	 * Props: `value: string[]`, `onchange`, `placeholder`, `id`, `disabled`, `readonly`, `mono`, `class`,
	 * `aria-label`.
	 */
	import { untrack } from 'svelte';
	import { Input } from '$lib/components/ui/input';
	import { cn } from '$lib/utils';

	interface Props {
		value: readonly (string | number)[];
		onchange?: (value: string[]) => void;
		placeholder?: string;
		id?: string;
		disabled?: boolean;
		readonly?: boolean;
		mono?: boolean;
		class?: string;
		'aria-label'?: string;
	}

	let { value, onchange, placeholder, id, disabled = false, readonly = false, mono = true, class: className, 'aria-label': ariaLabel }: Props = $props();

	const split = (text: string) =>
		text
			.split(',')
			.map((s) => s.trim())
			.filter(Boolean);

	let text = $state('');
	$effect.pre(() => {
		const joined = value.join(', ');
		untrack(() => {
			if (split(text).join(', ') !== joined) text = joined;
		});
	});
</script>

<Input
	{id}
	{placeholder}
	{disabled}
	{readonly}
	aria-label={ariaLabel}
	bind:value={text}
	oninput={() => onchange?.(split(text))}
	class={cn('h-8', mono && 'font-mono', className)}
/>
