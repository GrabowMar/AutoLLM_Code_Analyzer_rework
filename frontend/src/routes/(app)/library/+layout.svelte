<script lang="ts">
	/**
	 * Library shell: the kind rail (LibraryNav) beside every Library page, with live counts from the
	 * shared library store.
	 */
	import type { Snippet } from 'svelte';
	import { page } from '$app/state';
	import { LibraryNav, libraryStore } from '$lib/components/library';
	import { isLibraryApiKind, type LibraryApiKind } from '$lib/api/v3/types';

	let { children }: { children: Snippet } = $props();

	const active = $derived.by((): LibraryApiKind | 'playground' | null => {
		if (page.url.pathname.startsWith('/library/prompts/playground')) return 'playground';
		const kind = page.params.kind;
		return isLibraryApiKind(kind) ? kind : null;
	});
	const counts = $derived(libraryStore.counts());
</script>

<div class="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[13rem_minmax(0,1fr)]">
	<div class="min-w-0 lg:sticky lg:top-20 lg:self-start">
		<LibraryNav {active} {counts} />
	</div>
	<div class="min-w-0">
		{@render children()}
	</div>
</div>
