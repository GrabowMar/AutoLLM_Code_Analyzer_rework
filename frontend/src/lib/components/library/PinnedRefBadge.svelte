<script lang="ts">
	/**
	 * PinnedRefBadge — a library reference as studies pin it: mono `slug@v`, the content hash in the
	 * tooltip, and "latest is vN" when a newer version exists. Links to the version's Library page.
	 *
	 *   <PinnedRefBadge ref="crud_todo_list@2#a1b2c3d4e5f6" kind="task" />
	 *
	 * Props: `ref` (pinned `slug@v#hash12` or `slug@v`), `kind`, `latestVersion` (else looked up in
	 * `libraryStore`), `href` (overrides the Library link; `''` renders no link), `class`.
	 */
	import type { LibraryKind } from '$lib/api/v3/types';
	import { parseRef } from '$lib/api/v3/types';
	import { cn } from '$lib/utils';
	import { kindInfo, libraryHref } from './kinds';
	import { libraryStore } from './library.svelte';

	interface Props {
		ref: string;
		kind: LibraryKind;
		latestVersion?: number;
		href?: string;
		class?: string;
	}

	let { ref, kind, latestVersion, href, class: className }: Props = $props();

	const parsed = $derived(parseRef(ref));
	const latest = $derived(latestVersion ?? (parsed ? libraryStore.latestOf(kind, parsed.slug)?.version : undefined));
	const stale = $derived(!!parsed && latest !== undefined && latest > parsed.version);
	const link = $derived(href ?? (parsed ? libraryHref(kind, parsed.slug, parsed.version) : ''));
	const title = $derived(
		parsed
			? `${kindInfo(kind).label} ${parsed.slug}, version ${parsed.version}${parsed.hash12 ? ` · content ${parsed.hash12}` : ''}${stale ? ` · latest is v${latest}` : ''}`
			: ref,
	);
</script>

{#snippet body()}
	<span class="font-mono">{parsed ? `${parsed.slug}@${parsed.version}` : ref}</span>
	{#if parsed?.hash12}<span class="font-mono text-muted-foreground">#{parsed.hash12.slice(0, 6)}</span>{/if}
	{#if stale}<span class="rounded-sm bg-warning/15 px-1 text-warning-fg">latest v{latest}</span>{/if}
{/snippet}

{#if link}
	<a
		href={link}
		{title}
		class={cn(
			'inline-flex max-w-full items-center gap-1 truncate rounded-md border border-border px-1.5 py-0.5 text-[11px] leading-4 transition-colors duration-150 hover:border-primary/50 hover:text-primary',
			className,
		)}
	>
		{@render body()}
	</a>
{:else}
	<span {title} class={cn('inline-flex max-w-full items-center gap-1 truncate rounded-md border border-border px-1.5 py-0.5 text-[11px] leading-4', className)}>
		{@render body()}
	</span>
{/if}
