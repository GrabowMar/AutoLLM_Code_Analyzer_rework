<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import * as Dialog from '$lib/components/ui/dialog';
	import * as Select from '$lib/components/ui/select';
	import * as Tabs from '$lib/components/ui/tabs';
	import { Badge } from '$lib/components/ui/badge';
	import { Input } from '$lib/components/ui/input';
	import { Segmented } from '$lib/components/ui/segmented';
	import { toast } from 'svelte-sonner';
	import Download from '@lucide/svelte/icons/download';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import Loader from '@lucide/svelte/icons/loader-circle';
	import Play from '@lucide/svelte/icons/play';
	import Power from '@lucide/svelte/icons/power';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import ArrowUpCircle from '@lucide/svelte/icons/circle-arrow-up';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import Settings2 from '@lucide/svelte/icons/settings-2';
	import ScrollText from '@lucide/svelte/icons/scroll-text';
	import Info from '@lucide/svelte/icons/info';
	import Copy from '@lucide/svelte/icons/copy';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import Search from '@lucide/svelte/icons/search';
	import SearchX from '@lucide/svelte/icons/search-x';
	import Store from '@lucide/svelte/icons/store';
	import PackageOpen from '@lucide/svelte/icons/package-open';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import ToolConfigDialog from '$lib/components/analyzers/ToolConfigDialog.svelte';
	import ToolIcon from '$lib/components/analyzers/ToolIcon.svelte';
	import { formatDateTime } from '$lib/utils/formatters';
	import type { Tone } from '$lib/constants/colors';
	import {
		getToolCatalog,
		getWorkspace,
		getInstalledTools,
		installTool,
		bulkInstallTools,
		updateTool,
		updateAllTools,
		uninstallTool,
		provisionWorkspace,
		stopWorkspace,
		deleteWorkspace,
		type AnalyzerTool,
		type Workspace,
		type InstalledTool,
	} from '$lib/api/analyzers';

	// Tool install / workspace lifecycle → badge tone.
	const analyzerTones: Partial<Record<string, Tone>> = {
		installed: 'success',
		installing: 'info',
		updating: 'info',
		provisioning: 'info',
		stopped: 'warning',
	};

	// Also the shop's category order.
	const categoryLabels: Record<string, string> = {
		security: 'Security',
		dependencies: 'Dependencies',
		lint: 'Lint & quality',
		performance: 'Performance',
		secrets: 'Secrets',
		ai: 'AI review',
	};

	const languageLabels: Record<string, string> = {
		python: 'Python',
		javascript: 'JavaScript / TS',
		html: 'HTML',
		css: 'CSS',
		shell: 'Shell',
		any: 'Any language',
	};

	function languageOf(tool: AnalyzerTool): string {
		return tool.kind === 'ai' ? 'AI' : (languageLabels[tool.target_language] ?? tool.target_language);
	}

	const outputKindLabels: Record<string, string> = {
		findings: 'Findings',
		metrics: 'Metrics',
		mixed: 'Findings + metrics',
	};

	// Generic confirmation dialog for destructive actions (uninstall / teardown).
	let confirmState = $state<{
		open: boolean;
		title: string;
		description: string;
		confirmLabel: string;
		busy: boolean;
		action: () => Promise<void>;
	}>({ open: false, title: '', description: '', confirmLabel: 'Confirm', busy: false, action: async () => {} });

	function askConfirm(opts: { title: string; description: string; confirmLabel: string; action: () => Promise<void> }) {
		confirmState = { ...confirmState, ...opts, open: true, busy: false };
	}

	async function runConfirm() {
		confirmState.busy = true;
		try {
			await confirmState.action();
			confirmState.open = false;
		} finally {
			confirmState.busy = false;
		}
	}

	type Tab = 'shop' | 'installed' | 'workspace';
	type StateFilter = 'all' | 'installed' | 'available' | 'updates';

	let activeTab = $state<Tab>('shop');
	let catalog = $state<AnalyzerTool[]>([]);
	let installed = $state<InstalledTool[]>([]);
	let workspace = $state<Workspace | null>(null);
	let loading = $state(true);
	let loadError = $state('');
	// Requests in flight, keyed by tool slug (or a bulk action name).
	let busy = $state<Record<string, boolean>>({});
	let stateFilter = $state<StateFilter>('all');
	let categoryFilter = $state('all');
	let searchQuery = $state('');

	// Dialogs
	let configOpen = $state(false);
	let configTarget = $state<InstalledTool | null>(null);
	let logOpen = $state(false);
	let logTarget = $state<InstalledTool | null>(null);
	let detailOpen = $state(false);
	let detailSlug = $state<string | null>(null);

	let pollTimer: ReturnType<typeof setInterval> | null = null;

	const installedBySlug = $derived(new Map(installed.map((it) => [it.tool_slug, it])));
	const toolBySlug = $derived(new Map(catalog.map((t) => [t.slug, t])));
	const outdated = $derived(installed.filter((it) => it.update_available));
	const inFlight = $derived(installed.filter((it) => isWorking(it)));
	const detailTool = $derived(detailSlug ? (toolBySlug.get(detailSlug) ?? null) : null);
	const detailInstall = $derived(detailSlug ? (installedBySlug.get(detailSlug) ?? null) : null);

	const categoryItems = $derived([
		{ value: 'all', label: 'All categories' },
		...Array.from(new Set(catalog.map((t) => t.category))).map((c) => ({
			value: c,
			label: `${categoryLabels[c] ?? c} (${catalog.filter((t) => t.category === c).length})`,
		})),
	]);

	const stateItems = $derived([
		{ value: 'all', label: 'All' },
		{ value: 'installed', label: `Installed (${installed.length})` },
		{ value: 'available', label: 'Not installed' },
		{ value: 'updates', label: `Updates (${outdated.length})` },
	]);

	// Shop order: by category, then the catalog's own display order within it.
	const categoryRank = (c: string) => {
		const i = Object.keys(categoryLabels).indexOf(c);
		return i === -1 ? Infinity : i;
	};

	const visibleCatalog = $derived.by(() => {
		const q = searchQuery.trim().toLowerCase();
		const matches = catalog.filter((t) => {
			const it = installedBySlug.get(t.slug);
			const stateOk =
				stateFilter === 'all' ||
				(stateFilter === 'installed' && !!it) ||
				(stateFilter === 'available' && !it) ||
				(stateFilter === 'updates' && !!it?.update_available);
			return (
				stateOk &&
				(categoryFilter === 'all' || t.category === categoryFilter) &&
				(!q ||
					t.name.toLowerCase().includes(q) ||
					t.description.toLowerCase().includes(q) ||
					languageOf(t).toLowerCase().includes(q))
			);
		});
		return matches
			.map((t, i) => ({ t, i }))
			.sort((a, b) => categoryRank(a.t.category) - categoryRank(b.t.category) || a.i - b.i)
			.map(({ t }) => t);
	});

	const installableVisible = $derived(visibleCatalog.filter((t) => !installedBySlug.has(t.slug)));

	function isWorking(it: InstalledTool | undefined | null): boolean {
		return it?.status === 'installing' || it?.status === 'updating';
	}

	function versionLabel(it: InstalledTool): string {
		return it.installed_version || (it.catalog_version ? `v${it.catalog_version}` : '');
	}

	function errorDetail(e: any, fallback: string): string {
		return typeof e?.detail === 'string' ? e.detail : fallback;
	}

	// Announce installs that finished since the last refresh.
	function announceTransitions(prev: InstalledTool[], next: InstalledTool[]) {
		const before = new Map(prev.map((it) => [it.tool_slug, it.status]));
		for (const it of next) {
			const was = before.get(it.tool_slug);
			if (was !== 'installing' && was !== 'updating') continue;
			if (it.status === 'installed') {
				const failedUpdate = was === 'updating' && it.update_available;
				if (failedUpdate) toast.warning(`${it.tool_name} update failed; kept the current version`);
				else toast.success(`${it.tool_name} ${was === 'updating' ? 'updated' : 'installed'}`);
			} else if (it.status === 'failed') {
				toast.error(`${it.tool_name} failed to install`, {
					action: { label: 'View log', onClick: () => openLog(it) },
				});
			}
		}
	}

	async function loadAll() {
		loading = true;
		loadError = '';
		try {
			const [c, w, i] = await Promise.all([getToolCatalog(), getWorkspace(), getInstalledTools()]);
			catalog = c;
			workspace = w;
			installed = i;
			ensurePolling();
		} catch (e) {
			loadError = errorDetail(e, 'Failed to load analyzers');
		} finally {
			loading = false;
		}
	}

	async function refreshState() {
		try {
			const [w, i, c] = await Promise.all([getWorkspace(), getInstalledTools(), getToolCatalog()]);
			announceTransitions(installed, i);
			workspace = w;
			installed = i;
			catalog = c;
		} catch {
			/* ignore transient errors during polling */
		}
	}

	async function withBusy(key: string, fn: () => Promise<void>) {
		busy = { ...busy, [key]: true };
		try {
			await fn();
		} finally {
			busy = { ...busy, [key]: false };
		}
	}

	function mergeInstalled(rows: InstalledTool[]) {
		const bySlug = new Map(installed.map((it) => [it.tool_slug, it]));
		for (const r of rows) bySlug.set(r.tool_slug, r);
		installed = [...bySlug.values()];
	}

	async function handleInstall(tool: AnalyzerTool) {
		await withBusy(tool.slug, async () => {
			try {
				mergeInstalled([await installTool(tool.slug)]);
				toast.info(`Installing ${tool.name}…`);
				ensurePolling();
			} catch (e) {
				toast.error(errorDetail(e, `Failed to install ${tool.name}`));
			}
		});
	}

	async function handleInstallVisible() {
		const slugs = installableVisible.map((t) => t.slug);
		if (slugs.length === 0) return;
		await withBusy('bulk', async () => {
			try {
				const rows = await bulkInstallTools(slugs);
				mergeInstalled(rows);
				toast.info(`Installing ${rows.length} tool(s) one after another…`);
				ensurePolling();
			} catch (e) {
				toast.error(errorDetail(e, 'Failed to start installs'));
			}
		});
	}

	async function handleUpdate(it: InstalledTool) {
		await withBusy(it.tool_slug, async () => {
			try {
				mergeInstalled([await updateTool(it.tool_slug)]);
				toast.info(`${it.status === 'failed' ? 'Retrying' : 'Updating'} ${it.tool_name}…`);
				ensurePolling();
			} catch (e) {
				toast.error(errorDetail(e, `Failed to update ${it.tool_name}`));
			}
		});
	}

	async function handleUpdateAll(onlyOutdated: boolean) {
		await withBusy(onlyOutdated ? 'update-all' : 'reinstall-all', async () => {
			try {
				const rows = await updateAllTools(onlyOutdated);
				mergeInstalled(rows);
				if (rows.length === 0) toast.info('Nothing to update');
				else toast.info(`${onlyOutdated ? 'Updating' : 'Reinstalling'} ${rows.length} tool(s)…`);
				ensurePolling();
			} catch (e) {
				toast.error(errorDetail(e, 'Failed to start updates'));
			}
		});
	}

	async function handleUninstall(slug: string, name: string) {
		await withBusy(slug, async () => {
			try {
				const res = await uninstallTool(slug);
				installed = installed.filter((it) => it.tool_slug !== slug);
				toast.success(`Uninstalled ${name}`, res.message ? { description: res.message } : undefined);
				await refreshState();
			} catch (e) {
				toast.error(errorDetail(e, `Failed to uninstall ${name}`));
			}
		});
	}

	function confirmUninstall(slug: string, name: string) {
		askConfirm({
			title: `Uninstall ${name}?`,
			description:
				'The tool is removed from your workspace and its saved configuration is discarded. You can reinstall it anytime.',
			confirmLabel: 'Uninstall',
			action: () => handleUninstall(slug, name),
		});
	}

	function openConfig(it: InstalledTool) {
		configTarget = it;
		configOpen = true;
	}

	function openLog(it: InstalledTool) {
		logTarget = it;
		logOpen = true;
	}

	function openDetails(slug: string) {
		detailSlug = slug;
		detailOpen = true;
	}

	async function copyLog() {
		try {
			await navigator.clipboard.writeText(logTarget?.install_log ?? '');
			toast.success('Log copied');
		} catch {
			toast.error('Could not copy to clipboard');
		}
	}

	async function handleProvision() {
		try {
			workspace = await provisionWorkspace();
			toast.info(workspace.status === 'ready' ? 'Workspace is ready' : 'Starting workspace…');
			ensurePolling();
		} catch {
			toast.error('Failed to start workspace');
		}
	}

	async function handleStop() {
		await withBusy('ws-stop', async () => {
			try {
				workspace = await stopWorkspace();
				toast.success('Workspace stopped');
			} catch {
				toast.error('Failed to stop workspace');
			}
		});
	}

	async function handleDelete() {
		try {
			workspace = await deleteWorkspace();
			installed = [];
			catalog = await getToolCatalog();
			toast.success('Workspace removed');
		} catch {
			toast.error('Failed to remove workspace');
		}
	}

	// Poll while anything is installing/updating or the workspace is starting.
	function ensurePolling() {
		const active = () => workspace?.status === 'provisioning' || installed.some((it) => isWorking(it));
		if (pollTimer || !active()) return;
		pollTimer = setInterval(async () => {
			await refreshState();
			if (!active()) stopPolling();
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

{#snippet toolActions(tool: AnalyzerTool, it: InstalledTool | undefined)}
	{#if !it}
		<Button size="sm" disabled={busy[tool.slug]} onclick={() => handleInstall(tool)}>
			{#if busy[tool.slug]}<Loader class="size-4 animate-spin motion-reduce:animate-none" />{:else}<Download class="size-4" />{/if}
			Install
		</Button>
	{:else if isWorking(it)}
		<Button size="sm" variant="outline" disabled>
			<Loader class="size-4 animate-spin motion-reduce:animate-none" />
			{it.status === 'updating' ? 'Updating…' : 'Installing…'}
		</Button>
	{:else if it.status === 'failed'}
		<Button size="sm" variant="ghost" onclick={() => openLog(it)}>
			<ScrollText class="size-4" /> Log
		</Button>
		<Button size="sm" disabled={busy[tool.slug]} onclick={() => handleUpdate(it)}>
			{#if busy[tool.slug]}<Loader class="size-4 animate-spin motion-reduce:animate-none" />{:else}<RotateCcw class="size-4" />{/if}
			Retry
		</Button>
	{:else if it.update_available}
		<Button size="sm" variant="outline" onclick={() => openConfig(it)}>
			<Settings2 class="size-4" /> Configure
		</Button>
		<Button size="sm" disabled={busy[tool.slug]} onclick={() => handleUpdate(it)}>
			{#if busy[tool.slug]}<Loader class="size-4 animate-spin motion-reduce:animate-none" />{:else}<ArrowUpCircle class="size-4" />{/if}
			Update
		</Button>
	{:else}
		<Button size="sm" variant="outline" onclick={() => openConfig(it)}>
			<Settings2 class="size-4" /> Configure
		</Button>
	{/if}
{/snippet}

<div class="space-y-6">
	<PageHeader
		title="Analyzers"
		description="Browse the tool shop, install and update tools in your analyzer workspace, configure and test them."
	>
		{#snippet actions()}
			<Button variant="outline" size="sm" onclick={loadAll} disabled={loading}>
				<RefreshCw class="size-4" /> Refresh
			</Button>
			{#if outdated.length > 0}
				<Button size="sm" disabled={busy['update-all']} onclick={() => handleUpdateAll(true)}>
					{#if busy['update-all']}<Loader class="size-4 animate-spin motion-reduce:animate-none" />{:else}<ArrowUpCircle class="size-4" />{/if}
					Update all ({outdated.length})
				</Button>
			{/if}
		{/snippet}
	</PageHeader>

	{#if loadError}<ErrorAlert message={loadError} onretry={loadAll} />{/if}

	<Tabs.Root bind:value={activeTab} class="space-y-6">
		<Tabs.List aria-label="Analyzer sections">
			<Tabs.Trigger value="shop">Shop</Tabs.Trigger>
			<Tabs.Trigger value="installed">
				Installed ({installed.length}){#if outdated.length > 0}<span class="ml-1.5 text-info-fg">· {outdated.length} update{outdated.length === 1 ? '' : 's'}</span>{/if}
			</Tabs.Trigger>
			<Tabs.Trigger value="workspace">Workspace</Tabs.Trigger>
		</Tabs.List>

		{#if loading}
			<LoadingState label="loading analyzers" />
		{:else}
			<Tabs.Content value="shop" class="space-y-4">
				{#if workspace && workspace.status !== 'ready' && workspace.status !== 'provisioning'}
					<p class="flex items-center gap-2 text-sm text-muted-foreground">
						<Info class="size-4 shrink-0" />
						Your workspace is {workspace.status}. Installing a container tool starts it automatically.
					</p>
				{/if}

				<!-- State / category filter + search -->
				<div class="flex flex-wrap items-center gap-3">
					<Segmented aria-label="Filter by install state" size="sm" value={stateFilter} onValueChange={(v) => (stateFilter = v as StateFilter)} items={stateItems} />
					<Select.Simple
						aria-label="Filter by category"
						size="sm"
						class="w-48"
						bind:value={categoryFilter}
						items={categoryItems}
					/>
					<div class="relative w-full sm:ml-auto sm:w-60">
						<Search class="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
						<Input type="search" placeholder="Search tools…" aria-label="Search tools" class="pl-8" bind:value={searchQuery} />
					</div>
				</div>

				{#if installableVisible.length > 1}
					<div class="flex items-center justify-between gap-3 text-sm text-muted-foreground">
						<span>{visibleCatalog.length} tool(s) shown · {installableVisible.length} not installed</span>
						<Button size="sm" variant="outline" disabled={busy.bulk} onclick={handleInstallVisible}>
							{#if busy.bulk}<Loader class="size-4 animate-spin motion-reduce:animate-none" />{:else}<Download class="size-4" />{/if}
							Install {installableVisible.length} shown
						</Button>
					</div>
				{/if}

				{#if visibleCatalog.length === 0}
					{#if catalog.length === 0}
						<EmptyState icon={Store} title="The tool catalog is empty" />
					{:else}
						<EmptyState icon={SearchX} title="No tools match your filters">
							{#snippet action()}
								<Button
									variant="outline"
									size="sm"
									onclick={() => {
										searchQuery = '';
										categoryFilter = 'all';
										stateFilter = 'all';
									}}
								>
									Clear filters
								</Button>
							{/snippet}
						</EmptyState>
					{/if}
				{/if}

				<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
					{#each visibleCatalog as tool (tool.slug)}
						{@const it = installedBySlug.get(tool.slug)}
						<Card.Root class="flex flex-col">
							<Card.Header>
								<div class="flex items-start gap-3">
									<ToolIcon {tool} />
									<div class="min-w-0 flex-1">
										<div class="flex items-start justify-between gap-2">
											<Card.Title class="text-base">{tool.name}</Card.Title>
											<Badge variant="outline">{categoryLabels[tool.category] ?? tool.category}</Badge>
										</div>
										<p class="text-xs text-muted-foreground">
											{languageOf(tool)}{tool.version ? ` · v${tool.version}` : ''}
										</p>
									</div>
								</div>
								<Card.Description class="line-clamp-3">{tool.description}</Card.Description>
							</Card.Header>
							<Card.Content class="mt-auto space-y-3">
								{#if it}
									<StatusBadge
										status={it.update_available ? 'update available' : it.status}
										tone={it.update_available ? 'info' : undefined}
										tones={analyzerTones}
									/>
								{/if}
								<div class="flex items-center justify-between gap-2">
									<Button
										variant="ghost"
										size="sm"
										class="-ml-2"
										aria-label="{tool.name} details"
										onclick={() => openDetails(tool.slug)}
									>
										<Info class="size-4" /> Details
									</Button>
									<div class="flex gap-2">
										{@render toolActions(tool, it)}
									</div>
								</div>
							</Card.Content>
						</Card.Root>
					{/each}
				</div>
			</Tabs.Content>

			<Tabs.Content value="installed" class="space-y-4">
				{#if installed.length === 0}
					<EmptyState icon={PackageOpen} title="No tools installed yet" description="Install analyzers from the shop to use them in analysis runs.">
						{#snippet action()}
							<Button size="sm" onclick={() => (activeTab = 'shop')}>
								<Store class="size-4" /> Browse the shop
							</Button>
						{/snippet}
					</EmptyState>
				{:else}
					<div class="flex flex-wrap items-center justify-between gap-3">
						<p class="text-sm text-muted-foreground">
							{installed.length} installed
							{#if outdated.length > 0}· {outdated.length} with updates{/if}
							{#if inFlight.length > 0}· {inFlight.length} in progress{/if}
						</p>
						<div class="flex gap-2">
							<Button
								variant="outline"
								size="sm"
								disabled={busy['reinstall-all']}
								title="Re-run every tool's install, e.g. after the workspace container was rebuilt"
								onclick={() => handleUpdateAll(false)}
							>
								{#if busy['reinstall-all']}<Loader class="size-4 animate-spin motion-reduce:animate-none" />{:else}<RotateCcw class="size-4" />{/if}
								Reinstall all
							</Button>
							<Button size="sm" disabled={outdated.length === 0 || busy['update-all']} onclick={() => handleUpdateAll(true)}>
								{#if busy['update-all']}<Loader class="size-4 animate-spin motion-reduce:animate-none" />{:else}<ArrowUpCircle class="size-4" />{/if}
								Update all{outdated.length > 0 ? ` (${outdated.length})` : ''}
							</Button>
						</div>
					</div>

					<div class="space-y-3">
						{#each installed as it (it.id)}
							{@const working = isWorking(it)}
							<Card.Root>
								<Card.Content class="flex flex-wrap items-center justify-between gap-4 py-4">
									<div class="flex min-w-0 items-start gap-3">
									<ToolIcon tool={toolBySlug.get(it.tool_slug) ?? { slug: it.tool_slug, category: it.category }} />
									<div class="min-w-0 space-y-1">
										<div class="flex flex-wrap items-center gap-2">
											<button class="font-medium hover:underline" onclick={() => openDetails(it.tool_slug)}>
												{it.tool_name}
											</button>
											<StatusBadge status={it.status} tones={analyzerTones} />
											{#if it.update_available}
												<StatusBadge status="update" tone="info" label="v{it.latest_version} available" />
											{/if}
										</div>
										<p class="text-xs text-muted-foreground">
											{categoryLabels[it.category] ?? it.category}
											{#if versionLabel(it)}· <span class="font-mono">{versionLabel(it)}</span>{/if}
											{#if it.updated_at}· updated {formatDateTime(it.updated_at)}{/if}
										</p>
										{#if it.status === 'failed' && it.install_log}
											<p class="text-xs text-destructive line-clamp-2">{it.install_log}</p>
										{/if}
									</div>
									</div>
									<div class="flex shrink-0 flex-wrap gap-2">
										<Button
											variant={it.update_available || it.status === 'failed' ? 'default' : 'outline'}
											size="sm"
											disabled={working || busy[it.tool_slug]}
											onclick={() => handleUpdate(it)}
										>
											{#if working || busy[it.tool_slug]}
												<Loader class="size-4 animate-spin motion-reduce:animate-none" />
											{:else if it.update_available}
												<ArrowUpCircle class="size-4" />
											{:else}
												<RotateCcw class="size-4" />
											{/if}
											{#if working}
												{it.status === 'updating' ? 'Updating…' : 'Installing…'}
											{:else if it.status === 'failed'}
												Retry
											{:else if it.update_available}
												Update
											{:else}
												Reinstall
											{/if}
										</Button>
										<Button variant="outline" size="sm" disabled={working} onclick={() => openConfig(it)}>
											<Settings2 class="size-4" /> Configure & test
										</Button>
										<Button variant="ghost" size="sm" disabled={!it.install_log} onclick={() => openLog(it)}>
											<ScrollText class="size-4" /> Log
										</Button>
										<Button
											variant="ghost"
											size="sm"
											aria-label="Uninstall {it.tool_name}"
											disabled={working || busy[it.tool_slug]}
											onclick={() => confirmUninstall(it.tool_slug, it.tool_name)}
										>
											<Trash2 class="size-4" />
										</Button>
									</div>
								</Card.Content>
							</Card.Root>
						{/each}
					</div>
				{/if}
			</Tabs.Content>

			<Tabs.Content value="workspace">
				<Card.Root>
					<Card.Header>
						<Card.Title>Analyzer workspace</Card.Title>
						<Card.Description>
							A private container that holds your installed tools and runs analyses.
						</Card.Description>
					</Card.Header>
					<Card.Content class="space-y-4">
						<dl class="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[auto_1fr]">
							<dt class="text-muted-foreground">Status</dt>
							<dd><StatusBadge status={workspace?.status} tones={analyzerTones} /></dd>
							{#if workspace?.container_name}
								<dt class="text-muted-foreground">Container</dt>
								<dd><code>{workspace.container_name}</code></dd>
							{/if}
							{#if workspace?.image}
								<dt class="text-muted-foreground">Image</dt>
								<dd><code>{workspace.image}</code></dd>
							{/if}
							<dt class="text-muted-foreground">Tools</dt>
							<dd>{workspace?.installed_count ?? 0} installed</dd>
							{#if workspace?.last_used_at}
								<dt class="text-muted-foreground">Last used</dt>
								<dd>{formatDateTime(workspace.last_used_at)}</dd>
							{/if}
						</dl>
						{#if workspace?.error_message}
							<ErrorAlert message={workspace.error_message} onretry={handleProvision} />
						{/if}
						<div class="flex flex-wrap gap-2">
							{#if workspace?.status === 'ready'}
								<Button variant="outline" size="sm" disabled={busy['ws-stop']} onclick={handleStop}>
									<Power class="size-4" /> Stop
								</Button>
							{:else}
								<Button size="sm" onclick={handleProvision} disabled={workspace?.status === 'provisioning'}>
									{#if workspace?.status === 'provisioning'}<Loader class="size-4 animate-spin motion-reduce:animate-none" />{:else}<Play class="size-4" />{/if}
									{workspace?.status === 'stopped' ? 'Start' : 'Provision'}
								</Button>
							{/if}
							<Button
								variant="outline"
								size="sm"
								disabled={installed.length === 0 || busy['reinstall-all']}
								title="Re-run every tool's install inside the container"
								onclick={() => handleUpdateAll(false)}
							>
								<RotateCcw class="size-4" /> Repair tools
							</Button>
							<Button
								variant="ghost"
								size="sm"
								onclick={() =>
									askConfirm({
										title: 'Tear down workspace?',
										description: 'This removes the workspace container and ALL installed tools. This cannot be undone.',
										confirmLabel: 'Tear down',
										action: handleDelete,
									})}
							>
								<Trash2 class="size-4" /> Tear down
							</Button>
						</div>
					</Card.Content>
				</Card.Root>
			</Tabs.Content>
		{/if}
	</Tabs.Root>
</div>

<ToolConfigDialog
	bind:open={configOpen}
	installed={configTarget}
	tool={configTarget ? (toolBySlug.get(configTarget.tool_slug) ?? null) : null}
	onsaved={(updated) => mergeInstalled([updated])}
/>

<!-- Install log -->
<Dialog.Root bind:open={logOpen}>
	<Dialog.Content class="max-w-2xl">
		<Dialog.Header>
			<Dialog.Title>{logTarget?.tool_name} install log</Dialog.Title>
			<Dialog.Description>Output of the last install or update (last 4000 characters).</Dialog.Description>
		</Dialog.Header>
		<pre class="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-md border bg-muted/40 p-3 font-mono text-xs">{logTarget?.install_log || 'No output recorded.'}</pre>
		<Dialog.Footer>
			<Button variant="outline" size="sm" onclick={copyLog} disabled={!logTarget?.install_log}>
				<Copy class="size-4" /> Copy
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>

<!-- Tool details -->
<Dialog.Root bind:open={detailOpen}>
	<Dialog.Content class="max-h-[90vh] max-w-lg overflow-y-auto">
		{#if detailTool}
			<Dialog.Header>
				<Dialog.Title class="flex flex-wrap items-center gap-3">
					<ToolIcon tool={detailTool} size="lg" />
					{detailTool.name}
					{#if detailInstall}
						<StatusBadge status={detailInstall.status} tones={analyzerTones} />
					{/if}
				</Dialog.Title>
				<Dialog.Description>{detailTool.description}</Dialog.Description>
			</Dialog.Header>

			<dl class="mt-2 grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 border-t pt-4 text-sm">
				<dt class="text-muted-foreground">Category</dt>
				<dd>{categoryLabels[detailTool.category] ?? detailTool.category}</dd>
				<dt class="text-muted-foreground">Language</dt>
				<dd>{detailTool.kind === 'ai' ? 'Any (AI review)' : languageOf(detailTool)}</dd>
				<dt class="text-muted-foreground">Output</dt>
				<dd>{outputKindLabels[detailTool.output_kind ?? 'findings'] ?? detailTool.output_kind}</dd>
				<dt class="text-muted-foreground">Catalog version</dt>
				<dd>{detailTool.version ? `v${detailTool.version}` : '—'}</dd>
				{#if detailInstall}
					<dt class="text-muted-foreground">Installed</dt>
					<dd>
						<span class="font-mono">{versionLabel(detailInstall) || '—'}</span>
						{#if detailInstall.update_available}
							<span class="text-info-fg"> · update to v{detailInstall.latest_version}</span>
						{/if}
					</dd>
				{/if}
				<dt class="text-muted-foreground">Run timeout</dt>
				<dd>{detailTool.run_timeout}s</dd>
				{#if detailTool.docs_url}
					<dt class="text-muted-foreground">Docs</dt>
					<dd>
						<a
							href={detailTool.docs_url}
							target="_blank"
							rel="noopener noreferrer"
							class="inline-flex items-center gap-0.5 text-primary hover:underline"
						>
							{new URL(detailTool.docs_url).host} <ExternalLink class="size-3" />
						</a>
					</dd>
				{/if}
			</dl>

			{#if detailTool.config_schema.length > 0}
				<div class="space-y-2">
					<p class="section-label">Options</p>
					<ul class="space-y-1.5 text-sm">
						{#each detailTool.config_schema as field (field.name)}
							<li>
								<span class="font-medium">{field.label}</span>
								{#if field.description}<span class="text-muted-foreground"> — {field.description}</span>{/if}
							</li>
						{/each}
					</ul>
				</div>
			{/if}

			<Dialog.Footer class="gap-2">
				{#if detailInstall}
					<Button
						variant="ghost"
						size="sm"
						disabled={isWorking(detailInstall) || busy[detailTool.slug]}
						onclick={() => {
							detailOpen = false;
							confirmUninstall(detailTool.slug, detailTool.name);
						}}
					>
						<Trash2 class="size-4" /> Uninstall
					</Button>
					{#if detailInstall.install_log}
						<Button
							variant="ghost"
							size="sm"
							onclick={() => {
								detailOpen = false;
								openLog(detailInstall);
							}}
						>
							<ScrollText class="size-4" /> Log
						</Button>
					{/if}
				{/if}
				{@render toolActions(detailTool, detailInstall ?? undefined)}
			</Dialog.Footer>
		{/if}
	</Dialog.Content>
</Dialog.Root>

<ConfirmDialog
	bind:open={confirmState.open}
	title={confirmState.title}
	description={confirmState.description}
	confirmLabel={confirmState.confirmLabel}
	destructive
	busy={confirmState.busy}
	onConfirm={runConfirm}
/>
