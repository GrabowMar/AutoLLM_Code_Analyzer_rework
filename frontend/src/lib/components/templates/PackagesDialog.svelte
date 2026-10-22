<script lang="ts">
	/**
	 * PackagesDialog — starter packages, export builder and import for template
	 * packages (app specs, blocks, profiles, user stacks and run presets).
	 * `onImported()` must reload every asset list, stacks and presets included.
	 */
	import * as Dialog from '$lib/components/ui/dialog';
	import * as Select from '$lib/components/ui/select';
	import * as Tabs from '$lib/components/ui/tabs';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { formatApiError } from '$lib/api/core';
	import {
		exportTemplatePackage,
		importTemplatePackage,
		importStarterTemplatePackage,
		type AppRequirementTemplate,
		type ContentBlock,
		type GenerationProfile,
		type PackageConflictStrategy,
		type PresetKind,
		type RunPreset,
		type Stack,
		type StarterTemplatePackage,
		type TemplatePackageImportResult,
	} from '$lib/api/generation';
	import { PRESET_KINDS, PRESET_KIND_TITLES } from './presets';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import Download from '@lucide/svelte/icons/download';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Package from '@lucide/svelte/icons/package';

	interface Props {
		open: boolean;
		profiles: GenerationProfile[];
		appTemplates: AppRequirementTemplate[];
		contentBlocks: ContentBlock[];
		stacks?: Stack[];
		/** Visible presets; only your own are offered for export (system ones exist everywhere). */
		presets?: RunPreset[];
		starterPackages: StarterTemplatePackage[];
		starterPackagesLoading: boolean;
		starterPackagesError?: string;
		onRetryStarters?: () => void;
		onImported: () => Promise<void>;
	}
	let {
		open = $bindable(false),
		profiles,
		appTemplates,
		contentBlocks,
		stacks = [],
		presets = [],
		starterPackages,
		starterPackagesLoading,
		starterPackagesError = '',
		onRetryStarters,
		onImported,
	}: Props = $props();

	let section = $state('starters');
	let error = $state('');
	let conflictStrategy = $state<string>('rename');
	let lastImport = $state<{ source: string; result: TemplatePackageImportResult } | null>(null);

	const conflictItems = [
		{ value: 'rename', label: 'Keep both (rename)', description: 'Conflicting imports get a new slug, e.g. name-2.' },
		{
			value: 'overwrite',
			label: 'Overwrite mine',
			description: 'Replaces your own items; presets get a new version instead.',
		},
		{ value: 'error', label: 'Stop on conflict', description: 'Nothing is imported if anything already exists.' },
	];

	const NOUNS: Record<string, [string, string]> = {
		app_specs: ['app spec', 'app specs'],
		blocks: ['block', 'blocks'],
		profiles: ['profile', 'profiles'],
		stacks: ['stack', 'stacks'],
		presets: ['preset', 'presets'],
	};

	function sectionLabel(key: string): string {
		return NOUNS[key]?.[1] ?? key.replace(/_/g, ' ');
	}

	/** "1 app spec", "3 app specs". */
	function counted(key: string, n: number): string {
		const [one, many] = NOUNS[key] ?? [key.replace(/_/g, ' '), key.replace(/_/g, ' ')];
		return `${n} ${n === 1 ? one : many}`;
	}

	const importSummary = $derived(
		lastImport
			? Object.entries(lastImport.result)
					.filter(([, items]) => Array.isArray(items))
					.map(([key, items]) => ({ key, label: sectionLabel(key), items: items as string[] }))
			: []
	);
	const importTotal = $derived(importSummary.reduce((n, s) => n + s.items.length, 0));

	async function afterImport(source: string, result: TemplatePackageImportResult) {
		lastImport = { source, result };
		await onImported();
	}

	// ── Starters ──────────────────────────────────────────────────
	let starterImportSlug = $state('');

	async function importStarter(starter: StarterTemplatePackage) {
		starterImportSlug = starter.slug;
		error = '';
		lastImport = null;
		try {
			const result = await importStarterTemplatePackage(starter.slug, {
				conflict_strategy: conflictStrategy as PackageConflictStrategy,
			});
			await afterImport(starter.name, result);
		} catch (e) {
			error = formatApiError(e, 'Starter import failed.');
		} finally {
			starterImportSlug = '';
		}
	}

	function starterCounts(starter: StarterTemplatePackage): string[] {
		const counts: [string, number | undefined][] = [
			['app_specs', starter.app_spec_count ?? starter.app_template_count],
			['blocks', starter.block_count],
			['profiles', starter.profile_count ?? starter.bundle_count],
			['stacks', starter.stack_count],
			['presets', starter.preset_count],
		];
		return counts
			.filter((entry): entry is [string, number] => typeof entry[1] === 'number')
			.map(([key, n]) => counted(key, n));
	}

	// ── Export builder ────────────────────────────────────────────
	type Group = 'app' | 'profile' | 'block' | 'stack' | 'preset';
	let selection = $state<Record<Group, string[]>>({ app: [], profile: [], block: [], stack: [], preset: [] });
	let exporting = $state<'' | 'json' | 'yaml'>('');

	const selectedCount = $derived(Object.values(selection).reduce((n, list) => n + list.length, 0));
	const userStacks = $derived(stacks.filter((st) => !st.is_builtin));
	const ownPresets = $derived(presets.filter((p) => !p.is_system && !p.is_archived));
	const presetGroups = $derived(
		PRESET_KINDS.map((kind) => ({ kind, items: ownPresets.filter((p) => p.kind === kind) })).filter(
			(g) => g.items.length > 0
		)
	);

	// Blocks of different types may share a slug, so the type is part of the key.
	function blockKey(block: ContentBlock): string {
		return `${block.block_type}:${block.slug}:${block.version}`;
	}

	function presetKey(preset: RunPreset): string {
		return `${preset.kind}:${preset.slug}`;
	}

	function toggle(group: Group, value: string) {
		const current = selection[group];
		selection = {
			...selection,
			[group]: current.includes(value) ? current.filter((v) => v !== value) : [...current, value],
		};
	}

	async function exportSelected(format: 'json' | 'yaml') {
		error = '';
		exporting = format;
		try {
			const exported = await exportTemplatePackage(
				{
					app_template_slugs: selection.app,
					bundle_slugs: selection.profile,
					block_refs: contentBlocks
						.filter((b) => selection.block.includes(blockKey(b)))
						.map((b) => ({ type: b.block_type, slug: b.slug, version: b.version })),
					stack_slugs: selection.stack,
					preset_refs: ownPresets
						.filter((p) => selection.preset.includes(presetKey(p)))
						.map((p) => ({ kind: p.kind as PresetKind, slug: p.slug })),
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
		} catch (e) {
			error = formatApiError(e, 'Export failed.');
		} finally {
			exporting = '';
		}
	}

	// ── Import ────────────────────────────────────────────────────
	let packageText = $state('');
	let packageFileName = $state('');
	let importing = $state(false);

	async function readFile(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		if (!file) return;
		try {
			packageText = await file.text();
			packageFileName = file.name;
		} catch (e) {
			error = formatApiError(e, 'Could not read the file.');
		}
		input.value = '';
	}

	async function submitImport() {
		importing = true;
		error = '';
		lastImport = null;
		try {
			const result = await importTemplatePackage({
				package_text: packageText,
				conflict_strategy: conflictStrategy as PackageConflictStrategy,
			});
			await afterImport(packageFileName || 'pasted package', result);
			packageText = '';
			packageFileName = '';
		} catch (e) {
			error = formatApiError(e, 'Import failed.');
		} finally {
			importing = false;
		}
	}

	const chipBase =
		'rounded-md border px-2 py-0.5 text-[11px] transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40';
	function chipClass(on: boolean): string {
		return `${chipBase} ${on ? 'border-primary bg-primary/10 text-foreground' : 'border-border text-muted-foreground hover:border-primary/40 hover:text-foreground'}`;
	}
</script>

{#snippet chips(group: Group, items: { key: string; label: string; mono?: boolean; title?: string }[])}
	<div class="flex max-h-28 flex-wrap gap-1.5 overflow-y-auto">
		{#each items as item (item.key)}
			<button
				type="button"
				class="{chipClass(selection[group].includes(item.key))} {item.mono ? 'font-mono' : ''}"
				aria-pressed={selection[group].includes(item.key)}
				title={item.title}
				onclick={() => toggle(group, item.key)}
			>{item.label}</button>
		{/each}
	</div>
{/snippet}

<Dialog.Root bind:open>
	<Dialog.Content class="max-h-[85vh] max-w-2xl overflow-y-auto">
		<Dialog.Header>
			<Dialog.Title>Packages</Dialog.Title>
			<Dialog.Description>
				Share app specs, prompt profiles and blocks, user stacks and run presets as one portable file.
			</Dialog.Description>
		</Dialog.Header>

		<div class="flex flex-wrap items-center justify-end gap-2">
			<Label for="pkg-conflict" class="text-xs text-muted-foreground">When something already exists</Label>
			<Select.Simple
				id="pkg-conflict"
				size="sm"
				class="w-auto min-w-[11rem]"
				bind:value={conflictStrategy}
				items={conflictItems}
			/>
		</div>

		<Tabs.Root bind:value={section}>
			<Tabs.List>
				<Tabs.Trigger value="starters">Starter packages</Tabs.Trigger>
				<Tabs.Trigger value="export">Export</Tabs.Trigger>
				<Tabs.Trigger value="import">Import</Tabs.Trigger>
			</Tabs.List>

			<Tabs.Content value="starters" class="pt-3">
				{#if starterPackagesLoading}
					<LoadingState label="loading starter packages" class="py-6" />
				{:else if starterPackagesError}
					<ErrorAlert message={starterPackagesError} onretry={onRetryStarters} />
				{:else if starterPackages.length === 0}
					<EmptyState compact icon={Package} title="No starter packages available" />
				{:else}
					<div class="grid gap-3 sm:grid-cols-2">
						{#each starterPackages as starter (starter.slug)}
							<div class="flex flex-col gap-2 rounded-md border border-border bg-card p-3">
								<div class="space-y-1">
									<h3 class="text-sm font-semibold">{starter.name}</h3>
									<p class="text-xs text-muted-foreground">{starter.description}</p>
								</div>
								<div class="flex flex-wrap gap-1.5">
									{#each starterCounts(starter) as text (text)}
										<Badge variant="outline">{text}</Badge>
									{/each}
								</div>
								<Button
									size="sm"
									class="mt-auto self-start"
									disabled={starterImportSlug !== ''}
									onclick={() => importStarter(starter)}
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

			<Tabs.Content value="export" class="pt-3">
				<div class="space-y-3">
					<div class="space-y-2">
						<p class="section-label">App specs</p>
						{#if appTemplates.length === 0}
							<p class="text-xs text-muted-foreground">None.</p>
						{:else}
							{@render chips('app', appTemplates.map((t) => ({ key: t.slug, label: t.name })))}
						{/if}
					</div>
					<div class="space-y-2">
						<p class="section-label">
							Profiles <span class="font-normal normal-case">(their blocks and user stacks are included automatically)</span>
						</p>
						{#if profiles.length === 0}
							<p class="text-xs text-muted-foreground">None.</p>
						{:else}
							{@render chips('profile', profiles.map((p) => ({ key: p.slug, label: p.name })))}
						{/if}
					</div>
					<div class="space-y-2">
						<p class="section-label">Extra blocks</p>
						{@render chips(
							'block',
							contentBlocks.map((b) => ({ key: blockKey(b), label: `${b.slug} v${b.version}`, mono: true, title: b.block_type }))
						)}
					</div>
					{#if userStacks.length > 0}
						<div class="space-y-2">
							<p class="section-label">User stacks</p>
							{@render chips('stack', userStacks.map((st) => ({ key: st.slug, label: st.slug, mono: true })))}
						</div>
					{/if}
					<div class="space-y-2">
						<p class="section-label">Your presets <span class="font-normal normal-case">(latest version of each)</span></p>
						{#if presetGroups.length === 0}
							<p class="text-xs text-muted-foreground">You have no presets yet. System presets exist on every install, so they are never exported.</p>
						{:else}
							{#each presetGroups as group (group.kind)}
								<div class="flex flex-wrap items-baseline gap-2">
									<span class="w-16 shrink-0 text-[11px] text-muted-foreground">{PRESET_KIND_TITLES[group.kind]}</span>
									<div class="min-w-0 flex-1">
										{@render chips(
											'preset',
											group.items.map((p) => ({ key: presetKey(p), label: `${p.slug} v${p.version}`, mono: true, title: p.name }))
										)}
									</div>
								</div>
							{/each}
						{/if}
					</div>
					<div class="flex flex-wrap items-center gap-2 border-t pt-3">
						<Badge variant="outline">{selectedCount} selected</Badge>
						<div class="ml-auto flex gap-2">
							<Button variant="outline" size="sm" disabled={selectedCount === 0 || exporting !== ''} onclick={() => exportSelected('yaml')}>
								{#if exporting === 'yaml'}<LoaderCircle class="animate-spin" />{:else}<Download />{/if}
								YAML
							</Button>
							<Button size="sm" disabled={selectedCount === 0 || exporting !== ''} onclick={() => exportSelected('json')}>
								{#if exporting === 'json'}<LoaderCircle class="animate-spin" />{:else}<Download />{/if}
								JSON
							</Button>
						</div>
					</div>
				</div>
			</Tabs.Content>

			<Tabs.Content value="import" class="pt-3">
				<div class="space-y-3">
					<div class="space-y-1.5">
						<Label class="text-xs" for="pkg-import-text">Package JSON / YAML</Label>
						<Textarea
							id="pkg-import-text"
							bind:value={packageText}
							rows={10}
							class="font-mono text-xs"
							placeholder="Paste a package, or choose a file below"
							oninput={() => (packageFileName = '')}
						/>
						<input
							type="file"
							accept=".json,.yaml,.yml"
							aria-label="Package file"
							onchange={readFile}
							class="max-w-full cursor-pointer text-xs file:mr-2 file:cursor-pointer file:rounded-md file:border file:border-border file:bg-surface-2 file:px-2 file:py-1 file:text-xs file:text-foreground"
						/>
						<p class="text-[11px] text-muted-foreground">
							Package versions 1–3 are accepted. Items identical to ones you already have are reused, not duplicated;
							imported stacks wait for approval before others can run them.
						</p>
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

		{#if lastImport}
			<div class="space-y-2 rounded-md border border-success/30 bg-success/5 p-3 text-sm" role="status">
				<p class="flex items-start gap-2">
					<CircleCheck class="mt-0.5 size-4 shrink-0 text-success-fg" aria-hidden="true" />
					<span>
						Imported <span class="font-medium">{lastImport.source}</span>:
						{importTotal === 0
							? 'the package was empty.'
							: importSummary
									.filter((s) => s.items.length > 0)
									.map((s) => counted(s.key, s.items.length))
									.join(', ') + '.'}
					</span>
				</p>
				{#if importTotal > 0}
					<dl class="grid gap-x-3 gap-y-1 pl-6 text-xs sm:grid-cols-[auto_1fr]">
						{#each importSummary.filter((s) => s.items.length > 0) as entry (entry.key)}
							<dt class="text-muted-foreground capitalize">{entry.label}</dt>
							<dd class="break-words font-mono">{entry.items.join(', ')}</dd>
						{/each}
					</dl>
				{/if}
			</div>
		{/if}

		{#if error}
			<ErrorAlert message={error} />
		{/if}
	</Dialog.Content>
</Dialog.Root>
