<script lang="ts">
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Separator } from '$lib/components/ui/separator';
	import {
		getScaffoldingTemplates,
		getAppTemplates,
		getPromptTemplates,
		getTemplateBundles,
		createScaffoldingTemplate,
		updateScaffoldingTemplate,
		deleteScaffoldingTemplate,
		createAppTemplate,
		updateAppTemplate,
		deleteAppTemplate,
		createPromptTemplate,
		updatePromptTemplate,
		deletePromptTemplate,
		type ScaffoldingTemplate,
		type AppRequirementTemplate,
		type PromptTemplate,
		type TemplateBundle,
	} from '$lib/api/client';
	import { Textarea } from '$lib/components/ui/textarea';
	import Layers from '@lucide/svelte/icons/layers';
	import Package from '@lucide/svelte/icons/package';
	import FileText from '@lucide/svelte/icons/file-text';
	import MessageSquare from '@lucide/svelte/icons/message-square';
	import Plus from '@lucide/svelte/icons/plus';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Save from '@lucide/svelte/icons/save';
	import XIcon from '@lucide/svelte/icons/x';
	import Search from '@lucide/svelte/icons/search';

	type TabId = 'scaffolding' | 'app' | 'prompt' | 'bundles';
	let activeTab = $state<TabId>('scaffolding');

	let templateBundles = $state<TemplateBundle[]>([]);
	let bundlesLoading = $state(true);

	// Scaffolding templates
	let scaffoldingTemplates = $state<ScaffoldingTemplate[]>([]);
	let scaffoldingLoading = $state(true);
	let scaffoldingSearch = $state('');
	let editingScaffolding = $state<ScaffoldingTemplate | null>(null);
	let creatingScaffolding = $state(false);
	let scaffoldingForm = $state({ name: '', slug: '', description: '', tech_stack_json: '{}', substitution_vars_csv: '' });
	let scaffoldingSaving = $state(false);
	let scaffoldingError = $state('');

	// App templates
	let appTemplates = $state<AppRequirementTemplate[]>([]);
	let appLoading = $state(true);
	let appSearch = $state('');
	let editingApp = $state<AppRequirementTemplate | null>(null);
	let creatingApp = $state(false);
	let appForm = $state({ name: '', slug: '', description: '', backend_requirements: '', frontend_requirements: '', admin_requirements: '' });
	let appSaving = $state(false);
	let appError = $state('');

	// Prompt templates
	let promptTemplates = $state<PromptTemplate[]>([]);
	let promptLoading = $state(true);
	let promptStageFilter = $state('');
	let editingPrompt = $state<PromptTemplate | null>(null);
	let creatingPrompt = $state(false);
	let promptForm = $state({ name: '', slug: '', stage: 'backend', role: 'system', content: '' });
	let promptSaving = $state(false);
	let promptError = $state('');

	// Derived state for Master-Detail view
	const isEditingOrCreating = $derived(
		creatingScaffolding ||
		editingScaffolding !== null ||
		creatingApp ||
		editingApp !== null ||
		creatingPrompt ||
		editingPrompt !== null
	);

	const filteredScaffolding = $derived(
		scaffoldingTemplates.filter(t =>
			!scaffoldingSearch || t.name.toLowerCase().includes(scaffoldingSearch.toLowerCase()) || t.slug.includes(scaffoldingSearch.toLowerCase())
		)
	);

	const filteredApp = $derived(
		appTemplates.filter(t =>
			!appSearch || t.name.toLowerCase().includes(appSearch.toLowerCase()) || t.slug.includes(appSearch.toLowerCase())
		)
	);

	const filteredPrompt = $derived(
		promptTemplates.filter(t =>
			(!promptStageFilter || t.stage === promptStageFilter)
		)
	);

	const groupedPrompts = $derived(() => {
		const groups: Record<string, PromptTemplate[]> = {};
		for (const t of filteredPrompt) {
			const key = `${t.stage}/${t.role}`;
			if (!groups[key]) groups[key] = [];
			groups[key].push(t);
		}
		return groups;
	});

	async function loadScaffolding() {
		scaffoldingLoading = true;
		try { scaffoldingTemplates = await getScaffoldingTemplates(); } catch { /* ignore */ }
		scaffoldingLoading = false;
	}

	async function loadApp() {
		appLoading = true;
		try { appTemplates = await getAppTemplates(); } catch { /* ignore */ }
		appLoading = false;
	}

	async function loadPrompt() {
		promptLoading = true;
		try { promptTemplates = await getPromptTemplates(); } catch { /* ignore */ }
		promptLoading = false;
	}

	async function loadBundles() {
		bundlesLoading = true;
		try {
			templateBundles = await getTemplateBundles();
		} catch {
			/* ignore */
		}
		bundlesLoading = false;
	}

	onMount(() => {
		loadScaffolding();
		loadApp();
		loadPrompt();
		loadBundles();
	});

	function cancelAllForms() {
		cancelScaffoldingForm();
		cancelAppForm();
		cancelPromptForm();
	}

	function handleTabChange(tab: TabId) {
		cancelAllForms();
		activeTab = tab;
	}

	// ── Scaffolding CRUD ────────────────────────────────────────

	function startCreateScaffolding() {
		cancelAllForms();
		creatingScaffolding = true;
		scaffoldingForm = { name: '', slug: '', description: '', tech_stack_json: '{}', substitution_vars_csv: '' };
		scaffoldingError = '';
	}

	function startEditScaffolding(t: ScaffoldingTemplate) {
		cancelAllForms();
		editingScaffolding = t;
		scaffoldingForm = {
			name: t.name,
			slug: t.slug,
			description: t.description,
			tech_stack_json: JSON.stringify(t.tech_stack || {}, null, 2),
			substitution_vars_csv: (t.substitution_vars || []).join(', '),
		};
		scaffoldingError = '';
	}

	function cancelScaffoldingForm() {
		creatingScaffolding = false;
		editingScaffolding = null;
		scaffoldingError = '';
	}

	async function saveScaffolding() {
		scaffoldingSaving = true;
		scaffoldingError = '';
		try {
			let tech_stack = {};
			try { tech_stack = JSON.parse(scaffoldingForm.tech_stack_json); } catch { scaffoldingError = 'Invalid JSON for tech stack'; scaffoldingSaving = false; return; }
			const vars = scaffoldingForm.substitution_vars_csv.split(',').map(s => s.trim()).filter(Boolean);
			const data = { name: scaffoldingForm.name, slug: scaffoldingForm.slug, description: scaffoldingForm.description, tech_stack, substitution_vars: vars };
			if (editingScaffolding) {
				await updateScaffoldingTemplate(editingScaffolding.slug, data);
			} else {
				await createScaffoldingTemplate(data);
			}
			cancelScaffoldingForm();
			await loadScaffolding();
		} catch (e: any) {
			scaffoldingError = e?.message || 'Save failed';
		}
		scaffoldingSaving = false;
	}

	async function deleteScaffolding(t: ScaffoldingTemplate) {
		if (!confirm(`Delete scaffolding template "${t.name}"?`)) return;
		try {
			await deleteScaffoldingTemplate(t.slug);
			if (editingScaffolding?.slug === t.slug) cancelScaffoldingForm();
			await loadScaffolding();
		} catch { /* ignore */ }
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

	// ── Prompt CRUD ─────────────────────────────────────────────

	function startCreatePrompt() {
		cancelAllForms();
		creatingPrompt = true;
		promptForm = { name: '', slug: '', stage: 'backend', role: 'system', content: '' };
		promptError = '';
	}

	function startEditPrompt(t: PromptTemplate) {
		cancelAllForms();
		editingPrompt = t;
		promptForm = { name: t.name, slug: t.slug, stage: t.stage, role: t.role, content: t.content };
		promptError = '';
	}

	function cancelPromptForm() {
		creatingPrompt = false;
		editingPrompt = null;
		promptError = '';
	}

	async function savePrompt() {
		promptSaving = true;
		promptError = '';
		try {
			const data = { ...promptForm };
			if (editingPrompt) {
				await updatePromptTemplate(editingPrompt.slug, data);
			} else {
				await createPromptTemplate(data);
			}
			cancelPromptForm();
			await loadPrompt();
		} catch (e: any) {
			promptError = e?.message || 'Save failed';
		}
		promptSaving = false;
	}

	async function deletePrompt(t: PromptTemplate) {
		if (!confirm(`Delete prompt template "${t.name}"?`)) return;
		try {
			await deletePromptTemplate(t.slug);
			if (editingPrompt?.slug === t.slug) cancelPromptForm();
			await loadPrompt();
		} catch { /* ignore */ }
	}

	function slugify(name: string): string {
		return name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
	}
</script>

<svelte:head>
	<title>Template Management - LLM Lab</title>
</svelte:head>

<div class="space-y-6">
	<!-- Header -->
	<div class="flex items-center justify-between">
		<div class="page-header w-full">
			<div class="flex items-center gap-3">
				<a href="/sample-generator" class="text-muted-foreground hover:text-foreground transition-all duration-200" title="Back to Sample Generator">
					<ArrowLeft class="h-5 w-5" />
				</a>
				<div>
					<h1 class="text-2xl font-bold tracking-tight">Template Management</h1>
					<p class="text-sm text-muted-foreground">Create, edit, and orchestrate templates for sample generation.</p>
				</div>
			</div>
		</div>
	</div>

	<!-- Tabs Menu -->
	<div class="flex gap-1 rounded-lg bg-muted p-1 overflow-x-auto [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden flex-nowrap shadow-inner">
		<button
			type="button"
			class="flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-all duration-200 whitespace-nowrap cursor-pointer {activeTab === 'scaffolding' ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}"
			onclick={() => handleTabChange('scaffolding')}
		>
			<Layers class="h-4 w-4" />
			Scaffolding <span class="ml-1 text-xs opacity-60 font-mono">({scaffoldingTemplates.length})</span>
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
			class="flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-all duration-200 whitespace-nowrap cursor-pointer {activeTab === 'prompt' ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}"
			onclick={() => handleTabChange('prompt')}
		>
			<MessageSquare class="h-4 w-4" />
			Prompts <span class="ml-1 text-xs opacity-60 font-mono">({promptTemplates.length})</span>
		</button>
		<button
			type="button"
			class="flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-all duration-200 whitespace-nowrap cursor-pointer {activeTab === 'bundles' ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}"
			onclick={() => handleTabChange('bundles')}
		>
			<Package class="h-4 w-4" />
			Bundles <span class="ml-1 text-xs opacity-60 font-mono">({templateBundles.length})</span>
		</button>
	</div>

	<!-- Master-Detail Content Grid -->
	<div class="grid gap-6 {isEditingOrCreating ? 'lg:grid-cols-[1fr_1.2fr]' : 'grid-cols-1'}">
		
		<!-- LIST SECTION -->
		<div class="space-y-4 {isEditingOrCreating ? 'hidden lg:block' : ''}">
			
			<!-- SEARCH & ACTIONS BAR (Only for CRUD tabs) -->
			{#if activeTab !== 'bundles'}
				<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between bg-muted/10 p-3 rounded-lg border">
					<div class="relative w-full sm:w-72">
						<Search class="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
						{#if activeTab === 'scaffolding'}
							<Input bind:value={scaffoldingSearch} placeholder="Search scaffolding…" class="pl-9 h-9 text-xs transition-all hover:border-primary/45 focus-visible:ring-primary/20" />
						{:else if activeTab === 'app'}
							<Input bind:value={appSearch} placeholder="Search app requirements…" class="pl-9 h-9 text-xs transition-all hover:border-primary/45 focus-visible:ring-primary/20" />
						{:else if activeTab === 'prompt'}
							<div class="flex items-center gap-1 bg-muted/40 p-0.5 rounded border max-w-full">
								{#each [['', 'All'], ['backend', 'Backend'], ['frontend', 'Frontend']] as [val, label]}
									<button
										type="button"
										class="rounded px-2.5 py-1 text-[10px] font-medium transition-all duration-150 cursor-pointer {promptStageFilter === val ? 'bg-background text-foreground shadow-xs' : 'text-muted-foreground hover:text-foreground'}"
										onclick={() => promptStageFilter = val}
									>{label}</button>
								{/each}
							</div>
						{/if}
					</div>
					
					{#if activeTab === 'scaffolding'}
						<Button size="sm" class="w-full sm:w-auto text-xs font-semibold cursor-pointer shadow-xs transition-all duration-200" onclick={startCreateScaffolding}>
							<Plus class="mr-1.5 h-4 w-4" /> New Scaffolding
						</Button>
					{:else if activeTab === 'app'}
						<Button size="sm" class="w-full sm:w-auto text-xs font-semibold cursor-pointer shadow-xs transition-all duration-200" onclick={startCreateApp}>
							<Plus class="mr-1.5 h-4 w-4" /> New App Template
						</Button>
					{:else if activeTab === 'prompt'}
						<Button size="sm" class="w-full sm:w-auto text-xs font-semibold cursor-pointer shadow-xs transition-all duration-200" onclick={startCreatePrompt}>
							<Plus class="mr-1.5 h-4 w-4" /> New Prompt
						</Button>
					{/if}
				</div>
			{/if}

			<!-- SCAFFOLDING LIST -->
			{#if activeTab === 'scaffolding'}
				{#if scaffoldingLoading}
					<div class="flex items-center justify-center py-16 text-sm text-muted-foreground">
						<LoaderCircle class="mr-2 h-4 w-4 animate-spin text-primary" /> Loading scaffolding templates…
					</div>
				{:else if filteredScaffolding.length === 0}
					<Card.Root class="border-dashed">
						<Card.Content class="py-16 text-center text-sm text-muted-foreground">
							No scaffolding templates found.
						</Card.Content>
					</Card.Root>
				{:else}
					<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-2 {isEditingOrCreating ? 'xl:grid-cols-1' : 'xl:grid-cols-3'}">
						{#each filteredScaffolding as t (t.id)}
							{@const isActive = editingScaffolding?.slug === t.slug}
							<div class="group relative rounded-xl border bg-card text-card-foreground shadow-xs transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md {isActive ? 'ring-2 ring-primary bg-primary/[0.02]' : ''}">
								<div class="p-4 space-y-3">
									<div class="flex items-start justify-between gap-2">
										<div class="min-w-0">
											<h3 class="font-bold text-sm truncate text-foreground flex items-center gap-1.5">
												{t.name}
												{#if t.is_default}<Badge variant="secondary" class="text-[9px] px-1 py-0 scale-90">Default</Badge>{/if}
											</h3>
											<p class="text-[10px] text-muted-foreground font-mono truncate mt-0.5">{t.slug}</p>
										</div>
									</div>
									{#if t.description}
										<p class="text-xs text-muted-foreground line-clamp-2 leading-relaxed h-8">{t.description}</p>
									{:else}
										<div class="h-8"></div>
									{/if}
									{#if t.tech_stack && Object.keys(t.tech_stack).length}
										<div class="flex flex-wrap gap-1 min-h-[22px]">
											{#each Object.entries(t.tech_stack) as [k, v]}
												<Badge variant="outline" class="text-[9px] px-1.5 py-0 bg-muted/30">{k}: {v}</Badge>
											{/each}
										</div>
									{:else}
										<div class="min-h-[22px]"></div>
									{/if}
									<Separator class="my-2" />
									<div class="flex gap-2 justify-end">
										<button
											type="button"
											class="inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium border rounded hover:bg-muted/80 transition-colors cursor-pointer text-muted-foreground hover:text-foreground"
											onclick={() => startEditScaffolding(t)}
										>
											<Pencil class="h-3 w-3" /> Edit
										</button>
										{#if !t.is_default}
											<button
												type="button"
												class="inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium border border-red-500/20 rounded hover:bg-red-500/10 transition-colors cursor-pointer text-red-400 hover:text-red-300"
												onclick={() => deleteScaffolding(t)}
											>
												<Trash2 class="h-3 w-3" /> Delete
											</button>
										{/if}
									</div>
								</div>
							</div>
						{/each}
					</div>
				{/if}
			{/if}

			<!-- APP REQUIREMENTS LIST -->
			{#if activeTab === 'app'}
				{#if appLoading}
					<div class="flex items-center justify-center py-16 text-sm text-muted-foreground">
						<LoaderCircle class="mr-2 h-4 w-4 animate-spin text-primary" /> Loading requirement templates…
					</div>
				{:else if filteredApp.length === 0}
					<Card.Root class="border-dashed">
						<Card.Content class="py-16 text-center text-sm text-muted-foreground">
							No app templates found.
						</Card.Content>
					</Card.Root>
				{:else}
					<div class="space-y-2">
						{#each filteredApp as t (t.id)}
							{@const isActive = editingApp?.slug === t.slug}
							<div class="group flex items-start gap-4 rounded-xl border bg-card p-4 shadow-xs transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md {isActive ? 'ring-2 ring-primary bg-primary/[0.02]' : ''}">
								<div class="flex-1 min-w-0">
									<div class="flex items-center flex-wrap gap-2">
										<span class="font-bold text-sm text-foreground">{t.name}</span>
										<span class="text-[10px] font-mono text-muted-foreground">({t.slug})</span>
										{#if t.is_default}<Badge variant="secondary" class="text-[9px] px-1 py-0">Default</Badge>{/if}
									</div>
									{#if t.description}
										<p class="text-xs text-muted-foreground mt-1 line-clamp-1 leading-relaxed">{t.description}</p>
									{/if}
									<div class="flex gap-4 mt-2 text-[10px] text-muted-foreground font-medium">
										{#if t.backend_requirements?.length}
											<span class="flex items-center gap-1"><Badge variant="outline" class="text-[9px] px-1 py-0 font-mono tabular-nums">{t.backend_requirements.length}</Badge> Backend</span>
										{/if}
										{#if t.frontend_requirements?.length}
											<span class="flex items-center gap-1"><Badge variant="outline" class="text-[9px] px-1 py-0 font-mono tabular-nums">{t.frontend_requirements.length}</Badge> Frontend</span>
										{/if}
										{#if t.admin_requirements?.length}
											<span class="flex items-center gap-1"><Badge variant="outline" class="text-[9px] px-1 py-0 font-mono tabular-nums">{t.admin_requirements.length}</Badge> Admin</span>
										{/if}
									</div>
								</div>
								<div class="flex gap-1.5 ml-2 shrink-0 self-center">
									<button
										type="button"
										class="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium border rounded hover:bg-muted/80 transition-colors cursor-pointer text-muted-foreground hover:text-foreground"
										onclick={() => startEditApp(t)}
									>
										<Pencil class="h-3.5 w-3.5" /> Edit
									</button>
									{#if !t.is_default}
										<button
											type="button"
											class="inline-flex items-center justify-center p-1.5 text-xs font-medium border border-red-500/20 rounded hover:bg-red-500/10 transition-colors cursor-pointer text-red-400 hover:text-red-300"
											title="Delete Template"
											onclick={() => deleteApp(t)}
										>
											<Trash2 class="h-3.5 w-3.5" />
										</button>
									{/if}
								</div>
							</div>
						{/each}
					</div>
				{/if}
			{/if}

			<!-- PROMPTS LIST -->
			{#if activeTab === 'prompt'}
				{#if promptLoading}
					<div class="flex items-center justify-center py-16 text-sm text-muted-foreground">
						<LoaderCircle class="mr-2 h-4 w-4 animate-spin text-primary" /> Loading prompt templates…
					</div>
				{:else if filteredPrompt.length === 0}
					<Card.Root class="border-dashed">
						<Card.Content class="py-16 text-center text-sm text-muted-foreground">
							No prompts found.
						</Card.Content>
					</Card.Root>
				{:else}
					<div class="space-y-6">
						{#each Object.entries(groupedPrompts()) as [group, templates]}
							<div class="space-y-2">
								<h3 class="text-xs font-bold uppercase tracking-wider text-muted-foreground border-b pb-1 flex items-center gap-2">
									<MessageSquare class="h-3.5 w-3.5" />
									{group.replace('/', ' ➔ ')}
								</h3>
								<div class="grid gap-3 grid-cols-1 sm:grid-cols-2 {isEditingOrCreating ? 'xl:grid-cols-1' : 'xl:grid-cols-2'}">
									{#each templates as t (t.id)}
										{@const isActive = editingPrompt?.slug === t.slug}
										<div class="group relative rounded-xl border bg-card p-4 shadow-xs transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md {isActive ? 'ring-2 ring-primary bg-primary/[0.02]' : ''}">
											<div class="space-y-3">
												<div class="flex items-start justify-between gap-2">
													<div class="min-w-0">
														<span class="font-bold text-sm truncate block text-foreground">{t.name}</span>
														<div class="flex items-center gap-1.5 mt-1">
															<Badge variant="outline" class="text-[9px] px-1 py-0 uppercase font-mono">{t.stage}</Badge>
															<Badge variant="outline" class="text-[9px] px-1 py-0 uppercase font-mono">{t.role}</Badge>
															{#if t.is_default}<Badge variant="secondary" class="text-[9px] px-1 py-0">Default</Badge>{/if}
														</div>
													</div>
												</div>
												<pre class="text-[11px] text-muted-foreground bg-muted/30 p-2.5 rounded-lg border font-mono line-clamp-3 whitespace-pre-wrap leading-relaxed h-[62px]">{t.content}</pre>
												<div class="flex gap-2 justify-end">
													<button
														type="button"
														class="inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium border rounded hover:bg-muted/80 transition-colors cursor-pointer text-muted-foreground hover:text-foreground"
														onclick={() => startEditPrompt(t)}
													>
														<Pencil class="h-3 w-3" /> Edit
													</button>
													{#if !t.is_default}
														<button
															type="button"
															class="inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium border border-red-500/20 rounded hover:bg-red-500/10 transition-colors cursor-pointer text-red-400 hover:text-red-300"
															onclick={() => deletePrompt(t)}
														>
															<Trash2 class="h-3 w-3" /> Delete
														</button>
													{/if}
												</div>
											</div>
										</div>
									{/each}
								</div>
							</div>
						{/each}
					</div>
				{/if}
			{/if}

			<!-- BUNDLES LIST (Read Only) -->
			{#if activeTab === 'bundles'}
				<div class="space-y-4">
					<div class="p-4 bg-muted/20 border border-muted/50 rounded-xl">
						<p class="text-xs text-muted-foreground leading-relaxed flex items-start gap-1.5">
							<Package class="h-4 w-4 shrink-0 text-primary mt-0.5" />
							Template bundles compose structural requirement blocks and models into reproducible generation snapshot plans.
							Per-app bundle maps are configured technically inside <code class="bg-muted px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold">data/requirements/manifests/</code>.
						</p>
					</div>
					
					{#if bundlesLoading}
						<div class="flex justify-center py-16">
							<LoaderCircle class="h-6 w-6 animate-spin text-primary" />
						</div>
					{:else if templateBundles.length === 0}
						<div class="py-16 text-center text-xs text-muted-foreground">
							No bundles discovered. Run <span class="bg-muted px-1.5 py-0.5 rounded font-mono text-[10px]">seed_generation_templates</span> inside LLM Lab.
						</div>
					{:else}
						<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
							{#each templateBundles as bundle (bundle.id)}
								<div class="group relative rounded-xl border bg-card p-4 shadow-xs hover:shadow-md transition-all duration-200">
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

				<!-- SCAFFOLDING FORM -->
				{#if creatingScaffolding || editingScaffolding}
					<Card.Root class="shadow-lg border-primary/20">
						<Card.Header class="pb-3 bg-muted/10 border-b">
							<div class="flex items-center justify-between">
								<Card.Title class="text-sm font-bold text-foreground">
									{editingScaffolding ? 'Edit' : 'New'} Scaffolding Template
								</Card.Title>
								<button type="button" class="text-muted-foreground hover:text-foreground cursor-pointer" onclick={cancelScaffoldingForm}>
									<XIcon class="h-4 w-4" />
								</button>
							</div>
						</Card.Header>
						<Card.Content class="pt-5 space-y-4">
							<div class="grid gap-4 sm:grid-cols-2">
								<div class="space-y-1.5">
									<Label class="text-xs uppercase tracking-wider text-muted-foreground">Name</Label>
									<Input bind:value={scaffoldingForm.name} placeholder="e.g. React + FastAPI" class="h-9 text-xs transition-all hover:border-primary/45 focus-visible:ring-primary/20"
										oninput={() => { if (creatingScaffolding) scaffoldingForm.slug = slugify(scaffoldingForm.name); }} />
								</div>
								<div class="space-y-1.5">
									<Label class="text-xs uppercase tracking-wider text-muted-foreground">Slug</Label>
									<Input bind:value={scaffoldingForm.slug} placeholder="e.g. react-fastapi" class="h-9 text-xs font-mono transition-all hover:border-primary/45 focus-visible:ring-primary/20" />
								</div>
								<div class="space-y-1.5 sm:col-span-2">
									<Label class="text-xs uppercase tracking-wider text-muted-foreground">Description</Label>
									<Textarea bind:value={scaffoldingForm.description} rows={2} placeholder="Explain this stack setup and its intent..." class="text-xs leading-relaxed" />
								</div>
								<div class="space-y-1.5 sm:col-span-2">
									<Label class="text-xs uppercase tracking-wider text-muted-foreground">Tech Stack (JSON configuration)</Label>
									<Textarea bind:value={scaffoldingForm.tech_stack_json} rows={5} placeholder={'{\n  "frontend": "React",\n  "backend": "FastAPI",\n  "database": "PostgreSQL"\n}'} class="font-mono text-xs leading-relaxed bg-muted/20" />
								</div>
								<div class="space-y-1.5 sm:col-span-2">
									<Label class="text-xs uppercase tracking-wider text-muted-foreground">Substitution Variables (comma-separated)</Label>
									<Input bind:value={scaffoldingForm.substitution_vars_csv} placeholder="e.g. APP_NAME, ADMIN_EMAIL, PORT" class="h-9 text-xs font-mono transition-all hover:border-primary/45 focus-visible:ring-primary/20" />
								</div>
							</div>
							
							{#if scaffoldingError}
								<div class="rounded-lg bg-red-500/10 border border-red-500/30 p-3 text-xs text-red-400 font-medium">
									{scaffoldingError}
								</div>
							{/if}
							
							<div class="flex gap-2 pt-2 border-t">
								<Button size="sm" class="text-xs cursor-pointer shadow-xs" onclick={saveScaffolding} disabled={scaffoldingSaving || !scaffoldingForm.name || !scaffoldingForm.slug}>
									{#if scaffoldingSaving}
										<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin" /> Saving…
									{:else}
										<Save class="mr-1.5 h-3.5 w-3.5" /> Save Template
									{/if}
								</Button>
								<Button variant="outline" size="sm" class="text-xs cursor-pointer" onclick={cancelScaffoldingForm}>
									Cancel
								</Button>
							</div>
						</Card.Content>
					</Card.Root>
				{/if}

				<!-- APP FORM -->
				{#if creatingApp || editingApp}
					<Card.Root class="shadow-lg border-primary/20">
						<Card.Header class="pb-3 bg-muted/10 border-b">
							<div class="flex items-center justify-between">
								<Card.Title class="text-sm font-bold text-foreground">
									{editingApp ? 'Edit' : 'New'} App Requirement Template
								</Card.Title>
								<button type="button" class="text-muted-foreground hover:text-foreground cursor-pointer" onclick={cancelAppForm}>
									<XIcon class="h-4 w-4" />
								</button>
							</div>
						</Card.Header>
						<Card.Content class="pt-5 space-y-4">
							<div class="grid gap-4 sm:grid-cols-2">
								<div class="space-y-1.5">
									<Label class="text-xs uppercase tracking-wider text-muted-foreground">Name</Label>
									<Input bind:value={appForm.name} placeholder="e.g. Chat Messenger" class="h-9 text-xs transition-all hover:border-primary/45 focus-visible:ring-primary/20"
										oninput={() => { if (creatingApp) appForm.slug = slugify(appForm.name); }} />
								</div>
								<div class="space-y-1.5">
									<Label class="text-xs uppercase tracking-wider text-muted-foreground">Slug</Label>
									<Input bind:value={appForm.slug} placeholder="e.g. chat_messenger" class="h-9 text-xs font-mono transition-all hover:border-primary/45 focus-visible:ring-primary/20" />
								</div>
								<div class="space-y-1.5 sm:col-span-2">
									<Label class="text-xs uppercase tracking-wider text-muted-foreground">Description</Label>
									<Textarea bind:value={appForm.description} rows={2} placeholder="Brief summary of what this application does..." class="text-xs leading-relaxed" />
								</div>
								<div class="space-y-1.5">
									<Label class="text-xs uppercase tracking-wider text-muted-foreground">Backend Requirements (one per line)</Label>
									<Textarea bind:value={appForm.backend_requirements} rows={6} placeholder={"e.g.\nWebSockets for active connections\nCRUD endpoints for messages\nUser identity session management"} class="font-mono text-[11px] leading-normal bg-muted/20" />
								</div>
								<div class="space-y-1.5">
									<Label class="text-xs uppercase tracking-wider text-muted-foreground">Frontend Requirements (one per line)</Label>
									<Textarea bind:value={appForm.frontend_requirements} rows={6} placeholder={"e.g.\nReal-time scrolling chat view\nSidebar of direct message lists\nVisual status rings for users"} class="font-mono text-[11px] leading-normal bg-muted/20" />
								</div>
								<div class="space-y-1.5 sm:col-span-2">
									<Label class="text-xs uppercase tracking-wider text-muted-foreground">Admin Features (one per line)</Label>
									<Textarea bind:value={appForm.admin_requirements} rows={3} placeholder={"e.g.\nGlobal analytics tracking dashboard\nManage and freeze user rooms"} class="font-mono text-[11px] leading-normal bg-muted/20" />
								</div>
							</div>
							
							{#if appError}
								<div class="rounded-lg bg-red-500/10 border border-red-500/30 p-3 text-xs text-red-400 font-medium">
									{appError}
								</div>
							{/if}
							
							<div class="flex gap-2 pt-2 border-t">
								<Button size="sm" class="text-xs cursor-pointer shadow-xs" onclick={saveApp} disabled={appSaving || !appForm.name || !appForm.slug}>
									{#if appSaving}
										<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin" /> Saving…
									{:else}
										<Save class="mr-1.5 h-3.5 w-3.5" /> Save Template
									{/if}
								</Button>
								<Button variant="outline" size="sm" class="text-xs cursor-pointer" onclick={cancelAppForm}>
									Cancel
								</Button>
							</div>
						</Card.Content>
					</Card.Root>
				{/if}

				<!-- PROMPT FORM -->
				{#if creatingPrompt || editingPrompt}
					<Card.Root class="shadow-lg border-primary/20">
						<Card.Header class="pb-3 bg-muted/10 border-b">
							<div class="flex items-center justify-between">
								<Card.Title class="text-sm font-bold text-foreground">
									{editingPrompt ? 'Edit' : 'New'} Prompt Template
								</Card.Title>
								<button type="button" class="text-muted-foreground hover:text-foreground cursor-pointer" onclick={cancelPromptForm}>
									<XIcon class="h-4 w-4" />
								</button>
							</div>
						</Card.Header>
						<Card.Content class="pt-5 space-y-4">
							<div class="grid gap-4 sm:grid-cols-2">
								<div class="space-y-1.5">
									<Label class="text-xs uppercase tracking-wider text-muted-foreground">Name</Label>
									<Input bind:value={promptForm.name} placeholder="e.g. Frontend User Prompt" class="h-9 text-xs transition-all hover:border-primary/45 focus-visible:ring-primary/20"
										oninput={() => { if (creatingPrompt) promptForm.slug = slugify(promptForm.name); }} />
								</div>
								<div class="space-y-1.5">
									<Label class="text-xs uppercase tracking-wider text-muted-foreground">Slug</Label>
									<Input bind:value={promptForm.slug} placeholder="e.g. frontend-user-v1" class="h-9 text-xs font-mono transition-all hover:border-primary/45 focus-visible:ring-primary/20" />
								</div>
								<div class="space-y-1.5">
									<Label class="text-xs uppercase tracking-wider text-muted-foreground">Execution Stage</Label>
									<select bind:value={promptForm.stage} class="flex h-9 w-full rounded-md border border-input bg-background px-3 text-xs transition-all hover:border-primary/45 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring">
										<option value="backend">Backend generation</option>
										<option value="frontend">Frontend UI generation</option>
									</select>
								</div>
								<div class="space-y-1.5">
									<Label class="text-xs uppercase tracking-wider text-muted-foreground">LLM Chat Role</Label>
									<select bind:value={promptForm.role} class="flex h-9 w-full rounded-md border border-input bg-background px-3 text-xs transition-all hover:border-primary/45 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring">
										<option value="system">System prompt</option>
										<option value="user">User instruction</option>
									</select>
								</div>
								<div class="space-y-1.5 sm:col-span-2">
									<Label class="text-xs uppercase tracking-wider text-muted-foreground flex justify-between">
										<span>Prompt Body (Jinja2 template syntax)</span>
									</Label>
									<Textarea bind:value={promptForm.content} rows={12} placeholder="Write instructions using Jinja2 blocks, e.g. You are a developer building {{ name }}..." class="font-mono text-[11px] leading-relaxed bg-muted/20" />
									<div class="p-3 bg-muted/40 rounded-lg border text-[10px] text-muted-foreground leading-relaxed">
										<strong class="text-foreground">Jinja2 variables supplied by Generator:</strong> <code class="bg-muted px-1 rounded text-primary">name</code>, <code class="bg-muted px-1 rounded text-primary">description</code>, <code class="bg-muted px-1 rounded text-primary">backend_requirements</code>, <code class="bg-muted px-1 rounded text-primary">frontend_requirements</code>, <code class="bg-muted px-1 rounded text-primary">admin_requirements</code>, <code class="bg-muted px-1 rounded text-primary">api_endpoints</code>, <code class="bg-muted px-1 rounded text-primary">data_model</code>.
									</div>
								</div>
							</div>
							
							{#if promptError}
								<div class="rounded-lg bg-red-500/10 border border-red-500/30 p-3 text-xs text-red-400 font-medium">
									{promptError}
								</div>
							{/if}
							
							<div class="flex gap-2 pt-2 border-t">
								<Button size="sm" class="text-xs cursor-pointer shadow-xs" onclick={savePrompt} disabled={promptSaving || !promptForm.name || !promptForm.slug || !promptForm.content}>
									{#if promptSaving}
										<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin" /> Saving…
									{:else}
										<Save class="mr-1.5 h-3.5 w-3.5" /> Save Template
									{/if}
								</Button>
								<Button variant="outline" size="sm" class="text-xs cursor-pointer" onclick={cancelPromptForm}>
									Cancel
								</Button>
							</div>
						</Card.Content>
					</Card.Root>
				{/if}
			</div>
		{:else if activeTab !== 'bundles'}
			<!-- DESKTOP EMPTY STATE (Master detail placeholder when not editing) -->
			<div class="hidden lg:flex flex-col items-center justify-center rounded-xl border border-dashed border-muted-foreground/30 p-16 text-center bg-muted/5 min-h-[360px] max-w-lg mx-auto">
				<div class="h-10 w-10 rounded-lg bg-primary/10 text-primary flex items-center justify-center mb-4">
					{#if activeTab === 'scaffolding'}
						<Layers class="h-5 w-5" />
					{:else if activeTab === 'app'}
						<FileText class="h-5 w-5" />
					{:else if activeTab === 'prompt'}
						<MessageSquare class="h-5 w-5" />
					{/if}
				</div>
				<h3 class="font-semibold text-sm text-foreground">No template selected</h3>
				<p class="text-xs text-muted-foreground mt-1.5 max-w-[280px]">
					Select a template card from the list on the left to edit its details, or create a brand new template mapping.
				</p>
			</div>
		{/if}
	</div>
</div>
