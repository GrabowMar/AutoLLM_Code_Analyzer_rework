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
	<div class="data-list @container">
		<div class="data-list-head" aria-hidden="true">
			<span class="flex-1">Profile</span>
			<span class="hidden w-16 text-right @xl:block">Blocks</span>
			<span class="hidden w-40 @xl:block">Stack</span>
			<span class="w-[8.5rem]"></span>
		</div>
		{#each profiles as p (p.id)}
			{@const isActive = activeSlug === p.slug}
			{@const llmDefaults = Object.keys(p.llm_config ?? {}).length}
			<div class="data-list-row" data-active={isActive}>
				<div class="flex items-center gap-4">
					<div class="min-w-0 flex-1">
						<div class="flex flex-wrap items-center gap-x-2 gap-y-1">
							<span class="text-sm font-medium">{p.name}</span>
							<span class="font-mono text-[11px] text-muted-foreground">{p.slug}@{p.version}</span>
							{#if p.is_default}<Badge variant="outline" class="h-5 px-1.5 text-[11px] text-primary">Default</Badge>{/if}
							{#if p.is_system}<Badge variant="secondary" class="h-5 px-1.5 text-[11px]">System</Badge>{/if}
						</div>
						{#if p.description}
							<p class="cell-sub mt-0.5 line-clamp-1 text-xs text-muted-foreground" title={p.description}>{p.description}</p>
						{/if}
						<p class="cell-sub mt-0.5 font-mono text-[11px] text-muted-foreground @xl:hidden">
							{p.block_refs?.length ?? 0} blocks · {p.scaffolding_slug}
						</p>
					</div>
					<span class="hidden w-16 text-right font-mono text-sm tabular-nums @xl:block" title="Prompt blocks">{p.block_refs?.length ?? 0}</span>
					<div class="hidden w-40 min-w-0 @xl:block">
						<span class="block truncate font-mono text-xs">{p.scaffolding_slug}</span>
						{#if llmDefaults > 0}
							<span class="cell-sub block text-[11px] text-muted-foreground">{llmDefaults} LLM default{llmDefaults > 1 ? 's' : ''}</span>
						{/if}
					</div>
					<div class="flex w-[8.5rem] shrink-0 items-center justify-end gap-0.5">
						<div class="row-actions-more flex items-center gap-0.5">
							<Button
								variant="ghost"
								size="sm"
								class="size-8 p-0 text-muted-foreground hover:text-foreground"
								title="Version history"
								aria-label="Version history of {p.name}"
								aria-expanded={historySlug === p.slug}
								onclick={() => toggleHistory(p)}
							>
								<History />
							</Button>
							<Button variant="ghost" size="sm" class="size-8 p-0 text-muted-foreground hover:text-foreground" title="Duplicate as a new profile" aria-label="Duplicate {p.name}" onclick={() => onDuplicate(p)}>
								<Copy />
							</Button>
							{#if !p.is_system}
								<Button
									variant="ghost"
									size="sm"
									class="size-8 p-0 text-muted-foreground hover:bg-destructive/10 hover:text-danger-fg"
									title="Archive all versions (jobs keep their snapshots)"
									aria-label="Archive {p.name}"
									onclick={() => onArchive(p)}
								>
									<Archive />
								</Button>
							{/if}
						</div>
						{#if !p.is_system || canEditSystem}
							<Button variant="ghost" size="sm" class="h-8 px-2" title="Edit as a new version — versions are immutable" onclick={() => onEdit(p)}>
								<Pencil /> Edit
							</Button>
						{:else}
							<Button variant="ghost" size="sm" class="h-8 px-2" title="System profiles are read-only — duplicate to change" onclick={() => onDuplicate(p)}>
								<Copy /> Copy
							</Button>
						{/if}
					</div>
				</div>

				{#if historySlug === p.slug}
					<div class="mt-3 rounded-md border border-border bg-muted/20 px-3 py-2">
						{#if historyLoading}
							<LoadingState label="loading versions" class="py-2" />
						{:else}
							<ol class="space-y-1">
								{#each historyVersions as v, i (v.id)}
									<li class="flex items-baseline gap-3 text-xs">
										<span class="shrink-0 font-mono text-foreground">v{v.version}</span>
										<span class="text-muted-foreground">{refsDelta(v, historyVersions[i + 1])}</span>
										<time class="ml-auto shrink-0 font-mono text-[11px] text-muted-foreground" datetime={v.created_at}>{new Date(v.created_at).toLocaleDateString()}</time>
									</li>
								{/each}
							</ol>
						{/if}
					</div>
				{/if}
			</div>
		{/each}
	</div>
{/if}
