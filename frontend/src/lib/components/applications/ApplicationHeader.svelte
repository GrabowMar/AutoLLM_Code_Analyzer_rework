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
import { fmt, fmtDur, fmtCost, copyText, type CodeFootprint } from './utils';
import AppRuntimeControls from './AppRuntimeControls.svelte';

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
}: Props = $props();

// Agent runs sample at the provider default unless a temperature was set explicitly.
const temperature = $derived(
	job.temperature != null ? String(job.temperature) : job.mode === 'agent' ? 'default' : '—'
);

const kpis = $derived([
	{ label: 'Duration', value: fmtDur(job.duration_seconds) },
	{ label: 'Tokens', value: fmt(totalTokens, 0) },
	{ label: 'Cost', value: fmtCost(totalCost) },
	{ label: 'Temperature', value: temperature },
	{ label: 'Max Tokens', value: fmt(job.max_tokens, 0) },
	{ label: 'Lines of Code', value: fmt(codeFootprint?.totalLines ?? 0, 0) },
]);
</script>

<PageHeader
	title={job.model_name ?? 'Unknown Model'}
	back={{ href: '/applications', label: 'Applications' }}
>
	{#snippet meta()}
		<Badge variant="outline" class="capitalize">{job.mode}</Badge>
		<StatusBadge status={jobDisplayStatus(job)} tones={jobPhaseTones} />
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
		<Button variant="outline" size="sm" onclick={() => copyText(job.id, 'Copied Job ID')} title="Copy Job ID">
			<Copy class="h-3.5 w-3.5" />ID
		</Button>
		<Button variant="outline" size="sm" onclick={onExport} title="Export as JSON">
			<Download class="h-3.5 w-3.5" />Export
		</Button>
		<Button variant="outline" size="sm" onclick={onDownloadCode} title="Download generated code">
			<FileCode class="h-3.5 w-3.5" />Code
		</Button>
		{#if job.status !== 'running'}
			<Button variant="outline" size="sm" onclick={onDelete} title="Delete job" class="border-destructive/30 text-destructive hover:bg-destructive/10">
				<Trash2 class="h-3.5 w-3.5" />Delete
			</Button>
		{/if}
		<Button
			size="sm"
			href="/sample-generator?from_job={job.id}"
			title="New batch with this run's task, model, stack and presets (new seed)"
		>
			<RefreshCw class="h-3.5 w-3.5" />Re-generate
		</Button>
	{/snippet}
	<div class="flex flex-wrap items-center gap-2 pt-1">
		<Badge variant="outline">{provider}</Badge>
		{#if job.template_name}<Badge variant="outline">{job.template_name}</Badge>{/if}
		{#if job.stack_slug}<Badge variant="outline" class="font-mono">{job.stack_slug}</Badge>{/if}
		{#if job.batch_id}
			<a href={batchHref(job.batch_id)} class="rounded-full transition-colors hover:text-primary" title="Open the batch">
				<Badge variant="outline" class="hover:border-primary/50"><GitBranch class="h-3 w-3" />{job.batch_name || 'Batch'}</Badge>
			</a>
		{/if}
		<span class="truncate font-mono text-xs text-muted-foreground">{job.model_id_str} · {job.id.substring(0, 8)}</span>
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
		</div>
	{/each}
</div>
