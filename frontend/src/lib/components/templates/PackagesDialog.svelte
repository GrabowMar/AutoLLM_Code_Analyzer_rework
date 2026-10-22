<script lang="ts">
	import * as Dialog from '$lib/components/ui/dialog';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import * as Tabs from '$lib/components/ui/tabs';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import {
		exportTemplatePackage,
		importTemplatePackage,
		importStarterTemplatePackage,
		type AppRequirementTemplate,
		type ContentBlock,
		type GenerationProfile,
		type Stack,
		type StarterTemplatePackage,
	} from '$lib/api/client';

	interface Props {
		open: boolean;
		profiles: GenerationProfile[];
		appTemplates: AppRequirementTemplate[];
		contentBlocks: ContentBlock[];
		stacks?: Stack[];
		starterPackages: StarterTemplatePackage[];
		starterPackagesLoading: boolean;
		onImported: () => Promise<void>;
	}
	let {
		open = $bindable(false),
		profiles,
		appTemplates,
		contentBlocks,
		stacks = [],
		starterPackages,
		starterPackagesLoading,
		onImported,
	}: Props = $props();

	let section = $state('starters');
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
	let selection = $state({ app: [] as string[], profile: [] as string[], block: [] as string[], stack: [] as string[] });
	const selectedCount = $derived(
		selection.app.length + selection.profile.length + selection.block.length + selection.stack.length
	);

	function blockKey(block: ContentBlock): string {
		return `${block.slug}:${block.version}`;
	}

	function toggle(group: 'app' | 'profile' | 'block' | 'stack', value: string) {
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
					stack_slugs: selection.stack,
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

		<div class="flex items-center justify-end gap-1.5">
			<Label for="pkg-conflict" class="text-xs text-muted-foreground">On conflict</Label>
			<select id="pkg-conflict" bind:value={conflictStrategy} class="h-7 rounded border border-input bg-surface-1 px-1.5 text-xs cursor-pointer">
				<option value="rename">rename</option>
				<option value="overwrite">overwrite mine</option>
				<option value="error">error</option>
			</select>
		</div>

		<Tabs.Root bind:value={section}>
			<Tabs.List>
				<Tabs.Trigger value="starters">Starter packages</Tabs.Trigger>
				<Tabs.Trigger value="export">Export</Tabs.Trigger>
				<Tabs.Trigger value="import">Import</Tabs.Trigger>
			</Tabs.List>

			<Tabs.Content value="starters">
				{#if starterPackagesLoading}
					<LoadingState label="loading starter packages" class="py-6" />
				{:else if starterPackages.length === 0}
					<EmptyState compact title="No starter packages available" />
				{:else}
					<div class="grid gap-3 sm:grid-cols-2">
						{#each starterPackages as starter (starter.slug)}
							<div class="rounded-md border border-border bg-card p-3 space-y-2">
								<div>
									<h3 class="text-sm font-semibold">{starter.name}</h3>
									<p class="text-xs text-muted-foreground">{starter.description}</p>
								</div>
								<div class="flex flex-wrap gap-1.5">
									<Badge variant="outline">{starter.app_template_count} apps</Badge>
									<Badge variant="outline">{starter.block_count} blocks</Badge>
									<Badge variant="outline">{starter.bundle_count} profiles</Badge>
								</div>
								<Button
									size="sm"
									disabled={starterImportSlug === starter.slug}
									onclick={() => importStarter(starter.slug)}
								>
									{#if starterImportSlug === starter.slug}
										<LoaderCircle class="animate-spin" /> Importing…
									{:else}
										Import
									{/if}
								</Button>
							</div>
						{/each}
					</div>
				{/if}
			</Tabs.Content>

			<Tabs.Content value="export">
				<div class="space-y-3">
					<div class="space-y-2">
						<p class="section-label">App specs</p>
						<div class="flex max-h-28 flex-wrap gap-1.5 overflow-y-auto">
							{#each appTemplates as t (t.slug)}
								<button
									type="button"
									class="rounded-md border px-2 py-0.5 text-[11px] transition-colors cursor-pointer {selection.app.includes(t.slug) ? 'border-primary bg-primary/10 text-foreground' : 'border-border text-muted-foreground hover:text-foreground hover:border-primary/40'}"
									aria-pressed={selection.app.includes(t.slug)}
									onclick={() => toggle('app', t.slug)}
								>{t.name}</button>
							{/each}
						</div>
					</div>
					<div class="space-y-2">
						<p class="section-label">Profiles <span class="normal-case font-normal">(their blocks are bundled automatically)</span></p>
						<div class="flex max-h-28 flex-wrap gap-1.5 overflow-y-auto">
							{#each profiles as p (p.slug)}
								<button
									type="button"
									class="rounded-md border px-2 py-0.5 text-[11px] transition-colors cursor-pointer {selection.profile.includes(p.slug) ? 'border-primary bg-primary/10 text-foreground' : 'border-border text-muted-foreground hover:text-foreground hover:border-primary/40'}"
									aria-pressed={selection.profile.includes(p.slug)}
									onclick={() => toggle('profile', p.slug)}
								>{p.name}</button>
							{/each}
						</div>
					</div>
					<div class="space-y-2">
						<p class="section-label">Extra blocks</p>
						<div class="flex max-h-28 flex-wrap gap-1.5 overflow-y-auto">
							{#each contentBlocks as b (b.id)}
								<button
									type="button"
									class="rounded-md border px-2 py-0.5 text-[11px] font-mono transition-colors cursor-pointer {selection.block.includes(blockKey(b)) ? 'border-primary bg-primary/10 text-foreground' : 'border-border text-muted-foreground hover:text-foreground hover:border-primary/40'}"
									aria-pressed={selection.block.includes(blockKey(b))}
									onclick={() => toggle('block', blockKey(b))}
								>{b.slug} v{b.version}</button>
							{/each}
						</div>
					</div>
					{#if stacks.some((st) => !st.is_builtin)}
						<div class="space-y-2">
							<p class="section-label">User stacks</p>
							<div class="flex max-h-28 flex-wrap gap-1.5 overflow-y-auto">
								{#each stacks.filter((st) => !st.is_builtin) as st (st.slug)}
									<button
										type="button"
										class="rounded-md border px-2 py-0.5 text-[11px] font-mono transition-colors cursor-pointer {selection.stack.includes(st.slug) ? 'border-primary bg-primary/10 text-foreground' : 'border-border text-muted-foreground hover:text-foreground hover:border-primary/40'}"
										aria-pressed={selection.stack.includes(st.slug)}
										onclick={() => toggle('stack', st.slug)}
									>{st.slug}</button>
								{/each}
							</div>
						</div>
					{/if}
					<div class="flex items-center gap-2 border-t pt-3">
						<Badge variant="outline">{selectedCount} selected</Badge>
						<Button variant="outline" size="sm" class="ml-auto" disabled={selectedCount === 0} onclick={() => exportSelected('yaml')}>Export YAML</Button>
						<Button size="sm" disabled={selectedCount === 0} onclick={() => exportSelected('json')}>Export JSON</Button>
					</div>
				</div>
			</Tabs.Content>

			<Tabs.Content value="import">
				<div class="space-y-3">
					<div class="space-y-1.5">
						<Label class="text-xs" for="pkg-import-text">Package JSON / YAML</Label>
						<Textarea id="pkg-import-text" bind:value={packageText} rows={10} class="font-mono text-xs" placeholder="Paste a package, or pick a file below" />
						<input type="file" accept=".json,.yaml,.yml" aria-label="Package file" onchange={readFile} class="text-xs" />
					</div>
					<div class="flex justify-end">
						<Button size="sm" disabled={importing || !packageText.trim()} onclick={submitImport}>
							{#if importing}<LoaderCircle class="animate-spin" />{/if}
							Import package
						</Button>
					</div>
				</div>
			</Tabs.Content>
		</Tabs.Root>

		{#if error}
			<ErrorAlert message={error} />
		{/if}
	</Dialog.Content>
</Dialog.Root>
