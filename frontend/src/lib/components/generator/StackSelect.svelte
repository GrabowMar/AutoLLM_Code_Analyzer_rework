<script lang="ts">
	/**
	 * StackSelect — pick the stack skeleton for a run.
	 *
	 * Lists everything `GET /generation/stacks/` returns (builtin, approved, and
	 * the user's own stacks). The user's own unapproved stacks are marked
	 * "Pending approval — only you can run it".
	 *
	 * Props:
	 * - `value` (bindable string): stack slug; '' = none chosen yet.
	 * - `stacks`: preloaded list (otherwise fetched on mount).
	 * - `defaultSlug`: selected while `value` is '' (e.g. `AgentConfigInfo.default_stack_slug`).
	 * - `label` (default 'Stack'; '' hides it), `id`, `disabled`.
	 * - `onchange(stack | null)` after the user picks a stack.
	 */
	import { onMount } from 'svelte';
	import { getStacks, type Stack } from '$lib/api/generation';
	import { formatApiError } from '$lib/api/core';
	import { cn } from '$lib/utils';
	import { Label } from '$lib/components/ui/label';
	import * as Select from '$lib/components/ui/select';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	interface Props {
		value?: string;
		stacks?: Stack[];
		defaultSlug?: string;
		label?: string;
		id?: string;
		disabled?: boolean;
		onchange?: (stack: Stack | null) => void;
	}

	let {
		value = $bindable(''),
		stacks: providedStacks,
		defaultSlug,
		label = 'Stack',
		id = 'stack-select',
		disabled = false,
		onchange,
	}: Props = $props();

	let fetched = $state<Stack[]>([]);
	let loading = $state(false);
	let error = $state('');

	const stacks = $derived(providedStacks ?? fetched);
	const selected = $derived(
		value ? stacks.find((s) => s.slug === value || (s.aliases ?? []).includes(value)) : undefined
	);
	const sorted = $derived(
		[...stacks].sort(
			(a, b) => Number(b.is_builtin) - Number(a.is_builtin) || (a.name || a.slug).localeCompare(b.name || b.slug)
		)
	);

	function isPending(stack: Stack): boolean {
		return !stack.is_builtin && !stack.is_approved;
	}

	async function load() {
		loading = true;
		error = '';
		try {
			fetched = await getStacks();
		} catch (e) {
			error = formatApiError(e, 'Could not load stacks.');
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		if (!providedStacks) void load();
	});

	// Fill in the platform default once both the list and the default are known.
	$effect(() => {
		if (value || !defaultSlug || stacks.length === 0) return;
		if (stacks.some((s) => s.slug === defaultSlug)) value = defaultSlug;
	});

	function pick(slug: string) {
		value = slug;
		onchange?.(stacks.find((s) => s.slug === slug) ?? null);
	}

	const chip = 'rounded-full border px-1.5 text-[11px] font-medium leading-4';
</script>

{#snippet stackLine(stack: Stack)}
	<span class="flex min-w-0 flex-wrap items-center gap-1.5">
		<span class="truncate font-medium">{stack.name || stack.slug}</span>
		{#if stack.name && stack.name !== stack.slug}<span class="font-mono text-[11px] text-muted-foreground">{stack.slug}</span>{/if}
		{#if !stack.has_frontend}
			<span class={cn(chip, 'border-border bg-muted text-muted-foreground')}>backend only</span>
		{/if}
		{#if !stack.is_builtin}
			<span class={cn(chip, 'border-border text-muted-foreground')}>custom</span>
		{/if}
		{#if isPending(stack)}
			<span class={cn(chip, 'border-warning/30 bg-warning/10 text-warning-fg')}>pending approval</span>
		{/if}
	</span>
{/snippet}

<div class="min-w-0 space-y-1.5" data-slot="stack-select">
	{#if label}<Label for={id}>{label}</Label>{/if}
	{#if error}
		<ErrorAlert message={error} onretry={load} />
	{:else if !loading && stacks.length === 0}
		<p class="rounded-md border border-border px-3 py-2 text-sm text-muted-foreground">
			No stacks available. Builtin stacks are seeded on startup; ask an admin to check the deployment.
		</p>
	{:else}
		<Select.Root
			type="single"
			value={selected?.slug ?? value}
			onValueChange={pick}
			disabled={disabled || loading}
			items={sorted.map((s) => ({ value: s.slug, label: s.name || s.slug }))}
		>
			<Select.Trigger {id} aria-busy={loading}>
				{#if loading}
					<span class="flex items-center gap-2 text-muted-foreground">
						<LoaderCircle class="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />Loading stacks…
					</span>
				{:else if selected}
					{@render stackLine(selected)}
				{:else if value}
					<span class="text-warning-fg">{value} (unavailable)</span>
				{:else}
					<span class="text-muted-foreground">Choose a stack…</span>
				{/if}
			</Select.Trigger>
			<Select.Content class="max-w-[min(32rem,calc(100vw-2rem))]">
				{#each sorted as stack (stack.slug)}
					<Select.Item value={stack.slug} label={stack.name || stack.slug}>
						<span class="flex min-w-0 flex-col">
							{@render stackLine(stack)}
							{#if stack.description}
								<span class="truncate text-[11px] text-muted-foreground">{stack.description}</span>
							{/if}
						</span>
					</Select.Item>
				{/each}
			</Select.Content>
		</Select.Root>

		{#if selected && isPending(selected)}
			<p class="flex items-start gap-1.5 text-[11px] text-warning-fg" role="status">
				<TriangleAlert class="mt-px size-3.5 shrink-0" aria-hidden="true" />
				Pending approval — only you can run it until an admin approves the stack.
			</p>
		{:else if selected?.description}
			<p class="text-[11px] text-muted-foreground">{selected.description}</p>
		{:else if value && !selected && !loading}
			<p class="text-[11px] text-warning-fg" role="status">
				This stack is not in your list (archived or not shared with you). Choose another one.
			</p>
		{/if}
	{/if}
</div>
