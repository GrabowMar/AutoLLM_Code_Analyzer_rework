<script lang="ts">
	/**
	 * ReportMasthead — a report's header, set like the first page of a paper (the experiment
	 * record's `StudyMasthead` motif): eyebrow `REPORT · TYPE · RPT-XXXXXXXX`, the serif title,
	 * status + generated / took / expiry, the description as a pull quote, and a spec strip
	 * (scope · window · analysis · randomisation) read from the report's config. Owns the page
	 * `<h1>` and `<title>` (it stands in for PageHeader).
	 *
	 * Props: `report`, `studyName` (scope label when the report covers an experiment), `actions`
	 * snippet (exports, refresh, delete), `children` (a strip under the spec: progress, failure,
	 * comparison), `nav` (the page's tabs and compare picker, flush along the bottom edge).
	 */
	import type { Snippet } from 'svelte';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import FileText from '@lucide/svelte/icons/file-text';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { pageTitle } from '$lib/stores/page-title.svelte';
	import type { ReportDetail } from '$lib/api/reports';
	import { REPORT_TYPE_INFO } from './catalog';
	import { expiryLabel, generationTime, reportAccession, specItems } from './format';

	interface Props {
		report: ReportDetail;
		studyName?: string | null;
		actions?: Snippet;
		children?: Snippet;
		nav?: Snippet;
	}

	let { report, studyName = null, actions, children, nav }: Props = $props();

	$effect(() => {
		const title = report.title;
		pageTitle.current = title;
		return () => {
			if (pageTitle.current === title) pageTitle.current = '';
		};
	});

	const info = $derived(REPORT_TYPE_INFO[report.report_type]);
	const Icon = $derived(info?.icon ?? FileText);
	const spec = $derived(specItems(report.report_type, report.config, { study: studyName }));
	const dateFmt = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
	const took = $derived(generationTime(report.created_at, report.completed_at));
	const expiry = $derived(expiryLabel(report.expires_at));
</script>

<svelte:head>
	<title>{report.title} · LLM Lab</title>
</svelte:head>

<header class="overflow-clip rounded-md border border-border bg-card shadow-[var(--shadow-card)]">
	<div class="flex flex-wrap items-start gap-x-4 gap-y-3 px-4 pb-3 pt-3.5 sm:px-5">
		<a href="/reports" class="page-header-back mt-0.5" aria-label="Back to Reports" title="Back to Reports">
			<ArrowLeft class="size-4" aria-hidden="true" />
		</a>
		<div class="min-w-0 flex-[1_1_20rem] space-y-1.5">
			<p class="page-eyebrow flex-wrap">
				<Icon class="size-3.5" aria-hidden="true" />
				<span>Report</span>
				<span aria-hidden="true">·</span>
				<span>{info?.label ?? report.report_type}</span>
				<span aria-hidden="true">·</span>
				<span class="text-foreground" title={report.report_id}>{reportAccession(report.report_id)}</span>
			</p>
			<h1 class="break-words font-display text-[1.6rem] font-semibold leading-tight tracking-tight text-balance sm:text-[1.85rem]">{report.title}</h1>
			<p class="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-muted-foreground">
				<StatusBadge status={report.status} />
				<span>Requested {dateFmt.format(new Date(report.created_at))}</span>
				{#if took}<span aria-hidden="true">·</span><span>generated in {took}</span>{/if}
				{#if expiry}<span aria-hidden="true">·</span><span title={report.expires_at ?? ''}>{expiry}</span>{/if}
			</p>
		</div>
		{#if actions}
			<div class="ml-auto flex shrink-0 flex-wrap items-center gap-2 max-sm:ml-0 max-sm:w-full max-sm:shrink">{@render actions()}</div>
		{/if}
	</div>
	{#if report.description}
		<div class="grid gap-x-4 gap-y-1 border-t border-border/60 px-4 py-3 sm:grid-cols-[8rem_minmax(0,1fr)] sm:px-5">
			<p class="section-label pt-1">About</p>
			<blockquote class="max-w-4xl border-l-2 border-accent-2/60 pl-3 font-display text-base italic leading-relaxed text-foreground/90">{report.description}</blockquote>
		</div>
	{/if}
	{#if spec.length > 0}
		<dl class="rm-spec border-t border-border/60" style:--rm-cols={Math.min(spec.length, 4)}>
			{#each spec as item (item.label)}
				<div class="min-w-0 px-4 py-2.5 sm:px-5">
					<dt class="section-label">{item.label}</dt>
					<dd class="mt-0.5 truncate text-sm" title={item.value}>
						{#if item.href}<a href={item.href} class="settings-link">{item.value}</a>{:else}{item.value}{/if}
						{#if item.sub}<span class="text-xs text-muted-foreground"> · {item.sub}</span>{/if}
					</dd>
				</div>
			{/each}
		</dl>
	{/if}
	{#if children}<div class="border-t border-border/60 px-4 py-2.5 sm:px-5">{@render children()}</div>{/if}
	{#if nav}<div class="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-border/60 px-2 sm:px-3">{@render nav()}</div>{/if}
</header>

<style>
	.rm-spec {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
	}
	.rm-spec > div + div {
		border-top: 1px solid color-mix(in oklab, var(--border) 60%, transparent);
	}
	@media (min-width: 40rem) {
		.rm-spec {
			grid-template-columns: repeat(min(var(--rm-cols), 2), minmax(0, 1fr));
		}
		.rm-spec > div + div {
			border-top: 0;
		}
		.rm-spec > div:nth-child(2n) {
			border-left: 1px solid color-mix(in oklab, var(--border) 60%, transparent);
		}
		.rm-spec > div:nth-child(n + 3) {
			border-top: 1px solid color-mix(in oklab, var(--border) 60%, transparent);
		}
	}
	@media (min-width: 80rem) {
		.rm-spec {
			grid-template-columns: repeat(var(--rm-cols), minmax(0, 1fr));
		}
		.rm-spec > div:nth-child(n) {
			border-left: 1px solid color-mix(in oklab, var(--border) 60%, transparent);
			border-top: 0;
		}
		.rm-spec > div:nth-child(4n + 1) {
			border-left: 0;
		}
		.rm-spec > div:nth-child(n + 5) {
			border-top: 1px solid color-mix(in oklab, var(--border) 60%, transparent);
		}
	}
</style>
