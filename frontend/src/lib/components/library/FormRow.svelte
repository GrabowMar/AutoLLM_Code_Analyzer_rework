<script lang="ts">
	/**
	 * FormRow — label (+ help line) above or beside one control, with an optional error. The library
	 * editors build their forms from it so labels, help and errors line up everywhere.
	 *
	 * Props: `label`, `for` (the control id), `help`, `error`, `inline` (label left, control right from
	 * `sm`), `class`, `children` (the control).
	 */
	import type { Snippet } from 'svelte';
	import { cn } from '$lib/utils';

	interface Props {
		label: string;
		for?: string;
		help?: string;
		error?: string | null;
		inline?: boolean;
		class?: string;
		children: Snippet;
	}

	let { label, for: htmlFor, help, error = null, inline = false, class: className, children }: Props = $props();
</script>

<div class={cn(inline ? 'grid gap-1.5 sm:grid-cols-[minmax(0,12rem)_minmax(0,1fr)] sm:items-start sm:gap-4' : 'space-y-1.5', className)}>
	<div class="min-w-0">
		<label for={htmlFor} class="text-sm font-medium">{label}</label>
		{#if help}<p class="text-xs text-muted-foreground">{help}</p>{/if}
	</div>
	<div class="min-w-0 space-y-1">
		{@render children()}
		{#if error}<p class="text-xs text-danger-fg">{error}</p>{/if}
	</div>
</div>
