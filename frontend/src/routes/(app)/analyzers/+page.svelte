<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Badge } from '$lib/components/ui/badge';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Switch } from '$lib/components/ui/switch';
	import { toast } from 'svelte-sonner';
	import ScanSearch from '@lucide/svelte/icons/scan-search';
	import Download from '@lucide/svelte/icons/download';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import Loader from '@lucide/svelte/icons/loader-circle';
	import Play from '@lucide/svelte/icons/play';
	import Power from '@lucide/svelte/icons/power';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import CheckCircle2 from '@lucide/svelte/icons/check-circle-2';
	import XCircle from '@lucide/svelte/icons/circle-x';
	import {
		getToolCatalog,
		getWorkspace,
		getInstalledTools,
		installTool,
		uninstallTool,
		updateToolConfig,
		testTool,
		provisionWorkspace,
		stopWorkspace,
		deleteWorkspace,
		type AnalyzerTool,
		type Workspace,
		type InstalledTool,
		type TestResult,
	} from '$lib/api/analyzers';
	import { severityColors, statusColors } from '$lib/constants/analysis';

	type Tab = 'shop' | 'installed' | 'workspace';
	let activeTab = $state<Tab>('shop');

	let catalog = $state<AnalyzerTool[]>([]);
	let installed = $state<InstalledTool[]>([]);
	let workspace = $state<Workspace | null>(null);
	let loading = $state(true);
	let busy = $state<Record<string, boolean>>({});
	let categoryFilter = $state('all');

	// Config / test modal
	let configOpen = $state(false);
	let activeTool = $state<InstalledTool | null>(null);
	let activeSchema = $state<AnalyzerTool | null>(null);
	let configDraft = $state<Record<string, unknown>>({});
	let testing = $state(false);
	let testResult = $state<TestResult | null>(null);

	let pollTimer: ReturnType<typeof setInterval> | null = null;

	const categoryLabels: Record<string, string> = {
		security: 'Security',
		lint: 'Lint & quality',
		performance: 'Performance',
		secrets: 'Secrets',
		ai: 'AI review',
	};

	const categories = $derived([
		'all',
		...Array.from(new Set(catalog.map((t) => t.category))),
	]);

	const visibleCatalog = $derived(
		categoryFilter === 'all'
			? catalog
			: catalog.filter((t) => t.category === categoryFilter),
	);

	async function loadAll() {
		loading = true;
		try {
			const [c, w, i] = await Promise.all([
				getToolCatalog(),
				getWorkspace(),
				getInstalledTools(),
			]);
			catalog = c;
			workspace = w;
			installed = i;
		} catch (e) {
			toast.error('Failed to load analyzers');
		} finally {
			loading = false;
		}
	}

	async function refreshState() {
		try {
			[workspace, installed] = await Promise.all([getWorkspace(), getInstalledTools()]);
			catalog = await getToolCatalog();
		} catch {
			/* ignore transient errors during polling */
		}
	}

	function isInstalled(slug: string): boolean {
		return installed.some((t) => t.tool_slug === slug);
	}

	async function handleInstall(tool: AnalyzerTool) {
		busy = { ...busy, [tool.slug]: true };
		try {
			await installTool(tool.slug);
			toast.success(`Installing ${tool.name}…`);
			await refreshState();
			startPolling();
		} catch (e: any) {
			toast.error(e?.detail || `Failed to install ${tool.name}`);
		} finally {
			busy = { ...busy, [tool.slug]: false };
		}
	}

	async function handleUninstall(slug: string, name: string) {
		busy = { ...busy, [slug]: true };
		try {
			await uninstallTool(slug);
			toast.success(`Uninstalled ${name}`);
			await refreshState();
		} catch {
			toast.error(`Failed to uninstall ${name}`);
		} finally {
			busy = { ...busy, [slug]: false };
		}
	}

	function openConfig(it: InstalledTool) {
		activeTool = it;
		activeSchema = catalog.find((t) => t.slug === it.tool_slug) ?? null;
		configDraft = { ...(it.config ?? {}) };
		testResult = null;
		configOpen = true;
	}

	async function saveConfig() {
		if (!activeTool) return;
		try {
			const updated = await updateToolConfig(activeTool.tool_slug, configDraft);
			installed = installed.map((t) => (t.id === updated.id ? updated : t));
			toast.success('Configuration saved');
			configOpen = false;
		} catch {
			toast.error('Failed to save configuration');
		}
	}

	async function runTest() {
		if (!activeTool) return;
		testing = true;
		testResult = null;
		try {
			testResult = await testTool(activeTool.tool_slug, configDraft);
		} catch (e: any) {
			toast.error(e?.detail || 'Test failed');
		} finally {
			testing = false;
		}
	}

	async function handleProvision() {
		try {
			workspace = await provisionWorkspace();
			toast.success('Provisioning workspace…');
			startPolling();
		} catch {
			toast.error('Failed to provision workspace');
		}
	}

	async function handleStop() {
		try {
			workspace = await stopWorkspace();
			toast.success('Workspace stopped');
		} catch {
			toast.error('Failed to stop workspace');
		}
	}

	async function handleDelete() {
		if (!confirm('Tear down the workspace and remove all installed tools?')) return;
		try {
			workspace = await deleteWorkspace();
			installed = [];
			catalog = await getToolCatalog();
			toast.success('Workspace removed');
		} catch {
			toast.error('Failed to remove workspace');
		}
	}

	function startPolling() {
		stopPolling();
		pollTimer = setInterval(async () => {
			await refreshState();
			const stillBusy =
				workspace?.status === 'provisioning' ||
				installed.some((t) => t.status === 'installing');
			if (!stillBusy) stopPolling();
		}, 3000);
	}

	function stopPolling() {
		if (pollTimer) {
			clearInterval(pollTimer);
			pollTimer = null;
		}
	}

	onMount(loadAll);
	onDestroy(stopPolling);
</script>

<svelte:head><title>Analyzers - LLM Lab</title></svelte:head>

<div class="space-y-6">
	<div class="page-header flex items-center justify-between">
		<div class="flex items-center gap-3">
			<ScanSearch class="size-7 text-primary" />
			<div>
				<h1 class="text-2xl font-semibold">Analyzers</h1>
				<p class="text-sm text-muted-foreground">
					Browse the tool shop, install tools into your analyzer workspace, configure and test them.
				</p>
			</div>
		</div>
		<Button variant="outline" size="sm" onclick={loadAll}>
			<RefreshCw class="size-4" /> Refresh
		</Button>
	</div>

	<!-- Tabs -->
	<div class="flex gap-1 border-b">
		{#each [['shop', 'Shop'], ['installed', `Installed (${installed.length})`], ['workspace', 'Workspace']] as [key, label]}
			<button
				class="px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors {activeTab === key
					? 'border-primary text-primary'
					: 'border-transparent text-muted-foreground hover:text-foreground'}"
				onclick={() => (activeTab = key as Tab)}
			>
				{label}
			</button>
		{/each}
	</div>

	{#if loading}
		<div class="flex items-center justify-center py-20 text-muted-foreground">
			<Loader class="size-6 animate-spin" />
		</div>
	{:else if activeTab === 'shop'}
		<!-- Category filter -->
		<div class="flex flex-wrap gap-2">
			{#each categories as cat}
				<button
					class="rounded-full border px-3 py-1 text-xs {categoryFilter === cat
						? 'bg-primary text-primary-foreground border-primary'
						: 'text-muted-foreground hover:bg-muted'}"
					onclick={() => (categoryFilter = cat)}
				>
					{cat === 'all' ? 'All' : (categoryLabels[cat] ?? cat)}
				</button>
			{/each}
		</div>

		<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
			{#each visibleCatalog as tool (tool.slug)}
				<Card.Root class="flex flex-col">
					<Card.Header>
						<div class="flex items-start justify-between gap-2">
							<Card.Title class="text-base">{tool.name}</Card.Title>
							<Badge variant="outline">{categoryLabels[tool.category] ?? tool.category}</Badge>
						</div>
						<Card.Description class="line-clamp-3">{tool.description}</Card.Description>
					</Card.Header>
					<Card.Content class="mt-auto flex items-center justify-between">
						<span class="text-xs text-muted-foreground">
							{tool.kind === 'ai' ? 'AI' : tool.target_language}{tool.version ? ` · v${tool.version}` : ''}
						</span>
						{#if isInstalled(tool.slug)}
							<Button
								variant="outline"
								size="sm"
								disabled={busy[tool.slug]}
								onclick={() => handleUninstall(tool.slug, tool.name)}
							>
								{#if busy[tool.slug]}<Loader class="size-4 animate-spin" />{:else}<Trash2 class="size-4" />{/if}
								Uninstall
							</Button>
						{:else}
							<Button
								size="sm"
								disabled={busy[tool.slug]}
								onclick={() => handleInstall(tool)}
							>
								{#if busy[tool.slug]}<Loader class="size-4 animate-spin" />{:else}<Download class="size-4" />{/if}
								Install
							</Button>
						{/if}
					</Card.Content>
				</Card.Root>
			{/each}
		</div>
	{:else if activeTab === 'installed'}
		{#if installed.length === 0}
			<div class="rounded-lg border border-dashed py-16 text-center text-muted-foreground">
				No tools installed yet. Visit the <button class="text-primary underline" onclick={() => (activeTab = 'shop')}>Shop</button> to add some.
			</div>
		{:else}
			<div class="space-y-3">
				{#each installed as it (it.id)}
					<Card.Root>
						<Card.Content class="flex items-center justify-between gap-4 py-4">
							<div class="min-w-0">
								<div class="flex items-center gap-2">
									<span class="font-medium">{it.tool_name}</span>
									<Badge class={statusColors[it.status] ?? ''}>{it.status}</Badge>
									{#if it.installed_version}
										<span class="text-xs text-muted-foreground truncate">{it.installed_version}</span>
									{/if}
								</div>
								{#if it.status === 'failed' && it.install_log}
									<p class="mt-1 text-xs text-red-400 line-clamp-2">{it.install_log}</p>
								{/if}
							</div>
							<div class="flex shrink-0 gap-2">
								<Button variant="outline" size="sm" onclick={() => openConfig(it)}>
									Configure & test
								</Button>
								<Button
									variant="ghost"
									size="sm"
									disabled={busy[it.tool_slug]}
									onclick={() => handleUninstall(it.tool_slug, it.tool_name)}
								>
									<Trash2 class="size-4" />
								</Button>
							</div>
						</Card.Content>
					</Card.Root>
				{/each}
			</div>
		{/if}
	{:else if activeTab === 'workspace'}
		<Card.Root>
			<Card.Header>
				<Card.Title>Analyzer workspace</Card.Title>
				<Card.Description>
					A private container that holds your installed tools and runs analyses.
				</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-4">
				<div class="flex items-center gap-3">
					<span class="text-sm text-muted-foreground">Status</span>
					<Badge class={statusColors[workspace?.status ?? ''] ?? ''}>
						{workspace?.status ?? 'unknown'}
					</Badge>
					{#if workspace?.status === 'provisioning'}
						<Loader class="size-4 animate-spin text-muted-foreground" />
					{/if}
				</div>
				{#if workspace?.container_name}
					<div class="text-sm text-muted-foreground">Container: <code>{workspace.container_name}</code></div>
				{/if}
				{#if workspace?.error_message}
					<p class="text-sm text-red-400">{workspace.error_message}</p>
				{/if}
				<div class="text-sm text-muted-foreground">
					{workspace?.installed_count ?? 0} tool(s) installed
				</div>
				<div class="flex gap-2">
					{#if workspace?.status === 'ready'}
						<Button variant="outline" size="sm" onclick={handleStop}>
							<Power class="size-4" /> Stop
						</Button>
					{:else}
						<Button size="sm" onclick={handleProvision} disabled={workspace?.status === 'provisioning'}>
							<Play class="size-4" /> {workspace?.status === 'stopped' ? 'Start' : 'Provision'}
						</Button>
					{/if}
					<Button variant="ghost" size="sm" onclick={handleDelete}>
						<Trash2 class="size-4" /> Tear down
					</Button>
				</div>
			</Card.Content>
		</Card.Root>
	{/if}
</div>

<!-- Configure & test dialog -->
<Dialog.Root bind:open={configOpen}>
	<Dialog.Content class="max-w-lg">
		<Dialog.Header>
			<Dialog.Title>{activeTool?.tool_name}</Dialog.Title>
			<Dialog.Description>Configure options and run a sample test.</Dialog.Description>
		</Dialog.Header>

		<div class="space-y-4">
			{#if activeSchema && activeSchema.config_schema.length > 0}
				{#each activeSchema.config_schema as field}
					<div class="space-y-1.5">
						<Label>{field.label}</Label>
						{#if field.type === 'boolean'}
							<div>
								<Switch
									checked={!!configDraft[field.name]}
									onCheckedChange={(v) => (configDraft = { ...configDraft, [field.name]: v })}
								/>
							</div>
						{:else if field.type === 'select'}
							<select
								class="w-full rounded-md border bg-background px-3 py-2 text-sm"
								value={configDraft[field.name] ?? field.default}
								onchange={(e) =>
									(configDraft = { ...configDraft, [field.name]: e.currentTarget.value })}
							>
								{#each field.options as opt}
									<option value={opt.value}>{opt.label}</option>
								{/each}
							</select>
						{:else}
							<Input
								type={field.type === 'number' ? 'number' : 'text'}
								placeholder={field.placeholder}
								value={String(configDraft[field.name] ?? field.default ?? '')}
								oninput={(e) =>
									(configDraft = { ...configDraft, [field.name]: e.currentTarget.value })}
							/>
						{/if}
						{#if field.description}
							<p class="text-xs text-muted-foreground">{field.description}</p>
						{/if}
					</div>
				{/each}
			{:else}
				<p class="text-sm text-muted-foreground">This tool has no configurable options.</p>
			{/if}

			{#if testResult}
				<div class="rounded-md border p-3 text-sm">
					<div class="flex items-center gap-2 font-medium">
						{#if testResult.available}
							<CheckCircle2 class="size-4 text-emerald-500" /> Available
						{:else}
							<XCircle class="size-4 text-red-400" /> Unavailable
						{/if}
					</div>
					<p class="mt-1 text-xs text-muted-foreground">{testResult.message}</p>
					{#if testResult.findings.length > 0}
						<p class="mt-2 text-xs">Sample run produced {testResult.findings.length} finding(s):</p>
						<ul class="mt-1 space-y-1">
							{#each testResult.findings.slice(0, 5) as f}
								<li class="flex items-center gap-2 text-xs">
									<Badge class={severityColors[(f.severity as string) ?? 'info'] ?? ''}>
										{f.severity}
									</Badge>
									<span class="truncate">{f.title}</span>
								</li>
							{/each}
						</ul>
					{/if}
				</div>
			{/if}
		</div>

		<Dialog.Footer>
			<Button variant="outline" onclick={runTest} disabled={testing}>
				{#if testing}<Loader class="size-4 animate-spin" />{:else}<Play class="size-4" />{/if}
				Test
			</Button>
			<Button onclick={saveConfig}>Save</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
