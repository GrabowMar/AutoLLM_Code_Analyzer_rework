<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { toast } from 'svelte-sonner';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import PlanHash from '$lib/components/insights/PlanHash.svelte';
	import ReportBody from '$lib/components/insights/reports/ReportBody.svelte';
	import { reportTypeInfo } from '$lib/components/insights/reports/report-types';
	import { formatTimestamp, humanize } from '$lib/components/insights/format';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { saveJson } from '$lib/utils/download';
	import {
		deleteReport,
		generateReport,
		getReport,
		isReportInFlight,
		type ReportDetail
	} from '$lib/api/reports';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import FileJson from '@lucide/svelte/icons/file-json';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	const POLL_MS = 2_500;

	const reportId = $derived(page.params.id ?? '');

	let report = $state<ReportDetail | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let regenerating = $state(false);

	let confirmOpen = $state(false);
	let confirmBusy = $state(false);

	let controller: AbortController | null = null;

	async function load() {
		controller?.abort();
		const c = new AbortController();
		controller = c;
		try {
			const res = await getReport(reportId, { signal: c.signal });
			if (c.signal.aborted) return;
			report = res;
			error = null;
		} catch (e) {
			if (c.signal.aborted || isAbortError(e)) return;
			error = formatApiError(e, 'Could not load the report.');
		} finally {
			if (controller === c) loading = false;
		}
	}

	// Load on mount and whenever the route id changes.
	$effect(() => {
		void reportId;
		untrack(() => {
			report = null;
			loading = true;
			load();
		});
	});

	// Poll silently while the report is being generated.
	const inFlight = $derived(!!report && isReportInFlight(report.status));
	$effect(() => {
		if (!inFlight) return;
		const timer = setInterval(() => {
			if (document.visibilityState === 'visible') load();
		}, POLL_MS);
		return () => clearInterval(timer);
	});

	onMount(() => () => controller?.abort());

	async function remove() {
		if (!report) return;
		confirmBusy = true;
		try {
			await deleteReport(report.report_id);
			confirmOpen = false;
			toast.success('Report deleted');
			goto('/reports');
		} catch (e) {
			toast.error(formatApiError(e, 'Could not delete the report.'));
		} finally {
			confirmBusy = false;
		}
	}

	/** Generate a fresh report with the same type and config (the old one stays). */
	async function regenerate() {
		if (!report) return;
		regenerating = true;
		try {
			const next = await generateReport({
				report_type: report.report_type,
				config: report.config ?? {},
				title: report.title,
				description: report.description || undefined
			});
			toast.success('Generating a new version of the report');
			goto(`/reports/${next.report_id}`);
		} catch (e) {
			toast.error(formatApiError(e, 'Could not regenerate the report.'));
		} finally {
			regenerating = false;
		}
	}

	function downloadJson() {
		if (!report) return;
		const payload = {
			report_id: report.report_id,
			report_type: report.report_type,
			title: report.title,
			config: report.config,
			created_at: report.created_at,
			completed_at: report.completed_at,
			report_data: report.report_data
		};
		saveJson(payload, `${report.report_id}.json`);
	}

	const info = $derived(report ? reportTypeInfo(report.report_type) : null);
	const typed = $derived(!!report && report.report_data?.schema_version === 2);
	const configEntries = $derived(
		Object.entries(report?.config ?? {}).filter(([, v]) => v !== undefined && v !== '')
	);
</script>

<div class="space-y-6">
	<PageHeader
		title={report?.title ?? 'Report'}
		description={report?.description || (typed ? info?.description : undefined) || undefined}
		back={{ href: '/reports', label: 'Reports' }}
	>
		{#snippet meta()}
			{#if report}
				<StatusBadge status={report.status} />
				{#if info}
					{@const TypeIcon = info.icon}
					<Badge variant="outline" class="gap-1 text-[11px]">
						<TypeIcon class="h-3 w-3" aria-hidden="true" />{info.label}
					</Badge>
				{/if}
			{/if}
		{/snippet}
		{#snippet actions()}
			<Button
				variant="outline"
				size="sm"
				onclick={load}
				disabled={!report}
				aria-label="Refresh"
				title="Refresh"
			>
				<RefreshCw class="h-3.5 w-3.5" /><span class="hidden lg:inline">Refresh</span>
			</Button>
			<Button
				variant="outline"
				size="sm"
				onclick={downloadJson}
				disabled={report?.status !== 'completed'}
				aria-label="Download the report data as JSON"
				title="Download JSON"
			>
				<FileJson class="h-3.5 w-3.5" /><span class="hidden lg:inline">JSON</span>
			</Button>
			<Button
				variant="outline"
				size="sm"
				onclick={() => (confirmOpen = true)}
				disabled={!report}
				aria-label="Delete report"
				title="Delete"
			>
				<Trash2 class="h-3.5 w-3.5" /><span class="hidden lg:inline">Delete</span>
			</Button>
			<Button size="sm" onclick={regenerate} disabled={!report || regenerating || inFlight}>
				{#if regenerating}
					<LoaderCircle class="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
				{:else}
					<RotateCcw class="h-3.5 w-3.5" />
				{/if}
				Regenerate
			</Button>
		{/snippet}
	</PageHeader>

	{#if error}
		<ErrorAlert message={error} onretry={load} />
	{/if}

	{#if loading && !report}
		<LoadingState label="loading report" />
	{:else if report}
		<Card.Root>
			<Card.Content class="space-y-3 text-xs">
				<dl class="grid grid-cols-2 gap-3 lg:grid-cols-4">
					<div class="min-w-0">
						<dt class="section-label">Report id</dt>
						<dd class="mt-0.5 font-mono break-all">{report.report_id}</dd>
					</div>
					<div>
						<dt class="section-label">Created</dt>
						<dd class="mt-0.5">{formatTimestamp(report.created_at)}</dd>
					</div>
					<div>
						<dt class="section-label">Completed</dt>
						<dd class="mt-0.5">{formatTimestamp(report.completed_at)}</dd>
					</div>
					<div>
						<dt class="section-label">Expires</dt>
						<dd class="mt-0.5">
							{report.expires_at ? formatTimestamp(report.expires_at) : 'never'}
						</dd>
					</div>
				</dl>
				{#if configEntries.length > 0}
					<div class="flex flex-wrap items-center gap-1.5 border-t pt-3">
						<span class="section-label mr-1">Config</span>
						{#each configEntries as [key, value] (key)}
							{#if key === 'plan_hash' && typeof value === 'string'}
								<span class="inline-flex items-center gap-1"
									><span class="text-muted-foreground">plan</span><PlanHash hash={value} /></span
								>
							{:else}
								<span class="mono-tag">
									{humanize(key)}: {Array.isArray(value)
										? value.join(', ')
										: typeof value === 'object'
											? JSON.stringify(value)
											: String(value)}
								</span>
							{/if}
						{/each}
					</div>
				{/if}
			</Card.Content>
		</Card.Root>

		{#if isReportInFlight(report.status)}
			<Card.Root>
				<Card.Content class="space-y-2 py-2" aria-live="polite">
					<div class="flex items-center justify-between text-sm">
						<span class="inline-flex items-center gap-2">
							<LoaderCircle
								class="h-4 w-4 animate-spin text-primary motion-reduce:animate-none"
								aria-hidden="true"
							/>
							{report.status === 'pending' ? 'Queued' : 'Generating'}…
						</span>
						<span class="font-mono text-xs tabular-nums text-muted-foreground"
							>{report.progress_percent}%</span
						>
					</div>
					<div
						class="h-1.5 w-full overflow-hidden rounded-full bg-muted"
						role="progressbar"
						aria-valuemin={0}
						aria-valuemax={100}
						aria-valuenow={report.progress_percent}
						aria-label="Report generation progress"
					>
						<div
							class="h-full rounded-full bg-primary transition-[width] duration-300"
							style="width: {report.progress_percent}%"
						></div>
					</div>
					<p class="text-xs text-muted-foreground">
						This page updates by itself when the report is ready.
					</p>
				</Card.Content>
			</Card.Root>
		{:else if report.status === 'failed'}
			<ErrorAlert
				title="Generation failed"
				message={report.error_message || 'The report could not be generated.'}
				onretry={regenerate}
			/>
		{:else}
			<ReportBody {report} />
		{/if}
	{/if}
</div>

<ConfirmDialog
	bind:open={confirmOpen}
	title="Delete report?"
	description="This permanently removes the report. This cannot be undone."
	confirmLabel="Delete"
	destructive
	busy={confirmBusy}
	onConfirm={remove}
/>
