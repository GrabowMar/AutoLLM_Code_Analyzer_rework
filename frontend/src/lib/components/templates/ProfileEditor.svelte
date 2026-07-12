<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import { untrack } from 'svelte';
	import LLMParamsEditor from '$lib/components/sample-generator/LLMParamsEditor.svelte';
	import { slugify } from '$lib/components/templates/slugify';
	import {
		previewDraftProfile,
		type AppRequirementTemplate,
		type BlockRef,
		type ContentBlock,
		type GenerationProfile,
		type LLMParams,
		type ProfilePreview,
		type ProfileWritePayload,
		type Stack,
	} from '$lib/api/client';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Plus from '@lucide/svelte/icons/plus';
	import X from '@lucide/svelte/icons/x';
	import ArrowUp from '@lucide/svelte/icons/arrow-up';
	import ArrowDown from '@lucide/svelte/icons/arrow-down';
	import Eye from '@lucide/svelte/icons/eye';

	interface Props {
		/** Existing profile to edit (creates version+1) or null for a new profile. */
		initial: GenerationProfile | null;
		/** Duplicate mode: prefill from initial but save as a brand-new slug. */
		duplicate?: boolean;
		blocks: ContentBlock[];
		stacks: Stack[];
		appTemplates: AppRequirementTemplate[];
		saving: boolean;
		error: string;
		onSave: (payload: ProfileWritePayload, isNewSlug: boolean) => void;
		onCancel: () => void;
	}
	let { initial, duplicate = false, blocks, stacks, appTemplates, saving, error, onSave, onCancel }: Props = $props();

	// Snapshot the props once — the parent remounts this editor via {#key}
	// whenever a different profile is opened.
	const seed = untrack(() => initial);
	const isNewSlug = seed === null || untrack(() => duplicate);

	let name = $state(seed ? (isNewSlug ? `${seed.name} (copy)` : seed.name) : '');
	let slug = $state(seed && !isNewSlug ? seed.slug : '');
	let slugTouched = $state(false);
	let description = $state(seed?.description ?? '');
	let scaffoldingSlug = $state(seed?.scaffolding_slug ?? 'flask-react');
	let blockRefs = $state<BlockRef[]>(structuredClone(seed?.block_refs ?? []) as BlockRef[]);
	let llmConfig = $state<LLMParams>({ ...(seed?.llm_config ?? {}) });
	let isDefault = $state(seed?.is_default ?? false);

	$effect(() => {
		if (isNewSlug && !slugTouched) slug = slugify(name);
	});

	// ── Block composer ─────────────────────────────────────────────
	let blockFilter = $state('');
	let blockTypeFilter = $state('');

	const blockTypes = $derived([...new Set(blocks.map((b) => b.block_type))].sort());

	// Latest version per (type, slug), filtered by search/type.
	const availableBlocks = $derived.by(() => {
		const latest = new Map<string, ContentBlock>();
		for (const b of blocks) {
			const key = `${b.block_type}:${b.slug}`;
			if (!latest.has(key) || latest.get(key)!.version < b.version) latest.set(key, b);
		}
		return [...latest.values()]
			.filter((b) => !blockTypeFilter || b.block_type === blockTypeFilter)
			.filter((b) => !blockFilter || b.slug.includes(blockFilter.toLowerCase()) || b.name.toLowerCase().includes(blockFilter.toLowerCase()))
			.sort((a, b) => a.block_type.localeCompare(b.block_type) || a.slug.localeCompare(b.slug));
	});

	function refKey(r: BlockRef): string {
		return `${r.type}:${r.slug}`;
	}

	const usedKeys = $derived(new Set(blockRefs.map(refKey)));

	function blockContent(ref: BlockRef): ContentBlock | undefined {
		return blocks.find((b) => b.block_type === ref.type && b.slug === ref.slug && b.version === ref.version);
	}

	function latestVersionOf(ref: BlockRef): number {
		return Math.max(...blocks.filter((b) => b.block_type === ref.type && b.slug === ref.slug).map((b) => b.version), ref.version);
	}

	function addBlock(b: ContentBlock) {
		blockRefs = [...blockRefs, { type: b.block_type, slug: b.slug, version: b.version }];
	}

	function removeRef(index: number) {
		blockRefs = blockRefs.filter((_, i) => i !== index);
	}

	function moveRef(index: number, delta: number) {
		const target = index + delta;
		if (target < 0 || target >= blockRefs.length) return;
		const next = [...blockRefs];
		[next[index], next[target]] = [next[target], next[index]];
		blockRefs = next;
	}

	function setRefVersion(index: number, version: number) {
		const next = [...blockRefs];
		next[index] = { ...next[index], version };
		blockRefs = next;
	}

	let expandedRef = $state<number | null>(null);

	// ── Live preview ───────────────────────────────────────────────
	let previewOpen = $state(false);
	let previewAppSlug = $state('');
	let preview = $state<ProfilePreview | null>(null);
	let previewLoading = $state(false);
	let previewError = $state('');
	let previewStage = $state<'backend' | 'frontend'>('backend');
	let previewTimer: ReturnType<typeof setTimeout> | undefined;

	function schedulePreview() {
		clearTimeout(previewTimer);
		previewTimer = setTimeout(loadPreview, 400);
	}

	async function loadPreview() {
		previewLoading = true;
		previewError = '';
		try {
			preview = await previewDraftProfile({
				block_refs: $state.snapshot(blockRefs) as BlockRef[],
				app_slug: previewAppSlug || undefined,
				llm_config: $state.snapshot(llmConfig) as LLMParams,
			});
		} catch (e: any) {
			previewError = e?.detail ?? e?.message ?? 'Preview failed';
			preview = null;
		}
		previewLoading = false;
	}

	$effect(() => {
		if (!previewOpen) return;
		// touch the reactive deps so edits re-trigger the debounce
		void blockRefs;
		void previewAppSlug;
		void llmConfig;
		schedulePreview();
	});

	function submit() {
		onSave(
			{
				name,
				slug,
				description,
				scaffolding_slug: scaffoldingSlug,
				block_refs: $state.snapshot(blockRefs) as BlockRef[],
				llm_config: $state.snapshot(llmConfig) as LLMParams,
				is_default: isDefault,
			},
			isNewSlug,
		);
	}

	const previewText = $derived.by(() => {
		if (!preview) return null;
		const source = preview.rendered ?? preview.prompt_templates;
		return source?.[previewStage] ?? null;
	});
</script>

<Card.Root>
	<Card.Header class="pb-3">
		<Card.Title class="text-base">
			{#if initial === null}New Profile{:else if duplicate}Duplicate {initial.name}{:else}Edit {initial.name} <span class="text-xs font-normal text-muted-foreground">creates v{initial.version + 1} — versions are immutable</span>{/if}
		</Card.Title>
	</Card.Header>
	<Card.Content class="space-y-4">
		<div class="grid gap-3 sm:grid-cols-2">
			<div class="space-y-1.5">
				<Label class="text-xs" for="profile-name">Name</Label>
				<Input id="profile-name" bind:value={name} placeholder="e.g. Terse JSON-strict" class="h-9 text-sm" />
			</div>
			<div class="space-y-1.5">
				<Label class="text-xs" for="profile-slug">Slug</Label>
				<Input
					id="profile-slug"
					bind:value={slug}
					disabled={!isNewSlug}
					oninput={() => (slugTouched = true)}
					class="h-9 text-sm font-mono"
				/>
			</div>
		</div>
		<div class="space-y-1.5">
			<Label class="text-xs" for="profile-description">Description</Label>
			<Textarea id="profile-description" bind:value={description} rows={2} class="text-sm" placeholder="What this profile is for" />
		</div>
		<div class="grid gap-3 sm:grid-cols-2">
			<div class="space-y-1.5">
				<Label class="text-xs" for="profile-stack">Stack</Label>
				<select
					id="profile-stack"
					bind:value={scaffoldingSlug}
					class="flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs transition-all hover:border-primary/40 focus-visible:outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30"
				>
					{#each stacks as stack}
						<option value={stack.slug}>{stack.slug}{stack.has_frontend ? '' : ' (backend only)'}</option>
					{/each}
				</select>
				<p class="text-[10px] text-muted-foreground">Jobs using this profile run on this stack.</p>
			</div>
			<label class="flex items-center gap-2 self-end pb-1 text-xs cursor-pointer">
				<input type="checkbox" bind:checked={isDefault} class="accent-primary" />
				Use as my default profile
			</label>
		</div>

		<!-- Block composer -->
		<div class="space-y-2">
			<Label class="text-xs">Blocks <span class="font-normal text-muted-foreground">(ordered — prompt stages first, then tone/rules appended to the system prompt)</span></Label>

			{#if blockRefs.length === 0}
				<p class="rounded-md border border-dashed p-3 text-xs text-muted-foreground">No blocks yet — add prompt stages below, or leave empty to inherit the catalog defaults at run time.</p>
			{:else}
				<div class="space-y-1">
					{#each blockRefs as ref, i (refKey(ref) + i)}
						{@const latest = latestVersionOf(ref)}
						<div class="rounded border bg-card">
							<div class="flex items-center gap-2 px-2 py-1.5">
								<Badge variant="outline" class="text-[9px] px-1 py-0 shrink-0">{ref.type}</Badge>
								<button
									type="button"
									class="text-xs font-mono truncate hover:text-primary transition-colors cursor-pointer"
									title="Show content"
									onclick={() => (expandedRef = expandedRef === i ? null : i)}
								>{ref.slug}</button>
								<select
									value={ref.version}
									onchange={(e) => setRefVersion(i, parseInt(e.currentTarget.value, 10))}
									class="ml-auto h-6 rounded border border-input bg-surface-1 px-1 text-[10px] font-mono shrink-0"
									title="Pinned version"
								>
									{#each blocks.filter((b) => b.block_type === ref.type && b.slug === ref.slug) as v}
										<option value={v.version}>v{v.version}{v.version === latest ? ' (latest)' : ''}</option>
									{/each}
								</select>
								{#if ref.version < latest}
									<Badge variant="outline" class="text-[9px] px-1 py-0 text-amber-500 border-amber-500/30 shrink-0">outdated</Badge>
								{/if}
								<div class="flex shrink-0 gap-0.5">
									<button type="button" class="p-1 text-muted-foreground hover:text-foreground cursor-pointer disabled:opacity-30" disabled={i === 0} onclick={() => moveRef(i, -1)} aria-label="Move up"><ArrowUp class="h-3 w-3" /></button>
									<button type="button" class="p-1 text-muted-foreground hover:text-foreground cursor-pointer disabled:opacity-30" disabled={i === blockRefs.length - 1} onclick={() => moveRef(i, 1)} aria-label="Move down"><ArrowDown class="h-3 w-3" /></button>
									<button type="button" class="p-1 text-muted-foreground hover:text-destructive cursor-pointer" onclick={() => removeRef(i)} aria-label="Remove"><X class="h-3 w-3" /></button>
								</div>
							</div>
							{#if expandedRef === i}
								{@const content = blockContent(ref)}
								<pre class="max-h-48 overflow-auto border-t bg-muted/20 p-2 text-[10px] leading-relaxed whitespace-pre-wrap">{content?.content ?? '(block content not loaded)'}</pre>
							{/if}
						</div>
					{/each}
				</div>
			{/if}

			<!-- Add from catalog -->
			<div class="rounded-md border bg-muted/20 p-2 space-y-2">
				<div class="flex flex-wrap gap-2">
					<select
						bind:value={blockTypeFilter}
						class="h-7 rounded border border-input bg-surface-1 px-2 text-[11px]"
					>
						<option value="">all types</option>
						{#each blockTypes as t}<option value={t}>{t}</option>{/each}
					</select>
					<input
						type="text"
						bind:value={blockFilter}
						placeholder="filter blocks…"
						class="h-7 flex-1 min-w-32 rounded border border-input bg-surface-1 px-2 text-[11px]"
					/>
				</div>
				<div class="flex max-h-36 flex-wrap gap-1.5 overflow-y-auto">
					{#each availableBlocks as b (b.id)}
						<button
							type="button"
							class="inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[10px] transition-colors cursor-pointer {usedKeys.has(`${b.block_type}:${b.slug}`) ? 'opacity-40 cursor-not-allowed' : 'hover:border-primary/40 hover:text-foreground text-muted-foreground'}"
							disabled={usedKeys.has(`${b.block_type}:${b.slug}`)}
							title={b.description || b.name}
							onclick={() => addBlock(b)}
						>
							<Plus class="h-2.5 w-2.5" />
							<span class="font-mono">{b.slug}</span>
							<span class="opacity-60">{b.block_type}</span>
						</button>
					{/each}
				</div>
			</div>
		</div>

		<!-- LLM defaults -->
		<div class="space-y-1.5">
			<Label class="text-xs">LLM defaults <span class="font-normal text-muted-foreground">(runs can override per launch)</span></Label>
			<LLMParamsEditor bind:params={llmConfig} idPrefix="profile-llm" />
		</div>

		<!-- Live preview -->
		<div class="space-y-2">
			<button
				type="button"
				class="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
				onclick={() => (previewOpen = !previewOpen)}
			>
				<Eye class="h-3.5 w-3.5" />
				{previewOpen ? 'Hide preview' : 'Preview assembled prompts'}
			</button>
			{#if previewOpen}
				<div class="rounded-md border bg-muted/20 p-3 space-y-2">
					<div class="flex flex-wrap items-center gap-2">
						<select
							bind:value={previewAppSlug}
							class="h-7 rounded border border-input bg-surface-1 px-2 text-[11px]"
						>
							<option value="">raw templates (no app)</option>
							{#each appTemplates as app}
								<option value={app.slug}>rendered for: {app.name}</option>
							{/each}
						</select>
						<div class="flex items-center gap-1 rounded border bg-muted/40 p-0.5">
							{#each ['backend', 'frontend'] as stage}
								<button
									type="button"
									class="rounded px-2 py-0.5 text-[10px] font-medium transition-colors cursor-pointer {previewStage === stage ? 'bg-background text-foreground shadow-xs' : 'text-muted-foreground hover:text-foreground'}"
									onclick={() => (previewStage = stage as 'backend' | 'frontend')}
								>{stage}</button>
							{/each}
						</div>
						{#if previewLoading}<LoaderCircle class="h-3.5 w-3.5 animate-spin text-muted-foreground" />{/if}
					</div>
					{#if previewError}
						<p class="text-[10px] text-destructive">{previewError}</p>
					{:else if previewText}
						<div class="space-y-2">
							<div>
								<p class="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground mb-1">System ({(previewText.system ?? '').length.toLocaleString()} chars)</p>
								<pre class="max-h-40 overflow-auto rounded border bg-card p-2 text-[10px] leading-relaxed whitespace-pre-wrap">{previewText.system || '(empty — inherits at run time)'}</pre>
							</div>
							<div>
								<p class="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground mb-1">User ({(previewText.user ?? '').length.toLocaleString()} chars)</p>
								<pre class="max-h-40 overflow-auto rounded border bg-card p-2 text-[10px] leading-relaxed whitespace-pre-wrap">{previewText.user || '(empty — inherits at run time)'}</pre>
							</div>
						</div>
					{/if}
				</div>
			{/if}
		</div>

		{#if error}
			<div class="rounded-md bg-destructive/10 border border-destructive/30 px-4 py-3 text-sm text-destructive">{error}</div>
		{/if}

		<div class="flex items-center justify-end gap-2 border-t pt-4">
			<Button variant="outline" size="sm" onclick={onCancel} disabled={saving}>Cancel</Button>
			<Button size="sm" onclick={submit} disabled={saving || !name.trim() || !slug.trim()}>
				{#if saving}<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin" />{/if}
				{initial === null || duplicate ? 'Create profile' : `Save as v${initial.version + 1}`}
			</Button>
		</div>
	</Card.Content>
</Card.Root>
