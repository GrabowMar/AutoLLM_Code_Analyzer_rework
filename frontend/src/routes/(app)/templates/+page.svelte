<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { toast } from 'svelte-sonner';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import { Segmented } from '$lib/components/ui/segmented';
	import * as Tabs from '$lib/components/ui/tabs';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { formatApiError } from '$lib/api/core';
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
		getAgentConfig,
		listRunPresets,
		createRunPreset,
		updateRunPreset,
		archiveRunPreset,
		type AgentConfigInfo,
		type StackWritePayload,
		type Stack,
		type AppRequirementTemplate,
		type ContentBlock,
		type StarterTemplatePackage,
		type GenerationProfile,
		type ProfileWritePayload,
		type PresetKind,
		type RunPreset,
	} from '$lib/api/generation';
	import { invalidatePresets } from '$lib/components/generator';
	import StacksList from '$lib/components/templates/StacksList.svelte';
	import StackEditor from '$lib/components/templates/StackEditor.svelte';
	import AppTemplateList from '$lib/components/templates/AppTemplateList.svelte';
	import AppTemplateForm from '$lib/components/templates/AppTemplateForm.svelte';
	import ProfileList from '$lib/components/templates/ProfileList.svelte';
	import ProfileEditor from '$lib/components/templates/ProfileEditor.svelte';
	import BlockList from '$lib/components/templates/BlockList.svelte';
	import BlockEditor from '$lib/components/templates/BlockEditor.svelte';
	import type { BlockSavePayload } from '$lib/components/templates/BlockEditor.svelte';
	import PresetList from '$lib/components/templates/PresetList.svelte';
	import PresetEditor, { type PresetSaveData } from '$lib/components/templates/PresetEditor.svelte';
	import PackagesDialog from '$lib/components/templates/PackagesDialog.svelte';
	import { PRESET_KIND_TITLES, PRESET_KINDS, isPresetKind } from '$lib/components/templates/presets';
	import Layers from '@lucide/svelte/icons/layers';
	import Package from '@lucide/svelte/icons/package';
	import FileText from '@lucide/svelte/icons/file-text';
	import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';
	import Settings2 from '@lucide/svelte/icons/settings-2';
	import Plus from '@lucide/svelte/icons/plus';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import Search from '@lucide/svelte/icons/search';

	// ── URL state: ?tab=profiles|specs|stacks|presets&kind=harness|sampling|budget ──
	type LibraryTab = 'profiles' | 'specs' | 'stacks' | 'presets';
	const TABS: LibraryTab[] = ['profiles', 'specs', 'stacks', 'presets'];

	function parseTab(raw: string | null): LibraryTab {
		if (raw === 'app') return 'specs'; // pre-rework links
		return TABS.includes(raw as LibraryTab) ? (raw as LibraryTab) : 'profiles';
	}

	const activeTab = $derived(parseTab(page.url.searchParams.get('tab')));
	const presetKind = $derived.by((): PresetKind => {
		const raw = page.url.searchParams.get('kind');
		return isPresetKind(raw) ? raw : 'harness';
	});

	function navigate(next: { tab?: LibraryTab; kind?: PresetKind }) {
		const url = new URL(page.url);
		const tab = next.tab ?? activeTab;
		url.searchParams.set('tab', tab);
		if (tab === 'presets') url.searchParams.set('kind', next.kind ?? presetKind);
		else url.searchParams.delete('kind');
		goto(url, { replaceState: true, keepFocus: true, noScroll: true });
	}

	// Profiles + blocks
	let profiles = $state<GenerationProfile[]>([]);
	let profilesLoading = $state(true);
	let profilesLoadError = $state('');
	let profilesView = $state<'profiles' | 'blocks'>('profiles');
	let editingProfile = $state<GenerationProfile | null>(null);
	let creatingProfile = $state(false);
	let duplicatingProfile = $state(false);
	let profileSaving = $state(false);
	let profileError = $state('');

	let contentBlocks = $state<ContentBlock[]>([]);
	let blocksLoading = $state(true);
	let blocksLoadError = $state('');
	let editingBlock = $state<ContentBlock | null>(null);
	let creatingBlock = $state(false);
	let blockSaving = $state(false);
	let blockError = $state('');

	// Stacks (builtin rows are read-only; user stacks are editable)
	let stacks = $state<Stack[]>([]);
	let stacksLoading = $state(true);
	let stacksLoadError = $state('');
	let stackSearch = $state('');
	let editingStackSlug = $state<string | null>(null);
	let duplicatingStackSlug = $state<string | null>(null);
	let creatingStack = $state(false);
	let stackSaving = $state(false);
	let stackError = $state('');

	// App specs
	let appTemplates = $state<AppRequirementTemplate[]>([]);
	let appLoading = $state(true);
	let appLoadError = $state('');
	let appSearch = $state('');
	let editingApp = $state<AppRequirementTemplate | null>(null);
	let creatingApp = $state(false);
	let appForm = $state({ name: '', slug: '', description: '', backend_requirements: '', frontend_requirements: '', admin_requirements: '' });
	let appSaving = $state(false);
	let appError = $state('');

	// Presets (every kind; the sub-tab filters)
	let presets = $state<RunPreset[]>([]);
	let presetsLoading = $state(true);
	let presetsLoadError = $state('');
	let showArchived = $state(false);
	let presetEditor = $state<{ kind: PresetKind; preset: RunPreset | null; duplicate: boolean; harness: string } | null>(null);
	let presetSaving = $state(false);
	let presetError = $state('');

	// Platform defaults, limits and the harness catalog (option schemas)
	let agentConfig = $state<AgentConfigInfo | null>(null);
	let agentConfigLoading = $state(true);
	let agentConfigError = $state('');

	// Packages
	let starterPackages = $state<StarterTemplatePackage[]>([]);
	let starterPackagesLoading = $state(true);
	let starterPackagesError = $state('');
	let packagesOpen = $state(false);

	// Confirmation for destructive actions
	let confirmState = $state<{ title: string; description: string; confirmLabel: string; action: () => Promise<void> } | null>(null);
	let confirmOpen = $state(false);
	let confirmBusy = $state(false);
	let actionError = $state('');

	const isEditingOrCreating = $derived(
		creatingApp || editingApp !== null ||
		creatingProfile || editingProfile !== null ||
		creatingBlock || editingBlock !== null ||
		creatingStack || editingStackSlug !== null || duplicatingStackSlug !== null ||
		presetEditor !== null
	);

	const filteredStacks = $derived.by(() => {
		const q = stackSearch.trim().toLowerCase();
		return stacks.filter(
			(s) => !q || s.slug.includes(q) || (s.name ?? '').toLowerCase().includes(q) || (s.aliases ?? []).some((a) => a.includes(q))
		);
	});

	const filteredApp = $derived.by(() => {
		const q = appSearch.trim().toLowerCase();
		return appTemplates.filter((t) => !q || t.name.toLowerCase().includes(q) || t.slug.includes(q));
	});

	const kindPresets = $derived(presets.filter((p) => p.kind === presetKind));
	const presetCounts = $derived(
		Object.fromEntries(PRESET_KINDS.map((k) => [k, presets.filter((p) => p.kind === k && !p.is_archived).length])) as Record<PresetKind, number>
	);
	const kindItems = $derived(
		PRESET_KINDS.map((k) => ({ value: k, label: PRESET_KIND_TITLES[k], badge: presetsLoading ? undefined : String(presetCounts[k]) }))
	);
	const tunableHarnesses = $derived((agentConfig?.harnesses ?? []).filter((h) => h.option_schema.length > 0));

	const KIND_INTRO: Record<PresetKind, string> = {
		harness: 'Which agent harness runs, with which options. Pick several in one batch to compare harness configurations.',
		sampling: 'Model-agnostic sampling settings (temperature, reasoning effort, output cap, …) applied identically to every model in a batch.',
		budget: 'Per-run request budget, safety caps and network access; batch-wide, so every condition gets the same budget.',
	};

	// ── loading ───────────────────────────────────────────────────
	async function loadStacks() {
		stacksLoading = true;
		stacksLoadError = '';
		try {
			stacks = await getStacks();
		} catch (e) {
			stacksLoadError = formatApiError(e, 'Could not load stacks.');
		}
		stacksLoading = false;
	}

	async function loadApp() {
		appLoading = true;
		appLoadError = '';
		try {
			appTemplates = await getAppTemplates();
		} catch (e) {
			appLoadError = formatApiError(e, 'Could not load app specs.');
		}
		appLoading = false;
	}

	async function loadBlocks() {
		blocksLoading = true;
		blocksLoadError = '';
		try {
			contentBlocks = await getContentBlocks();
		} catch (e) {
			blocksLoadError = formatApiError(e, 'Could not load blocks.');
		}
		blocksLoading = false;
	}

	async function loadProfiles() {
		profilesLoading = true;
		profilesLoadError = '';
		try {
			profiles = await getGenerationProfiles();
		} catch (e) {
			profilesLoadError = formatApiError(e, 'Could not load profiles.');
		}
		profilesLoading = false;
	}

	async function loadPresets() {
		// Silent refresh keeps the list on screen.
		presetsLoading = presets.length === 0;
		presetsLoadError = '';
		try {
			presets = await listRunPresets(undefined, { include_archived: showArchived });
		} catch (e) {
			presetsLoadError = formatApiError(e, 'Could not load presets.');
		}
		presetsLoading = false;
	}

	async function loadAgentConfig() {
		agentConfigLoading = true;
		agentConfigError = '';
		try {
			agentConfig = await getAgentConfig();
		} catch (e) {
			agentConfigError = formatApiError(e, 'Could not load the harness catalog and budget limits.');
		}
		agentConfigLoading = false;
	}

	async function loadStarterPackages() {
		starterPackagesLoading = true;
		starterPackagesError = '';
		try {
			starterPackages = await getStarterTemplatePackages();
		} catch (e) {
			starterPackagesError = formatApiError(e, 'Could not load starter packages.');
		}
		starterPackagesLoading = false;
	}

	async function refreshAllAssets() {
		invalidatePresets();
		await Promise.all([loadApp(), loadBlocks(), loadProfiles(), loadStacks(), loadPresets()]);
	}

	onMount(() => {
		loadStacks();
		loadApp();
		loadBlocks();
		loadProfiles();
		loadPresets();
		loadAgentConfig();
		loadStarterPackages();
	});

	function cancelAllForms() {
		cancelAppForm();
		cancelProfileForm();
		cancelBlockForm();
		cancelStackForm();
		cancelPresetForm();
		actionError = '';
	}

	function askConfirm(state: { title: string; description: string; confirmLabel: string; action: () => Promise<void> }) {
		actionError = '';
		confirmState = state;
		confirmOpen = true;
	}

	async function runConfirmed() {
		if (!confirmState) return;
		confirmBusy = true;
		try {
			await confirmState.action();
		} catch (e) {
			actionError = formatApiError(e, 'The action failed.');
		} finally {
			confirmBusy = false;
			confirmOpen = false;
		}
	}

	// ── Stack CRUD ──────────────────────────────────────────────

	function startCreateStack() {
		cancelAllForms();
		creatingStack = true;
	}

	function startEditStack(s: Stack) {
		cancelAllForms();
		editingStackSlug = s.slug;
	}

	function startDuplicateStack(s: Stack) {
		cancelAllForms();
		duplicatingStackSlug = s.slug;
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
		} catch (e) {
			stackError = formatApiError(e, 'Save failed.');
		}
		stackSaving = false;
	}

	function archiveStackRow(s: Stack) {
		askConfirm({
			title: `Archive stack ${s.slug}?`,
			description: 'Every version is archived. Existing jobs keep their pinned snapshots; new runs can no longer pick it.',
			confirmLabel: 'Archive stack',
			action: async () => {
				await archiveStack(s.slug);
				if (editingStackSlug === s.slug) cancelStackForm();
				await loadStacks();
			},
		});
	}

	// ── Profile CRUD ────────────────────────────────────────────

	function startCreateProfile() {
		cancelAllForms();
		creatingProfile = true;
	}

	function startEditProfile(p: GenerationProfile) {
		cancelAllForms();
		editingProfile = p;
		duplicatingProfile = false;
	}

	function startDuplicateProfile(p: GenerationProfile) {
		cancelAllForms();
		editingProfile = p;
		duplicatingProfile = true;
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
		} catch (e) {
			profileError = formatApiError(e, 'Save failed.');
		}
		profileSaving = false;
	}

	function archiveProfile(p: GenerationProfile) {
		askConfirm({
			title: `Archive ${p.name}?`,
			description: 'Every version is archived. Existing jobs keep their frozen snapshots.',
			confirmLabel: 'Archive profile',
			action: async () => {
				await archiveGenerationProfile(p.slug);
				if (editingProfile?.slug === p.slug) cancelProfileForm();
				await loadProfiles();
			},
		});
	}

	// ── Block CRUD ──────────────────────────────────────────────

	function startCreateBlock() {
		cancelAllForms();
		creatingBlock = true;
	}

	function startEditBlock(b: ContentBlock) {
		cancelAllForms();
		editingBlock = b;
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
		} catch (e) {
			blockError = formatApiError(e, 'Save failed.');
		}
		blockSaving = false;
	}

	// ── App CRUD ────────────────────────────────────────────────

	function startCreateApp() {
		cancelAllForms();
		creatingApp = true;
		appForm = { name: '', slug: '', description: '', backend_requirements: '', frontend_requirements: '', admin_requirements: '' };
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
			const parse = (s: string) => s.split('\n').map((l) => l.trim()).filter(Boolean);
			const data = {
				name: appForm.name.trim(),
				description: appForm.description,
				backend_requirements: parse(appForm.backend_requirements),
				frontend_requirements: parse(appForm.frontend_requirements),
				admin_requirements: parse(appForm.admin_requirements),
			};
			if (editingApp) {
				await updateAppTemplate(editingApp.slug, data);
			} else {
				await createAppTemplate({ ...data, slug: appForm.slug.trim() });
			}
			cancelAppForm();
			await loadApp();
		} catch (e) {
			appError = formatApiError(e, 'Save failed.');
		}
		appSaving = false;
	}

	function deleteApp(t: AppRequirementTemplate) {
		askConfirm({
			title: `Delete app spec ${t.name}?`,
			description: 'The spec is removed from the Library. Jobs that already ran against it keep their snapshot.',
			confirmLabel: 'Delete app spec',
			action: async () => {
				await deleteAppTemplate(t.slug);
				if (editingApp?.slug === t.slug) cancelAppForm();
				await loadApp();
			},
		});
	}

	// ── Preset CRUD ─────────────────────────────────────────────

	function startCreatePreset(harness = '') {
		cancelAllForms();
		presetEditor = { kind: presetKind, preset: null, duplicate: false, harness };
	}

	function startEditPreset(p: RunPreset) {
		cancelAllForms();
		presetEditor = { kind: p.kind, preset: p, duplicate: false, harness: '' };
	}

	function startDuplicatePreset(p: RunPreset) {
		cancelAllForms();
		presetEditor = { kind: p.kind, preset: p, duplicate: true, harness: '' };
	}

	function cancelPresetForm() {
		presetEditor = null;
		presetError = '';
	}

	async function savePreset(data: PresetSaveData, isNew: boolean) {
		presetSaving = true;
		presetError = '';
		try {
			const saved = isNew
				? await createRunPreset({ ...data })
				: await updateRunPreset(data.kind, data.slug, {
						name: data.name,
						description: data.description,
						config: data.config,
					});
			invalidatePresets(data.kind);
			toast.success(isNew ? `Created ${saved.name}` : `Saved ${saved.name} v${saved.version}`);
			cancelPresetForm();
			await loadPresets();
		} catch (e) {
			presetError = formatApiError(e, 'Save failed.');
		}
		presetSaving = false;
	}

	function archivePreset(p: RunPreset) {
		askConfirm({
			title: `Archive ${p.name}?`,
			description:
				'Every version is archived and disappears from preset pickers. Jobs, batches and experiments that used it keep their reference.',
			confirmLabel: 'Archive preset',
			action: async () => {
				await archiveRunPreset(p.kind, p.slug);
				invalidatePresets(p.kind);
				if (presetEditor?.preset?.slug === p.slug) cancelPresetForm();
				toast.success(`Archived ${p.name}`);
				await loadPresets();
			},
		});
	}

	function toggleArchived(on: boolean) {
		showArchived = on;
		loadPresets();
	}
</script>

<div class="space-y-6">
	<PageHeader
		title="Generation Library"
		description="Everything a generation run is built from: what to build, where it runs, the prompts for template scaffolding, and reusable run settings."
	>
		{#snippet actions()}
			<Button variant="outline" size="sm" onclick={() => (packagesOpen = true)}>
				<Package /> Packages
			</Button>
		{/snippet}
		<p class="text-xs text-muted-foreground">
			<span class="font-medium text-foreground">App spec</span> what to build (graded) ·
			<span class="font-medium text-foreground">Stack</span> the skeleton it runs on ·
			<span class="font-medium text-foreground">Profile</span> prompt material for template scaffolding ·
			<span class="font-medium text-foreground">Preset</span> reusable harness, sampling or budget settings
		</p>
	</PageHeader>

	{#if actionError}
		<ErrorAlert message={actionError} />
	{/if}

	<Tabs.Root
		value={activeTab}
		onValueChange={(v) => {
			cancelAllForms();
			navigate({ tab: parseTab(v) });
		}}
	>
		<Tabs.List>
			<Tabs.Trigger value="profiles">
				<SlidersHorizontal />
				Profiles <span class="text-xs text-muted-foreground font-mono">({profiles.length})</span>
			</Tabs.Trigger>
			<Tabs.Trigger value="specs">
				<FileText />
				App Specs <span class="text-xs text-muted-foreground font-mono">({appTemplates.length})</span>
			</Tabs.Trigger>
			<Tabs.Trigger value="stacks">
				<Layers />
				Stacks <span class="text-xs text-muted-foreground font-mono">({stacks.length})</span>
			</Tabs.Trigger>
			<Tabs.Trigger value="presets">
				<Settings2 />
				Presets <span class="text-xs text-muted-foreground font-mono">({presets.filter((p) => !p.is_archived).length})</span>
			</Tabs.Trigger>
		</Tabs.List>

		<!-- Master-Detail Content Grid -->
		<div class="grid gap-6 pt-4 {isEditingOrCreating ? 'lg:grid-cols-[1fr_1.2fr]' : 'grid-cols-1'}">

			<!-- LIST SECTION -->
			<div class="min-w-0 {isEditingOrCreating ? 'hidden lg:block' : ''}">
				<Tabs.Content value="profiles" class="space-y-4 pt-0">
					<p class="text-xs text-muted-foreground">
						Prompt material for template scaffolding: ordered prompt blocks that target one stack, plus LLM defaults.
						Agent runs use a brief instead of prompt blocks.
					</p>
					<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
						<Segmented
							size="sm"
							aria-label="Show profiles or blocks"
							value={profilesView}
							items={[
								{ value: 'profiles', label: 'Profiles' },
								{ value: 'blocks', label: 'Blocks' },
							]}
							onValueChange={(v) => {
								cancelAllForms();
								profilesView = v as 'profiles' | 'blocks';
							}}
						/>
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
						{#if profilesLoadError}
							<ErrorAlert message={profilesLoadError} onretry={loadProfiles} />
						{:else}
							<ProfileList
								{profiles}
								loading={profilesLoading}
								activeSlug={editingProfile?.slug ?? null}
								onEdit={startEditProfile}
								onDuplicate={startDuplicateProfile}
								onArchive={archiveProfile}
							/>
						{/if}
					{:else if blocksLoadError}
						<ErrorAlert message={blocksLoadError} onretry={loadBlocks} />
					{:else}
						<BlockList
							blocks={contentBlocks}
							loading={blocksLoading}
							activeSlug={editingBlock?.slug ?? null}
							onEdit={startEditBlock}
						/>
					{/if}
				</Tabs.Content>

				<Tabs.Content value="specs" class="space-y-4 pt-0">
					<p class="text-xs text-muted-foreground">
						What to build. Runs against an app spec are graded against its requirements and API contract.
					</p>
					<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
						<div class="relative w-full sm:w-72">
							<Search class="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" aria-hidden="true" />
							<Input bind:value={appSearch} placeholder="Search app specs…" aria-label="Search app specs" class="pl-9 h-9" />
						</div>
						<Button size="sm" class="w-full sm:w-auto" onclick={startCreateApp}>
							<Plus /> New App Spec
						</Button>
					</div>
					{#if appLoadError}
						<ErrorAlert message={appLoadError} onretry={loadApp} />
					{:else}
						<AppTemplateList
							templates={filteredApp}
							loading={appLoading}
							activeSlug={editingApp?.slug ?? null}
							onEdit={startEditApp}
							onDelete={deleteApp}
						/>
					{/if}
				</Tabs.Content>

				<Tabs.Content value="stacks" class="space-y-4 pt-0">
					<p class="text-xs text-muted-foreground">
						The project skeleton a run starts from. Stacks you create need an admin's approval before anyone else can run them.
					</p>
					<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
						<div class="relative w-full sm:w-72">
							<Search class="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" aria-hidden="true" />
							<Input bind:value={stackSearch} placeholder="Search stacks…" aria-label="Search stacks" class="pl-9 h-9" />
						</div>
						<Button size="sm" class="w-full sm:w-auto" onclick={startCreateStack}>
							<Plus /> New Stack
						</Button>
					</div>
					{#if stacksLoadError}
						<ErrorAlert message={stacksLoadError} onretry={loadStacks} />
					{:else}
						<StacksList
							stacks={filteredStacks}
							loading={stacksLoading}
							compact={isEditingOrCreating}
							activeSlug={editingStackSlug ?? duplicatingStackSlug}
							onEdit={startEditStack}
							onDuplicate={startDuplicateStack}
							onArchive={archiveStackRow}
						/>
					{/if}
				</Tabs.Content>

				<Tabs.Content value="presets" class="space-y-4 pt-0">
					<p class="text-xs text-muted-foreground">
						Reusable, versioned run settings for agent runs. Changing a preset's content saves a new version; every job
						records the exact version it ran with.
					</p>
					<!-- Wraps instead of squeezing the kind switch when the editor column is open. -->
					<div class="flex flex-wrap items-center justify-between gap-3">
						<Segmented
							aria-label="Preset kind"
							value={presetKind}
							items={kindItems}
							onValueChange={(v) => {
								cancelPresetForm();
								navigate({ tab: 'presets', kind: isPresetKind(v) ? v : 'harness' });
							}}
						/>
						<div class="flex flex-wrap items-center gap-3">
							<label class="flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
								<Checkbox checked={showArchived} onCheckedChange={toggleArchived} />
								Show archived
							</label>
							<Button
								size="sm"
								class="w-full sm:w-auto"
								disabled={presetKind === 'harness' && tunableHarnesses.length === 0}
								title={presetKind === 'harness' && tunableHarnesses.length === 0 ? 'No harness has tunable options' : undefined}
								onclick={() => startCreatePreset()}
							>
								<Plus /> New {presetKind} preset
							</Button>
						</div>
					</div>
					<p class="text-xs text-muted-foreground">{KIND_INTRO[presetKind]}</p>
					{#if agentConfigError && presetKind !== 'sampling'}
						<ErrorAlert message={agentConfigError} onretry={loadAgentConfig} />
					{/if}
					{#if presetsLoadError}
						<ErrorAlert message={presetsLoadError} onretry={loadPresets} />
					{:else}
						<PresetList
							kind={presetKind}
							presets={kindPresets}
							harnesses={agentConfig?.harnesses ?? []}
							loading={presetsLoading || (presetKind === 'harness' && agentConfigLoading)}
							activeSlug={presetEditor?.kind === presetKind ? (presetEditor.preset?.slug ?? null) : null}
							onCreate={startCreatePreset}
							onEdit={startEditPreset}
							onDuplicate={startDuplicatePreset}
							onArchive={archivePreset}
						/>
					{/if}
				</Tabs.Content>
			</div>

			<!-- DETAIL/EDITOR COLUMN -->
			{#if isEditingOrCreating}
				<div class="min-w-0 space-y-4">
					<!-- Mobile back button to close panel and return to list -->
					<Button variant="ghost" size="sm" class="lg:hidden" onclick={cancelAllForms}>
						<ArrowLeft /> Back to library
					</Button>

					{#if creatingProfile || editingProfile}
						{#key `${editingProfile?.id ?? 'new'}:${duplicatingProfile}`}
							<ProfileEditor
								initial={editingProfile}
								duplicate={duplicatingProfile}
								blocks={contentBlocks}
								{stacks}
								defaultStackSlug={agentConfig?.default_stack_slug}
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
						{#key editingApp?.slug ?? 'new'}
							<AppTemplateForm
								bind:form={appForm}
								isEdit={editingApp !== null}
								saving={appSaving}
								error={appError}
								onSave={saveApp}
								onCancel={cancelAppForm}
							/>
						{/key}
					{/if}

					{#if presetEditor}
						{#key `${presetEditor.kind}:${presetEditor.preset?.id ?? 'new'}:${presetEditor.duplicate}:${presetEditor.harness}`}
							<PresetEditor
								kind={presetEditor.kind}
								initial={presetEditor.preset}
								duplicate={presetEditor.duplicate}
								harnessName={presetEditor.harness}
								{agentConfig}
								takenSlugs={presets.filter((p) => p.kind === presetEditor?.kind).map((p) => p.slug)}
								saving={presetSaving}
								error={presetError}
								onSave={savePreset}
								onCancel={cancelPresetForm}
							/>
						{/key}
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
	{presets}
	{starterPackages}
	{starterPackagesLoading}
	{starterPackagesError}
	onRetryStarters={loadStarterPackages}
	onImported={refreshAllAssets}
/>

<ConfirmDialog
	bind:open={confirmOpen}
	title={confirmState?.title ?? ''}
	description={confirmState?.description ?? ''}
	confirmLabel={confirmState?.confirmLabel ?? 'Confirm'}
	destructive
	busy={confirmBusy}
	onConfirm={runConfirmed}
/>
