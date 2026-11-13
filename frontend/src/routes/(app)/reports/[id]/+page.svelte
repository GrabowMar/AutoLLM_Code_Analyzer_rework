<script lang="ts">
	// A report, set like a paper: the masthead (type, accession id, status, scope / analysis /
	// randomisation from its config), the summary as key figures, then the report's curated tables
	// exactly as the Markdown/LaTeX exports have them (`GET /reports/{id}/tables/`, server-side
	// `renderers.report_tables`), each a numbered table with its note as the caption — sortable,
	// filterable, chartable — beside a scroll-following table of contents. Pending reports poll every 2 s.
	//
	// Two views (`?view=`): Report, and Notebook (JupyterLite in the browser with the report loaded;
	// kept mounted once opened so Python keeps running across tab switches). `?compare=<report id>`
	// sets another completed report of the same type beside this one: deltas under every number.
	// Study reports with a ranking get a live what-if ranking right after the ranking table.
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { onMount, onDestroy } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import * as Tabs from '$lib/components/ui/tabs';
	import * as Select from '$lib/components/ui/select';
	import { toast } from 'svelte-sonner';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import Download from '@lucide/svelte/icons/download';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import GitCompareArrows from '@lucide/svelte/icons/git-compare-arrows';
	import X from '@lucide/svelte/icons/x';
	import { formatApiError } from '$lib/api/core';
	import {
		deleteReport,
		generateReport,
		getReport,
		getReports,
		getReportTables,
		reportExportUrl,
		type ReportDetail,
		type ReportDocument,
		type ReportSummary
	} from '$lib/api/reports';
	import { getStudy } from '$lib/api/v3/studies';
	import ReportMasthead from '$lib/components/reports/ReportMasthead.svelte';
	import ReportKeyFigures from '$lib/components/reports/ReportKeyFigures.svelte';
	import ReportTableFigure from '$lib/components/reports/ReportTableFigure.svelte';
	import ReportContents from '$lib/components/reports/ReportContents.svelte';
	import ReportNotebook from '$lib/components/reports/ReportNotebook.svelte';
	import WhatIfRanking from '$lib/components/reports/WhatIfRanking.svelte';
	import { EXPORT_FORMATS } from '$lib/components/reports/catalog';
	import { ACTIVE_STATUSES, keyFigures, reportAccession, titleParts } from '$lib/components/reports/format';
	import { matchTable } from '$lib/components/reports/compare';
	import { rankingData } from '$lib/components/reports/ranking';
	import { formatDate } from '$lib/utils/formatters';

	let report = $state<ReportDetail | null>(null);
	let loading = $state(true);
	let error = $state('');
	let timer: ReturnType<typeof setInterval> | null = null;

	let doc = $state<ReportDocument | null>(null);
	let docError = $state('');
	let docKey = '';
	let studyName = $state<string | null>(null);

	// ── View (Report | Notebook) ────────────────────────────────────────────────
	const view = $derived(page.url.searchParams.get('view') === 'notebook' ? 'notebook' : 'report');
	let notebookOpened = $state(false);
	$effect(() => {
		if (view === 'notebook') notebookOpened = true;
	});
	function setParam(key: string, value: string | null) {
		const url = new URL(page.url);
		if (value) url.searchParams.set(key, value);
		else url.searchParams.delete(key);
		goto(url, { replaceState: true, noScroll: true, keepFocus: true });
	}

	async function load() {
		try {
			report = await getReport(page.params.id ?? '');
			error = '';
		} catch (e) {
			error = formatApiError(e, 'Failed to load report');
		} finally {
			loading = false;
		}
	}

	async function loadTables(r: ReportDetail) {
		docError = '';
		try {
			doc = await getReportTables(r.report_id);
		} catch (e) {
			docError = formatApiError(e, 'Could not load the report tables.');
		}
	}

	// The tables of a completed report, once per completion.
	$effect(() => {
		if (report?.status !== 'completed') return;
		const key = `${report.report_id}:${report.completed_at}`;
		if (key === docKey) return;
		docKey = key;
		doc = null;
		void loadTables(report);
	});

	// The experiment's name for the scope line (best effort; the id stays the link).
	let studyKey = '';
	$effect(() => {
		const id = (report?.config?.study_id ?? report?.config?.experiment_id) as string | undefined;
		if (!id || id === studyKey) return;
		studyKey = id;
		getStudy(id)
			.then((s) => (studyName = s.name))
			.catch(() => (studyName = null));
	});

	// ── Compare ──────────────────────────────────────────────────────────────────
	let candidates = $state<ReportSummary[]>([]);
	let candidatesKey = '';
	$effect(() => {
		if (report?.status !== 'completed') return;
		const key = `${report.report_id}:${report.report_type}`;
		if (key === candidatesKey) return;
		candidatesKey = key;
		const self = report.report_id;
		getReports({ report_type: report.report_type, status: 'completed', limit: 100 })
			.then((res) => (candidates = res.reports.filter((r) => r.report_id !== self)))
			.catch(() => (candidates = []));
	});
	const compareId = $derived(page.url.searchParams.get('compare') ?? '');
	let other = $state<ReportDetail | null>(null);
	let otherDoc = $state<ReportDocument | null>(null);
	let otherError = $state('');
	let otherKey = '';
	$effect(() => {
		const id = compareId;
		if (id === otherKey) return;
		otherKey = id;
		other = null;
		otherDoc = null;
		otherError = '';
		if (!id) return;
		Promise.all([getReport(id), getReportTables(id)])
			.then(([r, d]) => {
				if (otherKey !== id) return;
				other = r;
				otherDoc = d;
			})
			.catch((e) => {
				if (otherKey === id) otherError = formatApiError(e, 'Could not load the report to compare with.');
			});
	});
	const comparing = $derived(!!other && !!otherDoc);
	const otherLabel = $derived(other ? reportAccession(other.report_id) : '');
	const compareItems = $derived(
		candidates.map((r) => ({ value: r.report_id, label: r.title, description: `${reportAccession(r.report_id)} · ${formatDate(r.created_at)}` }))
	);

	onMount(() => {
		load().then(() => {
			timer = setInterval(() => {
				if (report && ACTIVE_STATUSES.includes(report.status)) load();
			}, 2000);
		});
	});

	onDestroy(() => {
		if (timer) clearInterval(timer);
	});

	let confirmOpen = $state(false);
	let confirmBusy = $state(false);

	async function remove() {
		if (!report) return;
		confirmBusy = true;
		try {
			await deleteReport(report.report_id);
			confirmOpen = false;
			goto('/reports');
		} catch (e) {
			toast.error(formatApiError(e, 'Failed to delete report'));
		} finally {
			confirmBusy = false;
		}
	}

	// Regenerate: the same type, config, title and description, as a new report.
	let regenerating = $state(false);
	async function regenerate() {
		if (!report) return;
		regenerating = true;
		try {
			const r = await generateReport({
				report_type: report.report_type,
				config: report.config,
				title: report.title,
				description: report.description || undefined
			});
			toast.success('Generating a new copy of this report');
			await goto(`/reports/${r.report_id}`);
			loading = true;
			doc = null;
			docKey = '';
			await load();
		} catch (e) {
			toast.error(formatApiError(e, 'Failed to regenerate the report'));
		} finally {
			regenerating = false;
		}
	}

	// Experiment and study reports re-read their leader (or a tie) from the data.
	const leaderData = (r: ReportDetail | null) => (r?.report_type === 'experiment_report' || r?.report_type === 'study_report' ? r.report_data : null);
	const figures = $derived(keyFigures(report?.summary, leaderData(report)));
	const previousFigures = $derived(comparing ? keyFigures(other?.summary, leaderData(other)) : null);
	const anchor = (i: number) => `report-table-${i + 1}`;
	const contents = $derived(
		(doc?.tables ?? []).map((t, i) => ({ id: anchor(i), number: i + 1, title: titleParts(t.title).join(' › '), rows: t.totalRows }))
	);
	const active = $derived(!!report && ACTIVE_STATUSES.includes(report.status));
	const ranking = $derived(report?.report_type === 'study_report' ? rankingData(report.report_data) : null);
	/** The what-if panel follows the report's ranking table (or ends the tables when there is none). */
	const rankingAt = $derived(ranking && doc ? doc.tables.findIndex((t) => /^ranking\b/i.test(t.title)) : -1);
</script>

{#snippet whatIf()}
	{#if ranking}
		<WhatIfRanking data={ranking} number="W" id="report-what-if" />
	{/if}
{/snippet}

<Tabs.Root value={view} onValueChange={(v) => setParam('view', v === 'notebook' ? 'notebook' : null)} class="space-y-4">
	{#if loading}
		<PageHeader title="Report" back={{ href: '/reports', label: 'Reports' }} />
		<LoadingState label="loading report" />
	{:else if error || !report}
		<PageHeader title="Report" back={{ href: '/reports', label: 'Reports' }} />
		<ErrorAlert message={error || 'Report not found'} onretry={load} />
	{:else}
		<ReportMasthead {report} {studyName}>
			{#snippet actions()}
				{#if report?.status === 'completed'}
					<div class="flex items-center overflow-clip rounded-md border border-border" role="group" aria-label="Download report">
						<span class="inline-flex h-8 items-center gap-1.5 border-r border-border bg-muted/40 px-2.5 text-xs text-muted-foreground">
							<Download class="size-3.5" aria-hidden="true" />Download
						</span>
						{#each EXPORT_FORMATS as f, i (f.format)}
							<a
								href={reportExportUrl(report.report_id, f.format)}
								download
								class="inline-flex h-8 cursor-pointer items-center px-2.5 text-xs font-medium transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring {i > 0 ? 'border-l border-border' : ''}"
								title="Download as {f.label} ({f.ext})">{f.label}</a
							>
						{/each}
					</div>
				{/if}
				<Button variant="outline" size="sm" onclick={load} aria-label="Refresh" title="Refresh">
					<RefreshCw class="size-3.5" />
				</Button>
				<Button variant="outline" size="sm" onclick={regenerate} disabled={regenerating || active} title="Generate a new copy with the same settings">
					{#if regenerating}<LoaderCircle class="size-3.5 animate-spin motion-reduce:animate-none" />{:else}<RotateCcw class="size-3.5" />{/if}
					Regenerate
				</Button>
				<Button variant="outline" size="sm" onclick={() => (confirmOpen = true)} aria-label="Delete report" title="Delete report">
					<Trash2 class="size-3.5" />
				</Button>
			{/snippet}
			{#if active}
				<div class="flex flex-wrap items-center gap-x-3 gap-y-2" role="status" aria-live="polite">
					<span class="text-sm">{report.status === 'pending' ? 'Queued' : 'Generating'}…</span>
					<span class="block h-1.5 min-w-40 flex-1 overflow-hidden rounded-full bg-muted" aria-hidden="true">
						<span class="block h-full rounded-full bg-primary transition-[width] duration-300" style:width="{Math.max(report.progress_percent, 3)}%"></span>
					</span>
					<span class="font-mono text-xs tabular-nums text-muted-foreground">{report.progress_percent}%</span>
				</div>
			{:else if report.status === 'failed'}
				<p class="text-sm text-danger-fg">{report.error_message || 'Generation failed.'}</p>
			{:else if comparing && other}
				<div class="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-sm" role="status">
					<GitCompareArrows class="size-4 text-[var(--chart-2)]" aria-hidden="true" />
					<span>
						Comparing with <a href="/reports/{other.report_id}" class="settings-link font-medium">{other.title}</a>
						<span class="font-mono text-xs text-muted-foreground">· {otherLabel} · {formatDate(other.created_at)}</span>
					</span>
					<span class="text-xs text-muted-foreground">Figures under each number are this report minus that one.</span>
					<Button variant="ghost" size="sm" class="ml-auto h-7 text-xs" onclick={() => setParam('compare', null)}><X class="size-3.5" />Stop comparing</Button>
				</div>
			{:else if compareId && otherError}
				<p class="text-sm text-danger-fg">{otherError}</p>
			{:else if compareId}
				<p class="text-sm text-muted-foreground"><LoaderCircle class="mr-1 inline size-3.5 animate-spin align-[-3px] motion-reduce:animate-none" />Loading the report to compare with…</p>
			{/if}
			{#snippet nav()}
				{#if report?.status === 'completed'}
					<Tabs.List aria-label="Report views" class="border-b-0">
						<Tabs.Trigger value="report">Report</Tabs.Trigger>
						<Tabs.Trigger value="notebook" class="gap-1.5">Notebook <span class="rounded-sm border border-border px-1 font-mono text-[10px] leading-4 text-muted-foreground">Python</span></Tabs.Trigger>
					</Tabs.List>
					{#if view === 'report'}
						<div class="ml-auto flex min-w-0 items-center gap-2 py-1.5 pr-1 max-sm:w-full">
							<label for="compare-with" class="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
								<GitCompareArrows class="size-3.5" aria-hidden="true" />Compare with
							</label>
							<Select.Simple
								id="compare-with"
								size="sm"
								class="min-w-0 sm:w-72"
								value={compareId}
								placeholder={candidates.length ? 'another report of this type…' : 'no other report of this type'}
								disabled={candidates.length === 0}
								clearLabel={compareId ? 'No comparison' : undefined}
								items={compareItems}
								onValueChange={(v) => setParam('compare', v || null)}
							/>
						</div>
					{/if}
				{/if}
			{/snippet}
		</ReportMasthead>

		{#if active}
			<LoadingState label="generating report… {report.progress_percent}%" />
		{:else if report.status === 'failed'}
			<EmptyState
				title="This report could not be generated"
				description="The error is shown above. Regenerate it with the same settings, or start a new report with a different scope."
			>
				{#snippet action()}
					<div class="flex flex-wrap justify-center gap-2">
						<Button size="sm" variant="outline" href="/reports/create?type={report?.report_type}">New report of this type</Button>
						<Button size="sm" onclick={regenerate} disabled={regenerating}><RotateCcw class="size-3.5" />Regenerate</Button>
					</div>
				{/snippet}
			</EmptyState>
		{:else if report.status === 'completed'}
			<div class="space-y-4" hidden={view !== 'report'}>
				{#if figures.length > 0}
					<section class="space-y-3" aria-labelledby="rk-heading">
						<SectionHeading id="rk-heading" size="sm" title="Key figures" />
						<ReportKeyFigures {figures} previous={previousFigures} previousLabel={otherLabel} />
					</section>
				{/if}

				<section class="space-y-3" aria-labelledby="rt-heading">
					<SectionHeading id="rt-heading" size="sm" title="Tables" count={doc ? doc.tables.length : undefined} />
					{#if docError}
						<ErrorAlert message={docError} onretry={() => report && loadTables(report)} />
					{:else if !doc}
						<LoadingState label="loading report tables" />
					{:else if doc.tables.length === 0}
						<EmptyState title="Nothing to tabulate" description="This report has no data; the JSON download has the raw payload." />
						{@render whatIf()}
					{:else}
						<div class={contents.length > 3 ? 'grid gap-4 xl:grid-cols-[17rem_minmax(0,1fr)] xl:items-start' : ''}>
							{#if contents.length > 3}
								<div class="max-xl:hidden xl:sticky xl:top-16">
									<ReportContents items={contents} />
								</div>
								<div class="xl:hidden">
									<ReportContents items={contents} variant="inline" />
								</div>
							{/if}
							<div class="min-w-0 space-y-4">
								{#each doc.tables as table, i (i)}
									<ReportTableFigure
										{table}
										number={i + 1}
										id={anchor(i)}
										reportId={report.report_id}
										comparing={comparing}
										other={comparing ? matchTable(table, i, otherDoc) : null}
										{otherLabel}
									/>
									{#if i === rankingAt}{@render whatIf()}{/if}
								{/each}
								{#if rankingAt < 0}{@render whatIf()}{/if}
								<p class="px-1 text-xs text-muted-foreground">
									The Markdown and LaTeX downloads hold these same tables; JSON has the raw data behind them. The Notebook tab
									loads them into pandas.
								</p>
							</div>
						</div>
					{/if}
				</section>
			</div>
			{#if notebookOpened && doc}
				<div hidden={view !== 'notebook'}>
					<ReportNotebook {report} {doc} />
				</div>
			{:else if view === 'notebook'}
				<LoadingState label="loading report tables" />
			{/if}
		{/if}
	{/if}
</Tabs.Root>

<ConfirmDialog
	bind:open={confirmOpen}
	title="Delete report?"
	description="This permanently removes the report. This cannot be undone."
	confirmLabel="Delete"
	destructive
	busy={confirmBusy}
	onConfirm={remove}
/>
