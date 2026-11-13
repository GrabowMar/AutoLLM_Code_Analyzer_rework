<script lang="ts">
	// Reports list. Same layout as the other list pages: header stats, a toolbar (search + Type / Status
	// menus with counts) on top of one table card. The list is small, so it is fetched once (up to 200)
	// and filtered / sorted client-side; the counts come from the same list.
	import { formatDate, formatDateTime } from '$lib/utils/formatters';
	import { timeAgo } from '$lib/utils/dashboard';
	import { expiryLabel, headline, reportAccession } from '$lib/components/reports/format';
	import { REPORT_GROUPS, REPORT_TYPES, REPORT_TYPE_INFO } from '$lib/components/reports/catalog';
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { DropdownMenu } from 'bits-ui';
	import FilterMenu, { type FilterMenuOption } from '$lib/components/FilterMenu.svelte';
	import { SortableTh, TableToolbar, rowLink, tableFrame } from '$lib/components/table';
	import Plus from '@lucide/svelte/icons/plus';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import Brain from '@lucide/svelte/icons/brain';
	import Layers from '@lucide/svelte/icons/layers';
	import Search from '@lucide/svelte/icons/search';
	import X from '@lucide/svelte/icons/x';
	import Flag from '@lucide/svelte/icons/flag';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import FileText from '@lucide/svelte/icons/file-text';
	import { toast } from 'svelte-sonner';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { downloadExport } from '$lib/api/export';
	import Download from '@lucide/svelte/icons/download';
	import { getReports, deleteReport, type ReportSummary, type ReportStatus, type ReportType } from '$lib/api/client';

	let all = $state<ReportSummary[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let error = $state('');
	let typeFilter = $state('');
	let statusFilter = $state('');
	let searchQuery = $state('');
	let sortBy = $state<'created' | 'title'>('created');
	let sortDir = $state<'asc' | 'desc'>('desc');

	const typeConfig = REPORT_TYPE_INFO;
	const STATUSES: ReportStatus[] = ['pending', 'generating', 'completed', 'failed'];

	async function load() {
		loading = all.length === 0;
		error = '';
		try {
			const res = await getReports({ limit: 200 });
			all = res.reports;
			total = res.pagination.total;
		} catch (e) {
			error = (e as Error)?.message || 'Failed to load reports';
		} finally {
			loading = false;
		}
	}

	onMount(load);

	// ── Filtering, counts, sorting (client-side) ────────────────────────────────
	const matchesSearch = (r: ReportSummary) => {
		const q = searchQuery.trim().toLowerCase();
		return !q || r.title.toLowerCase().includes(q) || (r.description ?? '').toLowerCase().includes(q);
	};
	// Each facet counts with every other filter applied (not its own).
	const count = (rows: ReportSummary[], key: 'report_type' | 'status') => {
		const m = new Map<string, number>();
		for (const r of rows) m.set(r[key], (m.get(r[key]) ?? 0) + 1);
		return m;
	};
	const typeOptions = $derived.by<FilterMenuOption[]>(() => {
		const c = count(all.filter((r) => matchesSearch(r) && (!statusFilter || r.status === statusFilter)), 'report_type');
		return (Object.keys(typeConfig) as ReportType[])
			.filter((t) => c.has(t) || t === typeFilter)
			.map((t) => ({ value: t, label: typeConfig[t].label, icon: typeConfig[t].icon, hint: String(c.get(t) ?? 0) }));
	});
	const statusOptions = $derived.by<FilterMenuOption[]>(() => {
		const c = count(all.filter((r) => matchesSearch(r) && (!typeFilter || r.report_type === typeFilter)), 'status');
		return STATUSES.map((s) => ({ value: s, label: s.charAt(0).toUpperCase() + s.slice(1), hint: String(c.get(s) ?? 0) }));
	});
	const reports = $derived.by(() => {
		const rows = all.filter((r) => matchesSearch(r) && (!typeFilter || r.report_type === typeFilter) && (!statusFilter || r.status === statusFilter));
		const dir = sortDir === 'asc' ? 1 : -1;
		return [...rows].sort((a, b) =>
			sortBy === 'title' ? dir * a.title.localeCompare(b.title) : dir * (new Date(a.created_at).getTime() - new Date(b.created_at).getTime()),
		);
	});
	const activeCount = $derived([typeFilter, statusFilter].filter(Boolean).length);
	const filtered = $derived(activeCount > 0 || !!searchQuery.trim());
	const completed = $derived(all.filter((r) => r.status === 'completed').length);
	const inProgress = $derived(all.filter((r) => r.status === 'pending' || r.status === 'generating').length);
	const failed = $derived(all.filter((r) => r.status === 'failed').length);

	function onSort(field: string) {
		if (sortBy === field) sortDir = sortDir === 'asc' ? 'desc' : 'asc';
		else {
			sortBy = field as 'created' | 'title';
			sortDir = field === 'title' ? 'asc' : 'desc';
		}
	}

	// Delete is gated behind a confirmation dialog.
	let confirmDeleteId = $state<string | null>(null);
	let confirmOpen = $state(false);
	let confirmBusy = $state(false);

	function remove(id: string) {
		confirmDeleteId = id;
		confirmOpen = true;
	}

	async function confirmDelete() {
		if (!confirmDeleteId) return;
		confirmBusy = true;
		try {
			await deleteReport(confirmDeleteId);
			confirmOpen = false;
			await load();
		} catch (e) {
			toast.error((e as Error)?.message || 'Failed to delete report');
		} finally {
			confirmBusy = false;
		}
	}

	const menuItem =
		'flex cursor-pointer select-none items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-surface-2';
</script>

<div class="space-y-4">
	<PageHeader title="Reports" description="Generated insights across models, experiments, tasks and analysis tools.">
		{#snippet actions()}
			<Button variant="outline" size="sm" onclick={load} aria-label="Refresh" title="Refresh">
				<RefreshCw class="h-3.5 w-3.5" />
			</Button>
			<DropdownMenu.Root>
				<DropdownMenu.Trigger>
					{#snippet child({ props })}
						<Button {...props} variant="outline" size="sm">
							<Download class="h-3.5 w-3.5" />Export<ChevronDown class="h-3 w-3 opacity-60" />
						</Button>
					{/snippet}
				</DropdownMenu.Trigger>
				<DropdownMenu.Portal>
					<DropdownMenu.Content align="end" sideOffset={6} class="z-50 min-w-40 rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md">
						<DropdownMenu.Item class={menuItem} onSelect={() => downloadExport('reports.csv')}>Reports CSV</DropdownMenu.Item>
						<DropdownMenu.Item class={menuItem} onSelect={() => downloadExport('reports.json')}>Reports JSON</DropdownMenu.Item>
					</DropdownMenu.Content>
				</DropdownMenu.Portal>
			</DropdownMenu.Root>
			<Button size="sm" href="/reports/create"><Plus class="h-3.5 w-3.5" />New report</Button>
		{/snippet}
		<div class="header-stats">
			{#if loading}
				<span class="header-stat text-muted-foreground">loading…</span>
			{:else if !error}
				<span class="header-stat"><b>{total}</b> reports</span>
				<span class="header-stat"><b>{completed}</b> completed</span>
				{#if inProgress}<span class="header-stat"><b>{inProgress}</b> in progress</span>{/if}
				{#if failed}<span class="header-stat"><b>{failed}</b> failed</span>{/if}
			{/if}
		</div>
	</PageHeader>

	<Card.Root class="gap-0 overflow-clip py-0">
		<div class="tb-bar" role="search" aria-label="Search and filter reports">
			<div class="tb-search">
				<Search class="tb-search-icon" aria-hidden="true" />
				<input type="search" bind:value={searchQuery} placeholder="Search title or description…" aria-label="Search reports" autocomplete="off" />
			</div>
			<div class="tb-menus">
				<FilterMenu label="Type" icon={Layers} value={typeFilter} options={typeOptions} onChange={(v) => (typeFilter = v)} anyLabel="Any type" />
				<FilterMenu label="Status" icon={Flag} value={statusFilter} options={statusOptions} onChange={(v) => (statusFilter = v)} anyLabel="Any status" />
				{#if activeCount > 0}
					<button class="inline-flex h-9 items-center gap-1 px-2 text-xs text-muted-foreground hover:text-foreground" onclick={() => { typeFilter = ''; statusFilter = ''; }}>
						<X class="size-3" aria-hidden="true" />Clear{activeCount > 1 ? ` ${activeCount}` : ''}
					</button>
				{/if}
			</div>
		</div>

		{#if loading}
			<!-- Skeleton rows: the table's shape while the list loads. -->
			<div class="divide-y divide-border/60" aria-busy="true" aria-label="Loading reports">
				{#each Array(4) as _, i (i)}
					<div class="flex items-center gap-4 px-4 py-3">
						<span class="skel h-3.5 w-64"></span><span class="skel ml-auto h-3 w-24"></span><span class="skel h-5 w-20"></span><span class="skel h-3 w-28"></span>
					</div>
				{/each}
			</div>
		{:else if error}
			<div class="p-3"><ErrorAlert message={error} onretry={load} /></div>
		{:else if reports.length === 0}
			<EmptyState
				compact
				icon={FileText}
				title={filtered ? 'No report matches' : 'No reports yet'}
				description={filtered ? 'Change or clear the filters.' : 'Generate your first report to get insights across models, experiments, tasks and tools.'}
			>
				{#snippet action()}
					{#if !filtered}<Button size="sm" href="/reports/create"><Plus class="h-3.5 w-3.5" />Generate your first report</Button>{/if}
				{/snippet}
			</EmptyState>
			{#if !filtered}
				<div class="grid gap-px border-t border-border bg-border sm:grid-cols-2 xl:grid-cols-4">
					{#each REPORT_TYPES.filter((t) => t.group === REPORT_GROUPS[0].key).concat(REPORT_TYPES.filter((t) => t.key === 'generation_analytics')) as t (t.key)}
						{@const Icon = t.icon}
						<a href="/reports/create?type={t.key}" class="group flex gap-3 bg-card p-4 transition-colors hover:bg-muted/40">
							<Icon class="mt-0.5 size-4 shrink-0 text-muted-foreground group-hover:text-primary" aria-hidden="true" />
							<span class="min-w-0 space-y-0.5">
								<span class="block text-sm font-medium group-hover:text-primary">{t.label}</span>
								<span class="line-clamp-2 block text-xs text-muted-foreground">{t.purpose}</span>
							</span>
						</a>
					{/each}
				</div>
			{/if}
		{:else}
			<TableToolbar>
				{#snippet summary()}{reports.length} of {total} reports{/snippet}
			</TableToolbar>
			<div class="table-frame" {@attach tableFrame}>
				<table>
					<thead>
						<tr>
							<SortableTh label="Report" field="title" {sortBy} {sortDir} {onSort} />
							<th class="max-md:hidden">Type</th>
							<th class="max-lg:hidden">Headline</th>
							<th>Status</th>
							<SortableTh label="Created" field="created" {sortBy} {sortDir} {onSort} class="max-md:hidden" />
							<th><span class="sr-only">Actions</span></th>
						</tr>
					</thead>
					<tbody>
						{#each reports as r (r.report_id)}
							{@const cfg = typeConfig[r.report_type]}
							{@const TypeIcon = cfg?.icon ?? Brain}
							{@const href = `/reports/${r.report_id}`}
							{@const head = r.status === 'completed' ? headline(r.summary) : null}
							<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (the title link is the keyboard path) -->
							<tr class="row-link" onclick={rowLink(href)}>
								<td class="max-w-0 w-[40%] max-md:w-full max-md:max-w-none">
									<a {href} class="block font-medium hover:text-primary md:truncate" title={r.title}>{r.title}</a>
									<span class="mt-0.5 flex flex-wrap items-center gap-x-1.5 font-mono text-[11px] text-muted-foreground">
										<span class="whitespace-nowrap" title={r.report_id}>{reportAccession(r.report_id)}</span>
										<span class="whitespace-nowrap font-sans md:hidden">· {cfg?.label ?? r.report_type}</span>
									</span>
									{#if r.status === 'failed' && r.error_message}
										<span class="block truncate text-xs text-danger-fg" title={r.error_message}>{r.error_message}</span>
									{:else if r.description}
										<span class="block truncate text-xs text-muted-foreground" title={r.description}>{r.description}</span>
									{/if}
								</td>
								<td class="whitespace-nowrap max-md:hidden">
									<span class="inline-flex items-center gap-1.5 text-xs text-muted-foreground"><TypeIcon class="size-3.5" aria-hidden="true" />{cfg?.label ?? r.report_type}</span>
								</td>
								<td class="max-w-56 max-lg:hidden">
									{#if head}
										<span class="block truncate text-sm" title="{head.label}: {head.value}">
											<span class="text-xs text-muted-foreground">{head.label}</span>
											<span class="font-mono text-[13px] tabular-nums">{head.value}</span>
										</span>
									{:else}
										<span class="text-muted-foreground">—</span>
									{/if}
								</td>
								<td class="whitespace-nowrap">
									<StatusBadge status={r.status} />
									{#if r.status === 'generating'}
										<span class="ml-2 inline-flex items-center gap-1.5 align-middle" title="{r.progress_percent}%">
											<span class="block h-1 w-14 overflow-hidden rounded-full bg-muted"><span class="block h-full bg-primary" style:width="{r.progress_percent}%"></span></span>
											<span class="font-mono text-[11px] text-muted-foreground">{r.progress_percent}%</span>
										</span>
									{/if}
								</td>
								<td class="whitespace-nowrap max-md:hidden" title={formatDateTime(r.created_at)}>
									<span class="block text-sm">{timeAgo(r.created_at)}</span>
									{#if expiryLabel(r.expires_at)}<span class="cell-sub block text-[11px] text-muted-foreground">{formatDate(r.created_at)} · {expiryLabel(r.expires_at)}</span>{/if}
								</td>
								<td class="text-right">
									<Button size="sm" variant="ghost" class="size-8 p-0" aria-label="Delete report {r.title}" title="Delete" onclick={(e: MouseEvent) => { e.stopPropagation(); remove(r.report_id); }}>
										<Trash2 class="h-3.5 w-3.5" />
									</Button>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</Card.Root>
</div>

<ConfirmDialog
	bind:open={confirmOpen}
	title="Delete report?"
	description="This permanently removes the report. This cannot be undone."
	confirmLabel="Delete"
	destructive
	busy={confirmBusy}
	onConfirm={confirmDelete}
/>

<style>
	.skel {
		display: block;
		border-radius: 0.25rem;
		background: color-mix(in oklab, var(--muted-foreground) 18%, transparent);
		animation: skel-pulse 1.4s ease-in-out infinite;
	}
	@keyframes skel-pulse {
		50% {
			opacity: 0.45;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.skel {
			animation: none;
		}
	}
</style>
