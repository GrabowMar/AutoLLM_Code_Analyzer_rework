<script lang="ts">
import { Button } from '$lib/components/ui/button';
import ChevronLeft from '@lucide/svelte/icons/chevron-left';
import ChevronRight from '@lucide/svelte/icons/chevron-right';
import ChevronsLeft from '@lucide/svelte/icons/chevrons-left';
import ChevronsRight from '@lucide/svelte/icons/chevrons-right';

interface Props {
	page: number;
	pages: number;
	onGoToPage: (p: number) => void;
}

let { page, pages, onGoToPage }: Props = $props();

const pageNumbers = $derived(
	Array.from({ length: Math.min(pages, 7) }, (_, i) => {
		if (pages <= 7) return i + 1;
		if (page <= 4) return i + 1;
		if (page >= pages - 3) return pages - 6 + i;
		return page - 3 + i;
	}),
);
</script>

<div class="flex flex-wrap items-center justify-center gap-1.5 sm:gap-1">
	<Button variant="outline" size="icon" class="h-11 w-11 sm:h-8 sm:w-8" disabled={page <= 1} onclick={() => onGoToPage(1)}>
		<ChevronsLeft class="h-4 w-4" />
	</Button>
	<Button variant="outline" size="icon" class="h-11 w-11 sm:h-8 sm:w-8" disabled={page <= 1} onclick={() => onGoToPage(page - 1)}>
		<ChevronLeft class="h-4 w-4" />
	</Button>
	{#each pageNumbers as p}
		<Button
			variant={p === page ? 'default' : 'outline'}
			size="sm"
			class="h-11 min-w-11 sm:h-8 sm:min-w-8"
			onclick={() => onGoToPage(p)}
		>
			{p}
		</Button>
	{/each}
	<Button variant="outline" size="icon" class="h-11 w-11 sm:h-8 sm:w-8" disabled={page >= pages} onclick={() => onGoToPage(page + 1)}>
		<ChevronRight class="h-4 w-4" />
	</Button>
	<Button variant="outline" size="icon" class="h-11 w-11 sm:h-8 sm:w-8" disabled={page >= pages} onclick={() => onGoToPage(pages)}>
		<ChevronsRight class="h-4 w-4" />
	</Button>
</div>
