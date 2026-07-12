<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
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
	<div class="flex items-center justify-center py-16 text-sm text-muted-foreground">
		<LoaderCircle class="mr-2 h-4 w-4 animate-spin text-primary" /> Loading stacks…
	</div>
{:else if stacks.length === 0}
	<Card.Root class="border-dashed">
		<Card.Content class="py-16 text-center text-sm text-muted-foreground">
			No stacks seeded yet — run migrations to seed the builtin stacks.
		</Card.Content>
	</Card.Root>
{:else}
	<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-2 {compact ? 'xl:grid-cols-1' : 'xl:grid-cols-3'}">
		{#each stacks as stack (stack.slug)}
			<div class="group relative rounded-md border border-border bg-card text-card-foreground shadow-sm transition-all hover:border-primary/40 hover:shadow-md">
				<div class="p-4 space-y-3">
					<div class="flex items-start justify-between gap-2">
						<div class="min-w-0">
							<h3 class="font-bold text-sm truncate text-foreground flex items-center gap-1.5">
								<Layers class="h-3.5 w-3.5 text-primary/70" />
								{stack.slug}
							</h3>
						</div>
						<Badge variant="outline" class="text-[9px] px-1.5 py-0 shrink-0 {stack.has_frontend ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30' : 'bg-muted/40'}">
							{stack.has_frontend ? 'full-stack' : 'backend-only'}
						</Badge>
					</div>
					{#if stack.aliases.length}
						<div class="flex flex-wrap gap-1 min-h-[22px]">
							{#each stack.aliases as alias}
								<Badge variant="outline" class="text-[9px] px-1.5 py-0 bg-muted/30 font-mono">{alias}</Badge>
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
