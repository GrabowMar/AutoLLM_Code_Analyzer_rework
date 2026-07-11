<script lang="ts">
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Package from '@lucide/svelte/icons/package';
	import {
		exportTemplatePackage,
		importStarterTemplatePackage,
		type AppRequirementTemplate,
		type ContentBlock,
		type StarterTemplatePackage,
		type TemplateBundle,
	} from '$lib/api/client';

	interface Props {
		bundles: TemplateBundle[];
		bundlesLoading: boolean;
		starterPackages: StarterTemplatePackage[];
		starterPackagesLoading: boolean;
		appTemplates: AppRequirementTemplate[];
		contentBlocks: ContentBlock[];
		blocksLoading: boolean;
		conflictStrategy: 'rename' | 'overwrite' | 'error';
		onStartImport: () => void;
		onRefreshAll: () => Promise<void>;
	}
	let {
		bundles,
		bundlesLoading,
		starterPackages,
		starterPackagesLoading,
		appTemplates,
		contentBlocks,
		blocksLoading,
		conflictStrategy,
		onStartImport,
		onRefreshAll,
	}: Props = $props();

	let starterImportSlug = $state('');
	let error = $state('');
	let packageSelection = $state({
		app: [] as string[],
		bundle: [] as string[],
		block: [] as string[],
	});

	const selectedAssetCount = $derived(
		packageSelection.app.length +
		packageSelection.bundle.length +
		packageSelection.block.length
	);

	function blockKey(block: ContentBlock): string {
		return `${block.slug}:${block.version}`;
	}

	function toggleSelection(
		group: 'app' | 'bundle' | 'block',
		value: string
	) {
		const current = packageSelection[group];
		packageSelection = {
			...packageSelection,
			[group]: current.includes(value)
				? current.filter((item) => item !== value)
				: [...current, value],
		};
	}

	function isSelected(
		group: 'app' | 'bundle' | 'block',
		value: string
	): boolean {
		return packageSelection[group].includes(value);
	}

	function clearPackageSelection() {
		packageSelection = {
			app: [],
			bundle: [],
			block: [],
		};
	}

	async function exportSelectedPackage(format: 'json' | 'yaml') {
		try {
			const selectedBlocks = contentBlocks
				.filter((block) => packageSelection.block.includes(blockKey(block)))
				.map((block) => ({
					type: block.block_type,
					slug: block.slug,
					version: block.version,
				}));
			const exported = await exportTemplatePackage(
				{
					app_template_slugs: packageSelection.app,
					bundle_slugs: packageSelection.bundle,
					block_refs: selectedBlocks,
				},
				format
			);
			const blob = new Blob([exported.content], { type: exported.contentType });
			const href = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = href;
			a.download = `generation-template-package.${format}`;
			a.click();
			URL.revokeObjectURL(href);
		} catch (e: any) {
			error = e?.detail ?? e?.message ?? 'Export failed';
		}
	}

	async function importStarterPackage(slug: string) {
		starterImportSlug = slug;
		error = '';
		try {
			await importStarterTemplatePackage(slug, {
				conflict_strategy: conflictStrategy,
			});
			await onRefreshAll();
		} catch (e: any) {
			error = e?.detail ?? e?.message ?? 'Starter import failed';
		}
		starterImportSlug = '';
	}
</script>

<div class="space-y-4">
	<div class="flex flex-col gap-3 rounded-md border border-border bg-card p-3 sm:flex-row sm:items-center sm:justify-between">
		<div>
			<h3 class="text-sm font-semibold text-foreground">Template packages</h3>
			<p class="text-xs text-muted-foreground">
				Pack app templates, blocks, and bundles into one shareable export.
			</p>
		</div>
		<div class="flex flex-wrap gap-2">
			<Button size="sm" variant="outline" class="text-xs cursor-pointer" onclick={onStartImport}>
				Import Package
			</Button>
		</div>
	</div>
	<div class="rounded-md border border-border bg-card p-4">
		<div class="rounded-md border border-border/60 bg-muted/20 p-4">
			<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
				<div>
					<h4 class="text-sm font-semibold text-foreground">Starter packages</h4>
					<p class="text-xs text-muted-foreground">
						One-click imports for built-in sample app requirements, blocks, and bundles.
					</p>
				</div>
				<Badge variant="outline" class="text-[10px]">Uses {conflictStrategy} on conflict</Badge>
			</div>
			{#if starterPackagesLoading}
				<div class="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
					<LoaderCircle class="h-3.5 w-3.5 animate-spin" /> Loading starter packages…
				</div>
			{:else if starterPackages.length === 0}
				<p class="mt-3 text-xs text-muted-foreground">No starter packages available.</p>
			{:else}
				<div class="mt-4 grid gap-3 md:grid-cols-2">
					{#each starterPackages as starter}
						<div class="rounded-md border border-border bg-card p-3 shadow-sm">
							<div class="space-y-2">
								<div>
									<h5 class="text-sm font-semibold text-foreground">{starter.name}</h5>
									<p class="text-xs text-muted-foreground">{starter.description}</p>
								</div>
								<div class="flex flex-wrap gap-1.5">
									<Badge variant="outline" class="text-[9px]">{starter.app_template_count} apps</Badge>
									<Badge variant="outline" class="text-[9px]">{starter.block_count} blocks</Badge>
									<Badge variant="outline" class="text-[9px]">{starter.bundle_count} bundles</Badge>
								</div>
								<Button
									size="sm"
									class="text-xs cursor-pointer shadow-xs"
									disabled={starterImportSlug === starter.slug}
									onclick={() => importStarterPackage(starter.slug)}
								>
									{#if starterImportSlug === starter.slug}
										<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin" /> Importing…
									{:else}
										Import starter package
									{/if}
								</Button>
							</div>
						</div>
					{/each}
				</div>
			{/if}
		</div>
		<div class="mt-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
			<div>
				<h4 class="text-sm font-semibold text-foreground">Package builder</h4>
				<p class="text-xs text-muted-foreground">
					Select the assets to ship together. Bundle-linked blocks are included automatically on export.
				</p>
			</div>
			<div class="flex items-center gap-2">
				<Badge variant="outline" class="text-[10px]">{selectedAssetCount} selected</Badge>
				<Button variant="outline" size="sm" class="text-xs cursor-pointer" onclick={clearPackageSelection}>
					Clear
				</Button>
			</div>
		</div>

		<div class="mt-4 grid gap-4 xl:grid-cols-2">
			<div class="space-y-2">
				<p class="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">App templates</p>
				<div class="flex flex-wrap gap-2">
					{#each appTemplates as template}
						<button
							type="button"
							class="rounded-md border px-2.5 py-1 text-[11px] transition-colors cursor-pointer {isSelected('app', template.slug) ? 'border-primary bg-primary/10 text-foreground' : 'border-border text-muted-foreground hover:text-foreground hover:border-primary/40'}"
							onclick={() => toggleSelection('app', template.slug)}
						>
							{template.name}
						</button>
					{/each}
				</div>
			</div>

			<div class="space-y-2">
				<p class="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Blocks</p>
				{#if blocksLoading}
					<p class="text-xs text-muted-foreground">Loading blocks…</p>
				{:else}
					<div class="flex max-h-44 flex-wrap gap-2 overflow-y-auto pr-1">
						{#each contentBlocks as block}
							<button
								type="button"
								class="rounded-md border px-2.5 py-1 text-[11px] transition-colors cursor-pointer {isSelected('block', blockKey(block)) ? 'border-primary bg-primary/10 text-foreground' : 'border-border text-muted-foreground hover:text-foreground hover:border-primary/40'}"
								onclick={() => toggleSelection('block', blockKey(block))}
							>
								{block.slug} v{block.version}
							</button>
						{/each}
					</div>
				{/if}
			</div>

			<div class="space-y-2 xl:col-span-2">
				<p class="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Bundles</p>
				<div class="flex flex-wrap gap-2">
					{#each bundles as bundle}
						<button
							type="button"
							class="rounded-md border px-2.5 py-1 text-[11px] transition-colors cursor-pointer {isSelected('bundle', bundle.slug) ? 'border-primary bg-primary/10 text-foreground' : 'border-border text-muted-foreground hover:text-foreground hover:border-primary/40'}"
							onclick={() => toggleSelection('bundle', bundle.slug)}
						>
							{bundle.name}
						</button>
					{/each}
				</div>
			</div>
		</div>

		{#if error}
			<div class="mt-4 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive font-medium">
				{error}
			</div>
		{/if}

		<div class="mt-4 flex flex-wrap gap-2 border-t pt-4">
			<Button
				size="sm"
				class="text-xs cursor-pointer shadow-xs"
				disabled={selectedAssetCount === 0}
				onclick={() => exportSelectedPackage('json')}
			>
				Export JSON Package
			</Button>
			<Button
				variant="outline"
				size="sm"
				class="text-xs cursor-pointer"
				disabled={selectedAssetCount === 0}
				onclick={() => exportSelectedPackage('yaml')}
			>
				Export YAML Package
			</Button>
		</div>
	</div>
	<div class="p-4 bg-muted/20 border border-muted/50 rounded-md">
		<p class="text-xs text-muted-foreground leading-relaxed flex items-start gap-1.5">
			<Package class="h-4 w-4 shrink-0 text-primary mt-0.5" />
			Template bundles compose structural requirement blocks and models into reproducible generation snapshot plans.
			Bundle exports are now superseded by template packages that can ship the whole setup together.
		</p>
	</div>

	{#if bundlesLoading}
		<div class="flex justify-center py-16">
			<LoaderCircle class="h-6 w-6 animate-spin text-primary" />
		</div>
	{:else if bundles.length === 0}
		<div class="py-16 text-center text-xs text-muted-foreground">
			No bundles discovered yet. Import a starter package above or run <span class="bg-muted px-1.5 py-0.5 rounded font-mono text-[10px]">seed_generation_templates</span> inside LLM Lab.
		</div>
	{:else}
		<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
			{#each bundles as bundle (bundle.id)}
				<div class="group relative rounded-md border border-border bg-card p-4 shadow-sm hover:border-primary/40 hover:shadow-md transition-all">
					<div class="space-y-2">
						<div class="flex items-start justify-between gap-1.5">
							<div class="min-w-0">
								<span class="font-bold text-sm block truncate text-foreground">{bundle.name}</span>
								<span class="font-mono text-[9px] text-muted-foreground mt-0.5 block truncate">{bundle.slug}</span>
							</div>
						</div>
						<p class="text-xs text-muted-foreground line-clamp-2 leading-relaxed h-8">{bundle.description || '—'}</p>
						<div class="flex flex-wrap items-center gap-1.5 pt-2">
							{#if bundle.is_default}
								<Badge variant="secondary" class="text-[9px] px-1.5 py-0">Default</Badge>
							{/if}
							{#if bundle.is_system}
								<Badge variant="outline" class="text-[9px] px-1.5 py-0 bg-muted/40">System</Badge>
							{/if}
							<Badge variant="outline" class="text-[9px] px-1.5 py-0 border-primary/20 text-primary font-mono tabular-nums bg-primary/[0.02]">
								{bundle.block_refs?.length ?? 0} blocks
							</Badge>
						</div>
						<div class="text-[9px] text-muted-foreground pt-1 border-t flex justify-between items-center font-mono">
							<span>Stack:</span>
							<span class="font-semibold text-foreground">{bundle.scaffolding_slug}</span>
						</div>
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>
