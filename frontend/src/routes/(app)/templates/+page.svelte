<script lang="ts">
	import { onMount } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import {
		getStacks,
		getAppTemplates,
		getContentBlocks,
		getGenerationProfiles,
		createAppTemplate,
		updateAppTemplate,
		deleteAppTemplate,
		importTemplatePackage,
		getStarterTemplatePackages,
		type Stack,
		type AppRequirementTemplate,
		type ContentBlock,
		type StarterTemplatePackage,
		type GenerationProfile,
	} from '$lib/api/client';
	import StacksList from '$lib/components/templates/StacksList.svelte';
	import AppTemplateList from '$lib/components/templates/AppTemplateList.svelte';
	import AppTemplateForm from '$lib/components/templates/AppTemplateForm.svelte';
	import ProfilesTab from '$lib/components/templates/ProfilesTab.svelte';
	import PackageImportForm from '$lib/components/templates/PackageImportForm.svelte';
	import Layers from '@lucide/svelte/icons/layers';
	import Package from '@lucide/svelte/icons/package';
	import FileText from '@lucide/svelte/icons/file-text';
	import Plus from '@lucide/svelte/icons/plus';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import Search from '@lucide/svelte/icons/search';

	type TabId = 'stacks' | 'app' | 'profiles';
	let activeTab = $state<TabId>('stacks');

	let profiles = $state<GenerationProfile[]>([]);
	let profilesLoading = $state(true);

	// Stacks (read-only, from the runtime scaffolding manifest)
	let stacks = $state<Stack[]>([]);
	let stacksLoading = $state(true);
	let stackSearch = $state('');

	// App templates
	let appTemplates = $state<AppRequirementTemplate[]>([]);
	let appLoading = $state(true);
	let appSearch = $state('');
	let editingApp = $state<AppRequirementTemplate | null>(null);
	let creatingApp = $state(false);
	let appForm = $state({ name: '', slug: '', description: '', backend_requirements: '', frontend_requirements: '', admin_requirements: '' });
	let appSaving = $state(false);
	let appError = $state('');

	let contentBlocks = $state<ContentBlock[]>([]);
	let blocksLoading = $state(true);
	let starterPackages = $state<StarterTemplatePackage[]>([]);
	let starterPackagesLoading = $state(true);

	// Package import
	let importingBundle = $state(false);
	let bundlePackageText = $state('');
	let bundleConflictStrategy = $state<'rename' | 'overwrite' | 'error'>('rename');
	let bundleImporting = $state(false);
	let bundleError = $state('');

	// Derived state for Master-Detail view
	const isEditingOrCreating = $derived(
		creatingApp ||
		editingApp !== null ||
		importingBundle
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
		try {
			profiles = await getGenerationProfiles();
		} catch {
			/* ignore */
		}
		profilesLoading = false;
	}

	async function loadStarterPackages() {
		starterPackagesLoading = true;
		try {
			starterPackages = await getStarterTemplatePackages();
		} catch {
			/* ignore */
		}
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
		cancelBundleImport();
	}

	function handleTabChange(tab: TabId) {
		cancelAllForms();
		activeTab = tab;
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
		if (!confirm(`Delete app template "${t.name}"?`)) return;
		try {
			await deleteAppTemplate(t.slug);
			if (editingApp?.slug === t.slug) cancelAppForm();
			await loadApp();
		} catch { /* ignore */ }
	}

	// ── Package import ──────────────────────────────────────────

	function startImportBundle() {
		cancelAllForms();
		importingBundle = true;
		bundlePackageText = '';
		bundleConflictStrategy = 'rename';
		bundleError = '';
	}

	function cancelBundleImport() {
		importingBundle = false;
		bundlePackageText = '';
		bundleError = '';
	}

	async function submitBundleImport() {
		bundleImporting = true;
		bundleError = '';
		try {
			await importTemplatePackage({
				package_text: bundlePackageText,
				conflict_strategy: bundleConflictStrategy,
			});
			cancelBundleImport();
			await refreshAllAssets();
		} catch (e: any) {
			bundleError = e?.detail ?? e?.message ?? 'Import failed';
		}
		bundleImporting = false;
	}
</script>

<svelte:head>
	<title>Template Management - LLM Lab</title>
</svelte:head>

<div class="space-y-6">
	<!-- Header -->
	<div class="page-header min-w-0">
		<h1>Template Management</h1>
		<p>Create, edit, and orchestrate templates for sample generation.</p>
	</div>

	<!-- Tabs Menu -->
	<div class="flex gap-1 rounded-md bg-muted p-1 overflow-x-auto [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden flex-nowrap shadow-inner">
		<button
			type="button"
			class="flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-all duration-200 whitespace-nowrap cursor-pointer {activeTab === 'stacks' ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}"
			onclick={() => handleTabChange('stacks')}
		>
			<Layers class="h-4 w-4" />
			Stacks <span class="ml-1 text-xs opacity-60 font-mono">({stacks.length})</span>
		</button>
		<button
			type="button"
			class="flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-all duration-200 whitespace-nowrap cursor-pointer {activeTab === 'app' ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}"
			onclick={() => handleTabChange('app')}
		>
			<FileText class="h-4 w-4" />
			App Requirements <span class="ml-1 text-xs opacity-60 font-mono">({appTemplates.length})</span>
		</button>
		<button
			type="button"
			class="flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-all duration-200 whitespace-nowrap cursor-pointer {activeTab === 'profiles' ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}"
			onclick={() => handleTabChange('profiles')}
		>
			<Package class="h-4 w-4" />
			Profiles <span class="ml-1 text-xs opacity-60 font-mono">({profiles.length})</span>
		</button>
	</div>

	<!-- Master-Detail Content Grid -->
	<div class="grid gap-6 {isEditingOrCreating ? 'lg:grid-cols-[1fr_1.2fr]' : 'grid-cols-1'}">

		<!-- LIST SECTION -->
		<div class="space-y-4 {isEditingOrCreating ? 'hidden lg:block' : ''}">

			<!-- SEARCH & ACTIONS BAR (Only for CRUD tabs) -->
			{#if activeTab !== 'profiles'}
				<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between rounded-md border border-border bg-card p-3">
					<div class="relative w-full sm:w-72">
						<Search class="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
						{#if activeTab === 'stacks'}
							<Input bind:value={stackSearch} placeholder="Search stacks…" class="pl-9 h-9 text-xs transition-all hover:border-primary/45 focus-visible:ring-primary/20" />
						{:else if activeTab === 'app'}
							<Input bind:value={appSearch} placeholder="Search app requirements…" class="pl-9 h-9 text-xs transition-all hover:border-primary/45 focus-visible:ring-primary/20" />
						{/if}
					</div>

					{#if activeTab === 'app'}
						<Button size="sm" class="w-full sm:w-auto text-xs font-semibold cursor-pointer shadow-xs transition-all duration-200" onclick={startCreateApp}>
							<Plus class="mr-1.5 h-4 w-4" /> New App Template
						</Button>
					{/if}
				</div>
			{/if}

			{#if activeTab === 'stacks'}
				<StacksList
					stacks={filteredStacks}
					loading={stacksLoading}
					compact={isEditingOrCreating}
				/>
			{/if}

			{#if activeTab === 'app'}
				<AppTemplateList
					templates={filteredApp}
					loading={appLoading}
					activeSlug={editingApp?.slug ?? null}
					onEdit={startEditApp}
					onDelete={deleteApp}
				/>
			{/if}

			{#if activeTab === 'profiles'}
				<ProfilesTab
					profiles={profiles}
					{profilesLoading}
					{starterPackages}
					{starterPackagesLoading}
					{appTemplates}
					{contentBlocks}
					{blocksLoading}
					conflictStrategy={bundleConflictStrategy}
					onStartImport={startImportBundle}
					onRefreshAll={refreshAllAssets}
				/>
			{/if}

		</div>

		<!-- DETAIL/EDITOR COLUMN -->
		{#if isEditingOrCreating}
			<div class="space-y-4">
				<!-- Mobile back button to close panel and return to list -->
				<button
					type="button"
					class="lg:hidden inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors cursor-pointer mb-2"
					onclick={cancelAllForms}
				>
					<ArrowLeft class="h-4 w-4" /> Back to templates list
				</button>

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

				{#if importingBundle}
					<PackageImportForm
						bind:packageText={bundlePackageText}
						bind:conflictStrategy={bundleConflictStrategy}
						importing={bundleImporting}
						error={bundleError}
						onSubmit={submitBundleImport}
						onCancel={cancelBundleImport}
					/>
				{/if}
			</div>
		{/if}
	</div>
</div>
