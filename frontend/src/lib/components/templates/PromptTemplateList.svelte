<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import MessageSquare from '@lucide/svelte/icons/message-square';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import type { PromptTemplate } from '$lib/api/client';

	interface Props {
		groups: Record<string, PromptTemplate[]>;
		total: number;
		loading: boolean;
		compact: boolean;
		activeSlug: string | null;
		onEdit: (t: PromptTemplate) => void;
		onDelete: (t: PromptTemplate) => void;
	}
	let { groups, total, loading, compact, activeSlug, onEdit, onDelete }: Props = $props();
</script>

{#if loading}
	<div class="flex items-center justify-center py-16 text-sm text-muted-foreground">
		<LoaderCircle class="mr-2 h-4 w-4 animate-spin text-primary" /> Loading prompt templates…
	</div>
{:else if total === 0}
	<Card.Root class="border-dashed">
		<Card.Content class="py-16 text-center text-sm text-muted-foreground">
			No prompts found.
		</Card.Content>
	</Card.Root>
{:else}
	<div class="space-y-6">
		{#each Object.entries(groups) as [group, templates]}
			<div class="space-y-2">
				<h3 class="text-xs font-bold uppercase tracking-wider text-muted-foreground border-b pb-1 flex items-center gap-2">
					<MessageSquare class="h-3.5 w-3.5" />
					{group.replace('/', ' ➔ ')}
				</h3>
				<div class="grid gap-3 grid-cols-1 sm:grid-cols-2 {compact ? 'xl:grid-cols-1' : 'xl:grid-cols-2'}">
					{#each templates as t (t.id)}
						{@const isActive = activeSlug === t.slug}
						<div class="group relative rounded-md border border-border bg-card p-4 shadow-sm transition-all hover:border-primary/40 hover:shadow-md {isActive ? 'ring-2 ring-primary bg-primary/[0.02]' : ''}">
							<div class="space-y-3">
								<div class="flex items-start justify-between gap-2">
									<div class="min-w-0">
										<span class="font-bold text-sm truncate block text-foreground">{t.name}</span>
										<div class="flex items-center gap-1.5 mt-1">
											<Badge variant="outline" class="text-[9px] px-1 py-0 uppercase font-mono">{t.stage}</Badge>
											<Badge variant="outline" class="text-[9px] px-1 py-0 uppercase font-mono">{t.role}</Badge>
											{#if t.is_default}<Badge variant="secondary" class="text-[9px] px-1 py-0">Default</Badge>{/if}
										</div>
									</div>
								</div>
								<pre class="text-[11px] text-muted-foreground bg-muted/30 p-2.5 rounded-md border font-mono line-clamp-3 whitespace-pre-wrap leading-relaxed h-[62px]">{t.content}</pre>
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
			</div>
		{/each}
	</div>
{/if}
