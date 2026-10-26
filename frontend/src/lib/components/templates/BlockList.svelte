<script lang="ts">
	import { Badge } from '$lib/components/ui/badge';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Blocks from '@lucide/svelte/icons/blocks';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
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
				<div class="data-list">
					{#each items as b (b.id)}
						{@const isActive = activeSlug === b.slug}
						{@const open = expandedId === b.id}
						<div class="data-list-row py-2" data-active={isActive}>
							<div class="flex items-center gap-2">
								<button
									type="button"
									class="flex min-w-0 cursor-pointer items-center gap-1.5 text-left hover:text-primary"
									aria-expanded={open}
									title={open ? 'Hide content' : 'Show content'}
									onclick={() => (expandedId = open ? null : b.id)}
								>
									<ChevronRight class="size-3.5 shrink-0 text-muted-foreground transition-transform duration-150 {open ? 'rotate-90' : ''}" aria-hidden="true" />
									<span class="truncate font-mono text-xs">{b.slug}</span>
								</button>
								<span class="shrink-0 font-mono text-[11px] text-muted-foreground">v{b.version}</span>
								{#if b.is_system}<Badge variant="secondary" class="h-5 shrink-0 px-1.5 text-[11px]">System</Badge>{/if}
								<span class="hidden truncate text-[11px] text-muted-foreground sm:inline">{b.name}</span>
								<Button
									variant="ghost"
									size="sm"
									class="ml-auto h-7 shrink-0 px-2 text-xs"
									title="Edit as new version — existing profiles keep their pinned version"
									onclick={() => onEdit(b)}
								>
									<Pencil class="size-3.5" /> New version
								</Button>
							</div>
							{#if open}
								<pre class="mt-2 max-h-56 overflow-auto rounded-md border border-border bg-muted/20 p-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap">{b.content}</pre>
							{/if}
						</div>
					{/each}
				</div>
			</div>
		{/each}
	</div>
{/if}
