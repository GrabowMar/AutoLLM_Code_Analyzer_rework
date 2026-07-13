<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Copy from '@lucide/svelte/icons/copy';
	import Archive from '@lucide/svelte/icons/archive';
	import History from '@lucide/svelte/icons/history';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import { getProfileVersions, type GenerationProfile } from '$lib/api/client';

	interface Props {
		profiles: GenerationProfile[];
		loading: boolean;
		activeSlug: string | null;
		onEdit: (p: GenerationProfile) => void;
		onDuplicate: (p: GenerationProfile) => void;
		onArchive: (p: GenerationProfile) => void;
	}
	let { profiles, loading, activeSlug, onEdit, onDuplicate, onArchive }: Props = $props();

	let historySlug = $state<string | null>(null);
	let historyLoading = $state(false);
	let historyVersions = $state<GenerationProfile[]>([]);

	async function toggleHistory(p: GenerationProfile) {
		if (historySlug === p.slug) {
			historySlug = null;
			return;
		}
		historySlug = p.slug;
		historyLoading = true;
		try {
			historyVersions = await getProfileVersions(p.slug);
		} catch {
			historyVersions = [];
		}
		historyLoading = false;
	}

	function refsDelta(current: GenerationProfile, previous: GenerationProfile | undefined): string {
		if (!previous) return 'initial version';
		const key = (r: { type: string; slug: string; version: number }) => `${r.type}:${r.slug}@${r.version}`;
		const cur = new Set((current.block_refs ?? []).map(key));
		const prev = new Set((previous.block_refs ?? []).map(key));
		const added = [...cur].filter((k) => !prev.has(k)).length;
		const removed = [...prev].filter((k) => !cur.has(k)).length;
		const parts = [];
		if (added) parts.push(`+${added} block${added > 1 ? 's' : ''}`);
		if (removed) parts.push(`−${removed} block${removed > 1 ? 's' : ''}`);
		if (JSON.stringify(current.llm_config ?? {}) !== JSON.stringify(previous.llm_config ?? {})) {
			parts.push('llm config changed');
		}
		if (current.scaffolding_slug !== previous.scaffolding_slug) parts.push(`stack → ${current.scaffolding_slug}`);
		return parts.length ? parts.join(', ') : 'metadata only';
	}
</script>

{#if loading}
	<div class="flex items-center justify-center py-16 text-sm text-muted-foreground">
		<LoaderCircle class="mr-2 h-4 w-4 animate-spin text-primary" /> Loading profiles…
	</div>
{:else if profiles.length === 0}
	<Card.Root class="border-dashed">
		<Card.Content class="py-16 text-center text-sm text-muted-foreground">
			No profiles yet. Create one, or import a starter package.
		</Card.Content>
	</Card.Root>
{:else}
	<div class="space-y-2">
		{#each profiles as p (p.id)}
			{@const isActive = activeSlug === p.slug}
			<div class="rounded-md border border-border bg-card shadow-sm transition-all hover:border-primary/40 hover:shadow-md {isActive ? 'ring-2 ring-primary bg-primary/[0.02]' : ''}">
				<div class="group flex items-start gap-4 p-4">
					<div class="flex-1 min-w-0">
						<div class="flex items-center flex-wrap gap-2">
							<span class="font-bold text-sm text-foreground">{p.name}</span>
							<span class="text-[10px] font-mono text-muted-foreground">{p.slug}@{p.version}</span>
							{#if p.is_default}<Badge variant="secondary" class="text-[9px] px-1 py-0">Default</Badge>{/if}
							{#if p.is_system}<Badge variant="outline" class="text-[9px] px-1 py-0 bg-muted/40">System</Badge>{/if}
						</div>
						{#if p.description}
							<p class="text-xs text-muted-foreground mt-1 line-clamp-1 leading-relaxed">{p.description}</p>
						{/if}
						<div class="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-[10px] text-muted-foreground font-medium">
							<span class="flex items-center gap-1">
								<Badge variant="outline" class="text-[9px] px-1 py-0 font-mono tabular-nums">{p.block_refs?.length ?? 0}</Badge> blocks
							</span>
							<span>stack <span class="font-mono text-foreground">{p.scaffolding_slug}</span></span>
							{#if Object.keys(p.llm_config ?? {}).length > 0}
								<span class="font-mono text-primary">{Object.keys(p.llm_config).length} llm default{Object.keys(p.llm_config).length > 1 ? 's' : ''}</span>
							{/if}
						</div>
					</div>
					<div class="flex gap-1.5 ml-2 shrink-0 self-center">
						<button
							type="button"
							class="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium border rounded hover:bg-muted/80 transition-colors cursor-pointer text-muted-foreground hover:text-foreground"
							title="Edit as a new version — versions are immutable"
							onclick={() => onEdit(p)}
						>
							<Pencil class="h-3 w-3" /> Edit
						</button>
						<button
							type="button"
							class="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium border rounded hover:bg-muted/80 transition-colors cursor-pointer text-muted-foreground hover:text-foreground"
							title="Version history"
							onclick={() => toggleHistory(p)}
						>
							<History class="h-3 w-3" />
						</button>
						<button
							type="button"
							class="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium border rounded hover:bg-muted/80 transition-colors cursor-pointer text-muted-foreground hover:text-foreground"
							title="Duplicate as a new profile"
							onclick={() => onDuplicate(p)}
						>
							<Copy class="h-3 w-3" />
						</button>
						{#if !p.is_system}
							<button
								type="button"
								class="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium border rounded hover:bg-destructive/10 hover:border-destructive/40 hover:text-destructive transition-colors cursor-pointer text-muted-foreground"
								title="Archive all versions (jobs keep their snapshots)"
								onclick={() => onArchive(p)}
							>
								<Archive class="h-3 w-3" />
							</button>
						{/if}
					</div>
				</div>

				{#if historySlug === p.slug}
					<div class="border-t bg-muted/20 px-4 py-3">
						{#if historyLoading}
							<div class="flex items-center gap-2 text-xs text-muted-foreground">
								<LoaderCircle class="h-3.5 w-3.5 animate-spin" /> Loading versions…
							</div>
						{:else}
							<div class="space-y-1">
								{#each historyVersions as v, i (v.id)}
									<div class="flex items-baseline gap-3 text-xs">
										<span class="font-mono text-foreground shrink-0">v{v.version}</span>
										<span class="text-muted-foreground">{refsDelta(v, historyVersions[i + 1])}</span>
										<span class="ml-auto text-[10px] text-muted-foreground shrink-0">{new Date(v.created_at).toLocaleDateString()}</span>
									</div>
								{/each}
							</div>
						{/if}
					</div>
				{/if}
			</div>
		{/each}
	</div>
{/if}
