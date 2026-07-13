<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import Pencil from '@lucide/svelte/icons/pencil';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import type { ContentBlock } from '$lib/api/client';

	interface Props {
		blocks: ContentBlock[];
		loading: boolean;
		activeSlug: string | null;
		onEdit: (b: ContentBlock) => void;
	}
	let { blocks, loading, activeSlug, onEdit }: Props = $props();

	let expandedId = $state<number | null>(null);

	// Latest version per (type, slug), grouped by type.
	const grouped = $derived.by(() => {
		const latest = new Map<string, ContentBlock>();
		for (const b of blocks) {
			const key = `${b.block_type}:${b.slug}`;
			if (!latest.has(key) || latest.get(key)!.version < b.version) latest.set(key, b);
		}
		const groups: Record<string, ContentBlock[]> = {};
		for (const b of latest.values()) {
			(groups[b.block_type] ??= []).push(b);
		}
		for (const list of Object.values(groups)) list.sort((a, b) => a.slug.localeCompare(b.slug));
		return groups;
	});
</script>

{#if loading}
	<div class="flex items-center justify-center py-16 text-sm text-muted-foreground">
		<LoaderCircle class="mr-2 h-4 w-4 animate-spin text-primary" /> Loading blocks…
	</div>
{:else if blocks.length === 0}
	<Card.Root class="border-dashed">
		<Card.Content class="py-16 text-center text-sm text-muted-foreground">
			No content blocks yet.
		</Card.Content>
	</Card.Root>
{:else}
	<div class="space-y-4">
		{#each Object.entries(grouped) as [type, items] (type)}
			<div class="space-y-1.5">
				<p class="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{type} <span class="font-mono normal-case">({items.length})</span></p>
				<div class="space-y-1">
					{#each items as b (b.id)}
						{@const isActive = activeSlug === b.slug}
						<div class="rounded-md border border-border bg-card shadow-sm transition-all hover:border-primary/40 {isActive ? 'ring-2 ring-primary bg-primary/[0.02]' : ''}">
							<div class="flex items-center gap-2 px-3 py-2">
								<button
									type="button"
									class="text-xs font-mono truncate hover:text-primary transition-colors cursor-pointer"
									onclick={() => (expandedId = expandedId === b.id ? null : b.id)}
								>{b.slug}</button>
								<span class="text-[10px] font-mono text-muted-foreground shrink-0">v{b.version}</span>
								{#if b.is_system}<Badge variant="outline" class="text-[9px] px-1 py-0 bg-muted/40 shrink-0">System</Badge>{/if}
								<span class="text-[10px] text-muted-foreground truncate hidden sm:inline">{b.name}</span>
								<button
									type="button"
									class="ml-auto inline-flex items-center gap-1 px-2 py-1 text-[10px] font-medium border rounded hover:bg-muted/80 transition-colors cursor-pointer text-muted-foreground hover:text-foreground shrink-0"
									title="Edit as new version — existing profiles keep their pinned version"
									onclick={() => onEdit(b)}
								>
									<Pencil class="h-2.5 w-2.5" /> New version
								</button>
							</div>
							{#if expandedId === b.id}
								<pre class="max-h-56 overflow-auto border-t bg-muted/20 p-2 text-[10px] leading-relaxed whitespace-pre-wrap">{b.content}</pre>
							{/if}
						</div>
					{/each}
				</div>
			</div>
		{/each}
	</div>
{/if}
