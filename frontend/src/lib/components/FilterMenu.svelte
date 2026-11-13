<script lang="ts" module>
	import type { Component } from 'svelte';

	export interface FilterMenuOption {
		value: string;
		label: string;
		icon?: Component;
		/** Muted text at the end of the row (a count, a unit). */
		hint?: string;
		/** Shorter form for the trigger button when selected (defaults to `label`). */
		short?: string;
	}
</script>

<script lang="ts">
	/**
	 * FilterMenu — one single-select filter as a toolbar button + popover list.
	 *
	 *   <FilterMenu label="Price" value={price} options={PRICE} onChange={(v) => (price = v)} />
	 *
	 * Idle the button reads "Price ▾"; with a value it reads "Price: Under $1" in the active style.
	 * The first row ("Any …") clears it. `searchable` adds a type-to-filter input for long lists
	 * (providers). Keyboard: arrows move, Enter picks, Esc closes (bits-ui Command + Popover).
	 *
	 * `multiple`: `value` is a comma list; rows toggle with checkboxes and the menu stays open,
	 * so several can be picked in one go (the API treats the list as "all must hold").
	 */
	import { Popover, Command } from 'bits-ui';
	import Check from '@lucide/svelte/icons/check';
	import Square from '@lucide/svelte/icons/square';
	import SquareCheck from '@lucide/svelte/icons/square-check';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import { cn } from '$lib/utils';

	interface Props {
		label: string;
		icon?: Component;
		value: string;
		options: FilterMenuOption[];
		onChange: (value: string) => void;
		searchable?: boolean;
		/** Label of the clearing row; defaults to "Any <label>". */
		anyLabel?: string;
		/** Small caption above the list (what the filter measures). */
		caption?: string;
		/** Pick several (comma-list `value`); the list stays open while toggling. */
		multiple?: boolean;
		class?: string;
	}

	let {
		label,
		icon: Icon,
		value,
		options,
		onChange,
		searchable = false,
		anyLabel,
		caption,
		multiple = false,
		class: className,
	}: Props = $props();

	let open = $state(false);
	const values = $derived(value ? value.split(',') : []);
	const picked = $derived(options.filter((o) => values.includes(o.value)));
	const summary = $derived(
		picked.length === 0 ? '' : picked.length <= 2 ? picked.map((o) => o.short ?? o.label).join(', ') : `${picked.length} selected`,
	);

	function pick(v: string) {
		if (!multiple || v === '') {
			open = multiple ? open : false;
			if (v !== value) onChange(v);
			return;
		}
		const next = values.includes(v) ? values.filter((x) => x !== v) : [...values, v];
		// Keep the options' order so the URL / request stays stable.
		onChange(options.filter((o) => next.includes(o.value)).map((o) => o.value).join(','));
	}
</script>

<Popover.Root bind:open>
	<Popover.Trigger
		class={cn('fm-trigger', summary && 'fm-trigger-on', className)}
		aria-label={summary ? `${label}: ${summary}` : `Filter by ${label.toLowerCase()}`}
	>
		{#if Icon}<Icon class="size-3.5 shrink-0" aria-hidden="true" />{/if}
		<span class="whitespace-nowrap">{label}</span>
		{#if summary}
			<span class="fm-sep" aria-hidden="true"></span>
			<span class="max-w-[12rem] truncate font-medium">{summary}</span>
		{/if}
		<ChevronDown class="size-3 shrink-0 opacity-60" aria-hidden="true" />
	</Popover.Trigger>
	<Popover.Portal>
		<Popover.Content
			align="start"
			sideOffset={6}
			collisionPadding={12}
			class="z-50 w-60 overflow-hidden rounded-md border border-border bg-popover text-popover-foreground shadow-md"
		>
			<Command.Root shouldFilter={searchable} loop>
				{#if searchable}
					<Command.Input
						class="fm-search h-9 w-full border-b border-border bg-transparent px-3 text-sm placeholder:text-muted-foreground/70"
						placeholder="Search {label.toLowerCase()}…"
					/>
				{/if}
				{#if caption}
					<p class="px-3 pt-2 text-[11px] text-muted-foreground">{caption}</p>
				{/if}
				<Command.List class="max-h-72 overflow-y-auto p-1">
					<Command.Viewport>
						<Command.Empty class="px-2 py-3 text-center text-xs text-muted-foreground">No match.</Command.Empty>
						<Command.Item value="__any__" keywords={['any', 'all']} onSelect={() => pick('')} class="fm-item">
							<span class="fm-check">{#if !value && !multiple}<Check class="size-3.5" />{/if}</span>
							<span class="text-muted-foreground">{anyLabel ?? (multiple ? 'Clear selection' : `Any ${label.toLowerCase()}`)}</span>
						</Command.Item>
						{#each options as opt (opt.value)}
							<Command.Item value={opt.value} keywords={[opt.label]} onSelect={() => pick(opt.value)} class="fm-item">
								<span class="fm-check">
									{#if multiple}
										{#if values.includes(opt.value)}<SquareCheck class="size-3.5" />{:else}<Square class="size-3.5 text-muted-foreground/60" />{/if}
									{:else if value === opt.value}<Check class="size-3.5" />{/if}
								</span>
								{#if opt.icon}<opt.icon class="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />{/if}
								<span class="min-w-0 flex-1 truncate">{opt.label}</span>
								{#if opt.hint}<span class="shrink-0 font-mono text-[11px] text-muted-foreground tabular-nums">{opt.hint}</span>{/if}
							</Command.Item>
						{/each}
					</Command.Viewport>
				</Command.List>
			</Command.Root>
		</Popover.Content>
	</Popover.Portal>
</Popover.Root>

<style>
	:global(.fm-trigger) {
		display: inline-flex;
		height: 2.25rem;
		flex-shrink: 0;
		cursor: pointer;
		align-items: center;
		gap: 0.375rem;
		border-radius: calc(var(--radius) + 0.125rem);
		border: 1px solid var(--border);
		background: var(--card);
		padding-inline: 0.625rem;
		font-size: 0.75rem;
		color: var(--muted-foreground);
		transition:
			color 150ms,
			background-color 150ms,
			border-color 150ms;
	}
	:global(.fm-trigger:hover) {
		color: var(--foreground);
		border-color: color-mix(in oklab, var(--primary) 40%, var(--border));
	}
	:global(.fm-trigger:focus-visible) {
		outline: 2px solid var(--ring);
		outline-offset: 1px;
	}
	:global(.fm-trigger[data-state='open']) {
		color: var(--foreground);
		background: var(--muted);
	}
	:global(.fm-trigger-on) {
		color: var(--accent-foreground);
		background: var(--accent);
		border-color: color-mix(in oklab, var(--primary) 45%, var(--border));
	}
	/* The popover already has focus context: a hairline is enough, not the global focus ring. */
	:global(.fm-search),
	:global(.fm-search:focus),
	:global(.fm-search:focus-visible) {
		outline: none;
		box-shadow: none;
		border-bottom: 1px solid var(--border);
	}
	.fm-sep {
		width: 1px;
		height: 0.875rem;
		background: currentColor;
		opacity: 0.25;
	}
	:global(.fm-item) {
		display: flex;
		cursor: pointer;
		user-select: none;
		align-items: center;
		gap: 0.5rem;
		border-radius: 0.25rem;
		padding: 0.375rem 0.5rem;
		font-size: 0.8125rem;
		outline: none;
	}
	:global(.fm-item[data-selected]) {
		background: var(--surface-2, var(--muted));
	}
	.fm-check {
		display: inline-flex;
		width: 0.875rem;
		flex-shrink: 0;
		justify-content: center;
		color: var(--primary);
	}
</style>
