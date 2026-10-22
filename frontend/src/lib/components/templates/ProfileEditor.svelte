<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Segmented } from '$lib/components/ui/segmented';
	import { Textarea } from '$lib/components/ui/textarea';
	import { untrack } from 'svelte';
	import { LLMParamsEditor, StackSelect } from '$lib/components/generator';
	import { slugify } from '$lib/components/templates/slugify';
	import { formatApiError } from '$lib/api/core';
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
	} from '$lib/api/generation';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Plus from '@lucide/svelte/icons/plus';
	import X from '@lucide/svelte/icons/x';
	import ArrowUp from '@lucide/svelte/icons/arrow-up';
	import ArrowDown from '@lucide/svelte/icons/arrow-down';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { cn } from '$lib/utils';

	interface Props {
		/** Existing profile to edit (creates version+1) or null for a new profile. */
		initial: GenerationProfile | null;
		/** Duplicate mode: prefill from initial but save as a brand-new slug. */
		duplicate?: boolean;
		blocks: ContentBlock[];
		stacks: Stack[];
		/** Stack preselected for a new profile (the platform default). */
		defaultStackSlug?: string;
		appTemplates: AppRequirementTemplate[];
		saving: boolean;
		error: string;
		onSave: (payload: ProfileWritePayload, isNewSlug: boolean) => void;
		onCancel: () => void;
	}
	let {
		initial,
		duplicate = false,
		blocks,
		stacks,
		defaultStackSlug,
		appTemplates,
		saving,
		error,
		onSave,
		onCancel,
	}: Props = $props();

	// Snapshot the props once — the parent remounts this editor via {#key}
	// whenever a different profile is opened.
	const seed = untrack(() => initial);
	const isNewSlug = seed === null || untrack(() => duplicate);

	let name = $state(seed ? (isNewSlug ? `${seed.name} (copy)` : seed.name) : '');
	let slugInput = $state(seed && !isNewSlug ? seed.slug : '');
	let slugTouched = $state(false);
	const slug = $derived(isNewSlug && !slugTouched ? slugify(name) : slugInput);
	let description = $state(seed?.description ?? '');
	let scaffoldingSlug = $state(seed?.scaffolding_slug ?? '');
	// $state.snapshot, not structuredClone: the profile arrives as a $state proxy.
	let blockRefs = $state<BlockRef[]>($state.snapshot(seed?.block_refs ?? []) as BlockRef[]);
	let llmConfig = $state<LLMParams>($state.snapshot(seed?.llm_config ?? {}) as LLMParams);
	let llmInvalid = $state(false);
	let isDefault = $state(seed?.is_default ?? false);

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
		const q = blockFilter.trim().toLowerCase();
		return [...latest.values()]
			.filter((b) => !blockTypeFilter || b.block_type === blockTypeFilter)
			.filter((b) => !q || b.slug.includes(q) || b.name.toLowerCase().includes(q))
			.sort((a, b) => a.block_type.localeCompare(b.block_type) || a.slug.localeCompare(b.slug));
	});

	function refKey(r: BlockRef): string {
		return `${r.type}:${r.slug}`;
	}

	const usedKeys = $derived(new Set(blockRefs.map(refKey)));

	function blockContent(ref: BlockRef): ContentBlock | undefined {
		return blocks.find((b) => b.block_type === ref.type && b.slug === ref.slug && b.version === ref.version);
	}

	function versionsOf(ref: BlockRef): ContentBlock[] {
		return blocks
			.filter((b) => b.block_type === ref.type && b.slug === ref.slug)
			.sort((a, b) => b.version - a.version);
	}

	function latestVersionOf(ref: BlockRef): number {
		return Math.max(...versionsOf(ref).map((b) => b.version), ref.version);
	}

	function addBlock(b: ContentBlock) {
		blockRefs = [...blockRefs, { type: b.block_type, slug: b.slug, version: b.version }];
	}

	function removeRef(index: number) {
		blockRefs = blockRefs.filter((_, i) => i !== index);
		if (expandedRef === index) expandedRef = null;
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
	let previewStage = $state('backend');

	// Debounced; a newer edit supersedes an in-flight request.
	$effect(() => {
		if (!previewOpen) return;
		const body = {
			block_refs: $state.snapshot(blockRefs) as BlockRef[],
			app_slug: previewAppSlug || undefined,
			llm_config: $state.snapshot(llmConfig) as LLMParams,
		};
		let cancelled = false;
		previewLoading = true;
		const timer = setTimeout(() => {
			previewDraftProfile(body)
				.then((result) => {
					if (cancelled) return;
					preview = result;
					previewError = '';
				})
				.catch((e: unknown) => {
					if (cancelled) return;
					preview = null;
					previewError = formatApiError(e, 'Preview failed.');
				})
				.finally(() => {
					if (!cancelled) previewLoading = false;
				});
		}, 400);
		return () => {
			cancelled = true;
			clearTimeout(timer);
		};
	});

	const previewStages = $derived(Object.keys(preview?.rendered ?? preview?.prompt_templates ?? {}));
	const activeStage = $derived(previewStages.includes(previewStage) ? previewStage : (previewStages[0] ?? ''));
	const previewText = $derived.by(() => {
		if (!preview || !activeStage) return null;
		const source = preview.rendered ?? preview.prompt_templates;
		return source?.[activeStage] ?? null;
	});

	const canSave = $derived(!saving && !!name.trim() && !!slug.trim() && !!scaffoldingSlug && !llmInvalid);

	function submit() {
		if (!canSave) return;
		onSave(
			{
				name: name.trim(),
				slug: slug.trim(),
				description,
				scaffolding_slug: scaffoldingSlug,
				block_refs: $state.snapshot(blockRefs) as BlockRef[],
				llm_config: $state.snapshot(llmConfig) as LLMParams,
				is_default: isDefault,
			},
			isNewSlug,
		);
	}

	const blockTypeItems = $derived(blockTypes.map((t) => ({ value: t, label: t })));
	const previewAppItems = $derived(appTemplates.map((app) => ({ value: app.slug, label: `Rendered for: ${app.name}` })));
</script>

<Card.Root>
	<Card.Header class="border-b">
		<div class="flex items-start justify-between gap-2">
			<div class="min-w-0 space-y-1">
				<Card.Title>
					{#if initial === null}New profile{:else if duplicate}Duplicate {initial.name}{:else}Edit {initial.name}{/if}
				</Card.Title>
				<Card.Description>
					{#if initial !== null && !duplicate}
						Saving creates v{initial.version + 1}; versions are immutable.
					{:else}
						Prompt material for template scaffolding: ordered prompt blocks, the stack they target, and LLM defaults.
					{/if}
				</Card.Description>
			</div>
			<Button variant="ghost" size="icon" class="size-8 shrink-0" aria-label="Close editor" onclick={onCancel}>
				<X />
			</Button>
		</div>
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
					value={slug}
					disabled={!isNewSlug}
					oninput={(e: Event) => {
						slugTouched = true;
						slugInput = (e.currentTarget as HTMLInputElement).value;
					}}
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
				<StackSelect
					bind:value={scaffoldingSlug}
					{stacks}
					defaultSlug={defaultStackSlug}
					id="profile-stack"
					label="Stack"
				/>
				<p class="text-[11px] text-muted-foreground">Scaffolding runs with this profile must use this stack.</p>
			</div>
			<label class="flex cursor-pointer items-center gap-2 self-start pt-0 text-sm sm:pt-7">
				<Checkbox bind:checked={isDefault} />
				Use as my default profile for this stack
			</label>
		</div>

		<!-- Block composer -->
		<div class="space-y-2">
			<p class="text-xs font-medium">
				Blocks <span class="font-normal text-muted-foreground">(ordered — prompt stages first, then tone/rules appended to the system prompt)</span>
			</p>

			{#if blockRefs.length === 0}
				<p class="rounded-md border border-dashed p-3 text-xs text-muted-foreground">No blocks yet — add prompt stages below, or leave empty to inherit the catalog defaults at run time.</p>
			{:else}
				<ol class="space-y-1">
					{#each blockRefs as ref, i (refKey(ref) + i)}
						{@const latest = latestVersionOf(ref)}
						<li class="rounded border bg-card">
							<div class="flex flex-wrap items-center gap-2 px-2 py-1.5">
								<Badge variant="outline" class="shrink-0">{ref.type}</Badge>
								<button
									type="button"
									class="min-w-0 truncate rounded text-xs font-mono transition-colors hover:text-primary cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
									title="Show content"
									aria-expanded={expandedRef === i}
									onclick={() => (expandedRef = expandedRef === i ? null : i)}
								>{ref.slug}</button>
								<div class="ml-auto flex shrink-0 items-center gap-1">
									{#if ref.version < latest}
										<StatusBadge status="outdated" tone="warning" />
									{/if}
									<Select.Simple
										size="sm"
										class="h-7 w-auto min-w-[6.5rem] font-mono text-[11px]"
										aria-label="Pinned version of {ref.slug}"
										value={String(ref.version)}
										items={versionsOf(ref).map((v) => ({
											value: String(v.version),
											label: `v${v.version}${v.version === latest ? ' (latest)' : ''}`,
										}))}
										onValueChange={(v) => setRefVersion(i, Number(v))}
									/>
									<button type="button" class="rounded p-1 text-muted-foreground hover:text-foreground cursor-pointer disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40" disabled={i === 0} onclick={() => moveRef(i, -1)} aria-label="Move {ref.slug} up"><ArrowUp class="h-3.5 w-3.5" /></button>
									<button type="button" class="rounded p-1 text-muted-foreground hover:text-foreground cursor-pointer disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40" disabled={i === blockRefs.length - 1} onclick={() => moveRef(i, 1)} aria-label="Move {ref.slug} down"><ArrowDown class="h-3.5 w-3.5" /></button>
									<button type="button" class="rounded p-1 text-muted-foreground hover:text-danger-fg cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40" onclick={() => removeRef(i)} aria-label="Remove {ref.slug}"><X class="h-3.5 w-3.5" /></button>
								</div>
							</div>
							{#if expandedRef === i}
								{@const content = blockContent(ref)}
								<pre class="max-h-48 overflow-auto border-t bg-muted/20 p-2 text-[11px] font-mono leading-relaxed whitespace-pre-wrap">{content?.content ?? '(block content not loaded)'}</pre>
							{/if}
						</li>
					{/each}
				</ol>
			{/if}

			<!-- Add from catalog -->
			<div class="space-y-2 rounded-md border bg-muted/20 p-2">
				<div class="flex flex-wrap gap-2">
					<Select.Simple
						size="sm"
						class="w-auto min-w-[9rem]"
						aria-label="Filter by block type"
						bind:value={blockTypeFilter}
						items={blockTypeItems}
						placeholder="All types"
						clearLabel="All types"
					/>
					<Input
						type="text"
						bind:value={blockFilter}
						placeholder="Filter blocks…"
						aria-label="Filter blocks"
						class="h-8 min-w-32 flex-1 text-xs"
					/>
				</div>
				<div class="flex max-h-36 flex-wrap gap-1.5 overflow-y-auto">
					{#each availableBlocks as b (b.id)}
						{@const used = usedKeys.has(`${b.block_type}:${b.slug}`)}
						<button
							type="button"
							class="inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[11px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 {used ? 'cursor-not-allowed opacity-40' : 'cursor-pointer text-muted-foreground hover:border-primary/40 hover:text-foreground'}"
							disabled={used}
							title={b.description || b.name}
							onclick={() => addBlock(b)}
						>
							<Plus class="h-3 w-3" aria-hidden="true" />
							<span class="font-mono">{b.slug}</span>
							<span class="opacity-70">{b.block_type}</span>
						</button>
					{:else}
						<p class="text-[11px] text-muted-foreground">No blocks match the filter.</p>
					{/each}
				</div>
			</div>
		</div>

		<!-- LLM defaults -->
		<div class="space-y-1.5">
			<p class="text-xs font-medium">LLM defaults</p>
			<LLMParamsEditor
				bind:params={llmConfig}
				mode="profile"
				idPrefix="profile-llm"
				bind:invalid={llmInvalid}
				hint="The lowest settings layer for runs that use this profile: sampling presets and per-run overrides win over these."
			/>
		</div>

		<!-- Live preview -->
		<div class="space-y-2">
			<button
				type="button"
				class="flex cursor-pointer items-center gap-1 rounded text-xs font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
				aria-expanded={previewOpen}
				aria-controls="profile-preview"
				onclick={() => (previewOpen = !previewOpen)}
			>
				<ChevronRight class={cn('size-3.5 transition-transform duration-150', previewOpen && 'rotate-90')} aria-hidden="true" />
				Preview assembled prompts
			</button>
			{#if previewOpen}
				<div id="profile-preview" class="space-y-2 rounded-md border bg-muted/20 p-3">
					<div class="flex flex-wrap items-center gap-2">
						<Select.Simple
							size="sm"
							class="w-auto min-w-[12rem] max-w-full"
							aria-label="App to render the preview for"
							bind:value={previewAppSlug}
							items={previewAppItems}
							placeholder="Raw templates (no app)"
							clearLabel="Raw templates (no app)"
						/>
						{#if previewStages.length > 1}
							<Segmented
								size="sm"
								aria-label="Preview stage"
								value={activeStage}
								items={previewStages.map((stage) => ({ value: stage, label: stage }))}
								onValueChange={(v) => (previewStage = v)}
							/>
						{/if}
						{#if previewLoading}
							<LoaderCircle class="h-3.5 w-3.5 animate-spin text-muted-foreground motion-reduce:animate-none" aria-label="Rendering" />
						{/if}
					</div>
					{#if previewError}
						<p class="text-xs text-danger-fg">{previewError}</p>
					{:else if previewText}
						<div class="space-y-2">
							<div>
								<p class="section-label mb-1">System ({(previewText.system ?? '').length.toLocaleString()} chars)</p>
								<pre class="max-h-40 overflow-auto rounded border bg-card p-2 text-[11px] font-mono leading-relaxed whitespace-pre-wrap">{previewText.system || '(empty — inherits at run time)'}</pre>
							</div>
							<div>
								<p class="section-label mb-1">User ({(previewText.user ?? '').length.toLocaleString()} chars)</p>
								<pre class="max-h-40 overflow-auto rounded border bg-card p-2 text-[11px] font-mono leading-relaxed whitespace-pre-wrap">{previewText.user || '(empty — inherits at run time)'}</pre>
							</div>
						</div>
					{:else if preview && previewStages.length === 0}
						<p class="text-xs text-muted-foreground">No prompt stages yet; runs fall back to the catalog defaults.</p>
					{/if}
				</div>
			{/if}
		</div>

		{#if error}
			<ErrorAlert message={error} />
		{/if}

		<div class="flex items-center justify-end gap-2 border-t pt-4">
			<Button variant="outline" size="sm" onclick={onCancel} disabled={saving}>Cancel</Button>
			<Button size="sm" onclick={submit} disabled={!canSave}>
				{#if saving}<LoaderCircle class="animate-spin" />{/if}
				{initial === null || duplicate ? 'Create profile' : `Save as v${initial.version + 1}`}
			</Button>
		</div>
	</Card.Content>
</Card.Root>
