<script lang="ts">
	import * as Dialog from '$lib/components/ui/dialog';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import {
		exportTemplatePackage,
		importTemplatePackage,
		importStarterTemplatePackage,
		type AppRequirementTemplate,
		type ContentBlock,
		type GenerationProfile,
		type StarterTemplatePackage,
	} from '$lib/api/client';

	interface Props {
		open: boolean;
		profiles: GenerationProfile[];
		appTemplates: AppRequirementTemplate[];
		contentBlocks: ContentBlock[];
		starterPackages: StarterTemplatePackage[];
		starterPackagesLoading: boolean;
		onImported: () => Promise<void>;
	}
	let {
		open = $bindable(false),
		profiles,
		appTemplates,
		contentBlocks,
		starterPackages,
		starterPackagesLoading,
		onImported,
	}: Props = $props();

	type Section = 'starters' | 'export' | 'import';
	let section = $state<Section>('starters');
	let error = $state('');

	// ── Starters ──────────────────────────────────────────────────
	let starterImportSlug = $state('');
	let conflictStrategy = $state<'rename' | 'overwrite' | 'error'>('rename');

	async function importStarter(slug: string) {
		starterImportSlug = slug;
		error = '';
		try {
			await importStarterTemplatePackage(slug, { conflict_strategy: conflictStrategy });
			await onImported();
		} catch (e: any) {
			error = e?.detail ?? e?.message ?? 'Starter import failed';
		}
		starterImportSlug = '';
	}

	// ── Export builder ────────────────────────────────────────────
	let selection = $state({ app: [] as string[], profile: [] as string[], block: [] as string[] });
	const selectedCount = $derived(selection.app.length + selection.profile.length + selection.block.length);

	function blockKey(block: ContentBlock): string {
		return `${block.slug}:${block.version}`;
	}

	function toggle(group: 'app' | 'profile' | 'block', value: string) {
		const current = selection[group];
		selection = {
			...selection,
			[group]: current.includes(value) ? current.filter((v) => v !== value) : [...current, value],
		};
	}

	async function exportSelected(format: 'json' | 'yaml') {
		error = '';
		try {
			const selectedBlocks = contentBlocks
				.filter((b) => selection.block.includes(blockKey(b)))
				.map((b) => ({ type: b.block_type, slug: b.slug, version: b.version }));
			const exported = await exportTemplatePackage(
				{
					app_template_slugs: selection.app,
					bundle_slugs: selection.profile,
					block_refs: selectedBlocks,
				},
				format,
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

	// ── Import ────────────────────────────────────────────────────
	let packageText = $state('');
	let importing = $state(false);

	async function readFile(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		if (!file) return;
		packageText = await file.text();
		input.value = '';
	}

	async function submitImport() {
		importing = true;
		error = '';
		try {
			await importTemplatePackage({ package_text: packageText, conflict_strategy: conflictStrategy });
			packageText = '';
			await onImported();
		} catch (e: any) {
			error = e?.detail ?? e?.message ?? 'Import failed';
		}
		importing = false;
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="max-w-2xl max-h-[85vh] overflow-y-auto">
		<Dialog.Header>
			<Dialog.Title>Packages</Dialog.Title>
			<Dialog.Description>Share app specs, blocks, and profiles as portable packages.</Dialog.Description>
		</Dialog.Header>

		<div class="flex items-center gap-1 rounded-md bg-muted p-1 text-xs">
			{#each [['starters', 'Starter packages'], ['export', 'Export'], ['import', 'Import']] as [id, label]}
				<button
					type="button"
					class="rounded px-3 py-1.5 font-medium transition-colors cursor-pointer {section === id ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}"
					onclick={() => (section = id as Section)}
				>{label}</button>
			{/each}
			<div class="ml-auto flex items-center gap-1.5">
				<Label class="text-[10px] text-muted-foreground">on conflict</Label>
				<select bind:value={conflictStrategy} class="h-7 rounded border border-input bg-surface-1 px-1.5 text-[11px] cursor-pointer">
					<option value="rename">rename</option>
					<option value="overwrite">overwrite mine</option>
					<option value="error">error</option>
				</select>
			</div>
		</div>

		{#if section === 'starters'}
			{#if starterPackagesLoading}
				<div class="flex items-center gap-2 py-6 text-xs text-muted-foreground">
					<LoaderCircle class="h-3.5 w-3.5 animate-spin" /> Loading starter packages…
				</div>
			{:else if starterPackages.length === 0}
				<p class="py-6 text-xs text-muted-foreground">No starter packages available.</p>
			{:else}
				<div class="grid gap-3 sm:grid-cols-2">
					{#each starterPackages as starter (starter.slug)}
						<div class="rounded-md border border-border bg-card p-3 shadow-sm space-y-2">
							<div>
								<h5 class="text-sm font-semibold text-foreground">{starter.name}</h5>
								<p class="text-xs text-muted-foreground">{starter.description}</p>
							</div>
							<div class="flex flex-wrap gap-1.5">
								<Badge variant="outline" class="text-[9px]">{starter.app_template_count} apps</Badge>
								<Badge variant="outline" class="text-[9px]">{starter.block_count} blocks</Badge>
								<Badge variant="outline" class="text-[9px]">{starter.bundle_count} profiles</Badge>
							</div>
							<Button
								size="sm"
								class="text-xs cursor-pointer shadow-xs"
								disabled={starterImportSlug === starter.slug}
								onclick={() => importStarter(starter.slug)}
							>
								{#if starterImportSlug === starter.slug}
									<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin" /> Importing…
								{:else}
									Import
								{/if}
							</Button>
						</div>
					{/each}
				</div>
			{/if}
		{:else if section === 'export'}
			<div class="space-y-3">
				<div class="space-y-2">
					<p class="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">App specs</p>
					<div class="flex max-h-28 flex-wrap gap-1.5 overflow-y-auto">
						{#each appTemplates as t (t.slug)}
							<button
								type="button"
								class="rounded-md border px-2 py-0.5 text-[11px] transition-colors cursor-pointer {selection.app.includes(t.slug) ? 'border-primary bg-primary/10 text-foreground' : 'border-border text-muted-foreground hover:text-foreground hover:border-primary/40'}"
								onclick={() => toggle('app', t.slug)}
							>{t.name}</button>
						{/each}
					</div>
				</div>
				<div class="space-y-2">
					<p class="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Profiles <span class="normal-case font-normal">(their blocks are bundled automatically)</span></p>
					<div class="flex max-h-28 flex-wrap gap-1.5 overflow-y-auto">
						{#each profiles as p (p.slug)}
							<button
								type="button"
								class="rounded-md border px-2 py-0.5 text-[11px] transition-colors cursor-pointer {selection.profile.includes(p.slug) ? 'border-primary bg-primary/10 text-foreground' : 'border-border text-muted-foreground hover:text-foreground hover:border-primary/40'}"
								onclick={() => toggle('profile', p.slug)}
							>{p.name}</button>
						{/each}
					</div>
				</div>
				<div class="space-y-2">
					<p class="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Extra blocks</p>
					<div class="flex max-h-28 flex-wrap gap-1.5 overflow-y-auto">
						{#each contentBlocks as b (b.id)}
							<button
								type="button"
								class="rounded-md border px-2 py-0.5 text-[10px] font-mono transition-colors cursor-pointer {selection.block.includes(blockKey(b)) ? 'border-primary bg-primary/10 text-foreground' : 'border-border text-muted-foreground hover:text-foreground hover:border-primary/40'}"
								onclick={() => toggle('block', blockKey(b))}
							>{b.slug} v{b.version}</button>
						{/each}
					</div>
				</div>
				<div class="flex items-center gap-2 border-t pt-3">
					<Badge variant="outline" class="text-[10px]">{selectedCount} selected</Badge>
					<Button size="sm" class="ml-auto text-xs cursor-pointer" disabled={selectedCount === 0} onclick={() => exportSelected('json')}>Export JSON</Button>
					<Button variant="outline" size="sm" class="text-xs cursor-pointer" disabled={selectedCount === 0} onclick={() => exportSelected('yaml')}>Export YAML</Button>
				</div>
			</div>
		{:else}
			<div class="space-y-3">
				<div class="space-y-1.5">
					<Label class="text-xs" for="pkg-import-text">Package JSON / YAML</Label>
					<Textarea id="pkg-import-text" bind:value={packageText} rows={10} class="font-mono text-xs" placeholder="Paste a package, or pick a file below" />
					<input type="file" accept=".json,.yaml,.yml" onchange={readFile} class="text-xs" />
				</div>
				<div class="flex justify-end">
					<Button size="sm" class="text-xs cursor-pointer" disabled={importing || !packageText.trim()} onclick={submitImport}>
						{#if importing}<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin" />{/if}
						Import package
					</Button>
				</div>
			</div>
		{/if}

		{#if error}
			<div class="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive font-medium">{error}</div>
		{/if}
	</Dialog.Content>
</Dialog.Root>
