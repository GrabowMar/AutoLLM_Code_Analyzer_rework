<script lang="ts">
	/**
	 * VersionHistory — every visible version of a slug, newest first: version, name, content hash, author,
	 * date, `derived_from`. Each row opens the version read-only (`?v=`), and two versions can be picked
	 * for a diff (`ondiff(a, b)`, a < b).
	 *
	 * Props: `kind`, `slug`, `versions`, `current` (the version shown), `latest`, `ondiff`.
	 */
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import GitCompare from '@lucide/svelte/icons/git-compare';
	import type { LibraryKind, VersionSummary } from '$lib/api/v3/types';
	import { shortRef } from '$lib/api/v3/types';
	import { cn } from '$lib/utils';
	import { libraryHref } from './kinds';

	interface Props {
		kind: LibraryKind;
		slug: string;
		versions: VersionSummary[];
		current: number;
		latest: number;
		ondiff: (a: number, b: number) => void;
	}

	let { kind, slug, versions, current, latest, ondiff }: Props = $props();

	const sorted = $derived([...versions].sort((x, y) => y.version - x.version));
	let picked = $state<number[]>([]);

	function pick(v: number) {
		picked = picked.includes(v) ? picked.filter((p) => p !== v) : [...picked, v].slice(-2);
	}

	function compare() {
		if (picked.length !== 2) return;
		const [a, b] = [...picked].sort((x, y) => x - y);
		ondiff(a, b);
	}

	const date = (iso: string) => new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
</script>

<div class="space-y-2">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<p class="text-xs text-muted-foreground">
			Versions are immutable: saving creates the next version. Pick two to compare.
		</p>
		<Button variant="outline" size="sm" disabled={picked.length !== 2} onclick={compare}>
			<GitCompare />Compare {picked.length === 2 ? `v${Math.min(...picked)} → v${Math.max(...picked)}` : ''}
		</Button>
	</div>
	<ul class="data-list" aria-label="Versions">
		{#each sorted as v (v.version)}
			{@const isPicked = picked.includes(v.version)}
			<li class={cn('data-list-row flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2')} data-active={v.version === current || undefined}>
				<label class="flex cursor-pointer items-center gap-2">
					<Checkbox checked={isPicked} onCheckedChange={() => pick(v.version)} aria-label="Pick v{v.version} for the diff" />
					<span class="font-mono text-sm font-medium tabular-nums">v{v.version}</span>
				</label>
				<a href={libraryHref(kind, slug, v.version === latest ? null : v.version)} class="min-w-0 flex-1 truncate text-sm hover:text-primary hover:underline">
					{v.name}
				</a>
				{#if v.version === latest}<span class="rounded-sm bg-primary/10 px-1 text-[11px] text-primary">latest</span>{/if}
				{#if v.version === current}<span class="text-[11px] text-muted-foreground">shown</span>{/if}
				{#if v.is_archived}<span class="text-[11px] text-muted-foreground">archived</span>{/if}
				<span class="font-mono text-[11px] text-muted-foreground" title={v.content_hash}>#{v.content_hash.slice(0, 12)}</span>
				<span class="w-full text-[11px] text-muted-foreground sm:w-auto">
					{v.created_by?.username ?? 'system'} · {date(v.created_at)}
					{#if v.derived_from}· from <span class="font-mono">{shortRef(v.derived_from)}</span>{/if}
				</span>
			</li>
		{/each}
	</ul>
</div>
