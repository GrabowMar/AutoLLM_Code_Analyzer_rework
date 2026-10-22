<script lang="ts">
	import { onMount } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import * as Tabs from '$lib/components/ui/tabs';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import {
		getStacks,
		getAppTemplates,
		getContentBlocks,
		getGenerationProfiles,
		createAppTemplate,
		updateAppTemplate,
		deleteAppTemplate,
		createGenerationProfile,
		updateGenerationProfile,
		archiveGenerationProfile,
		createContentBlock,
		createBlockVersion,
		createStack,
		updateStack,
		archiveStack,
		getStarterTemplatePackages,
		type StackWritePayload,
		type Stack,
		type AppRequirementTemplate,
		type ContentBlock,
		type StarterTemplatePackage,
		type GenerationProfile,
		type ProfileWritePayload,
	} from '$lib/api/client';
	import StacksList from '$lib/components/templates/StacksList.svelte';
	import StackEditor from '$lib/components/templates/StackEditor.svelte';
	import AppTemplateList from '$lib/components/templates/AppTemplateList.svelte';
	import AppTemplateForm from '$lib/components/templates/AppTemplateForm.svelte';
	import ProfileList from '$lib/components/templates/ProfileList.svelte';
	import ProfileEditor from '$lib/components/templates/ProfileEditor.svelte';
	import BlockList from '$lib/components/templates/BlockList.svelte';
	import BlockEditor from '$lib/components/templates/BlockEditor.svelte';
	import type { BlockSavePayload } from '$lib/components/templates/BlockEditor.svelte';
	import PackagesDialog from '$lib/components/templates/PackagesDialog.svelte';
	import Layers from '@lucide/svelte/icons/layers';
	import Package from '@lucide/svelte/icons/package';
	import FileText from '@lucide/svelte/icons/file-text';
	import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';
	import Plus from '@lucide/svelte/icons/plus';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import Search from '@lucide/svelte/icons/search';

	let activeTab = $state('profiles');

	// Profiles + blocks
	let profiles = $state<GenerationProfile[]>([]);
	let profilesLoading = $state(true);
	let profilesView = $state<'profiles' | 'blocks'>('profiles');
	let editingProfile = $state<GenerationProfile | null>(null);
	let creatingProfile = $state(false);
	let duplicatingProfile = $state(false);
	let profileSaving = $state(false);
	let profileError = $state('');

	let contentBlocks = $state<ContentBlock[]>([]);
	let blocksLoading = $state(true);
	let editingBlock = $state<ContentBlock | null>(null);
	let creatingBlock = $state(false);
	let blockSaving = $state(false);
	let blockError = $state('');

	// Stacks (builtin rows are read-only; user stacks are editable)
	let stacks = $state<Stack[]>([]);
	let stacksLoading = $state(true);
	let stackSearch = $state('');
	let editingStackSlug = $state<string | null>(null);
	let duplicatingStackSlug = $state<string | null>(null);
	let creatingStack = $state(false);
	let stackSaving = $state(false);
	let stackError = $state('');

	// App specs
	let appTemplates = $state<AppRequirementTemplate[]>([]);
	let appLoading = $state(true);
	let appSearch = $state('');
	let editingApp = $state<AppRequirementTemplate | null>(null);
	let creatingApp = $state(false);
	let appForm = $state({ name: '', slug: '', description: '', backend_requirements: '', frontend_requirements: '', admin_requirements: '' });
	let appSaving = $state(false);
	let appError = $state('');

	// Packages
	let starterPackages = $state<StarterTemplatePackage[]>([]);
	let starterPackagesLoading = $state(true);
	let packagesOpen = $state(false);

	// Derived state for Master-Detail view
	const isEditingOrCreating = $derived(
		creatingApp || editingApp !== null ||
		creatingProfile || editingProfile !== null ||
		creatingBlock || editingBlock !== null ||
		creatingStack || editingStackSlug !== null || duplicatingStackSlug !== null
	);

	const filteredStacks = $derived(
		stacks.filter(s =>
			!stackSearch || s.slug.includes(stackSearch.toLowerCase()) || s.aliases.some(a => a.includes(stackSearch.toLowerCase()))
		)
	);

	const filteredApp = $derived(
		appTemplates.filter(t =>
			!appSearch || t.name.toLowerCase().includes(appSearch.toLowerCase()) || t.slug.includes(appSearch.toLowerCase())
		)
	);

	async function loadStacks() {
		stacksLoading = true;
		try { stacks = await getStacks(); } catch { /* ignore */ }
		stacksLoading = false;
	}

	async function loadApp() {
		appLoading = true;
		try { appTemplates = await getAppTemplates(); } catch { /* ignore */ }
		appLoading = false;
	}

	async function loadBlocks() {
		blocksLoading = true;
		try { contentBlocks = await getContentBlocks(); } catch { /* ignore */ }
		blocksLoading = false;
	}

	async function loadProfiles() {
		profilesLoading = true;
		try { profiles = await getGenerationProfiles(); } catch { /* ignore */ }
		profilesLoading = false;
	}

	async function loadStarterPackages() {
		starterPackagesLoading = true;
		try { starterPackages = await getStarterTemplatePackages(); } catch { /* ignore */ }
		starterPackagesLoading = false;
	}

	async function refreshAllAssets() {
		await Promise.all([loadApp(), loadBlocks(), loadProfiles()]);
	}

	onMount(() => {
		loadStacks();
		loadApp();
		loadBlocks();
		loadProfiles();
		loadStarterPackages();
	});

	function cancelAllForms() {
		cancelAppForm();
		cancelProfileForm();
		cancelBlockForm();
		cancelStackForm();
	}

	// ── Stack CRUD ──────────────────────────────────────────────

	function startCreateStack() {
		cancelAllForms();
		creatingStack = true;
		stackError = '';
	}

	function startEditStack(s: Stack) {
		cancelAllForms();
		editingStackSlug = s.slug;
		stackError = '';
	}

	function startDuplicateStack(s: Stack) {
		cancelAllForms();
		duplicatingStackSlug = s.slug;
		stackError = '';
	}

	function cancelStackForm() {
		creatingStack = false;
		editingStackSlug = null;
		duplicatingStackSlug = null;
		stackError = '';
	}

	async function saveStack(payload: StackWritePayload, isNewSlug: boolean) {
		stackSaving = true;
		stackError = '';
		try {
			if (isNewSlug) {
				await createStack(payload);
			} else {
				await updateStack(editingStackSlug!, payload);
			}
			cancelStackForm();
			await loadStacks();
		} catch (e: any) {
			stackError = e?.detail ?? e?.message ?? 'Save failed';
		}
		stackSaving = false;
	}

	async function archiveStackRow(s: Stack) {
		if (!confirm(`Archive all versions of stack "${s.slug}"? Existing jobs keep their pinned snapshots.`)) return;
		try {
			await archiveStack(s.slug);
			if (editingStackSlug === s.slug) cancelStackForm();
			await loadStacks();
		} catch { /* ignore */ }
	}

	// ── Profile CRUD ────────────────────────────────────────────

	function startCreateProfile() {
		cancelAllForms();
		creatingProfile = true;
		profileError = '';
	}

	function startEditProfile(p: GenerationProfile) {
		cancelAllForms();
		editingProfile = p;
		duplicatingProfile = false;
		profileError = '';
	}

	function startDuplicateProfile(p: GenerationProfile) {
		cancelAllForms();
		editingProfile = p;
		duplicatingProfile = true;
		profileError = '';
	}

	function cancelProfileForm() {
		creatingProfile = false;
		editingProfile = null;
		duplicatingProfile = false;
		profileError = '';
	}

	async function saveProfile(payload: ProfileWritePayload, isNewSlug: boolean) {
		profileSaving = true;
		profileError = '';
		try {
			if (isNewSlug) {
				await createGenerationProfile(payload);
			} else {
				await updateGenerationProfile(payload.slug, payload);
			}
			cancelProfileForm();
			await loadProfiles();
		} catch (e: any) {
			profileError = e?.detail ?? e?.message ?? 'Save failed';
		}
		profileSaving = false;
	}

	async function archiveProfile(p: GenerationProfile) {
		if (!confirm(`Archive all versions of "${p.name}"? Existing jobs keep their frozen snapshots.`)) return;
		try {
			await archiveGenerationProfile(p.slug);
			if (editingProfile?.slug === p.slug) cancelProfileForm();
			await loadProfiles();
		} catch { /* ignore */ }
	}

	// ── Block CRUD ──────────────────────────────────────────────

	function startCreateBlock() {
		cancelAllForms();
		creatingBlock = true;
		blockError = '';
	}

	function startEditBlock(b: ContentBlock) {
		cancelAllForms();
		editingBlock = b;
		blockError = '';
	}

	function cancelBlockForm() {
		creatingBlock = false;
		editingBlock = null;
		blockError = '';
	}

	async function saveBlock(payload: BlockSavePayload) {
		blockSaving = true;
		blockError = '';
		try {
			if (payload.mode === 'new-version') {
				await createBlockVersion(payload.slug, {
					content: payload.content,
					name: payload.name,
					description: payload.description,
					metadata: payload.metadata,
				});
			} else {
				await createContentBlock({
					block_type: payload.block_type,
					slug: payload.slug,
					name: payload.name,
					description: payload.description,
					content: payload.content,
					metadata: payload.metadata,
				});
			}
			cancelBlockForm();
			await loadBlocks();
		} catch (e: any) {
			blockError = e?.detail ?? e?.message ?? 'Save failed';
		}
		blockSaving = false;
	}

	// ── App CRUD ────────────────────────────────────────────────

	function startCreateApp() {
		cancelAllForms();
		creatingApp = true;
		appForm = { name: '', slug: '', description: '', backend_requirements: '', frontend_requirements: '', admin_requirements: '' };
		appError = '';
	}

	function startEditApp(t: AppRequirementTemplate) {
		cancelAllForms();
		editingApp = t;
		appForm = {
			name: t.name, slug: t.slug, description: t.description,
			backend_requirements: (t.backend_requirements || []).join('\n'),
			frontend_requirements: (t.frontend_requirements || []).join('\n'),
			admin_requirements: (t.admin_requirements || []).join('\n'),
		};
		appError = '';
	}

	function cancelAppForm() {
		creatingApp = false;
		editingApp = null;
		appError = '';
	}

	async function saveApp() {
		appSaving = true;
		appError = '';
		try {
			const parse = (s: string) => s.split('\n').map(l => l.trim()).filter(Boolean);
			const data = {
				name: appForm.name, slug: appForm.slug, description: appForm.description,
				backend_requirements: parse(appForm.backend_requirements),
				frontend_requirements: parse(appForm.frontend_requirements),
				admin_requirements: parse(appForm.admin_requirements),
			};
			if (editingApp) {
				await updateAppTemplate(editingApp.slug, data);
			} else {
				await createAppTemplate(data);
			}
			cancelAppForm();
			await loadApp();
		} catch (e: any) {
			appError = e?.message || 'Save failed';
		}
		appSaving = false;
	}

	async function deleteApp(t: AppRequirementTemplate) {
		if (!confirm(`Delete app spec "${t.name}"?`)) return;
		try {
			await deleteAppTemplate(t.slug);
			if (editingApp?.slug === t.slug) cancelAppForm();
			await loadApp();
		} catch { /* ignore */ }
	}
</script>

<div class="space-y-6">
	<PageHeader
		title="Generation Library"
		description="App specs describe what to build, profiles define the prompts and LLM behavior, stacks are where it runs."
	>
		{#snippet actions()}
			<Button variant="outline" size="sm" onclick={() => (packagesOpen = true)}>
				<Package /> Packages
			</Button>
		{/snippet}
	</PageHeader>

	<Tabs.Root bind:value={activeTab} onValueChange={cancelAllForms}>
		<Tabs.List>
			<Tabs.Trigger value="profiles">
				<SlidersHorizontal />
				Profiles <span class="text-xs text-muted-foreground font-mono">({profiles.length})</span>
			</Tabs.Trigger>
			<Tabs.Trigger value="app">
				<FileText />
				App Specs <span class="text-xs text-muted-foreground font-mono">({appTemplates.length})</span>
			</Tabs.Trigger>
			<Tabs.Trigger value="stacks">
				<Layers />
				Stacks <span class="text-xs text-muted-foreground font-mono">({stacks.length})</span>
			</Tabs.Trigger>
		</Tabs.List>

		<!-- Master-Detail Content Grid -->
		<div class="grid gap-6 pt-4 {isEditingOrCreating ? 'lg:grid-cols-[1fr_1.2fr]' : 'grid-cols-1'}">

			<!-- LIST SECTION -->
			<div class="{isEditingOrCreating ? 'hidden lg:block' : ''}">
				<Tabs.Content value="profiles" class="space-y-4 pt-0">
					<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
						<div class="flex items-center gap-1 rounded-md border bg-muted/40 p-0.5" role="group" aria-label="Show profiles or blocks">
							{#each [['profiles', 'Profiles'], ['blocks', 'Blocks']] as [id, label] (id)}
								<button
									type="button"
									aria-pressed={profilesView === id}
									class="rounded px-3 py-1 text-xs font-medium transition-colors {profilesView === id ? 'bg-background text-foreground shadow-xs' : 'text-muted-foreground hover:text-foreground'}"
									onclick={() => { cancelAllForms(); profilesView = id as 'profiles' | 'blocks'; }}
								>{label}</button>
							{/each}
						</div>
						{#if profilesView === 'profiles'}
							<Button size="sm" class="w-full sm:w-auto" onclick={startCreateProfile}>
								<Plus /> New Profile
							</Button>
						{:else}
							<Button size="sm" class="w-full sm:w-auto" onclick={startCreateBlock}>
								<Plus /> New Block
							</Button>
						{/if}
					</div>
					{#if profilesView === 'profiles'}
						<ProfileList
							{profiles}
							loading={profilesLoading}
							activeSlug={editingProfile?.slug ?? null}
							onEdit={startEditProfile}
							onDuplicate={startDuplicateProfile}
							onArchive={archiveProfile}
						/>
					{:else}
						<BlockList
							blocks={contentBlocks}
							loading={blocksLoading}
							activeSlug={editingBlock?.slug ?? null}
							onEdit={startEditBlock}
						/>
					{/if}
				</Tabs.Content>

				<Tabs.Content value="app" class="space-y-4 pt-0">
					<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
						<div class="relative w-full sm:w-72">
							<Search class="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" aria-hidden="true" />
							<Input bind:value={appSearch} placeholder="Search app specs…" aria-label="Search app specs" class="pl-9 h-9" />
						</div>
						<Button size="sm" class="w-full sm:w-auto" onclick={startCreateApp}>
								<Plus /> New App Spec
							</Button>
					</div>
					<AppTemplateList
						templates={filteredApp}
						loading={appLoading}
						activeSlug={editingApp?.slug ?? null}
						onEdit={startEditApp}
						onDelete={deleteApp}
					/>
				</Tabs.Content>

				<Tabs.Content value="stacks" class="space-y-4 pt-0">
					<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
						<div class="relative w-full sm:w-72">
							<Search class="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" aria-hidden="true" />
							<Input bind:value={stackSearch} placeholder="Search stacks…" aria-label="Search stacks" class="pl-9 h-9" />
						</div>
						<Button size="sm" class="w-full sm:w-auto" onclick={startCreateStack}>
								<Plus /> New Stack
							</Button>
					</div>
					<StacksList
						stacks={filteredStacks}
						loading={stacksLoading}
						compact={isEditingOrCreating}
						onEdit={startEditStack}
						onDuplicate={startDuplicateStack}
						onArchive={archiveStackRow}
					/>
				</Tabs.Content>
			</div>

			<!-- DETAIL/EDITOR COLUMN -->
			{#if isEditingOrCreating}
				<div class="space-y-4">
					<!-- Mobile back button to close panel and return to list -->
					<Button variant="ghost" size="sm" class="lg:hidden" onclick={cancelAllForms}>
						<ArrowLeft /> Back to library
					</Button>

					{#if creatingProfile || editingProfile}
						{#key editingProfile?.id ?? 'new'}
							<ProfileEditor
								initial={editingProfile}
								duplicate={duplicatingProfile}
								blocks={contentBlocks}
								{stacks}
								{appTemplates}
								saving={profileSaving}
								error={profileError}
								onSave={saveProfile}
								onCancel={cancelProfileForm}
							/>
						{/key}
					{/if}

					{#if creatingBlock || editingBlock}
						{#key editingBlock?.id ?? 'new'}
							<BlockEditor
								initial={editingBlock}
								saving={blockSaving}
								error={blockError}
								onSave={saveBlock}
								onCancel={cancelBlockForm}
							/>
						{/key}
					{/if}

					{#if creatingStack || editingStackSlug || duplicatingStackSlug}
						{#key editingStackSlug ?? duplicatingStackSlug ?? 'new'}
							<StackEditor
								editSlug={editingStackSlug}
								duplicateFromSlug={duplicatingStackSlug}
								saving={stackSaving}
								error={stackError}
								onSave={saveStack}
								onCancel={cancelStackForm}
							/>
						{/key}
					{/if}

					{#if creatingApp || editingApp}
						<AppTemplateForm
							bind:form={appForm}
							isEdit={editingApp !== null}
							saving={appSaving}
							error={appError}
							onSave={saveApp}
							onCancel={cancelAppForm}
						/>
					{/if}
				</div>
			{/if}
		</div>
	</Tabs.Root>
</div>

<PackagesDialog
	bind:open={packagesOpen}
	{profiles}
	{appTemplates}
	{contentBlocks}
	{stacks}
	{starterPackages}
	{starterPackagesLoading}
	onImported={refreshAllAssets}
/>
