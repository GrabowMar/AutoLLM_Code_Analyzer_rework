<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import Cpu from '@lucide/svelte/icons/cpu';
	import Users from '@lucide/svelte/icons/users';
	import ChevronLeft from '@lucide/svelte/icons/chevron-left';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import type { LLMModelSummary } from '$lib/api/client';

	interface Props {
		provider: string;
		relatedModels: LLMModelSummary[];
	}

	let { provider, relatedModels }: Props = $props();

	let relatedScrollEl = $state<HTMLDivElement | null>(null);

	function scrollRelated(dir: 'left' | 'right') {
		if (!relatedScrollEl) return;
		const amount = 300;
		relatedScrollEl.scrollBy({ left: dir === 'left' ? -amount : amount, behavior: 'smooth' });
	}
</script>

<section id="related" class="space-y-4">
	<h2 class="text-lg font-semibold flex items-center gap-2"><Users class="h-5 w-5" /> Related Models</h2>

	<Card.Root>
		<Card.Header class="pb-2">
			<div class="flex items-center justify-between">
				<Card.Title class="text-sm font-medium flex items-center gap-2">
					Models from {provider}
					<Badge variant="outline" class="text-xs">{relatedModels.length}</Badge>
				</Card.Title>
				{#if relatedModels.length > 4}
					<div class="flex items-center gap-1">
						<Button variant="ghost" size="icon" class="h-7 w-7" onclick={() => scrollRelated('left')}>
							<ChevronLeft class="h-4 w-4" />
						</Button>
						<Button variant="ghost" size="icon" class="h-7 w-7" onclick={() => scrollRelated('right')}>
							<ChevronRight class="h-4 w-4" />
						</Button>
					</div>
				{/if}
			</div>
		</Card.Header>
		<Card.Content>
			{#if relatedModels.length === 0}
				<div class="flex h-24 flex-col items-center justify-center gap-2 rounded-lg border border-dashed bg-muted/20">
					<Users class="h-6 w-6 text-muted-foreground/40" />
					<p class="text-sm text-muted-foreground">No related models from {provider} found</p>
				</div>
			{:else}
				<div bind:this={relatedScrollEl} class="flex gap-3 overflow-x-auto pb-2 scrollbar-thin">
					{#each relatedModels as related}
						<a
							href="/models/{related.canonical_slug}"
							class="group flex-shrink-0 w-56 rounded-lg border bg-card p-3 transition-all hover:shadow-md hover:border-primary/30"
						>
							<div class="flex items-start gap-2 mb-2">
								<div class="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg bg-primary/10">
									<Cpu class="h-3.5 w-3.5 text-primary" />
								</div>
								<div class="min-w-0 flex-1">
									<div class="text-sm font-medium truncate group-hover:text-primary transition-colors">{related.model_name}</div>
									<div class="text-xs text-muted-foreground">{related.provider}</div>
								</div>
							</div>
							<div class="grid grid-cols-2 gap-1.5 text-xs">
								<div>
									<span class="text-muted-foreground">Context:</span>
									<span class="font-medium ml-0.5">{related.context_window >= 1000 ? `${Math.round(related.context_window / 1000)}K` : related.context_window}</span>
								</div>
								<div>
									<span class="text-muted-foreground">Input:</span>
									<span class="font-medium ml-0.5">{related.input_price_per_million === 0 ? 'Free' : `$${related.input_price_per_million.toFixed(2)}`}</span>
								</div>
							</div>
							{#if related.capabilities && related.capabilities.length > 0}
								<div class="flex flex-wrap gap-1 mt-1.5">
									{#each related.capabilities.slice(0, 3) as cap}
										<Badge variant="outline" class="text-[10px] px-1 py-0">{cap}</Badge>
									{/each}
								</div>
							{/if}
						</a>
					{/each}
				</div>
			{/if}
		</Card.Content>
	</Card.Root>
</section>
