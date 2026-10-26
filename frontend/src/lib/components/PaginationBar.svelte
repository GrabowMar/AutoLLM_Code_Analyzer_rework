<script lang="ts">
	import ChevronLeft from '@lucide/svelte/icons/chevron-left';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import ChevronsLeft from '@lucide/svelte/icons/chevrons-left';
	import ChevronsRight from '@lucide/svelte/icons/chevrons-right';

	interface Props {
		resultsText?: string;
		page?: number;
		pages?: number;
		onGoToPage?: (p: number) => void;
		/** Extra classes on the wrapper — e.g. "border-t border-border" as a table card's footer */
		class?: string;
		/** Below a list: after a page change, bring the top of the enclosing card back into view. */
		scrollToCard?: boolean;
	}

	let { resultsText = '', page, pages, onGoToPage, class: cls = '', scrollToCard = false }: Props = $props();

	let bar = $state<HTMLElement>();

	function go(p: number) {
		onGoToPage?.(p);
		if (!scrollToCard || !bar) return;
		const card = bar.closest('[data-slot="card"]') ?? bar;
		const top = card.getBoundingClientRect().top + window.scrollY - 64; // clear the 3rem app bar
		if (top < window.scrollY) window.scrollTo({ top });
	}

	const hasPagination = $derived(!!page && !!pages && !!onGoToPage && pages > 1);

	const visiblePages = $derived.by(() => {
		if (!page || !pages) return [];
		const w = 2;
		const start = Math.max(1, Math.min(page - w, pages - w * 2));
		const end = Math.min(pages, start + w * 2);
		return Array.from({ length: end - start + 1 }, (_, i) => start + i);
	});
</script>

{#if resultsText || hasPagination}
	<div bind:this={bar} class="flex items-center justify-between gap-3 bg-muted/20 px-3 py-1.5 {cls}">
		<span class="text-[11px] text-muted-foreground tabular-nums shrink-0">
			{resultsText}
		</span>

		{#if hasPagination && page && pages && onGoToPage}
			<nav class="flex items-center gap-0.5" aria-label="Pagination">
				<button
					class="inline-flex size-8 items-center sm:size-7 justify-center rounded border border-transparent text-muted-foreground transition-colors hover:border-border hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-30"
					disabled={page <= 1}
					onclick={() => go(1)}
					aria-label="First page"
				><ChevronsLeft class="size-3.5" /></button>
				<button
					class="inline-flex size-8 items-center sm:size-7 justify-center rounded border border-transparent text-muted-foreground transition-colors hover:border-border hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-30"
					disabled={page <= 1}
					onclick={() => go(page - 1)}
					aria-label="Previous page"
				><ChevronLeft class="size-3.5" /></button>

				{#if visiblePages[0] > 1}
					<span class="px-0.5 text-[11px] text-muted-foreground">…</span>
				{/if}

				{#each visiblePages as p}
					<button
						class="inline-flex h-8 min-w-8 items-center sm:h-7 sm:min-w-7 justify-center rounded border px-1 text-[11px] font-medium tabular-nums transition-colors
							{p === page
								? 'border-primary/50 bg-primary/10 text-primary'
								: 'border-transparent text-muted-foreground hover:border-border hover:bg-muted hover:text-foreground'}"
						onclick={() => go(p)}
						aria-current={p === page ? 'page' : undefined}
					>{p}</button>
				{/each}

				{#if visiblePages[visiblePages.length - 1] < pages}
					<span class="px-0.5 text-[11px] text-muted-foreground">…</span>
				{/if}

				<button
					class="inline-flex size-8 items-center sm:size-7 justify-center rounded border border-transparent text-muted-foreground transition-colors hover:border-border hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-30"
					disabled={page >= pages}
					onclick={() => go(page + 1)}
					aria-label="Next page"
				><ChevronRight class="size-3.5" /></button>
				<button
					class="inline-flex size-8 items-center sm:size-7 justify-center rounded border border-transparent text-muted-foreground transition-colors hover:border-border hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-30"
					disabled={page >= pages}
					onclick={() => go(pages)}
					aria-label="Last page"
				><ChevronsRight class="size-3.5" /></button>
			</nav>
		{/if}
	</div>
{/if}
