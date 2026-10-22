<script lang="ts">
	import { Badge } from '$lib/components/ui/badge';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import FileText from '@lucide/svelte/icons/file-text';
	import { Button } from '$lib/components/ui/button';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
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
	<LoadingState label="loading app specs" />
{:else if templates.length === 0}
	<EmptyState icon={FileText} title="No app specs found" description="Create one, or clear the search filter." />
{:else}
	<div class="space-y-2">
		{#each templates as t (t.id)}
			{@const isActive = activeSlug === t.slug}
			<div class="group flex items-start gap-4 rounded-md border border-border bg-card p-4 transition-colors {isActive ? 'ring-2 ring-primary' : ''}">
				<div class="flex-1 min-w-0">
					<div class="flex items-center flex-wrap gap-2">
						<span class="font-semibold text-sm">{t.name}</span>
						<span class="text-[11px] font-mono text-muted-foreground">({t.slug})</span>
						{#if t.is_default}<Badge variant="secondary">Default</Badge>{/if}
					</div>
					{#if t.description}
						<p class="text-xs text-muted-foreground mt-1 line-clamp-1 leading-relaxed">{t.description}</p>
					{/if}
					<div class="flex gap-4 mt-2 text-[11px] text-muted-foreground font-medium">
						{#if t.backend_requirements?.length}
							<span class="flex items-center gap-1"><Badge variant="outline" class="font-mono">{t.backend_requirements.length}</Badge> Backend</span>
						{/if}
						{#if t.frontend_requirements?.length}
							<span class="flex items-center gap-1"><Badge variant="outline" class="font-mono">{t.frontend_requirements.length}</Badge> Frontend</span>
						{/if}
						{#if t.admin_requirements?.length}
							<span class="flex items-center gap-1"><Badge variant="outline" class="font-mono">{t.admin_requirements.length}</Badge> Admin</span>
						{/if}
					</div>
				</div>
				<div class="flex gap-1.5 ml-2 shrink-0 self-center">
					<Button variant="outline" size="sm" onclick={() => onEdit(t)}>
						<Pencil /> Edit
					</Button>
					{#if !t.is_default}
						<Button
							variant="outline"
							size="sm"
							class="text-danger-fg hover:bg-destructive/10 hover:text-danger-fg"
							title="Delete app spec"
							aria-label="Delete app spec"
							onclick={() => onDelete(t)}
						>
							<Trash2 />
						</Button>
					{/if}
				</div>
			</div>
		{/each}
	</div>
{/if}
