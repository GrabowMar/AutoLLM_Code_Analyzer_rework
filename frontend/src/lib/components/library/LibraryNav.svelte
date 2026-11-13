<script lang="ts">
	/**
	 * LibraryNav — the Library's kinds grouped by the question they answer about a run (what to build,
	 * where it runs, what else it is told, prompts, how agents run, how it is measured), plus the prompt
	 * playground and the generation dry run. A rail from `lg`, a horizontally scrolling chip row below it. Items are links
	 * (`/library/{kind}`), so every list is bookmarkable.
	 *
	 * Props: `active` (the kind's API name, `'playground'`, `'dry-run'`, or null on the home page), `counts` (per kind,
	 * null while loading).
	 */
	import type { LibraryApiKind, LibraryKind } from '$lib/api/v3/types';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import { KIND_GROUPS, KIND_INFO, PlaygroundIcon } from './kinds';

	interface Props {
		active: LibraryApiKind | 'playground' | 'dry-run' | null;
		counts?: Partial<Record<LibraryKind, number | null>>;
	}

	let { active, counts = {} }: Props = $props();

	interface Item {
		id: string;
		href: string;
		label: string;
		icon: (typeof KIND_INFO)[LibraryKind]['icon'];
		count: number | null;
	}

	const groups = $derived(
		KIND_GROUPS.map((g) => ({
			title: g.title,
			items: [
				...g.kinds.map(
					(k): Item => ({
						id: KIND_INFO[k].api,
						href: `/library/${KIND_INFO[k].api}`,
						label: KIND_INFO[k].plural,
						icon: KIND_INFO[k].icon,
						count: counts[k] ?? null,
					}),
				),
				...(g.id === 'prompts'
					? [
							{ id: 'playground', href: '/library/prompts/playground', label: 'Playground', icon: PlaygroundIcon, count: null } as Item,
							{ id: 'dry-run', href: '/library/dry-run', label: 'Generation dry run', icon: FlaskConical as Item['icon'], count: null } as Item,
						]
					: []),
			],
		})),
	);
</script>

<nav aria-label="Library sections">
	<div class="hidden space-y-4 rounded-md border border-border bg-card p-2 pt-3 shadow-[var(--shadow-card)] lg:block">
		<a
			href="/library"
			aria-current={active === null ? 'page' : undefined}
			class="block rounded-md px-2 py-1 text-sm transition-colors duration-150 {active === null ? 'font-medium text-primary' : 'text-muted-foreground hover:text-foreground'}"
		>
			Overview
		</a>
		{#each groups as g (g.title)}
			<div class="space-y-1 border-t border-border/60 pt-3">
				<p class="section-label px-2">{g.title}</p>
				<ul class="space-y-0.5">
					{#each g.items as item (item.id)}
						{@const on = item.id === active}
						<li class="relative">
							{#if on}<span class="absolute inset-y-1 left-0 w-0.5 rounded-full bg-primary" aria-hidden="true"></span>{/if}
							<a
								href={item.href}
								aria-current={on ? 'page' : undefined}
								class="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors duration-150 {on
									? 'bg-primary/10 font-medium text-primary'
									: 'text-muted-foreground hover:bg-muted hover:text-foreground'}"
							>
								<item.icon class="size-4 shrink-0" aria-hidden="true" />
								<span class="flex-1 truncate">{item.label}</span>
								{#if item.count !== null}
									<span class="font-mono text-xs tabular-nums {on ? 'text-primary' : 'text-muted-foreground'}">{item.count}</span>
								{/if}
							</a>
						</li>
					{/each}
				</ul>
			</div>
		{/each}
	</div>

	<ul class="-mx-4 flex gap-1.5 overflow-x-auto px-4 pb-1 lg:hidden">
		{#each groups as g (g.title)}
			{#each g.items as item (item.id)}
				{@const on = item.id === active}
				<li class="shrink-0">
					<a
						href={item.href}
						aria-current={on ? 'page' : undefined}
						title={g.title}
						class="inline-flex min-h-9 cursor-pointer items-center gap-1.5 rounded-md border px-2.5 text-sm transition-colors duration-150 {on
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
