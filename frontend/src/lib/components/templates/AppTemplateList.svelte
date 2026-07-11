<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import type { AppRequirementTemplate } from '$lib/api/client';

	interface Props {
		templates: AppRequirementTemplate[];
		loading: boolean;
		activeSlug: string | null;
		onEdit: (t: AppRequirementTemplate) => void;
		onDelete: (t: AppRequirementTemplate) => void;
	}
	let { templates, loading, activeSlug, onEdit, onDelete }: Props = $props();
</script>

{#if loading}
	<div class="flex items-center justify-center py-16 text-sm text-muted-foreground">
		<LoaderCircle class="mr-2 h-4 w-4 animate-spin text-primary" /> Loading requirement templates…
	</div>
{:else if templates.length === 0}
	<Card.Root class="border-dashed">
		<Card.Content class="py-16 text-center text-sm text-muted-foreground">
			No app templates found.
		</Card.Content>
	</Card.Root>
{:else}
	<div class="space-y-2">
		{#each templates as t (t.id)}
			{@const isActive = activeSlug === t.slug}
			<div class="group flex items-start gap-4 rounded-md border border-border bg-card p-4 shadow-sm transition-all hover:border-primary/40 hover:shadow-md {isActive ? 'ring-2 ring-primary bg-primary/[0.02]' : ''}">
				<div class="flex-1 min-w-0">
					<div class="flex items-center flex-wrap gap-2">
						<span class="font-bold text-sm text-foreground">{t.name}</span>
						<span class="text-[10px] font-mono text-muted-foreground">({t.slug})</span>
						{#if t.is_default}<Badge variant="secondary" class="text-[9px] px-1 py-0">Default</Badge>{/if}
					</div>
					{#if t.description}
						<p class="text-xs text-muted-foreground mt-1 line-clamp-1 leading-relaxed">{t.description}</p>
					{/if}
					<div class="flex gap-4 mt-2 text-[10px] text-muted-foreground font-medium">
						{#if t.backend_requirements?.length}
							<span class="flex items-center gap-1"><Badge variant="outline" class="text-[9px] px-1 py-0 font-mono tabular-nums">{t.backend_requirements.length}</Badge> Backend</span>
						{/if}
						{#if t.frontend_requirements?.length}
							<span class="flex items-center gap-1"><Badge variant="outline" class="text-[9px] px-1 py-0 font-mono tabular-nums">{t.frontend_requirements.length}</Badge> Frontend</span>
						{/if}
						{#if t.admin_requirements?.length}
							<span class="flex items-center gap-1"><Badge variant="outline" class="text-[9px] px-1 py-0 font-mono tabular-nums">{t.admin_requirements.length}</Badge> Admin</span>
						{/if}
					</div>
				</div>
				<div class="flex gap-1.5 ml-2 shrink-0 self-center">
					<button
						type="button"
						class="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium border rounded hover:bg-muted/80 transition-colors cursor-pointer text-muted-foreground hover:text-foreground"
						onclick={() => onEdit(t)}
					>
						<Pencil class="h-3.5 w-3.5" /> Edit
					</button>
					{#if !t.is_default}
						<button
							type="button"
							class="inline-flex items-center justify-center p-1.5 text-xs font-medium border border-red-500/20 rounded hover:bg-destructive/10 transition-colors cursor-pointer text-destructive hover:text-red-300"
							title="Delete Template"
							onclick={() => onDelete(t)}
						>
							<Trash2 class="h-3.5 w-3.5" />
						</button>
					{/if}
				</div>
			</div>
		{/each}
	</div>
{/if}
