<script lang="ts">
	/**
	 * ReportContents — the report's table of contents: numbered links to its tables that follow
	 * the scroll position (the current table is marked, `aria-current`). A sticky rail beside the
	 * tables from `xl`; below that a folded "Contents" disclosure above them.
	 *
	 * Props: `items` ({ id, number, title, rows }), `variant` ('rail' | 'inline').
	 */
	import { onMount } from 'svelte';
	import { cn } from '$lib/utils';

	interface Item {
		id: string;
		number: number;
		title: string;
		rows: number;
	}

	interface Props {
		items: Item[];
		variant?: 'rail' | 'inline';
	}

	let { items, variant = 'rail' }: Props = $props();
	let current = $state<string | null>(null);

	onMount(() => {
		if (typeof IntersectionObserver === 'undefined') return;
		const visible = new Map<string, number>();
		const io = new IntersectionObserver(
			(entries) => {
				for (const e of entries) {
					if (e.isIntersecting) visible.set(e.target.id, e.boundingClientRect.top);
					else visible.delete(e.target.id);
				}
				// The topmost table still on screen.
				const first = items.find((i) => visible.has(i.id));
				if (first) current = first.id;
			},
			{ rootMargin: '-64px 0px -45% 0px' }
		);
		for (const i of items) {
			const el = document.getElementById(i.id);
			if (el) io.observe(el);
		}
		return () => io.disconnect();
	});
</script>

{#snippet list()}
	<ol class="space-y-px">
		{#each items as item (item.id)}
			<li>
				<a
					href="#{item.id}"
					aria-current={current === item.id ? 'location' : undefined}
					class={cn(
						'rc-link group grid grid-cols-[1.75rem_minmax(0,1fr)_auto] items-baseline gap-1 rounded-sm py-1 pl-2 pr-1.5 text-[13px] leading-snug transition-colors',
						current === item.id ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground'
					)}
				>
					<span class="font-mono text-[11px] tabular-nums">{item.number}</span>
					<span class="line-clamp-2 min-w-0" title={item.title}>{item.title}</span>
					<span class="font-mono text-[11px] tabular-nums opacity-70" title="{item.rows} rows">{item.rows}</span>
				</a>
			</li>
		{/each}
	</ol>
{/snippet}

{#if variant === 'rail'}
	<nav aria-label="Report contents" class="rounded-md border border-border bg-card p-2 shadow-[var(--shadow-card)]">
		<p class="section-label flex items-center justify-between px-2 pb-1.5 pt-1">
			<span>Contents</span><span class="font-mono normal-case tracking-normal">{items.length} tables</span>
		</p>
		<div class="max-h-[calc(100dvh-10rem)] overflow-y-auto">{@render list()}</div>
	</nav>
{:else}
	<details class="group rounded-md border border-border bg-card shadow-[var(--shadow-card)]">
		<summary class="flex cursor-pointer list-none items-center justify-between px-4 py-2.5 text-sm font-medium">
			<span>Contents <span class="font-mono text-xs font-normal text-muted-foreground">· {items.length} tables</span></span>
			<span class="text-xs text-muted-foreground group-open:hidden">Show</span><span class="hidden text-xs text-muted-foreground group-open:inline">Hide</span>
		</summary>
		<div class="border-t border-border/60 p-2">{@render list()}</div>
	</details>
{/if}

<style>
	.rc-link[aria-current='location'] {
		box-shadow: inset 2px 0 0 var(--primary);
	}
</style>
