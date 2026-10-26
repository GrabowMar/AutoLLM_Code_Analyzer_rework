<script lang="ts">
	/**
	 * SpecList — app specs as a data list: identity, category and difficulty,
	 * what the conformance suite can grade (endpoints, model fields), and one
	 * next step. Catalog specs open read-only unless `canEditCatalog`.
	 */
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import FileText from '@lucide/svelte/icons/file-text';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Eye from '@lucide/svelte/icons/eye';
	import Copy from '@lucide/svelte/icons/copy';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import WandSparkles from '@lucide/svelte/icons/wand-sparkles';
	import type { AppRequirementTemplate } from '$lib/api/generation';
	import { canEditSpec } from './spec';

	interface Props {
		specs: AppRequirementTemplate[];
		loading: boolean;
		activeSlug: string | null;
		canEditCatalog: boolean;
		/** Shown when `specs` is empty only because of a filter. */
		filtered?: boolean;
		onOpen: (s: AppRequirementTemplate) => void;
		onDuplicate: (s: AppRequirementTemplate) => void;
		onDelete: (s: AppRequirementTemplate) => void;
	}
	let { specs, loading, activeSlug, canEditCatalog, filtered = false, onOpen, onDuplicate, onDelete }: Props = $props();

	function fieldCount(s: AppRequirementTemplate): number {
		const fields = (s.data_model as { fields?: unknown } | null)?.fields;
		return fields && typeof fields === 'object' ? Object.keys(fields).length : 0;
	}
</script>

{#if loading}
	<LoadingState label="loading app specs" />
{:else if specs.length === 0}
	<EmptyState
		icon={FileText}
		title={filtered ? 'No app specs match' : 'No app specs yet'}
		description={filtered ? 'Clear the search or category filter.' : 'Create one, or import a starter package.'}
	/>
{:else}
	<div class="data-list @container">
		<div class="data-list-head" aria-hidden="true">
			<span class="flex-1">App spec</span>
			<span class="hidden w-44 @xl:block">Graded contract</span>
			<span class="w-[8.5rem]"></span>
		</div>
		{#each specs as s (s.id)}
			{@const editable = canEditSpec(s, canEditCatalog)}
			{@const endpoints = (s.api_endpoints?.length ?? 0) + (s.admin_api_endpoints?.length ?? 0)}
			{@const fields = fieldCount(s)}
			<div class="data-list-row" data-active={activeSlug === s.slug}>
				<div class="flex items-center gap-4">
					<div class="min-w-0 flex-1">
						<div class="flex flex-wrap items-center gap-x-2 gap-y-1">
							<button type="button" class="cursor-pointer text-left text-sm font-medium hover:text-primary" onclick={() => onOpen(s)}>{s.name}</button>
							<span class="font-mono text-[11px] text-muted-foreground">{s.slug}</span>
							{#if !s.is_default}<Badge variant="outline" class="h-5 px-1.5 text-[11px]">Yours</Badge>{/if}
						</div>
						<div class="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
							{#if s.category}<span>{s.category}</span>{/if}
							{#if s.difficulty}<span aria-hidden="true">·</span><span class="capitalize">{s.difficulty}</span>{/if}
							{#if s.description}
								<span aria-hidden="true" class="cell-sub">·</span>
								<span class="cell-sub line-clamp-1 min-w-0 flex-1" title={s.description}>{s.description}</span>
							{/if}
						</div>
						<!-- Narrow rows: the contract summary moves under the name. -->
						<p class="mt-0.5 text-[11px] text-muted-foreground @xl:hidden">
							{#if endpoints}{endpoints} endpoints · {fields} fields{:else}<span class="text-warning-fg">Not graded: no endpoints</span>{/if}
						</p>
					</div>
					<div class="hidden w-44 items-center gap-3 @xl:flex">
						{#if endpoints}
							<span class="inline-flex items-baseline gap-1 text-[11px] text-muted-foreground" title="{endpoints} endpoints the conformance suite calls">
								<span class="font-mono text-sm tabular-nums text-foreground">{endpoints}</span>endpoints
							</span>
							<span class="inline-flex items-baseline gap-1 text-[11px] text-muted-foreground" title="{fields} data model fields">
								<span class="font-mono text-sm tabular-nums text-foreground">{fields}</span>fields
							</span>
						{:else}
							<span class="text-[11px] text-warning-fg" title="Agent runs on this spec cannot be graded">Not graded</span>
						{/if}
					</div>
					<div class="flex w-[8.5rem] shrink-0 items-center justify-end gap-0.5">
						<div class="row-actions-more flex items-center gap-0.5">
							<Button
								variant="ghost"
								size="sm"
								class="size-8 p-0 text-muted-foreground"
								href="/sample-generator?app={encodeURIComponent(s.slug)}"
								title="Generate samples from this spec"
								aria-label="Generate samples from {s.name}"
							>
								<WandSparkles />
							</Button>
							<Button variant="ghost" size="sm" class="size-8 p-0 text-muted-foreground" title="Duplicate" aria-label="Duplicate {s.name}" onclick={() => onDuplicate(s)}>
								<Copy />
							</Button>
							{#if !s.is_default}
								<Button
									variant="ghost"
									size="sm"
									class="size-8 p-0 text-muted-foreground hover:bg-destructive/10 hover:text-danger-fg"
									title="Delete app spec"
									aria-label="Delete app spec {s.name}"
									onclick={() => onDelete(s)}
								>
									<Trash2 />
								</Button>
							{/if}
						</div>
						<Button variant="ghost" size="sm" class="h-8 px-2" onclick={() => onOpen(s)}>
							{#if editable}<Pencil /> Edit{:else}<Eye /> View{/if}
						</Button>
					</div>
				</div>
			</div>
		{/each}
	</div>
{/if}
