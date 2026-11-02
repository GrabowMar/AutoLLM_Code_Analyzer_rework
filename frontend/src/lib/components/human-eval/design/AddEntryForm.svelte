<script lang="ts">
	/**
	 * AddEntryForm — one-line "type a label, press Enter / Add" row. The new
	 * entry's id is derived from this label by the caller (and then stays put
	 * when the label is edited later).
	 *
	 * Props: `id`, `placeholder`, `buttonLabel`, `onadd(label)`, `disabled`,
	 * `maxlength`, `children` (extra controls before the input, e.g. a kind select).
	 */
	import type { Snippet } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import Plus from '@lucide/svelte/icons/plus';

	interface Props {
		id: string;
		placeholder: string;
		buttonLabel?: string;
		onadd: (label: string) => void;
		disabled?: boolean;
		maxlength?: number;
		children?: Snippet;
	}

	let { id, placeholder, buttonLabel = 'Add', onadd, disabled = false, maxlength = 300, children }: Props = $props();
	let text = $state('');

	function submit(event: Event) {
		event.preventDefault();
		const label = text.trim();
		if (!label || disabled) return;
		onadd(label);
		text = '';
	}
</script>

<div class="flex flex-wrap items-center gap-2">
	{@render children?.()}
	<Input
		{id}
		bind:value={text}
		{placeholder}
		{maxlength}
		{disabled}
		aria-label={placeholder}
		class="h-8 min-w-48 flex-1 text-sm"
		onkeydown={(e) => {
			if (e.key === 'Enter') submit(e);
		}}
	/>
	<Button type="button" variant="outline" size="sm" class="h-8" disabled={disabled || !text.trim()} onclick={submit}>
		<Plus class="size-3.5" />{buttonLabel}
	</Button>
</div>
