<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Separator } from '$lib/components/ui/separator';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import type { ScaffoldingTemplate } from '$lib/api/client';

	interface Props {
		templates: ScaffoldingTemplate[];
		loading: boolean;
		compact: boolean;
		activeSlug: string | null;
		onEdit: (t: ScaffoldingTemplate) => void;
		onDelete: (t: ScaffoldingTemplate) => void;
	}
	let { templates, loading, compact, activeSlug, onEdit, onDelete }: Props = $props();
</script>

{#if loading}
	<div class="flex items-center justify-center py-16 text-sm text-muted-foreground">
		<LoaderCircle class="mr-2 h-4 w-4 animate-spin text-primary" /> Loading scaffolding templates…
	</div>
{:else if templates.length === 0}
	<Card.Root class="border-dashed">
		<Card.Content class="py-16 text-center text-sm text-muted-foreground">
			No scaffolding templates found.
		</Card.Content>
	</Card.Root>
{:else}
	<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-2 {compact ? 'xl:grid-cols-1' : 'xl:grid-cols-3'}">
		{#each templates as t (t.id)}
			{@const isActive = activeSlug === t.slug}
			<div class="group relative rounded-md border border-border bg-card text-card-foreground shadow-sm transition-all hover:border-primary/40 hover:shadow-md {isActive ? 'ring-2 ring-primary bg-primary/[0.02]' : ''}">
				<div class="p-4 space-y-3">
					<div class="flex items-start justify-between gap-2">
						<div class="min-w-0">
							<h3 class="font-bold text-sm truncate text-foreground flex items-center gap-1.5">
								{t.name}
								{#if t.is_default}<Badge variant="secondary" class="text-[9px] px-1 py-0 scale-90">Default</Badge>{/if}
							</h3>
							<p class="text-[10px] text-muted-foreground font-mono truncate mt-0.5">{t.slug}</p>
						</div>
					</div>
					{#if t.description}
						<p class="text-xs text-muted-foreground line-clamp-2 leading-relaxed h-8">{t.description}</p>
					{:else}
						<div class="h-8"></div>
					{/if}
					{#if t.tech_stack && Object.keys(t.tech_stack).length}
						<div class="flex flex-wrap gap-1 min-h-[22px]">
							{#each Object.entries(t.tech_stack) as [k, v]}
								<Badge variant="outline" class="text-[9px] px-1.5 py-0 bg-muted/30">{k}: {v}</Badge>
							{/each}
						</div>
					{:else}
						<div class="min-h-[22px]"></div>
					{/if}
					<Separator class="my-2" />
					<div class="flex gap-2 justify-end">
						<button
							type="button"
							class="inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium border rounded hover:bg-muted/80 transition-colors cursor-pointer text-muted-foreground hover:text-foreground"
							onclick={() => onEdit(t)}
						>
							<Pencil class="h-3 w-3" /> Edit
						</button>
						{#if !t.is_default}
							<button
								type="button"
								class="inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium border border-red-500/20 rounded hover:bg-red-500/10 transition-colors cursor-pointer text-red-400 hover:text-red-300"
								onclick={() => onDelete(t)}
							>
								<Trash2 class="h-3 w-3" /> Delete
							</button>
						{/if}
					</div>
				</div>
			</div>
		{/each}
	</div>
{/if}
