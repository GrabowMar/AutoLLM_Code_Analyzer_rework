<script lang="ts" module>
	import type { Component } from 'svelte';

	export interface OptionCardItem {
		value: string;
		title: string;
		/** One sentence: what choosing it means. */
		description: string;
		/** Short facts listed under the description. */
		points?: string[];
		icon?: Component;
		/** Small tag next to the title ("Default"). */
		badge?: string;
	}
</script>

<script lang="ts">
	/**
	 * OptionCards — a one-of-N choice where each option needs a sentence to be understood (mode, kind):
	 * selectable cards in a bits-ui RadioGroup (a `radiogroup`; arrow keys move and select, Tab leaves).
	 * The checked card carries a primary border, a filled radio dot and a check icon, so the state never
	 * rests on color alone.
	 *
	 * Props: `value`, `items`, `aria-label`, `disabled`, `columns` (2 | 3), `onValueChange(value)`.
	 */
	import { RadioGroup } from 'bits-ui';
	import { cn } from '$lib/utils';

	interface Props {
		value: string;
		items: OptionCardItem[];
		'aria-label': string;
		disabled?: boolean;
		columns?: 2 | 3;
		onValueChange: (value: string) => void;
	}

	let { value, items, 'aria-label': ariaLabel, disabled = false, columns = 2, onValueChange }: Props = $props();

	// Controlled: the parent may defer or refuse a change (e.g. behind a confirmation), so the checked
	// card always follows `value` and never the click alone.
	let current = $state('');
	$effect.pre(() => {
		current = value;
	});
	function change(next: string) {
		onValueChange(next);
		current = value;
	}
</script>

<RadioGroup.Root
	bind:value={current}
	onValueChange={change}
	{disabled}
	loop
	aria-label={ariaLabel}
	class={cn('grid gap-2.5', columns === 3 ? '@xl:grid-cols-3' : '@lg:grid-cols-2')}
>
	{#each items as item (item.value)}
		<RadioGroup.Item
			value={item.value}
			class={cn(
				'group relative flex h-full cursor-pointer flex-col gap-2 rounded-lg border border-border bg-card p-3.5 text-left transition-[border-color,background-color,box-shadow] duration-150',
				'hover:border-primary/45 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none',
				'data-[state=checked]:border-primary data-[state=checked]:bg-primary/[0.06] data-[state=checked]:shadow-[inset_0_0_0_1px_var(--primary)]',
				'disabled:cursor-not-allowed disabled:opacity-60'
			)}
		>
			{#snippet children({ checked })}
				<span class="flex items-start gap-3">
					{#if item.icon}
						<span
							class={cn(
								'flex size-9 shrink-0 items-center justify-center rounded-md border transition-colors duration-150',
								checked ? 'border-primary/40 bg-primary/12 text-primary' : 'border-border bg-surface-2 text-muted-foreground'
							)}
							aria-hidden="true"
						>
							<item.icon class="size-[18px]" />
						</span>
					{/if}
					<span class="min-w-0 flex-1">
						<span class="flex flex-wrap items-center gap-2">
							<span class="text-sm font-semibold text-foreground">{item.title}</span>
							{#if item.badge}
								<span class="rounded-full bg-muted px-1.5 py-px font-mono text-[11px] tracking-wide text-muted-foreground uppercase">{item.badge}</span>
							{/if}
						</span>
						<span class="mt-0.5 block text-xs leading-relaxed text-muted-foreground">{item.description}</span>
					</span>
					<span
						class={cn(
							'mt-0.5 flex size-[18px] shrink-0 items-center justify-center rounded-full border-2 transition-colors duration-150',
							checked ? 'border-primary' : 'border-muted-foreground/40'
						)}
						aria-hidden="true"
					>
						<span class={cn('size-2 rounded-full bg-primary transition-transform duration-150', checked ? 'scale-100' : 'scale-0')}></span>
					</span>
				</span>
				{#if item.points?.length}
					<ul class="ml-12 space-y-0.5 text-[11px] leading-snug text-muted-foreground">
						{#each item.points as point (point)}
							<li class="flex gap-1.5"><span class="text-primary/70" aria-hidden="true">–</span>{point}</li>
						{/each}
					</ul>
				{/if}
			{/snippet}
		</RadioGroup.Item>
	{/each}
</RadioGroup.Root>
