<script lang="ts">
	import { Badge } from '$lib/components/ui/badge';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Copy from '@lucide/svelte/icons/copy';
	import Archive from '@lucide/svelte/icons/archive';
	import History from '@lucide/svelte/icons/history';
	import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';
	import { Button } from '$lib/components/ui/button';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { getProfileVersions, type GenerationProfile } from '$lib/api/client';

	interface Props {
		profiles: GenerationProfile[];
		loading: boolean;
		activeSlug: string | null;
		/** System profiles are read-only for non-staff users: offer Duplicate instead of Edit. */
		canEditSystem?: boolean;
		onEdit: (p: GenerationProfile) => void;
		onDuplicate: (p: GenerationProfile) => void;
		onArchive: (p: GenerationProfile) => void;
	}
	let { profiles, loading, activeSlug, canEditSystem = false, onEdit, onDuplicate, onArchive }: Props = $props();

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
	<LoadingState label="loading profiles" />
{:else if profiles.length === 0}
	<EmptyState icon={SlidersHorizontal} title="No profiles yet" description="Create one, or import a starter package." />
{:else}
	<div class="space-y-2">
		{#each profiles as p (p.id)}
			{@const isActive = activeSlug === p.slug}
			<div class="rounded-md border border-border bg-card {isActive ? 'ring-2 ring-primary' : ''}">
				<div class="flex items-start gap-4 p-4">
					<div class="flex-1 min-w-0">
						<div class="flex items-center flex-wrap gap-2">
							<span class="font-semibold text-sm">{p.name}</span>
							<span class="text-[11px] font-mono text-muted-foreground">{p.slug}@{p.version}</span>
							{#if p.is_default}<Badge variant="secondary">Default</Badge>{/if}
							{#if p.is_system}<Badge variant="secondary">System</Badge>{/if}
						</div>
						{#if p.description}
							<p class="text-xs text-muted-foreground mt-1 line-clamp-1 leading-relaxed">{p.description}</p>
						{/if}
						<div class="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-[11px] text-muted-foreground font-medium">
							<span class="flex items-center gap-1">
								<Badge variant="outline" class="font-mono">{p.block_refs?.length ?? 0}</Badge> blocks
							</span>
							<span>stack <span class="font-mono text-foreground">{p.scaffolding_slug}</span></span>
							{#if Object.keys(p.llm_config ?? {}).length > 0}
								<span class="font-mono text-success-fg">{Object.keys(p.llm_config).length} llm default{Object.keys(p.llm_config).length > 1 ? 's' : ''}</span>
							{/if}
						</div>
					</div>
					<div class="flex gap-1.5 ml-2 shrink-0 self-center">
						{#if !p.is_system || canEditSystem}
							<Button variant="outline" size="sm" title="Edit as a new version — versions are immutable" onclick={() => onEdit(p)}>
								<Pencil /> Edit
							</Button>
						{/if}
						<Button
							variant="outline"
							size="sm"
							title="Version history"
							aria-label="Version history of {p.name}"
							aria-expanded={historySlug === p.slug}
							onclick={() => toggleHistory(p)}
						>
							<History />
						</Button>
						<Button variant="outline" size="sm" title="Duplicate as a new profile" aria-label="Duplicate {p.name}" onclick={() => onDuplicate(p)}>
							<Copy />
						</Button>
						{#if !p.is_system}
							<Button
								variant="outline"
								size="sm"
								class="hover:bg-destructive/10 hover:text-danger-fg"
								title="Archive all versions (jobs keep their snapshots)"
								aria-label="Archive {p.name}"
								onclick={() => onArchive(p)}
							>
								<Archive />
							</Button>
						{/if}
					</div>
				</div>

				{#if historySlug === p.slug}
					<div class="border-t bg-muted/20 px-4 py-3">
						{#if historyLoading}
							<LoadingState label="loading versions" class="py-2" />
						{:else}
							<div class="space-y-1">
								{#each historyVersions as v, i (v.id)}
									<div class="flex items-baseline gap-3 text-xs">
										<span class="font-mono text-foreground shrink-0">v{v.version}</span>
										<span class="text-muted-foreground">{refsDelta(v, historyVersions[i + 1])}</span>
										<span class="ml-auto text-[11px] text-muted-foreground shrink-0">{new Date(v.created_at).toLocaleDateString()}</span>
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
