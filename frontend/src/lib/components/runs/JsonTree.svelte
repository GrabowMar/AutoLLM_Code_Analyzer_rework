<script lang="ts">
	/**
	 * JsonTree — a collapsible tree view of a JSON value (the manifest). Pinned refs under a known key link to
	 * their exact Library version; hashes are mono and shortened with the full value as a tooltip.
	 *
	 * Props: `value`, `name` (the key shown for this node), `path` (keys from the root, for ref detection),
	 * `depth` (nodes deeper than `openDepth` start folded).
	 */
	import { untrack } from 'svelte';
	import JsonTree from './JsonTree.svelte';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import { kindForPath, libraryHref } from './refs';

	interface Props {
		value: unknown;
		name?: string | number | null;
		path?: (string | number)[];
		depth?: number;
		openDepth?: number;
	}

	let { value, name = null, path = [], depth = 0, openDepth = 1 }: Props = $props();

	// folded by depth on first render only; the user's toggles win afterwards
	let open = $state(untrack(() => depth < openDepth));

	const isObject = $derived(value !== null && typeof value === 'object');
	const entries = $derived(
		isObject
			? Array.isArray(value)
				? value.map((v, i) => [i, v] as [number, unknown])
				: Object.entries(value as Record<string, unknown>)
			: [],
	);
	const summary = $derived(
		Array.isArray(value) ? `[${(value as unknown[]).length}]` : isObject ? `{${entries.length}}` : '',
	);
	const href = $derived(typeof value === 'string' ? libraryHref(kindForPath(path) ?? '', value) : null);
	const isHash = $derived(typeof value === 'string' && /^[0-9a-f]{40,64}$/.test(value));
</script>

<div class="json-node text-xs leading-5">
	{#if isObject}
		<button
			type="button"
			class="inline-flex cursor-pointer items-center gap-0.5 rounded text-left hover:text-primary"
			aria-expanded={open}
			onclick={() => (open = !open)}
		>
			{#if open}<ChevronDown class="h-3 w-3" />{:else}<ChevronRight class="h-3 w-3" />{/if}
			{#if name !== null}<span class="font-mono text-foreground">{name}</span>{/if}
			<span class="font-mono text-muted-foreground">{summary}</span>
		</button>
		{#if open}
			<div class="ml-3 border-l border-border pl-2">
				{#if entries.length === 0}
					<span class="text-muted-foreground">empty</span>
				{/if}
				{#each entries as [key, child] (key)}
					<JsonTree value={child} name={key} path={[...path, key]} depth={depth + 1} {openDepth} />
				{/each}
			</div>
		{/if}
	{:else}
		<div class="flex min-w-0 gap-1.5 pl-3.5">
			{#if name !== null}<span class="shrink-0 font-mono text-muted-foreground">{name}:</span>{/if}
			{#if href}
				<a class="truncate font-mono text-primary hover:underline" {href} title="Open this exact version in the Library">{value}</a>
			{:else if isHash}
				<span class="truncate font-mono" title={String(value)}>{String(value).slice(0, 16)}…</span>
			{:else if value === null}
				<span class="font-mono text-muted-foreground">null</span>
			{:else if typeof value === 'string'}
				<span class="min-w-0 break-all font-mono">{value}</span>
			{:else}
				<span class="font-mono tabular-nums">{String(value)}</span>
			{/if}
		</div>
	{/if}
</div>
