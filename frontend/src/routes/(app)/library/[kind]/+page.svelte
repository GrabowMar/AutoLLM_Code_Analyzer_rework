<script lang="ts">
	/**
	 * /library/[kind] — one kind's entities (latest version per slug) with search, tag, "mine" and
	 * archived filters; "New" for editable kinds.
	 */
	import { Button } from '$lib/components/ui/button';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import Package from '@lucide/svelte/icons/package';
	import Plus from '@lucide/svelte/icons/plus';
	import Play from '@lucide/svelte/icons/play';
	import { LibraryList, PackagesDialog, kindInfo } from '$lib/components/library';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const info = $derived(kindInfo(data.kind));
	let packagesOpen = $state(false);
</script>

<div class="space-y-4">
	<PageHeader title={info.plural} description={info.description} back={{ href: '/library', label: 'Library' }}>
		{#snippet leading()}<info.icon class="size-5 text-primary" aria-hidden="true" />{/snippet}
		{#snippet actions()}
			{#if info.kind === 'prompt' || info.kind === 'prompt_set'}
				<Button variant="outline" size="sm" href="/library/prompts/playground"><Play />Playground</Button>
			{/if}
			<Button variant="outline" size="sm" onclick={() => (packagesOpen = true)}><Package />Import</Button>
			{#if info.editing === 'editable'}
				<Button size="sm" href="/library/{info.api}/new"><Plus />New {info.label.toLowerCase()}</Button>
			{/if}
		{/snippet}
	</PageHeader>

	{#if info.editing === 'readonly'}
		<p class="text-xs text-muted-foreground">
			{info.plural} are seeded by the platform and shown read-only. To use a variant, import it as a YAML package (it becomes your own slug).
		</p>
	{/if}

	<LibraryList kind={data.kind} />
</div>

<PackagesDialog bind:open={packagesOpen} />
