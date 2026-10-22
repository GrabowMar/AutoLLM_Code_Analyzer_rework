<script lang="ts">
	import { Badge } from '$lib/components/ui/badge';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import Layers from '@lucide/svelte/icons/layers';
	import type { Stack } from '$lib/api/client';

	interface Props {
		stacks: Stack[];
		loading: boolean;
		compact: boolean;
		onEdit?: (s: Stack) => void;
		onDuplicate?: (s: Stack) => void;
		onArchive?: (s: Stack) => void;
	}
	let { stacks, loading, compact, onEdit, onDuplicate, onArchive }: Props = $props();
</script>

{#if loading}
	<LoadingState label="loading stacks" />
{:else if stacks.length === 0}
	<EmptyState icon={Layers} title="No stacks found" description="If the search is empty, run migrations to seed the builtin stacks." />
{:else}
	<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-2 {compact ? 'xl:grid-cols-1' : 'xl:grid-cols-3'}">
		{#each stacks as stack (stack.slug)}
			<div class="rounded-md border border-border bg-card text-card-foreground">
				<div class="p-4 space-y-3">
					<div class="flex items-start justify-between gap-2">
						<div class="min-w-0">
							<h3 class="font-semibold text-sm truncate flex items-center gap-1.5">
								<Layers class="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
								{stack.slug}
							</h3>
						</div>
						<Badge variant={stack.has_frontend ? 'default' : 'secondary'} class="shrink-0">
							{stack.has_frontend ? 'full-stack' : 'backend-only'}
						</Badge>
					</div>
					{#if stack.aliases.length}
						<div class="flex flex-wrap gap-1 min-h-[22px]">
							{#each stack.aliases as alias}
								<Badge variant="outline" class="font-mono">{alias}</Badge>
							{/each}
						</div>
					{:else}
						<div class="min-h-[22px]"></div>
					{/if}
				</div>
			</div>
		{/each}
	</div>
{/if}
