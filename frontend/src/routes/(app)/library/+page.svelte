<script lang="ts">
	/**
	 * /library — the Library home: every kind as a card (what it holds, how many there are), grouped by
	 * the question it answers about a run, plus packages (import/export) and the prompt playground.
	 */
	import { Button } from '$lib/components/ui/button';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import Package from '@lucide/svelte/icons/package';
	import Play from '@lucide/svelte/icons/play';
	import { KIND_GROUPS, KIND_INFO, PackagesDialog, libraryStore } from '$lib/components/library';

	const counts = $derived(libraryStore.counts());
	let packagesOpen = $state(false);
</script>

<div class="space-y-6">
	<PageHeader
		title="Library"
		description="Everything a sample is generated from — tasks, stacks, starters, styles, guidance, prompts and presets — versioned and immutable: experiments pin exact versions."
	>
		{#snippet help()}
			<p>
				Every entity is immutable per <span class="font-mono">slug@version</span> and carries a content hash. Editing saves the next version; archiving
				hides a slug from pickers but keeps every version for the experiments that pin it. System entities are seeded; fork one to make your own.
			</p>
		{/snippet}
		{#snippet actions()}
			<Button variant="outline" size="sm" onclick={() => (packagesOpen = true)}><Package />Packages</Button>
			<Button size="sm" href="/library/prompts/playground"><Play />Prompt playground</Button>
		{/snippet}
	</PageHeader>

	{#each KIND_GROUPS as group, gi (group.id)}
		<section class="space-y-3" aria-labelledby="lib-group-{group.id}">
			<SectionHeading id="lib-group-{group.id}" title={group.title} number={gi + 1} size="sm" />
			<div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
				{#each group.kinds as kind (kind)}
					{@const info = KIND_INFO[kind]}
					<a href="/library/{info.api}" class="card-lift block rounded-md border border-border bg-card p-4 shadow-[var(--shadow-card)] transition-colors duration-150 hover:border-primary/40">
						<div class="flex items-start gap-3">
							<info.icon class="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" />
							<div class="min-w-0 flex-1">
								<div class="flex items-baseline justify-between gap-2">
									<h3 class="font-display text-base font-medium">{info.plural}</h3>
									<span class="font-mono text-lg font-medium tabular-nums">{counts[kind] ?? '—'}</span>
								</div>
								<p class="mt-1 text-xs text-muted-foreground">{info.description}</p>
								{#if info.editing === 'readonly'}<p class="mt-1 text-[11px] text-muted-foreground">Seeded · read-only here</p>{/if}
							</div>
						</div>
					</a>
				{/each}
			</div>
		</section>
	{/each}
</div>

<PackagesDialog bind:open={packagesOpen} />
