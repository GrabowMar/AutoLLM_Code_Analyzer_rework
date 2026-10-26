<script lang="ts" module>
	import type { Component } from 'svelte';
	import type { PresetKind } from '$lib/api/generation';

	export type LibraryTab = 'specs' | 'stacks' | 'presets' | 'profiles' | 'blocks';

	export interface LibraryNavItem {
		tab: LibraryTab;
		kind?: PresetKind;
		label: string;
		icon: Component;
		count: number | null;
	}

	export interface LibraryNavGroup {
		/** The question this group answers about a run. */
		title: string;
		/** Groups only one generation mode uses are marked, not hidden. */
		note?: string;
		items: LibraryNavItem[];
	}

	export function libraryHref(tab: LibraryTab, kind?: PresetKind): string {
		return tab === 'presets' ? `/templates?tab=presets&kind=${kind ?? 'harness'}` : `/templates?tab=${tab}`;
	}
</script>

<script lang="ts">
	/**
	 * LibraryNav — the Library's sections grouped by what they answer about a
	 * run (what to build, where it runs, how agents run, template prompts).
	 * A vertical rail from `lg`, a horizontally scrolling chip row below it.
	 * Items are links (`?tab=…&kind=…`), so sections are bookmarkable.
	 */
	interface Props {
		groups: LibraryNavGroup[];
		activeTab: LibraryTab;
		activeKind: PresetKind;
		onNavigate?: () => void;
	}
	let { groups, activeTab, activeKind, onNavigate }: Props = $props();

	const isActive = (item: LibraryNavItem) => item.tab === activeTab && (item.tab !== 'presets' || item.kind === activeKind);
</script>

<nav aria-label="Library sections">
	<!-- Rail (lg and up) -->
	<div class="hidden space-y-5 lg:block">
		{#each groups as g (g.title)}
			<div class="space-y-1">
				<p class="section-label px-2">{g.title}</p>
				{#if g.note}<p class="px-2 text-[11px] text-muted-foreground">{g.note}</p>{/if}
				<ul class="space-y-0.5">
					{#each g.items as item (item.label)}
						{@const active = isActive(item)}
						<li>
							<a
								href={libraryHref(item.tab, item.kind)}
								data-sveltekit-replacestate
								data-sveltekit-noscroll
								aria-current={active ? 'page' : undefined}
								onclick={onNavigate}
								class="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors duration-150 {active
									? 'bg-primary/10 font-medium text-primary'
									: 'text-muted-foreground hover:bg-muted hover:text-foreground'}"
							>
								<item.icon class="size-4 shrink-0" aria-hidden="true" />
								<span class="flex-1 truncate">{item.label}</span>
								{#if item.count !== null}
									<span class="font-mono text-xs tabular-nums {active ? 'text-primary' : 'text-muted-foreground'}">{item.count}</span>
								{/if}
							</a>
						</li>
					{/each}
				</ul>
			</div>
		{/each}
	</div>

	<!-- Chip row (below lg) -->
	<ul class="-mx-4 flex gap-1.5 overflow-x-auto px-4 pb-1 lg:hidden">
		{#each groups as g (g.title)}
			{#each g.items as item (item.label)}
				{@const active = isActive(item)}
				<li class="shrink-0">
					<a
						href={libraryHref(item.tab, item.kind)}
						data-sveltekit-replacestate
						data-sveltekit-noscroll
						aria-current={active ? 'page' : undefined}
						title={g.title}
						onclick={onNavigate}
						class="inline-flex min-h-9 cursor-pointer items-center gap-1.5 rounded-md border px-2.5 text-sm transition-colors duration-150 {active
							? 'border-primary/40 bg-primary/10 font-medium text-primary'
							: 'border-border text-muted-foreground hover:text-foreground'}"
					>
						<item.icon class="size-4" aria-hidden="true" />
						{item.label}
						{#if item.count !== null}<span class="font-mono text-xs tabular-nums">{item.count}</span>{/if}
					</a>
				</li>
			{/each}
		{/each}
	</ul>
</nav>
