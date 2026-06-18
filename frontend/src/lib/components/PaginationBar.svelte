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
		/** Extra classes on the wrapper — e.g. "rounded-md border border-border" for standalone use */
		class?: string;
	}

	let { resultsText = '', page, pages, onGoToPage, class: cls = '' }: Props = $props();

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
	<div class="flex items-center justify-between gap-3 bg-muted/20 px-3 py-1.5 {cls}">
		<span class="text-[11px] text-muted-foreground/80 tabular-nums shrink-0">
			{resultsText}
		</span>

		{#if hasPagination && page && pages && onGoToPage}
			<div class="flex items-center gap-0.5">
				<button
					class="inline-flex h-6 w-6 items-center justify-center rounded border border-transparent text-muted-foreground/60 transition-colors hover:border-border hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-30"
					disabled={page <= 1}
					onclick={() => onGoToPage!(1)}
					aria-label="First page"
				><ChevronsLeft class="h-3 w-3" /></button>
				<button
					class="inline-flex h-6 w-6 items-center justify-center rounded border border-transparent text-muted-foreground/60 transition-colors hover:border-border hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-30"
					disabled={page <= 1}
					onclick={() => onGoToPage!(page - 1)}
					aria-label="Previous page"
				><ChevronLeft class="h-3 w-3" /></button>

				{#if visiblePages[0] > 1}
					<span class="px-0.5 text-[10px] text-muted-foreground/40">…</span>
				{/if}

				{#each visiblePages as p}
					<button
						class="inline-flex h-6 min-w-6 items-center justify-center rounded border px-1 text-[11px] font-medium tabular-nums transition-colors
							{p === page
								? 'border-primary/50 bg-primary/10 text-primary'
								: 'border-transparent text-muted-foreground/70 hover:border-border hover:bg-muted hover:text-foreground'}"
						onclick={() => onGoToPage!(p)}
						aria-current={p === page ? 'page' : undefined}
					>{p}</button>
				{/each}

				{#if visiblePages[visiblePages.length - 1] < pages}
					<span class="px-0.5 text-[10px] text-muted-foreground/40">…</span>
				{/if}

				<button
					class="inline-flex h-6 w-6 items-center justify-center rounded border border-transparent text-muted-foreground/60 transition-colors hover:border-border hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-30"
					disabled={page >= pages}
					onclick={() => onGoToPage!(page + 1)}
					aria-label="Next page"
				><ChevronRight class="h-3 w-3" /></button>
				<button
					class="inline-flex h-6 w-6 items-center justify-center rounded border border-transparent text-muted-foreground/60 transition-colors hover:border-border hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-30"
					disabled={page >= pages}
					onclick={() => onGoToPage!(pages)}
					aria-label="Last page"
				><ChevronsRight class="h-3 w-3" /></button>
			</div>
		{/if}
	</div>
{/if}
