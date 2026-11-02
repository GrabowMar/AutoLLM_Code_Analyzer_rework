<script lang="ts">
import type { Snippet } from 'svelte';
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import PageHeader from '$lib/components/PageHeader.svelte';
import StatusBadge from '$lib/components/StatusBadge.svelte';
import Copy from '@lucide/svelte/icons/copy';
import Download from '@lucide/svelte/icons/download';
import FileCode from '@lucide/svelte/icons/file-code';
import AlertTriangle from '@lucide/svelte/icons/alert-triangle';
import GitBranch from '@lucide/svelte/icons/git-branch';
import RefreshCw from '@lucide/svelte/icons/refresh-cw';
import Ban from '@lucide/svelte/icons/ban';
import Trash2 from '@lucide/svelte/icons/trash-2';
import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
import type { GenerationJob } from '$lib/api/client';
import { batchHref, jobDisplayStatus, jobPhaseTones } from '$lib/components/generator/batches';
import { fmt, fmtDur, fmtCost, copyText, jobDescription, type CodeFootprint } from './utils';
import ProviderIcon from '$lib/components/ProviderIcon.svelte';
import Layers from '@lucide/svelte/icons/layers';
import { shortModelName, modelNamePrefix } from '$lib/components/table';
import AppRuntimeControls from './AppRuntimeControls.svelte';
import CloudUpload from '@lucide/svelte/icons/cloud-upload';
import ExperimentCellChip from './insights/ExperimentCellChip.svelte';
import type { CodeProfile, ExperimentCell } from '$lib/api/job-insights';

interface Props {
	job: GenerationJob;
	provider: string;
	codeFootprint: CodeFootprint | null;
	/** Source LOC (study denominator) and all-lines count; null while loading or unavailable. */
	codeProfile?: CodeProfile | null;
	/** Set when the code profile could not be loaded (the KPI then says so instead of "…"). */
	codeProfileError?: string;
	/** Experiment cell the job fills (from the timeline endpoint). */
	experimentCell?: ExperimentCell | null;
	/** null when nothing was recorded (no artifacts, no metrics): unknown, never 0. */
	totalTokens: number | null;
	totalCost: number | null;
	onCancel: () => void;
	onRetry: () => void;
	onExport: () => void;
	onDownloadCode: () => void;
	onDelete: () => void;
	/** Opens the GitHub export dialog; the button is hidden without it. */
	onExportGithub?: () => void;
	/** Section tabs, rendered in the header's bottom strip (the page owns `Tabs.Root`). */
	nav?: Snippet;
}

let {
	job,
	provider,
	codeFootprint,
	codeProfile = null,
	codeProfileError = '',
	experimentCell = null,
	totalTokens,
	totalCost,
	onCancel,
	onRetry,
	onExport,
	onDownloadCode,
	onDelete,
	onExportGithub,
	nav,
}: Props = $props();

// Agent runs sample at the provider default unless a temperature was set explicitly.
const temperature = $derived(
	job.temperature != null ? String(job.temperature) : job.mode === 'agent' ? 'default' : '—'
);

// Source LOC = the study tables' per-KLOC denominator (non-blank, non-comment source
// lines, tests and scratch/dependency dirs apart); the all-lines count (lockfiles,
// scratch folders) is only a secondary reference.
const allLines = $derived(codeProfile?.all_lines ?? codeFootprint?.totalLines ?? null);
const locKpi = $derived.by(() => {
	const total = codeProfile?.loc.total_loc;
	if (codeProfile && total != null) {
		return {
			value: fmt(total, 0),
			title: `Source LOC in ${codeProfile.loc.source_files ?? '—'} source files (tests and dependency/scratch dirs apart): the per-KLOC denominator of the study tables.\nAll lines incl. lockfiles/scratch: ${allLines != null ? fmt(allLines, 0) : '—'} in ${codeProfile.all_files} files.`,
		};
	}
	if (codeProfile) return { value: '—', title: codeProfile.notes[0] ?? 'Source LOC not measured for this run.' };
	if (codeProfileError) return { value: '—', title: `Source LOC could not be loaded: ${codeProfileError}` };
	return { value: '…', title: allLines != null ? `Loading source LOC (all lines incl. lockfiles/scratch: ${fmt(allLines, 0)})` : 'Loading source LOC' };
});
// Subscription-billed runs (OpenCode Go) record cost 0; the provider's list price is kept apart.
const listCost = $derived(typeof job.metrics?.list_cost === 'number' ? (job.metrics.list_cost as number) : null);
const billingSource = $derived(typeof job.metrics?.billing_source === 'string' ? (job.metrics.billing_source as string) : null);

const kpis = $derived([
	{ label: 'duration', value: fmtDur(job.duration_seconds), title: job.started_at ? 'Wall clock' : 'Not started' },
	{ label: 'tokens', value: fmt(totalTokens, 0), title: totalTokens == null ? 'No LLM usage was recorded for this job' : 'Prompt + completion' },
	{
		label: 'cost',
		// A subscription-billed run is $0 billed, not "Free": its list price is shown next to it.
		value: totalCost == null ? '—' : listCost != null && totalCost === 0 ? '$0' : fmtCost(totalCost),
		title: totalCost == null ? 'No LLM usage was recorded for this job' : `USD billed${billingSource ? ` (${billingSource})` : ''}${listCost != null ? `\nList-price equivalent reported by the provider (not billed): $${listCost.toFixed(4)}` : ''}`,
	},
	...(listCost != null
		? [{ label: 'list-price equiv.', value: `$${listCost.toFixed(listCost < 0.01 ? 5 : 3)}`, title: `List-price equivalent reported by the provider${billingSource ? ` (${billingSource})` : ''}; not billed, never part of cost or budget` }]
		: []),
	{ label: 'src LOC', value: locKpi.value, title: locKpi.title },
	{ label: 'temp', value: temperature, title: 'Sampling temperature' },
	{ label: 'max tokens', value: fmt(job.max_tokens, 0), title: 'Max tokens per response' },
]);
</script>

<PageHeader
	title={jobDescription(job)}
	documentTitle="{jobDescription(job)} · {job.model_name ?? 'Unknown model'}"
	back={{ href: '/applications', label: 'Applications' }}
	{nav}
>
	{#snippet meta()}
		<StatusBadge status={jobDisplayStatus(job)} tones={jobPhaseTones} />
		<Badge variant="outline" class="capitalize">{job.mode}</Badge>
	{/snippet}
	{#snippet actions()}
		<AppRuntimeControls jobId={job.id} jobStatus={job.status} />
		{#if job.status === 'pending' || job.status === 'running'}
			<Button variant="outline" size="sm" onclick={onCancel} title="Cancel job">
				<Ban class="h-3.5 w-3.5" />Cancel
			</Button>
		{/if}
		{#if job.status === 'failed' || job.status === 'cancelled'}
			<Button variant="outline" size="sm" onclick={onRetry} title="Retry job">
				<RotateCcw class="h-3.5 w-3.5" />Retry
			</Button>
		{/if}
		<div class="flex items-center gap-0.5">
			<Button variant="ghost" size="sm" class="size-8 p-0" onclick={onDownloadCode} title="Download generated code" aria-label="Download generated code">
				<FileCode />
			</Button>
			<Button variant="ghost" size="sm" class="size-8 p-0" onclick={onExport} title="Export run as JSON" aria-label="Export run as JSON">
				<Download />
			</Button>
			{#if onExportGithub}
				<Button variant="ghost" size="sm" class="size-8 p-0" onclick={onExportGithub} title="Export to GitHub" aria-label="Export to GitHub">
					<CloudUpload />
				</Button>
			{/if}
			{#if job.status !== 'running'}
				<Button variant="ghost" size="sm" class="size-8 p-0 text-muted-foreground hover:bg-destructive/10 hover:text-danger-fg" onclick={onDelete} title="Delete job" aria-label="Delete job">
					<Trash2 />
				</Button>
			{/if}
		</div>
		<Button
			size="sm"
			href="/sample-generator?from_job={job.id}"
			title="New batch with this run's task, model, stack and presets (new seed)"
		>
			<RefreshCw class="h-3.5 w-3.5" />Re-generate
		</Button>
	{/snippet}
	<div class="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
		<span class="inline-flex items-center gap-1.5 text-foreground" title={job.model_name ?? undefined}>
			{#if provider && provider !== '—'}<ProviderIcon {provider} size={16} />{/if}
			{shortModelName(job.model_name) || 'Unknown model'}
			{#if modelNamePrefix(job.model_name)}<span class="text-muted-foreground">· {modelNamePrefix(job.model_name)}</span>{/if}
		</span>
		{#if job.stack_slug}
			<span aria-hidden="true" class="opacity-50">·</span>
			<span class="inline-flex items-center gap-1"><Layers class="size-3" aria-hidden="true" /><span class="font-mono">{job.stack_slug}</span></span>
		{/if}
		{#if experimentCell}
			<span aria-hidden="true" class="opacity-50">·</span>
			<ExperimentCellChip cell={experimentCell} />
		{/if}
		{#if job.batch_id}
			<span aria-hidden="true" class="opacity-50">·</span>
			<a href={batchHref(job.batch_id)} class="inline-flex items-center gap-1 hover:text-primary" title="Open the batch">
				<GitBranch class="size-3" aria-hidden="true" />{job.batch_name || 'Batch'}
			</a>
		{/if}
		<span aria-hidden="true" class="opacity-50">·</span>
		<button
			type="button"
			class="group/id inline-flex cursor-pointer items-center gap-1 font-mono hover:text-foreground"
			title="Copy job ID {job.id}"
			onclick={() => copyText(job.id, 'Copied Job ID')}
		>
			{job.id.substring(0, 8)}<Copy class="size-3 opacity-60 group-hover/id:opacity-100" aria-hidden="true" />
			<span class="sr-only">Copy job ID</span>
		</button>
		<span aria-hidden="true" class="opacity-50">·</span>
		<span class="header-stats">
			{#each kpis as kpi (kpi.label)}
				<span class="header-stat" title={kpi.title}><b>{kpi.value}</b>{kpi.label}</span>
			{/each}
		</span>
	</div>
	{#if job.status === 'failed'}
		<p class="mt-1 flex min-w-0 items-center gap-1.5 text-danger-fg" role="alert">
			<AlertTriangle class="size-3.5 shrink-0" aria-hidden="true" />
			<span class="font-medium">Generation failed:</span>
			<span class="min-w-0 truncate" title={job.error_message || undefined}>{job.error_message || 'Unknown error'}</span>
			<a href="/applications/{job.id}/failure" class="shrink-0 underline hover:text-foreground">View details</a>
		</p>
	{/if}
</PageHeader>
