<script lang="ts">
	import { tableFrame } from '$lib/components/table';
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
	import Globe from '@lucide/svelte/icons/globe';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import ToolConfigDialog from '$lib/components/analyzers/ToolConfigDialog.svelte';
	import ToolIcon from '$lib/components/analyzers/ToolIcon.svelte';
	import CoveragePanel from '$lib/components/analyzers/CoveragePanel.svelte';
	import { formatDateTime } from '$lib/utils/formatters';
	import type { Tone } from '$lib/constants/colors';
	import {
		getToolCatalog,
		getCatalogMeta,
		getAppCoverage,
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
		type CatalogMeta,
		type AppCoverage,
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

	// Fallback labels and order when the catalog vocabulary can't be loaded.
	const fallbackCategoryLabels: Record<string, string> = {
		security: 'Security',
		dependencies: 'Dependencies',
		lint: 'Lint & quality',
		performance: 'Performance',
		secrets: 'Secrets',
		ai: 'AI review',
	};

	// Shop vocabulary (categories, modes, technologies) from data/tools/catalog.yaml.
	let meta = $state<CatalogMeta | null>(null);

	// Category labels in shop order: the catalog's, else the fallback.
	const categoryLabels = $derived<Record<string, string>>(
		meta?.categories.length ? Object.fromEntries(meta.categories.map((c) => [c.slug, c.label])) : fallbackCategoryLabels
	);
	const techLabels = $derived(new Map((meta?.technologies ?? []).map((t) => [t.slug, t.label])));

	function techLabel(slug: string): string {
		return techLabels.get(slug) ?? slug;
	}

	// What the user's generated apps contain; null until loaded (or if it failed).
	let coverage = $state<AppCoverage | null>(null);
	const appsTotal = $derived(coverage?.apps_total ?? 0);

	// Tools that scan *slug* specifically; for a technology no tool targets
	// (e.g. Vue), the language-agnostic tools are the only ones that read it.
	function toolsFor(slug: string): AnalyzerTool[] {
		const specific = catalog.filter((t) => t.technologies?.includes(slug));
		return specific.length > 0 ? specific : catalog.filter((t) => t.technologies?.includes('generic'));
	}

	// Technology filter: every technology some tool scans or the user's apps contain, by group.
	const techItems = $derived([
		{ value: '', label: 'All technologies' },
		...(meta?.technology_groups ?? []).flatMap((g) =>
			(meta?.technologies ?? [])
				.filter((t) => t.group === g.slug)
				.map((t) => ({ t, count: catalog.filter((tool) => tool.technologies?.includes(t.slug)).length }))
				.filter(({ t, count }) => count > 0 || (coverage?.technologies[t.slug] ?? 0) > 0)
				.map(({ t, count }) => ({ value: t.slug, label: count > 0 ? `${t.label} (${count})` : `${t.label} (no specific tool)` }))
		),
	]);


	/** Apps (of `appsTotal`) containing something *tool* scans; null when unknown. */
	function fitOf(tool: AnalyzerTool): number | null {
		if (!coverage || appsTotal === 0) return null;
		return coverage.tools[tool.slug] ?? 0;
	}


	const modeDescriptions = $derived(new Map((meta?.modes ?? []).map((m) => [m.slug, m.description])));

	function isDynamic(tool: AnalyzerTool): boolean {
		return tool.analysis_mode === 'dynamic';
	}

	// Reports keep AI-reviewer findings apart from measured ones.
	const aiNote = 'An LLM\'s opinion, not a measurement: reports and rankings list its findings separately from measured ones.';

	const outputKindLabels: Record<string, string> = {
		findings: 'Findings',
		metrics: 'Metrics',
		mixed: 'Findings + metrics',
	};

	// Generic confirmation dialog: uninstall / teardown, and version updates.
	let confirmState = $state<{
		open: boolean;
		title: string;
		description: string;
		confirmLabel: string;
		destructive: boolean;
		busy: boolean;
		action: () => Promise<void>;
	}>({ open: false, title: '', description: '', confirmLabel: 'Confirm', destructive: true, busy: false, action: async () => {} });

	function askConfirm(opts: {
		title: string;
		description: string;
		confirmLabel: string;
		destructive?: boolean;
		action: () => Promise<void>;
	}) {
		confirmState = { ...confirmState, destructive: true, ...opts, open: true, busy: false };
	}

	// A new tool version can change what it reports, so findings from runs
	// before and after the update are not directly comparable (each result
	// records the tool version it ran with).
	const comparabilityNote =
		'A new version can find more, fewer or different issues. Runs analyzed before and after the update are not directly comparable, so finish a study on one version first. Each result records the tool version it ran with.';

	function confirmUpdate(it: InstalledTool) {
		if (!it.update_available) {
			void handleUpdate(it);
			return;
		}
		askConfirm({
			title: `Update ${it.tool_name} to v${it.latest_version}?`,
			description: comparabilityNote,
			confirmLabel: 'Update',
			destructive: false,
			action: () => handleUpdate(it),
		});
	}

	function confirmUpdateAll() {
		askConfirm({
			title: `Update ${outdated.length} tool${outdated.length === 1 ? '' : 's'}?`,
			description: `${outdated.map((it) => it.tool_name).join(', ')}. ${comparabilityNote}`,
			confirmLabel: 'Update all',
			destructive: false,
			action: () => handleUpdateAll(true),
		});
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
	type ModeFilter = 'all' | 'static' | 'dynamic';
	type FitFilter = 'all' | 'fits';

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
	let modeFilter = $state<ModeFilter>('all');
	let techFilter = $state('');
	let fitFilter = $state<FitFilter>('all');
	const techMatches = $derived(new Set(techFilter ? toolsFor(techFilter).map((t) => t.slug) : []));
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
	const readySlugs = $derived(new Set(installed.filter((it) => it.status === 'installed').map((it) => it.tool_slug)));
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

	const dynamicCount = $derived(catalog.filter(isDynamic).length);
	const modeItems = $derived([
		{ value: 'all', label: 'All' },
		{ value: 'static', label: 'Static', title: modeDescriptions.get('static') },
		{ value: 'dynamic', label: `Dynamic (${dynamicCount})`, title: modeDescriptions.get('dynamic') },
	]);

	const fittingCount = $derived(catalog.filter((t) => (fitOf(t) ?? 0) > 0).length);
	const fitItems = $derived([
		{ value: 'all', label: 'All tools' },
		{
			value: 'fits',
			label: `Fits my apps (${fittingCount})`,
			title: 'Tools that scan something your generated apps contain',
		},
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
				(modeFilter === 'all' || (t.analysis_mode ?? 'static') === modeFilter) &&
				(!techFilter || techMatches.has(t.slug)) &&
				(fitFilter === 'all' || (fitOf(t) ?? 1) > 0) &&
				(!q ||
					t.name.toLowerCase().includes(q) ||
					t.description.toLowerCase().includes(q) ||
					(t.technologies ?? []).some((slug) => techLabel(slug).toLowerCase().includes(q)))
			);
		});
		return matches
			.map((t, i) => ({ t, i }))
			.sort((a, b) => categoryRank(a.t.category) - categoryRank(b.t.category) || a.i - b.i)
			.map(({ t }) => t);
	});

	const installableVisible = $derived(visibleCatalog.filter((t) => !installedBySlug.has(t.slug)));

	function clearFilters() {
		searchQuery = '';
		categoryFilter = 'all';
		stateFilter = 'all';
		modeFilter = 'all';
		techFilter = '';
		fitFilter = 'all';
	}

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
			const [c, w, i, m] = await Promise.all([
				getToolCatalog(),
				getWorkspace(),
				getInstalledTools(),
				// Labels only: the shop still works with fallbacks if this fails.
				getCatalogMeta().catch(() => null),
			]);
			catalog = c;
			meta = m;
			// Optional context; the shop works without it.
			getAppCoverage()
				.then((cov) => (coverage = cov))
				.catch(() => (coverage = null));
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

{#snippet shopToolbar()}
	<!-- Filters, count and bulk install in one strip at the top of the catalogue. -->
	<div class="table-toolbar">
		<div class="flex min-w-0 flex-wrap items-center gap-2">
			{#if appsTotal > 0}
				<Segmented aria-label="Filter by fit to your apps" size="sm" value={fitFilter} onValueChange={(v) => (fitFilter = v as FitFilter)} items={fitItems} />
			{/if}
			<Segmented aria-label="Filter by install state" size="sm" value={stateFilter} onValueChange={(v) => (stateFilter = v as StateFilter)} items={stateItems} />
			{#if dynamicCount > 0}
				<Segmented aria-label="Filter by analysis mode" size="sm" value={modeFilter} onValueChange={(v) => (modeFilter = v as ModeFilter)} items={modeItems} />
			{/if}
			<Select.Simple aria-label="Filter by category" size="sm" class="w-44" bind:value={categoryFilter} items={categoryItems} />
			{#if techItems.length > 1}
				<Select.Simple aria-label="Filter by technology" size="sm" class="w-48" bind:value={techFilter} items={techItems} />
			{/if}
			<div class="relative w-full sm:w-56">
				<Search class="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
				<Input type="search" placeholder="Search tools…" aria-label="Search tools" class="h-8 pl-8 text-xs" bind:value={searchQuery} />
			</div>
		</div>
		<div class="flex items-center gap-3">
			<span class="tabular-nums">{visibleCatalog.length} shown · {installableVisible.length} not installed</span>
			{#if installableVisible.length > 1}
				<Button size="sm" variant="outline" class="h-7 text-xs" disabled={busy.bulk} onclick={handleInstallVisible}>
					{#if busy.bulk}<Loader class="size-3.5 animate-spin motion-reduce:animate-none" />{:else}<Download class="size-3.5" />{/if}
					Install {installableVisible.length} shown
				</Button>
			{/if}
		</div>
	</div>
{/snippet}

{#snippet fitCell(tool: AnalyzerTool)}
	{@const fit = fitOf(tool) ?? 0}
	{#if fit === 0}
		<span class="text-[11px] text-warning-fg" title="None of your generated apps contain anything this tool scans; it would report 0 findings">Fits none of your apps</span>
	{:else}
		<span class="flex items-center gap-2 text-[11px]" title="{fit} of {appsTotal} generated apps contain something this tool scans">
			<span class="h-1.5 w-14 overflow-hidden rounded-full bg-muted" aria-hidden="true">
				<span class="block h-full rounded-full bg-primary/70" style:width="{Math.round((fit / appsTotal) * 100)}%"></span>
			</span>
			<span class="tabular-nums text-muted-foreground">fits {fit}/{appsTotal} apps</span>
		</span>
	{/if}
{/snippet}

{#snippet techChips(tool: AnalyzerTool, max: number)}
	{@const techs = tool.technologies ?? []}
	<div class="flex flex-wrap gap-1">
		{#each techs.slice(0, max) as slug (slug)}
			<span class="rounded-full border border-border px-1.5 text-[11px] font-medium leading-4 text-muted-foreground">{techLabel(slug)}</span>
		{/each}
		{#if techs.length > max}
			<span class="text-[11px] leading-4 text-muted-foreground" title={techs.slice(max).map(techLabel).join(', ')}>+{techs.length - max}</span>
		{/if}
	</div>
{/snippet}

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
		<Button size="sm" disabled={busy[tool.slug]} onclick={() => confirmUpdate(it)}>
			{#if busy[tool.slug]}<Loader class="size-4 animate-spin motion-reduce:animate-none" />{:else}<RotateCcw class="size-4" />{/if}
			Retry
		</Button>
	{:else if it.update_available}
		<Button size="sm" variant="outline" onclick={() => openConfig(it)}>
			<Settings2 class="size-4" /> Configure
		</Button>
		<Button size="sm" disabled={busy[tool.slug]} onclick={() => confirmUpdate(it)}>
			{#if busy[tool.slug]}<Loader class="size-4 animate-spin motion-reduce:animate-none" />{:else}<ArrowUpCircle class="size-4" />{/if}
			Update
		</Button>
	{:else}
		<Button size="sm" variant="outline" onclick={() => openConfig(it)}>
			<Settings2 class="size-4" /> Configure
		</Button>
	{/if}
{/snippet}

<Tabs.Root bind:value={activeTab} class="space-y-4">
	<PageHeader
		title="Analyzers"
		description="Choose the tools that measure your generated apps. Their findings are what analysis runs, experiments, reports and rankings compare across models."
	>
		{#snippet actions()}
			<Button variant="outline" size="sm" onclick={loadAll} disabled={loading}>
				<RefreshCw class="size-4" /> Refresh
			</Button>
			{#if outdated.length > 0}
				<Button size="sm" disabled={busy['update-all']} onclick={confirmUpdateAll}>
					{#if busy['update-all']}<Loader class="size-4 animate-spin motion-reduce:animate-none" />{:else}<ArrowUpCircle class="size-4" />{/if}
					Update all ({outdated.length})
				</Button>
			{/if}
		{/snippet}
		{#snippet nav()}
			<Tabs.List aria-label="Analyzer sections">
				<Tabs.Trigger value="shop">Shop</Tabs.Trigger>
				<Tabs.Trigger value="installed">
					Installed ({installed.length}){#if outdated.length > 0}<span class="ml-1.5 text-info-fg">· {outdated.length} update{outdated.length === 1 ? '' : 's'}</span>{/if}
				</Tabs.Trigger>
				<Tabs.Trigger value="workspace">Workspace</Tabs.Trigger>
			</Tabs.List>
			{#if workspace && workspace.status !== 'ready' && workspace.status !== 'provisioning'}
				<span class="ml-auto flex items-center gap-1.5 px-2 text-xs text-muted-foreground">
					<Info class="size-3.5 shrink-0" />
					{workspace.status === 'absent' ? 'No analyzer workspace is running yet' : `Analyzer workspace ${workspace.status}`} — installing a container tool starts it.
				</span>
			{/if}
		{/snippet}
	</PageHeader>

	{#if loadError}<ErrorAlert message={loadError} onretry={loadAll} />{/if}

		{#if loading}
			<LoadingState label="loading analyzers" />
		{:else}
			<Tabs.Content value="shop" class="space-y-4">
				{#if coverage}
					<CoveragePanel
						{coverage}
						{meta}
						{catalog}
						installedSlugs={readySlugs}
						selected={techFilter}
						onselect={(slug) => (techFilter = slug)}
					/>
				{/if}
				{#if visibleCatalog.length === 0}
					<Card.Root class="gap-0 overflow-clip py-0">{@render shopToolbar()}</Card.Root>
					{#if catalog.length === 0}
						<EmptyState icon={Store} title="The tool catalog is empty" />
					{:else}
						<EmptyState icon={SearchX} title="No tools match your filters">
							{#snippet action()}
								<Button
									variant="outline"
									size="sm"
									onclick={clearFilters}
								>
									Clear filters
								</Button>
							{/snippet}
						</EmptyState>
					{/if}
				{/if}

				{#if visibleCatalog.length > 0}
					<!-- One row per tool: the catalogue is scanned and compared, not browsed. -->
					<Card.Root class="gap-0 overflow-clip py-0">
						{@render shopToolbar()}
						<div class="table-frame" {@attach tableFrame}>
							<table>
								<thead>
									<tr>
										<th>Tool</th>
										<th>Category</th>
										<th>Scans</th>
										<th>Status</th>
										<th class="text-right">Actions</th>
									</tr>
								</thead>
								<tbody>
									{#each visibleCatalog as tool (tool.slug)}
										{@const it = installedBySlug.get(tool.slug)}
										<tr>
											<td class="max-w-[30rem]">
												<div class="flex items-center gap-2.5">
													<ToolIcon {tool} size="sm" />
													<div class="min-w-0">
														<button
															class="font-medium hover:text-primary hover:underline"
															aria-label="{tool.name} details"
															onclick={() => openDetails(tool.slug)}
														>{tool.name}</button>
														{#if tool.version}<span class="ml-1 font-mono text-[11px] text-muted-foreground">v{tool.version}</span>{/if}
														{#if isDynamic(tool)}
															<Badge variant="outline" class="ml-1.5 gap-1 align-middle text-[11px]" title={modeDescriptions.get('dynamic') || 'Scans the running app'}>
																<Globe class="size-3" /> Dynamic
															</Badge>
														{/if}
														{#if tool.kind === 'ai'}
															<Badge variant="outline" class="ml-1.5 align-middle text-[11px]" title={aiNote}>Opinion</Badge>
														{/if}
														<p class="cell-sub truncate text-xs text-muted-foreground" title={tool.description}>{tool.description}</p>
													</div>
												</div>
											</td>
											<td class="whitespace-nowrap"><Badge variant="outline">{categoryLabels[tool.category] ?? tool.category}</Badge></td>
											<td class="min-w-36 max-w-56">
												{@render techChips(tool, 3)}
												{#if appsTotal > 0}<div class="mt-1">{@render fitCell(tool)}</div>{/if}
											</td>
											<td class="whitespace-nowrap">
												{#if it}
													<StatusBadge
														status={it.update_available ? 'update available' : it.status}
														tone={it.update_available ? 'info' : undefined}
														tones={analyzerTones}
													/>
												{:else}
													<span class="text-xs text-muted-foreground">Not installed</span>
												{/if}
											</td>
											<td>
												<div class="flex justify-end gap-1.5">
													{@render toolActions(tool, it)}
												</div>
											</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					</Card.Root>
				{/if}
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
							<Button size="sm" disabled={outdated.length === 0 || busy['update-all']} onclick={confirmUpdateAll}>
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
											onclick={() => confirmUpdate(it)}
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
				<dt class="text-muted-foreground">Mode</dt>
				<dd>
					{isDynamic(detailTool) ? 'Dynamic — scans the running app' : 'Static — reads the source'}
					{#if isDynamic(detailTool)}
						<p class="text-xs text-muted-foreground">
							Start the app's container before the analysis run; otherwise this tool is skipped. Experiment post-run
							analysis can't use it yet, since apps aren't kept running after evaluation.
						</p>
					{/if}
				</dd>
				<dt class="text-muted-foreground">Scans</dt>
				<dd>{@render techChips(detailTool, 99)}</dd>
				{#if appsTotal > 0}
					<dt class="text-muted-foreground">Fits your apps</dt>
					<dd>{@render fitCell(detailTool)}</dd>
				{/if}
				<dt class="text-muted-foreground">Output</dt>
				<dd>
					{outputKindLabels[detailTool.output_kind ?? 'findings'] ?? detailTool.output_kind}
					{#if detailTool.kind === 'ai'}<p class="text-xs text-muted-foreground">{aiNote}</p>{/if}
				</dd>
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
	destructive={confirmState.destructive}
	busy={confirmState.busy}
	onConfirm={runConfirm}
/>
