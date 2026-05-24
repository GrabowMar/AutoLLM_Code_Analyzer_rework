<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import Star from '@lucide/svelte/icons/star';
import Pencil from '@lucide/svelte/icons/pencil';
import Trash2 from '@lucide/svelte/icons/trash-2';
import type { AnalysisProfile } from '$lib/api/analysis';

interface Props {
	profile: AnalysisProfile;
	onEdit: (profile: AnalysisProfile) => void;
	onDelete: (profile: AnalysisProfile) => void;
}

let { profile, onEdit, onDelete }: Props = $props();
</script>

<Card.Root class="flex flex-col">
	<Card.Header class="pb-2">
		<div class="flex items-start justify-between gap-2">
			<div class="flex items-center gap-2 min-w-0">
				{#if profile.is_default}
					<Star class="h-4 w-4 shrink-0 fill-amber-400 text-amber-400" />
				{/if}
				<Card.Title class="text-sm truncate">{profile.name}</Card.Title>
			</div>
			<div class="flex shrink-0 gap-1">
				<Button variant="ghost" size="icon" class="h-7 w-7" onclick={() => onEdit(profile)}>
					<Pencil class="h-3.5 w-3.5" />
				</Button>
				<Button variant="ghost" size="icon" class="h-7 w-7 text-destructive hover:text-destructive" onclick={() => onDelete(profile)}>
					<Trash2 class="h-3.5 w-3.5" />
				</Button>
			</div>
		</div>
		{#if profile.description}
			<Card.Description class="text-xs">{profile.description}</Card.Description>
		{/if}
	</Card.Header>
	<Card.Content class="pt-0">
		{#if profile.analyzers.length > 0}
			<div class="flex flex-wrap gap-1">
				{#each profile.analyzers as name}
					<Badge variant="outline" class="text-xs">{name}</Badge>
				{/each}
			</div>
		{:else}
			<p class="text-xs text-muted-foreground italic">No analyzers configured.</p>
		{/if}
	</Card.Content>
	<Card.Footer class="pt-0">
		<p class="text-xs text-muted-foreground">
			{profile.analyzers.length} analyzer{profile.analyzers.length !== 1 ? 's' : ''}
		</p>
	</Card.Footer>
</Card.Root>
