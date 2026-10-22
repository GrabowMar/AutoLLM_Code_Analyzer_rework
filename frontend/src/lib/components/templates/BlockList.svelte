<script lang="ts">
	import { Badge } from '$lib/components/ui/badge';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Blocks from '@lucide/svelte/icons/blocks';
	import { Button } from '$lib/components/ui/button';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
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
	<LoadingState label="loading blocks" />
{:else if blocks.length === 0}
	<EmptyState icon={Blocks} title="No content blocks yet" description="Blocks are reusable prompt fragments that profiles compose." />
{:else}
	<div class="space-y-4">
		{#each Object.entries(grouped) as [type, items] (type)}
			<div class="space-y-1.5">
				<p class="section-label">{type} <span class="font-mono normal-case">({items.length})</span></p>
				<div class="space-y-1">
					{#each items as b (b.id)}
						{@const isActive = activeSlug === b.slug}
						<div class="rounded-md border border-border bg-card {isActive ? 'ring-2 ring-primary' : ''}">
							<div class="flex items-center gap-2 px-3 py-2">
								<button
									type="button"
									class="text-xs font-mono truncate hover:text-success-fg transition-colors cursor-pointer"
									aria-expanded={expandedId === b.id}
									onclick={() => (expandedId = expandedId === b.id ? null : b.id)}
								>{b.slug}</button>
								<span class="text-[11px] font-mono text-muted-foreground shrink-0">v{b.version}</span>
								{#if b.is_system}<Badge variant="secondary" class="shrink-0">System</Badge>{/if}
								<span class="text-[11px] text-muted-foreground truncate hidden sm:inline">{b.name}</span>
								<Button
									variant="outline"
									size="sm"
									class="ml-auto h-7 shrink-0"
									title="Edit as new version — existing profiles keep their pinned version"
									onclick={() => onEdit(b)}
								>
									<Pencil /> New version
								</Button>
							</div>
							{#if expandedId === b.id}
								<pre class="max-h-56 overflow-auto border-t bg-muted/20 p-2 text-[11px] font-mono leading-relaxed whitespace-pre-wrap">{b.content}</pre>
							{/if}
						</div>
					{/each}
				</div>
			</div>
		{/each}
	</div>
{/if}
