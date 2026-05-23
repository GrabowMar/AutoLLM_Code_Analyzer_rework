<script lang="ts">
	import { Badge } from '$lib/components/ui/badge';
	import { Label } from '$lib/components/ui/label';
	import type { TemplateBundle } from '$lib/api/client';
	import { getBundlePreview } from '$lib/api/generation';
	import Package from '@lucide/svelte/icons/package';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	interface Props {
		bundles: TemplateBundle[];
		loading?: boolean;
		selectedId: number | '';
		appSlugs?: string[];
	}

	let { bundles, loading = false, selectedId = $bindable('' as number | ''), appSlugs = [] }: Props = $props();

	let previewLoading = $state(false);
	let previewError = $state('');
	let previewStages = $state<Record<string, { system: number; user: number }> | null>(null);

	const suggestedBundle = $derived.by(() => {
		if (appSlugs.length !== 1) return null;
		const dash = appSlugs[0].replace(/_/g, '-');
		return bundles.find((b) => b.slug === `app-${dash}`) ?? null;
	});

	$effect(() => {
		if (suggestedBundle && selectedId === '' && bundles.length > 0) {
			selectedId = suggestedBundle.id;
		}
	});

	const selected = $derived(bundles.find((b) => b.id === selectedId));

	async function loadPreview(slug: string) {
		previewLoading = true;
		previewError = '';
		previewStages = null;
		try {
			const data = await getBundlePreview(slug);
			const templates = data.prompt_templates as Record<string, { system?: string; user?: string }>;
			previewStages = {
				backend: {
					system: templates.backend?.system?.length ?? 0,
					user: templates.backend?.user?.length ?? 0,
				},
				frontend: {
					system: templates.frontend?.system?.length ?? 0,
					user: templates.frontend?.user?.length ?? 0,
				},
			};
		} catch (e: unknown) {
			previewError = e instanceof Error ? e.message : 'Preview failed';
		} finally {
			previewLoading = false;
		}
	}

	$effect(() => {
		if (selected?.slug) {
			loadPreview(selected.slug);
		} else {
			previewStages = null;
		}
	});
</script>

<div class="space-y-2">
	<div class="flex items-center justify-between gap-2">
		<Label>Prompt bundle</Label>
		{#if suggestedBundle}
			<span class="text-[10px] text-muted-foreground">
				Suggested for {appSlugs[0]}: <span class="font-medium text-foreground">{suggestedBundle.name}</span>
			</span>
		{/if}
	</div>
	{#if loading}
		<div class="flex h-9 items-center gap-2 rounded-md border px-3 text-sm text-muted-foreground">
			<LoaderCircle class="h-3.5 w-3.5 animate-spin" /> Loading bundles…
		</div>
	{:else}
		<div class="grid gap-2 sm:grid-cols-2">
			{#each bundles as bundle}
				<button
					type="button"
					class="rounded-lg border p-3 text-left transition-colors {selectedId === bundle.id
						? 'ring-2 ring-primary bg-primary/5'
						: 'hover:bg-muted/50'}"
					onclick={() => (selectedId = bundle.id)}
				>
					<div class="flex items-center gap-2">
						<Package class="h-4 w-4 shrink-0 text-muted-foreground" />
						<span class="text-sm font-medium">{bundle.name}</span>
						{#if bundle.is_default}
							<Badge variant="secondary" class="text-[10px]">Default</Badge>
						{/if}
					</div>
					<p class="mt-1 line-clamp-2 text-xs text-muted-foreground">{bundle.description || bundle.slug}</p>
					<p class="mt-1 font-mono text-[10px] text-muted-foreground">
						{bundle.block_refs?.length ?? 0} blocks · {bundle.scaffolding_slug}
					</p>
				</button>
			{/each}
		</div>
		{#if bundles.length === 0}
			<p class="text-sm text-muted-foreground">No bundles found. Run seed_generation_templates.</p>
		{/if}
		{#if selected && previewStages}
			<div class="rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
				Prompt sizes (chars): backend sys {previewStages.backend.system} / user {previewStages.backend.user};
				frontend sys {previewStages.frontend.system} / user {previewStages.frontend.user}
			</div>
		{:else if previewLoading}
			<p class="text-xs text-muted-foreground">Loading preview…</p>
		{:else if previewError}
			<p class="text-xs text-red-400">{previewError}</p>
		{/if}
	{/if}
</div>
