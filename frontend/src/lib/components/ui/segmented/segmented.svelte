<script lang="ts" module>
	import type { Component } from 'svelte';

	export interface SegmentedItem {
		value: string;
		label: string;
		/** Lucide icon component shown before the label. */
		icon?: Component;
		/** Small trailing tag, e.g. "Recommended". */
		badge?: string;
		/** Native tooltip (e.g. a one-line explanation of the option). */
		title?: string;
		disabled?: boolean;
	}
</script>

<script lang="ts">
	/**
	 * Segmented control: a one-of-N choice rendered as a pill group. Built on
	 * bits-ui RadioGroup, so it is a `radiogroup` and arrow keys move + select.
	 *
	 *   <Segmented aria-label="Generation mode" bind:value={mode} items={[
	 *     { value: 'agent', label: 'Agent', badge: 'Recommended' },
	 *     { value: 'scaffolding', label: 'Template scaffolding' },
	 *   ]} />
	 *
	 * Props:
	 * - `value` (bindable string), `items: SegmentedItem[]`.
	 * - `aria-label` (or `aria-labelledby`): required for an accessible name.
	 * - `size`: 'default' (h-9) | 'sm' (h-8, text-xs).
	 * - `fullWidth`: stretch to the container with equal-width items.
	 * - `disabled`, `name` (hidden form input), `class`, `onValueChange(value)`.
	 * Narrow containers scroll horizontally instead of wrapping.
	 */
	import { RadioGroup } from 'bits-ui';
	import { cn } from '$lib/utils';

	interface Props {
		value?: string;
		items: SegmentedItem[];
		size?: 'sm' | 'default';
		fullWidth?: boolean;
		disabled?: boolean;
		name?: string;
		class?: string;
		'aria-label'?: string;
		'aria-labelledby'?: string;
		onValueChange?: (value: string) => void;
	}

	let {
		value = $bindable(''),
		items,
		size = 'default',
		fullWidth = false,
		disabled = false,
		name,
		class: className,
		'aria-label': ariaLabel,
		'aria-labelledby': ariaLabelledby,
		onValueChange,
	}: Props = $props();
</script>

<RadioGroup.Root
	bind:value
	{onValueChange}
	{disabled}
	{name}
	orientation="horizontal"
	loop
	aria-label={ariaLabel}
	aria-labelledby={ariaLabelledby}
	data-slot="segmented"
	class={cn(
		'no-scrollbar max-w-full items-center gap-0.5 overflow-x-auto rounded-md border border-border bg-surface-2 p-0.5',
		fullWidth ? 'flex w-full' : 'inline-flex',
		className
	)}
>
	{#each items as item (item.value)}
		<RadioGroup.Item
			value={item.value}
			disabled={item.disabled}
			title={item.title}
			class={cn(
				'inline-flex shrink-0 cursor-pointer items-center justify-center gap-1.5 whitespace-nowrap rounded-[calc(var(--radius)-2px)] font-medium text-muted-foreground transition-colors duration-150',
				'hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40',
				'data-[state=checked]:bg-card data-[state=checked]:text-foreground data-[state=checked]:shadow-sm',
				'disabled:cursor-not-allowed disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0',
				size === 'sm' ? 'h-7 px-2.5 text-xs' : 'h-8 px-3 text-sm',
				fullWidth && 'flex-1'
			)}
		>
			{#snippet children({ checked })}
				{#if item.icon}
					<item.icon class={checked ? 'text-primary' : ''} aria-hidden="true" />
				{/if}
				<span>{item.label}</span>
				{#if item.badge}
					<span
						class={cn(
							'rounded-full px-1.5 py-px text-[11px] font-medium leading-4',
							checked ? 'bg-primary/15 text-success-fg' : 'bg-muted text-muted-foreground'
						)}>{item.badge}</span
					>
				{/if}
			{/snippet}
		</RadioGroup.Item>
	{/each}
</RadioGroup.Root>
