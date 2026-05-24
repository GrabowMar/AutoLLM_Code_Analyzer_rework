<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import BookmarkCheck from '@lucide/svelte/icons/bookmark-check';
import Loader from '@lucide/svelte/icons/loader-circle';
import { getAnalysisProfiles, type AnalysisProfile } from '$lib/api/analysis';

interface Props {
	onSelect: (profile: AnalysisProfile | null) => void;
	selectedProfileId?: number | null;
}

let { onSelect, selectedProfileId = null }: Props = $props();

let profiles = $state<AnalysisProfile[]>([]);
let loading = $state(true);

$effect(() => {
	getAnalysisProfiles()
		.then((data) => {
			profiles = data;
		})
		.catch(() => {})
		.finally(() => {
			loading = false;
		});
});

function handleSelect(e: Event) {
	const val = (e.target as HTMLSelectElement).value;
	if (!val) {
		onSelect(null);
		return;
	}
	const profile = profiles.find((p) => String(p.id) === val) ?? null;
	onSelect(profile);
}
</script>

<Card.Root>
	<Card.Header class="pb-3">
		<div class="flex items-center gap-2">
			<BookmarkCheck class="h-4 w-4 text-emerald-500" />
			<Card.Title class="text-sm">Start from a Profile</Card.Title>
		</div>
		<Card.Description>Load a saved set of analyzers and settings, or start fresh.</Card.Description>
	</Card.Header>
	<Card.Content>
		{#if loading}
			<div class="flex items-center gap-2 text-sm text-muted-foreground">
				<Loader class="h-3 w-3 animate-spin" />
				Loading profiles…
			</div>
		{:else if profiles.length === 0}
			<p class="text-sm text-muted-foreground">No profiles saved yet. Configure analyzers manually or save one after your first run.</p>
		{:else}
			<div class="flex items-center gap-3">
				<select
					class="h-9 flex-1 rounded-md border border-input bg-background px-3 text-sm"
					value={selectedProfileId ? String(selectedProfileId) : ''}
					onchange={handleSelect}
				>
					<option value="">— Start fresh —</option>
					{#each profiles as profile}
						<option value={String(profile.id)}>
							{profile.name}{profile.is_default ? ' (default)' : ''}
						</option>
					{/each}
				</select>
				{#if selectedProfileId}
					{@const profile = profiles.find((p) => p.id === selectedProfileId)}
					{#if profile}
						<div class="flex flex-wrap gap-1">
							{#each profile.analyzers.slice(0, 4) as name}
								<Badge variant="outline" class="text-xs">{name}</Badge>
							{/each}
							{#if profile.analyzers.length > 4}
								<Badge variant="outline" class="text-xs">+{profile.analyzers.length - 4}</Badge>
							{/if}
						</div>
					{/if}
				{/if}
			</div>
		{/if}
	</Card.Content>
</Card.Root>
