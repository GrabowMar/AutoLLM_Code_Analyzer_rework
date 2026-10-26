<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import FilterBar, { type FilterTag } from '$lib/components/FilterBar.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import NaValue from '$lib/components/analysis/NaValue.svelte';
	import { REPORT_TYPE_INFO, reportTypeInfo } from '$lib/components/insights/reports/report-types';
	import { fmtInt, fmtShare, formatTimestamp, shortHash } from '$lib/components/insights/format';
	import { toast } from 'svelte-sonner';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { exportUrl } from '$lib/api/export';
	import {
		REPORT_TYPES,
		deleteReport,
		getReports,
		isReportInFlight,
		type ReportGenerationStatus,
		type ReportListItem,
		type ReportType
	} from '$lib/api/reports';
	import Plus from '@lucide/svelte/icons/plus';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import Download from '@lucide/svelte/icons/download';
	import FileText from '@lucide/svelte/icons/file-text';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	const FETCH_LIMIT = 200;
	const PAGE_SIZE = 24;
	const POLL_MS = 3_000;
	const STATUSES: ReportGenerationStatus[] = ['pending', 'generating', 'completed', 'failed'];
	type SortKey = 'newest' | 'oldest' | 'title';

	const initial = page.url.searchParams;
	const initialType = initial.get('type') ?? '';
	const initialStatus = initial.get('status') ?? '';

	let typeFilter = $state<ReportType | ''>(
		(REPORT_TYPES as readonly string[]).includes(initialType) ? (initialType as ReportType) : ''
	);
	let statusFilter = $state<ReportGenerationStatus | ''>(
		(STATUSES as string[]).includes(initialStatus) ? (initialStatus as ReportGenerationStatus) : ''
	);
	let sortBy = $state<SortKey>('newest');
	let searchQuery = $state('');
	let pageNo = $state(1);

	let reports = $state<ReportListItem[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let error = $state<string | null>(null);

	let controller: AbortController | null = null;

	async function load(silent = false) {
		controller?.abort();
		const c = new AbortController();
		controller = c;
		if (!silent) loading = reports.length === 0;
		error = null;
		try {
			const res = await getReports(
				{
					report_type: typeFilter || undefined,
					status: statusFilter || undefined,
					limit: FETCH_LIMIT
				},
				{ signal: c.signal }
			);
			if (c.signal.aborted) return;
			reports = res.reports;
			total = res.pagination.total;
		} catch (e) {
			if (c.signal.aborted || isAbortError(e)) return;
			error = formatApiError(e, 'Could not load the reports.');
		} finally {
			if (controller === c) loading = false;
		}
	}

	// Server-side filters reload; search/sort/paging are client-side.
	$effect(() => {
		void [typeFilter, statusFilter];
		untrack(() => {
			pageNo = 1;
			load();
		});
	});

	// Keep the URL in sync with the server-side filters (shareable views).
	$effect(() => {
		const usp = new URLSearchParams();
		if (typeFilter) usp.set('type', typeFilter);
		if (statusFilter) usp.set('status', statusFilter);
		const next = usp.toString();
		untrack(() => {
			if (next === page.url.searchParams.toString()) return;
			const url = new URL(page.url);
			url.search = next;
			goto(url, { replaceState: true, keepFocus: true, noScroll: true });
		});
	});

	const anyInFlight = $derived(reports.some((r) => isReportInFlight(r.status)));
	$effect(() => {
		if (!anyInFlight) return;
		const timer = setInterval(() => {
			if (document.visibilityState === 'visible') load(true);
		}, POLL_MS);
		return () => clearInterval(timer);
	});

	onMount(() => () => controller?.abort());

	const visible = $derived.by(() => {
		const q = searchQuery.trim().toLowerCase();
		let items = q
			? reports.filter(
					(r) =>
						r.title.toLowerCase().includes(q) ||
						(r.description ?? '').toLowerCase().includes(q) ||
						r.report_id.toLowerCase().includes(q)
				)
			: reports;
		const time = (r: ReportListItem) => new Date(r.created_at).getTime();
		if (sortBy === 'newest') items = [...items].sort((a, b) => time(b) - time(a));
		else if (sortBy === 'oldest') items = [...items].sort((a, b) => time(a) - time(b));
		else items = [...items].sort((a, b) => a.title.localeCompare(b.title));
		return items;
	});
	const pages = $derived(Math.max(1, Math.ceil(visible.length / PAGE_SIZE)));
	const pageItems = $derived(visible.slice((pageNo - 1) * PAGE_SIZE, pageNo * PAGE_SIZE));
	$effect(() => {
		if (pageNo > pages) pageNo = pages;
	});

	const activeTags = $derived.by((): FilterTag[] => {
		const tags: FilterTag[] = [];
		if (typeFilter) {
			tags.push({
				key: 'type',
				label: `Type: ${reportTypeInfo(typeFilter).label}`,
				onRemove: () => (typeFilter = '')
			});
		}
		if (statusFilter) {
			tags.push({
				key: 'status',
				label: `Status: ${statusFilter}`,
				onRemove: () => (statusFilter = '')
			});
		}
		if (searchQuery.trim()) {
			tags.push({
				key: 'search',
				label: `“${searchQuery.trim()}”`,
				onRemove: () => (searchQuery = '')
			});
		}
		return tags;
	});

	const resultsText = $derived(
		loading && reports.length === 0
			? ''
			: `${visible.length} of ${total} report${total === 1 ? '' : 's'}${total > FETCH_LIMIT ? ` (newest ${FETCH_LIMIT} loaded)` : ''}`
	);

	const exportParams = $derived({
		report_type: typeFilter || undefined,
		status: statusFilter || undefined
	});

	function clearAll() {
		typeFilter = '';
		statusFilter = '';
		searchQuery = '';
		sortBy = 'newest';
	}

	// ── Delete ──
	let confirmTarget = $state<ReportListItem | null>(null);
	let confirmOpen = $state(false);
	let confirmBusy = $state(false);

	function askDelete(r: ReportListItem) {
		confirmTarget = r;
		confirmOpen = true;
	}

	async function confirmDelete() {
		if (!confirmTarget) return;
		confirmBusy = true;
		try {
			await deleteReport(confirmTarget.report_id);
			reports = reports.filter((r) => r.report_id !== confirmTarget?.report_id);
			total = Math.max(0, total - 1);
			confirmOpen = false;
			toast.success('Report deleted');
		} catch (e) {
			toast.error(formatApiError(e, 'Could not delete the report.'));
		} finally {
			confirmBusy = false;
		}
	}

	const typeItems = REPORT_TYPES.map((t) => ({ value: t, label: REPORT_TYPE_INFO[t].label }));
	const statusItems = STATUSES.map((s) => ({
		value: s,
		label: s.charAt(0).toUpperCase() + s.slice(1)
	}));
	const sortItems = [
		{ value: 'newest', label: 'Newest first' },
		{ value: 'oldest', label: 'Oldest first' },
		{ value: 'title', label: 'Title A–Z' }
	];
</script>

{#snippet summaryChips(r: ReportListItem)}
	{@const s = r.summary ?? {}}
	{@const info = reportTypeInfo(r.report_type)}
	<div class="flex flex-wrap items-center gap-1.5 text-[11px]">
		{#if r.report_type === 'study_report'}
			{@const apps = s.static_measured_apps as number | null | undefined}
			{@const unique = s.unique_issues as number | null | undefined}
			{@const warnings = s.warnings as number | undefined}
			<span class="mono-tag">
				{#if apps != null}{apps} statically measured app{apps === 1 ? '' : 's'}{:else}<NaValue
						reason="Not in the summary"
					/>&nbsp;measured{/if}
			</span>
			<span class="mono-tag">
				{#if unique != null}{fmtInt(unique)} unique issues{:else}<NaValue
						reason="No app has a static measurement"
					/>&nbsp;unique issues{/if}
			</span>
			{#if s.plan_hash}
				<span class="mono-tag" title="Analysis plan {s.plan_hash}"
					>plan {shortHash(s.plan_hash, 8)}</span
				>
			{/if}
			{#if warnings}
				<span class="inline-flex items-center gap-1 text-warning-fg">
					<TriangleAlert class="h-3 w-3" aria-hidden="true" />{warnings} validity warning{warnings ===
					1
						? ''
						: 's'}
				</span>
			{/if}
		{:else if info.measured}
			<span class="mono-tag">
				{#if s.measured_jobs != null}{s.measured_jobs} measured job{s.measured_jobs === 1
						? ''
						: 's'}{:else}<NaValue reason="Not in the summary" />&nbsp;measured{/if}
			</span>
			{#if r.report_type === 'model_analysis'}
				<span class="mono-tag">
					{#if s.total_findings != null}{fmtInt(s.total_findings)} static findings{:else}<NaValue
							reason="No usable static measurement"
						/>&nbsp;static findings{/if}
				</span>
			{:else if r.report_type === 'experiment_report' && s.condition_count != null}
				<span class="mono-tag">{s.condition_count} conditions</span>
			{:else if r.report_type === 'template_comparison' && s.model_count != null}
				<span class="mono-tag">{s.model_count} models</span>
			{/if}
			{#if s.plan_hash}
				<span class="mono-tag" title="Analysis plan {s.plan_hash}"
					>plan {shortHash(s.plan_hash, 8)}</span
				>
			{/if}
			{#if s.mixed_plans}
				<span
					class="inline-flex items-center gap-1 text-warning-fg"
					title="Jobs were analysed under several plans; only one plan is compared"
				>
					<TriangleAlert class="h-3 w-3" aria-hidden="true" />mixed plans
				</span>
			{/if}
		{:else if r.report_type === 'tool_analysis'}
			{#if s.tool_count != null}<span class="mono-tag">{s.tool_count} tools</span>{/if}
			{#if s.total_executions != null}<span class="mono-tag">{s.total_executions} executions</span
				>{/if}
		{:else if r.report_type === 'generation_analytics'}
			{#if s.window_days != null}<span class="mono-tag">{s.window_days} days</span>{/if}
			{#if s.total_jobs != null}<span class="mono-tag">{s.total_jobs} jobs</span>{/if}
			{#if s.success_rate !== undefined}
				<span class="mono-tag"
					>{#if s.success_rate != null}{fmtShare(s.success_rate, 0)}{:else}<NaValue
							reason="No job has finished"
						/>{/if}&nbsp;success</span
				>
			{/if}
		{:else if r.report_type === 'comprehensive' && s.platform}
			<span class="mono-tag">{s.platform.measured_jobs ?? '—'} measured jobs</span>
			<span class="mono-tag">{s.platform.plans_in_use ?? '—'} plans</span>
		{/if}
	</div>
{/snippet}

<div class="space-y-6">
	<PageHeader
		title="Reports"
		description="Generated reports across models, templates, experiments and analysis tools. Analysis numbers come from one analysis plan per report."
	>
		{#snippet actions()}
			<Button
				variant="outline"
				size="sm"
				onclick={() => load(true)}
				aria-label="Refresh"
				title="Refresh"
			>
				<RefreshCw class="h-3.5 w-3.5" /><span class="hidden lg:inline">Refresh</span>
			</Button>
			<Button variant="outline" size="sm" href={exportUrl('reports.csv', exportParams)} download>
				<Download class="h-3.5 w-3.5" />CSV
			</Button>
			<Button variant="outline" size="sm" href={exportUrl('reports.json', exportParams)} download>
				<Download class="h-3.5 w-3.5" />JSON
			</Button>
			<Button size="sm" href="/reports/create">
				<Plus class="h-3.5 w-3.5" />New report
			</Button>
		{/snippet}
	</PageHeader>

	<FilterBar
		searchPlaceholder="Search reports by title…"
		bind:searchValue={searchQuery}
		onSearchInput={() => (pageNo = 1)}
		{activeTags}
		{resultsText}
		page={pages > 1 ? pageNo : undefined}
		pages={pages > 1 ? pages : undefined}
		onGoToPage={(p) => (pageNo = p)}
		onClearAll={clearAll}
	>
		{#snippet filters()}
			<div class="fb-group">
				<span class="fb-group-label">Type</span>
				<Select.Simple
					size="sm"
					class="w-56"
					aria-label="Filter by report type"
					value={typeFilter}
					items={typeItems}
					placeholder="All types"
					clearLabel="All types"
					onValueChange={(v) => (typeFilter = v as ReportType | '')}
				/>
			</div>
			<div class="fb-group">
				<span class="fb-group-label">Status</span>
				<Select.Simple
					size="sm"
					class="w-44"
					aria-label="Filter by status"
					value={statusFilter}
					items={statusItems}
					placeholder="All statuses"
					clearLabel="All statuses"
					onValueChange={(v) => (statusFilter = v as ReportGenerationStatus | '')}
				/>
			</div>
			<div class="fb-group">
				<span class="fb-group-label">Sort</span>
				<Select.Simple
					size="sm"
					class="w-44"
					aria-label="Sort reports"
					value={sortBy}
					items={sortItems}
					onValueChange={(v) => (sortBy = v as SortKey)}
				/>
			</div>
		{/snippet}
	</FilterBar>

	{#if error}
		<ErrorAlert message={error} onretry={() => load()} />
	{/if}

	{#if loading && reports.length === 0}
		<LoadingState label="loading reports" />
	{:else if !error && visible.length === 0}
		{#if reports.length === 0 && !typeFilter && !statusFilter}
			<EmptyState
				icon={FileText}
				title="No reports yet"
				description="Generate a report to summarise models, templates, experiments or tool behaviour."
			>
				{#snippet action()}
					<Button size="sm" href="/reports/create"><Plus class="h-3.5 w-3.5" />New report</Button>
				{/snippet}
			</EmptyState>
		{:else}
			<EmptyState
				icon={FileText}
				title="No reports match"
				description="No report matches the current filters."
			>
				{#snippet action()}
					<Button variant="outline" size="sm" onclick={clearAll}>Clear filters</Button>
				{/snippet}
			</EmptyState>
		{/if}
	{:else}
		<ul class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
			{#each pageItems as r (r.report_id)}
				{@const info = reportTypeInfo(r.report_type)}
				{@const TypeIcon = info.icon}
				<li>
					<Card.Root class="h-full gap-3">
						<Card.Header class="gap-2 pb-0">
							<div class="flex items-start justify-between gap-2">
								<Badge variant="outline" class="gap-1 text-[11px]">
									<TypeIcon class="h-3 w-3" aria-hidden="true" />{info.label}
								</Badge>
								<StatusBadge status={r.status} />
							</div>
							<Card.Title class="text-base">
								<a href="/reports/{r.report_id}" class="line-clamp-2 hover:underline">{r.title}</a>
							</Card.Title>
							{#if r.description}
								<Card.Description class="line-clamp-2">{r.description}</Card.Description>
							{/if}
						</Card.Header>
						<Card.Content class="flex-1 space-y-2 text-xs text-muted-foreground">
							<div>Created {formatTimestamp(r.created_at)}</div>
							{#if isReportInFlight(r.status)}
								<div class="space-y-1">
									<div class="flex justify-between">
										<span>{r.status === 'pending' ? 'Queued' : 'Generating'}…</span>
										<span class="font-mono tabular-nums">{r.progress_percent}%</span>
									</div>
									<div
										class="h-1.5 w-full overflow-hidden rounded-full bg-muted"
										role="progressbar"
										aria-valuemin={0}
										aria-valuemax={100}
										aria-valuenow={r.progress_percent}
										aria-label="Generation progress of {r.title}"
									>
										<div
											class="h-full rounded-full bg-primary transition-[width] duration-300"
											style="width: {r.progress_percent}%"
										></div>
									</div>
								</div>
							{:else if r.status === 'failed'}
								<p class="line-clamp-2 text-danger-fg" title={r.error_message}>
									{r.error_message || 'Generation failed.'}
								</p>
							{:else if r.status === 'completed'}
								{@render summaryChips(r)}
							{/if}
						</Card.Content>
						<Card.Footer class="gap-2">
							<Button size="sm" variant="outline" class="flex-1" href="/reports/{r.report_id}"
								>Open</Button
							>
							<Button
								size="sm"
								variant="outline"
								aria-label="Delete report {r.title}"
								onclick={() => askDelete(r)}
							>
								<Trash2 class="h-3.5 w-3.5" />
							</Button>
						</Card.Footer>
					</Card.Root>
				</li>
			{/each}
		</ul>
		{#if pages > 1}
			<PaginationBar
				resultsText="Page {pageNo} of {pages}"
				page={pageNo}
				{pages}
				onGoToPage={(p) => (pageNo = p)}
				class="rounded-md border border-border"
			/>
		{/if}
	{/if}
</div>

<ConfirmDialog
	bind:open={confirmOpen}
	title="Delete report?"
	description={confirmTarget
		? `“${confirmTarget.title}” will be removed permanently. This cannot be undone.`
		: 'This permanently removes the report.'}
	confirmLabel="Delete"
	destructive
	busy={confirmBusy}
	onConfirm={confirmDelete}
/>
