<script lang="ts">
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import { Alert, AlertDescription } from '$lib/components/ui/alert';
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

interface Props {
	job: GenerationJob;
	provider: string;
	codeFootprint: CodeFootprint | null;
	totalTokens: number;
	totalCost: number;
	onCancel: () => void;
	onRetry: () => void;
	onExport: () => void;
	onDownloadCode: () => void;
	onDelete: () => void;
	/** Opens the GitHub export dialog; the button is hidden without it. */
	onExportGithub?: () => void;
}

let {
	job,
	provider,
	codeFootprint,
	totalTokens,
	totalCost,
	onCancel,
	onRetry,
	onExport,
	onDownloadCode,
	onDelete,
	onExportGithub,
}: Props = $props();

// Agent runs sample at the provider default unless a temperature was set explicitly.
const temperature = $derived(
	job.temperature != null ? String(job.temperature) : job.mode === 'agent' ? 'default' : '—'
);

const kpis = $derived([
	{ label: 'Duration', value: fmtDur(job.duration_seconds), sub: job.started_at ? 'wall clock' : 'not started' },
	{ label: 'Tokens', value: fmt(totalTokens, 0), sub: 'prompt + completion' },
	{ label: 'Cost', value: fmtCost(totalCost), sub: 'USD' },
	{ label: 'Lines of code', value: fmt(codeFootprint?.totalLines ?? 0, 0), sub: codeFootprint ? `${codeFootprint.fileCount} file${codeFootprint.fileCount === 1 ? '' : 's'}` : 'no code' },
	{ label: 'Temperature', value: temperature, sub: 'sampling' },
	{ label: 'Max tokens', value: fmt(job.max_tokens, 0), sub: 'per response' },
]);
</script>

<PageHeader
	title={jobDescription(job)}
	documentTitle="{jobDescription(job)} · {job.model_name ?? 'Unknown model'}"
	back={{ href: '/applications', label: 'Applications' }}
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
	</div>
</PageHeader>

{#if job.status === 'failed'}
	<Alert variant="destructive">
		<AlertTriangle />
		<div class="flex flex-wrap items-center justify-between gap-2">
			<div class="min-w-0">
				<p class="font-medium">Generation failed</p>
				<AlertDescription class="truncate">{job.error_message || 'Unknown error'}</AlertDescription>
			</div>
			<Button variant="outline" size="sm" href="/applications/{job.id}/failure">View Details</Button>
		</div>
	</Alert>
{/if}

<div class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
	{#each kpis as kpi (kpi.label)}
		<div class="kpi-card">
			<span class="kpi-label">{kpi.label}</span>
			<span class="kpi-value text-xl">{kpi.value}</span>
			<span class="text-[11px] text-muted-foreground">{kpi.sub}</span>
		</div>
	{/each}
</div>
