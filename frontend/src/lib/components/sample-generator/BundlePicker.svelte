<script lang="ts">
	import { Badge } from '$lib/components/ui/badge';
	import type { TemplateBundle } from '$lib/api/client';
	import { getBundlePreview } from '$lib/api/generation';
	import Check from '@lucide/svelte/icons/check';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	interface Props {
		bundles: TemplateBundle[];
		loading?: boolean;
		selectedId: number | '';
		appSlugs?: string[];
	}

	let { bundles, loading = false, selectedId = $bindable('' as number | ''), appSlugs = [] }: Props = $props();

	let open = $state(false);
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
		if (open && selected?.slug) {
			loadPreview(selected.slug);
		} else if (!open) {
			previewStages = null;
		}
	});

	function select(id: number | '') {
		selectedId = id;
		open = false;
	}
</script>

<div class="space-y-1">
	<!-- Compact trigger row -->
	<div class="flex items-center gap-2 rounded-md border bg-muted/20 px-3 py-2">
		<span class="text-xs font-medium text-muted-foreground shrink-0">Bundle</span>
		{#if loading}
			<LoaderCircle class="h-3 w-3 animate-spin text-muted-foreground" />
		{:else if selected}
			<span class="text-sm font-medium truncate">{selected.name}</span>
			{#if selected.is_default}
				<Badge variant="secondary" class="text-[9px] shrink-0">Default</Badge>
			{/if}
			{#if suggestedBundle?.id === selected.id}
				<Badge variant="outline" class="text-[9px] text-primary border-primary/30 shrink-0">Suggested</Badge>
			{/if}
			<span class="text-[10px] text-muted-foreground shrink-0">{selected.block_refs?.length ?? 0} blocks</span>
		{:else}
			<span class="text-sm text-muted-foreground">Using default</span>
		{/if}

		{#if suggestedBundle && suggestedBundle.id !== selectedId}
			<button
				type="button"
				class="ml-1 text-[10px] text-primary hover:underline shrink-0 cursor-pointer"
				onclick={() => select(suggestedBundle!.id)}
			>Use suggested</button>
		{/if}

		<button
			type="button"
			class="ml-auto flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors shrink-0 cursor-pointer"
			onclick={() => (open = !open)}
		>
			Change <ChevronDown class="h-3 w-3 transition-transform duration-150 {open ? 'rotate-180' : ''}" />
		</button>
	</div>

	<!-- Expanded picker -->
	{#if open}
		<div class="rounded-md border bg-popover shadow-sm overflow-hidden">
			<!-- "No override" option -->
			<button
				type="button"
				class="flex w-full items-center gap-2 border-b px-3 py-2 text-left text-xs hover:bg-muted/50 transition-colors cursor-pointer {selectedId === '' ? 'bg-primary/5 text-primary' : 'text-muted-foreground'}"
				onclick={() => select('')}
			>
				<div class="flex h-3.5 w-3.5 items-center justify-center">
					{#if selectedId === ''}<Check class="h-3 w-3 text-primary" />{/if}
				</div>
				<span class="font-medium">System default</span>
				<span class="ml-1 text-muted-foreground">(no override)</span>
			</button>
			<div class="max-h-52 overflow-y-auto divide-y">
				{#each bundles as bundle}
					<button
						type="button"
						class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs hover:bg-muted/50 transition-colors cursor-pointer {selectedId === bundle.id ? 'bg-primary/5' : ''}"
						onclick={() => select(bundle.id)}
					>
						<div class="flex h-3.5 w-3.5 shrink-0 items-center justify-center">
							{#if selectedId === bundle.id}<Check class="h-3 w-3 text-primary" />{/if}
						</div>
						<span class="font-medium truncate">{bundle.name}</span>
						{#if bundle.is_default}
							<Badge variant="secondary" class="text-[9px] shrink-0">Default</Badge>
						{/if}
						{#if suggestedBundle?.id === bundle.id}
							<Badge variant="outline" class="text-[9px] text-primary border-primary/30 shrink-0">Suggested</Badge>
						{/if}
						<span class="ml-auto text-[10px] text-muted-foreground shrink-0 tabular-nums">{bundle.block_refs?.length ?? 0} blocks</span>
					</button>
				{/each}
			</div>

			<!-- Preview, shown at bottom of open picker -->
			{#if previewLoading}
				<div class="flex items-center gap-1.5 border-t px-3 py-2 text-xs text-muted-foreground">
					<LoaderCircle class="h-3 w-3 animate-spin" /> Loading preview…
				</div>
			{:else if previewStages && selected}
				<div class="grid grid-cols-[1fr_auto] gap-x-4 gap-y-0.5 border-t bg-muted/20 px-3 py-2 text-[10px]">
					<span class="text-muted-foreground">Backend sys / user</span>
					<span class="font-mono text-right tabular-nums">{previewStages.backend.system.toLocaleString()} / {previewStages.backend.user.toLocaleString()}</span>
					<span class="text-muted-foreground">Frontend sys / user</span>
					<span class="font-mono text-right tabular-nums">{previewStages.frontend.system.toLocaleString()} / {previewStages.frontend.user.toLocaleString()}</span>
				</div>
			{:else if previewError}
				<p class="border-t px-3 py-2 text-[10px] text-destructive">{previewError}</p>
			{/if}
		</div>
	{/if}
</div>
