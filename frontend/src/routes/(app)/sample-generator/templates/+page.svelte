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
	} from '$lib/api/client';
	import Layers from '@lucide/svelte/icons/layers';
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

	type TabId = 'scaffolding' | 'app' | 'prompt';
	let activeTab = $state<TabId>('scaffolding');

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

	onMount(() => {
		loadScaffolding();
		loadApp();
		loadPrompt();
	});

	// ── Scaffolding CRUD ────────────────────────────────────────

	function startCreateScaffolding() {
		creatingScaffolding = true;
		editingScaffolding = null;
		scaffoldingForm = { name: '', slug: '', description: '', tech_stack_json: '{}', substitution_vars_csv: '' };
		scaffoldingError = '';
	}

	function startEditScaffolding(t: ScaffoldingTemplate) {
		editingScaffolding = t;
		creatingScaffolding = false;
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
		try { await deleteScaffoldingTemplate(t.slug); await loadScaffolding(); } catch { /* ignore */ }
	}

	// ── App CRUD ────────────────────────────────────────────────

	function startCreateApp() {
		creatingApp = true;
		editingApp = null;
		appForm = { name: '', slug: '', description: '', backend_requirements: '', frontend_requirements: '', admin_requirements: '' };
		appError = '';
	}

	function startEditApp(t: AppRequirementTemplate) {
		editingApp = t;
		creatingApp = false;
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
		try { await deleteAppTemplate(t.slug); await loadApp(); } catch { /* ignore */ }
	}

	// ── Prompt CRUD ─────────────────────────────────────────────

	function startCreatePrompt() {
		creatingPrompt = true;
		editingPrompt = null;
		promptForm = { name: '', slug: '', stage: 'backend', role: 'system', content: '' };
		promptError = '';
	}

	function startEditPrompt(t: PromptTemplate) {
		editingPrompt = t;
		creatingPrompt = false;
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
		try { await deletePromptTemplate(t.slug); await loadPrompt(); } catch { /* ignore */ }
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
				<a href="/sample-generator" class="text-muted-foreground hover:text-foreground transition-colors">
					<ArrowLeft class="h-4 w-4" />
				</a>
				<div>
					<h1>Template Management</h1>
					<p>Create, edit, and manage generation templates.</p>
				</div>
			</div>
		</div>
	</div>

	<!-- Tabs -->
	<div class="flex gap-1 rounded-lg bg-muted p-1 overflow-x-auto flex-nowrap">
		<button
			class="flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap {activeTab === 'scaffolding' ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}"
			onclick={() => activeTab = 'scaffolding'}
		>
			<Layers class="h-4 w-4" />
			Scaffolding ({scaffoldingTemplates.length})
		</button>
		<button
			class="flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap {activeTab === 'app' ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}"
			onclick={() => activeTab = 'app'}
		>
			<FileText class="h-4 w-4" />
			App Requirements ({appTemplates.length})
		</button>
		<button
			class="flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap {activeTab === 'prompt' ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}"
			onclick={() => activeTab = 'prompt'}
		>
			<MessageSquare class="h-4 w-4" />
			Prompts ({promptTemplates.length})
		</button>
	</div>

	<!-- ==================== SCAFFOLDING TAB ==================== -->
	{#if activeTab === 'scaffolding'}
		<div class="space-y-4">
			<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
				<div class="relative w-full sm:w-64">
					<Search class="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
					<Input bind:value={scaffoldingSearch} placeholder="Search templates…" class="pl-9 h-9" />
				</div>
				<Button size="sm" class="w-full sm:w-auto" onclick={startCreateScaffolding}>
					<Plus class="mr-1.5 h-3.5 w-3.5" /> New Template
				</Button>
			</div>

			{#if creatingScaffolding || editingScaffolding}
				<Card.Root>
					<Card.Header class="pb-3">
						<Card.Title class="text-sm">{editingScaffolding ? 'Edit' : 'New'} Scaffolding Template</Card.Title>
					</Card.Header>
					<Card.Content>
						<div class="grid gap-4 md:grid-cols-2">
							<div class="space-y-1.5">
								<Label class="text-xs">Name</Label>
								<Input bind:value={scaffoldingForm.name} placeholder="React + Flask" class="h-8 text-sm"
									oninput={() => { if (creatingScaffolding) scaffoldingForm.slug = slugify(scaffoldingForm.name); }} />
							</div>
							<div class="space-y-1.5">
								<Label class="text-xs">Slug</Label>
								<Input bind:value={scaffoldingForm.slug} placeholder="react-flask" class="h-8 text-sm font-mono" />
							</div>
							<div class="space-y-1.5 md:col-span-2">
								<Label class="text-xs">Description</Label>
								<textarea bind:value={scaffoldingForm.description} rows="2" class="flex w-full rounded-md border bg-transparent px-3 py-2 text-sm" placeholder="Description…"></textarea>
							</div>
							<div class="space-y-1.5">
								<Label class="text-xs">Tech Stack (JSON)</Label>
								<textarea bind:value={scaffoldingForm.tech_stack_json} rows="3" class="flex w-full rounded-md border bg-transparent px-3 py-2 text-xs font-mono overflow-x-auto" placeholder={'{"frontend": "React", "backend": "Flask"}'}></textarea>
							</div>
							<div class="space-y-1.5">
								<Label class="text-xs">Substitution Vars (comma-separated)</Label>
								<Input bind:value={scaffoldingForm.substitution_vars_csv} placeholder="APP_NAME, PORT" class="h-8 text-sm font-mono" />
							</div>
						</div>
						{#if scaffoldingError}
							<p class="mt-2 text-xs text-red-400">{scaffoldingError}</p>
						{/if}
						<div class="mt-4 flex gap-2">
							<Button size="sm" onclick={saveScaffolding} disabled={scaffoldingSaving || !scaffoldingForm.name || !scaffoldingForm.slug}>
								{#if scaffoldingSaving}<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin" />{:else}<Save class="mr-1.5 h-3.5 w-3.5" />{/if}
								Save
							</Button>
							<Button variant="outline" size="sm" onclick={cancelScaffoldingForm}><XIcon class="mr-1.5 h-3.5 w-3.5" /> Cancel</Button>
						</div>
					</Card.Content>
				</Card.Root>
			{/if}

			{#if scaffoldingLoading}
				<div class="flex items-center justify-center py-12 text-sm text-muted-foreground">
					<LoaderCircle class="mr-2 h-4 w-4 animate-spin" /> Loading…
				</div>
			{:else if filteredScaffolding.length === 0}
				<Card.Root>
					<Card.Content class="py-12 text-center text-sm text-muted-foreground">
						No scaffolding templates found.
					</Card.Content>
				</Card.Root>
			{:else}
				<div class="grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
					{#each filteredScaffolding as t}
						<Card.Root>
							<Card.Header class="pb-2">
								<div class="flex items-center justify-between">
									<Card.Title class="text-sm">{t.name}</Card.Title>
									<div class="flex gap-1">
										{#if t.is_default}<Badge variant="secondary" class="text-[10px]">Default</Badge>{/if}
									</div>
								</div>
								<p class="text-xs text-muted-foreground font-mono">{t.slug}</p>
							</Card.Header>
							<Card.Content class="space-y-2">
								{#if t.description}
									<p class="text-xs text-muted-foreground line-clamp-2">{t.description}</p>
								{/if}
								{#if t.tech_stack && Object.keys(t.tech_stack).length}
									<div class="flex flex-wrap gap-1">
										{#each Object.entries(t.tech_stack) as [k, v]}
											<Badge variant="outline" class="text-[10px]">{k}: {v}</Badge>
										{/each}
									</div>
								{/if}
								<div class="flex gap-1 pt-1">
									<Button variant="outline" size="sm" class="h-7 text-xs" onclick={() => startEditScaffolding(t)}>
										<Pencil class="mr-1 h-3 w-3" /> Edit
									</Button>
									{#if !t.is_default}
										<Button variant="outline" size="sm" class="h-7 text-xs text-red-400 hover:text-red-300" onclick={() => deleteScaffolding(t)}>
											<Trash2 class="mr-1 h-3 w-3" /> Delete
										</Button>
									{/if}
								</div>
							</Card.Content>
						</Card.Root>
					{/each}
				</div>
			{/if}
		</div>
	{/if}

	<!-- ==================== APP REQUIREMENTS TAB ==================== -->
	{#if activeTab === 'app'}
		<div class="space-y-4">
			<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
				<div class="relative w-full sm:w-64">
					<Search class="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
					<Input bind:value={appSearch} placeholder="Search app templates…" class="pl-9 h-9" />
				</div>
				<Button size="sm" class="w-full sm:w-auto" onclick={startCreateApp}>
					<Plus class="mr-1.5 h-3.5 w-3.5" /> New Template
				</Button>
			</div>

			{#if creatingApp || editingApp}
				<Card.Root>
					<Card.Header class="pb-3">
						<Card.Title class="text-sm">{editingApp ? 'Edit' : 'New'} App Requirement Template</Card.Title>
					</Card.Header>
					<Card.Content>
						<div class="grid gap-4 md:grid-cols-2">
							<div class="space-y-1.5">
								<Label class="text-xs">Name</Label>
								<Input bind:value={appForm.name} placeholder="Todo App" class="h-8 text-sm"
									oninput={() => { if (creatingApp) appForm.slug = slugify(appForm.name); }} />
							</div>
							<div class="space-y-1.5">
								<Label class="text-xs">Slug</Label>
								<Input bind:value={appForm.slug} placeholder="todo_app" class="h-8 text-sm font-mono" />
							</div>
							<div class="space-y-1.5 md:col-span-2">
								<Label class="text-xs">Description</Label>
								<textarea bind:value={appForm.description} rows="2" class="flex w-full rounded-md border bg-transparent px-3 py-2 text-sm" placeholder="What does this app do?"></textarea>
							</div>
							<div class="space-y-1.5">
								<Label class="text-xs">Backend Requirements (one per line)</Label>
								<textarea bind:value={appForm.backend_requirements} rows="4" class="flex w-full rounded-md border bg-transparent px-3 py-2 text-xs font-mono" placeholder="REST API with CRUD&#10;JWT authentication&#10;SQLite database"></textarea>
							</div>
							<div class="space-y-1.5">
								<Label class="text-xs">Frontend Requirements (one per line)</Label>
								<textarea bind:value={appForm.frontend_requirements} rows="4" class="flex w-full rounded-md border bg-transparent px-3 py-2 text-xs font-mono" placeholder="Responsive layout&#10;Search and filtering&#10;Dark theme"></textarea>
							</div>
							<div class="space-y-1.5 md:col-span-2">
								<Label class="text-xs">Admin Requirements (one per line)</Label>
								<textarea bind:value={appForm.admin_requirements} rows="2" class="flex w-full rounded-md border bg-transparent px-3 py-2 text-xs font-mono" placeholder="Dashboard with stats&#10;User management"></textarea>
							</div>
						</div>
						{#if appError}
							<p class="mt-2 text-xs text-red-400">{appError}</p>
						{/if}
						<div class="mt-4 flex gap-2">
							<Button size="sm" onclick={saveApp} disabled={appSaving || !appForm.name || !appForm.slug}>
								{#if appSaving}<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin" />{:else}<Save class="mr-1.5 h-3.5 w-3.5" />{/if}
								Save
							</Button>
							<Button variant="outline" size="sm" onclick={cancelAppForm}><XIcon class="mr-1.5 h-3.5 w-3.5" /> Cancel</Button>
						</div>
					</Card.Content>
				</Card.Root>
			{/if}

			{#if appLoading}
				<div class="flex items-center justify-center py-12 text-sm text-muted-foreground">
					<LoaderCircle class="mr-2 h-4 w-4 animate-spin" /> Loading…
				</div>
			{:else if filteredApp.length === 0}
				<Card.Root>
					<Card.Content class="py-12 text-center text-sm text-muted-foreground">
						No app requirement templates found.
					</Card.Content>
				</Card.Root>
			{:else}
				<div class="space-y-2">
					{#each filteredApp as t}
						<Card.Root>
							<Card.Content class="py-3 px-4">
								<div class="flex items-start justify-between">
									<div class="flex-1 min-w-0">
										<div class="flex items-center gap-2">
											<span class="font-medium text-sm">{t.name}</span>
											<span class="text-xs font-mono text-muted-foreground">{t.slug}</span>
											{#if t.is_default}<Badge variant="secondary" class="text-[10px]">Default</Badge>{/if}
										</div>
										{#if t.description}
											<p class="text-xs text-muted-foreground mt-0.5 line-clamp-1">{t.description}</p>
										{/if}
										<div class="flex gap-3 mt-1 text-[10px] text-muted-foreground">
											{#if t.backend_requirements?.length}<span>Backend: {t.backend_requirements.length} reqs</span>{/if}
											{#if t.frontend_requirements?.length}<span>Frontend: {t.frontend_requirements.length} reqs</span>{/if}
											{#if t.admin_requirements?.length}<span>Admin: {t.admin_requirements.length} reqs</span>{/if}
										</div>
									</div>
									<div class="flex gap-1 ml-2 shrink-0 flex-wrap">
										<Button variant="outline" size="sm" class="h-7 text-xs" onclick={() => startEditApp(t)}>
											<Pencil class="mr-1 h-3 w-3" /> Edit
										</Button>
										{#if !t.is_default}
											<Button variant="outline" size="sm" class="h-7 text-xs text-red-400 hover:text-red-300" onclick={() => deleteApp(t)}>
												<Trash2 class="h-3 w-3" />
											</Button>
										{/if}
									</div>
								</div>
							</Card.Content>
						</Card.Root>
					{/each}
				</div>
			{/if}
		</div>
	{/if}

	<!-- ==================== PROMPT TAB ==================== -->
	{#if activeTab === 'prompt'}
		<div class="space-y-4">
			<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
				<div class="flex gap-2">
					<select bind:value={promptStageFilter} class="h-9 w-full sm:w-auto rounded-md border bg-transparent px-2 text-sm">
						<option value="">All stages</option>
						<option value="backend">Backend</option>
						<option value="frontend">Frontend</option>
					</select>
				</div>
				<Button size="sm" class="w-full sm:w-auto" onclick={startCreatePrompt}>
					<Plus class="mr-1.5 h-3.5 w-3.5" /> New Prompt
				</Button>
			</div>

			{#if creatingPrompt || editingPrompt}
				<Card.Root>
					<Card.Header class="pb-3">
						<Card.Title class="text-sm">{editingPrompt ? 'Edit' : 'New'} Prompt Template</Card.Title>
					</Card.Header>
					<Card.Content>
						<div class="grid gap-4 md:grid-cols-2">
							<div class="space-y-1.5">
								<Label class="text-xs">Name</Label>
								<Input bind:value={promptForm.name} placeholder="Backend System Prompt v2" class="h-8 text-sm"
									oninput={() => { if (creatingPrompt) promptForm.slug = slugify(promptForm.name); }} />
							</div>
							<div class="space-y-1.5">
								<Label class="text-xs">Slug</Label>
								<Input bind:value={promptForm.slug} placeholder="backend-system-v2" class="h-8 text-sm font-mono" />
							</div>
							<div class="space-y-1.5">
								<Label class="text-xs">Stage</Label>
								<select bind:value={promptForm.stage} class="flex h-8 w-full rounded-md border bg-transparent px-3 text-sm">
									<option value="backend">Backend</option>
									<option value="frontend">Frontend</option>
								</select>
							</div>
							<div class="space-y-1.5">
								<Label class="text-xs">Role</Label>
								<select bind:value={promptForm.role} class="flex h-8 w-full rounded-md border bg-transparent px-3 text-sm">
									<option value="system">System</option>
									<option value="user">User</option>
								</select>
							</div>
							<div class="space-y-1.5 md:col-span-2">
								<Label class="text-xs">Content (Jinja2 template)</Label>
								<textarea bind:value={promptForm.content} rows="12" class="flex w-full rounded-md border bg-transparent px-3 py-2 text-xs font-mono leading-relaxed overflow-x-auto" placeholder="You are a senior developer..."></textarea>
								<p class="text-[10px] text-muted-foreground">
									Available Jinja2 variables: name, description, backend_requirements, frontend_requirements, admin_requirements, api_endpoints, data_model, backend_api_context
								</p>
							</div>
						</div>
						{#if promptError}
							<p class="mt-2 text-xs text-red-400">{promptError}</p>
						{/if}
						<div class="mt-4 flex gap-2">
							<Button size="sm" onclick={savePrompt} disabled={promptSaving || !promptForm.name || !promptForm.slug || !promptForm.content}>
								{#if promptSaving}<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin" />{:else}<Save class="mr-1.5 h-3.5 w-3.5" />{/if}
								Save
							</Button>
							<Button variant="outline" size="sm" onclick={cancelPromptForm}><XIcon class="mr-1.5 h-3.5 w-3.5" /> Cancel</Button>
						</div>
					</Card.Content>
				</Card.Root>
			{/if}

			{#if promptLoading}
				<div class="flex items-center justify-center py-12 text-sm text-muted-foreground">
					<LoaderCircle class="mr-2 h-4 w-4 animate-spin" /> Loading…
				</div>
			{:else if filteredPrompt.length === 0}
				<Card.Root>
					<Card.Content class="py-12 text-center text-sm text-muted-foreground">
						No prompt templates found.
					</Card.Content>
				</Card.Root>
			{:else}
				{#each Object.entries(groupedPrompts()) as [group, templates]}
					<div class="space-y-2">
						<h3 class="text-sm font-medium capitalize">{group.replace('/', ' → ')}</h3>
						{#each templates as t}
							<Card.Root>
								<Card.Content class="py-3 px-4">
									<div class="flex items-start justify-between">
										<div class="flex-1 min-w-0">
											<div class="flex items-center gap-2">
												<span class="font-medium text-sm">{t.name}</span>
												<Badge variant="outline" class="text-[10px]">{t.stage}</Badge>
												<Badge variant="outline" class="text-[10px]">{t.role}</Badge>
												{#if t.is_default}<Badge variant="secondary" class="text-[10px]">Default</Badge>{/if}
											</div>
											<pre class="text-xs text-muted-foreground mt-1 line-clamp-3 font-mono whitespace-pre-wrap">{t.content.slice(0, 200)}{t.content.length > 200 ? '…' : ''}</pre>
										</div>
										<div class="flex gap-1 ml-2 shrink-0">
											<Button variant="outline" size="sm" class="h-7 text-xs" onclick={() => startEditPrompt(t)}>
												<Pencil class="mr-1 h-3 w-3" /> Edit
											</Button>
											{#if !t.is_default}
												<Button variant="outline" size="sm" class="h-7 text-xs text-red-400 hover:text-red-300" onclick={() => deletePrompt(t)}>
													<Trash2 class="h-3 w-3" />
												</Button>
											{/if}
										</div>
									</div>
								</Card.Content>
							</Card.Root>
						{/each}
					</div>
				{/each}
			{/if}
		</div>
	{/if}
</div>
