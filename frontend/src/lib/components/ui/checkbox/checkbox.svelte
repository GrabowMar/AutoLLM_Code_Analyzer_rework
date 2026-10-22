<script lang="ts">
	/**
	 * Checkbox (bits-ui `Checkbox.Root`, shadcn style). Renders a
	 * `button[role=checkbox]`, so pair it with `<Label for={id}>` or wrap it in a
	 * `<label>` — clicking the label text toggles it.
	 *
	 *   <label class="flex items-center gap-2 text-sm">
	 *     <Checkbox bind:checked={freeOnly} /> Free only
	 *   </label>
	 *
	 * Props: `checked` / `indeterminate` (bindable), `onCheckedChange`, `disabled`,
	 * `id`, `name`/`value` (form use), `class`, plus button attributes.
	 */
	import { Checkbox as CheckboxPrimitive } from 'bits-ui';
	import Check from '@lucide/svelte/icons/check';
	import Minus from '@lucide/svelte/icons/minus';
	import { cn } from '$lib/utils';

	let {
		ref = $bindable(null),
		checked = $bindable(false),
		indeterminate = $bindable(false),
		class: className,
		...restProps
	}: Omit<CheckboxPrimitive.RootProps, 'child' | 'children'> = $props();
</script>

<CheckboxPrimitive.Root
	bind:ref
	bind:checked
	bind:indeterminate
	data-slot="checkbox"
	class={cn(
		'peer flex size-4 shrink-0 cursor-pointer items-center justify-center rounded-[4px] border border-muted-foreground/60 bg-surface-1 shadow-xs transition-colors duration-150',
		'hover:border-primary/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:ring-offset-1 focus-visible:ring-offset-background',
		'data-[state=checked]:border-primary data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground',
		'data-[state=indeterminate]:border-primary data-[state=indeterminate]:bg-primary data-[state=indeterminate]:text-primary-foreground',
		'disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive',
		className
	)}
	{...restProps}
>
	{#snippet children({ checked: isChecked, indeterminate: isIndeterminate })}
		<span data-slot="checkbox-indicator" class="flex items-center justify-center text-current">
			{#if isIndeterminate}
				<Minus class="size-3" strokeWidth={3} />
			{:else if isChecked}
				<Check class="size-3" strokeWidth={3} />
			{/if}
		</span>
	{/snippet}
</CheckboxPrimitive.Root>
