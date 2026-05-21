<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import ArrowLeft from '@lucide/svelte/icons/arrow-left';
import Copy from '@lucide/svelte/icons/copy';
import Download from '@lucide/svelte/icons/download';
import Layers from '@lucide/svelte/icons/layers';
import Bot from '@lucide/svelte/icons/bot';
import Pencil from '@lucide/svelte/icons/pencil';
import FileCode from '@lucide/svelte/icons/file-code';
import AlertTriangle from '@lucide/svelte/icons/alert-triangle';
import GitBranch from '@lucide/svelte/icons/git-branch';
import RefreshCw from '@lucide/svelte/icons/refresh-cw';
import Ban from '@lucide/svelte/icons/ban';
import Trash2 from '@lucide/svelte/icons/trash-2';
import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
import type { GenerationJob } from '$lib/api/client';
import { statusColors, modeColors, fmt, fmtDur, fmtCost, copyText, type CodeFootprint } from './utils';
import AppRuntimeControls from './AppRuntimeControls.svelte';

interface Section { id: string; label: string; icon: any }

interface Props {
	job: GenerationJob;
	provider: string;
	codeFootprint: CodeFootprint | null;
	totalTokens: number;
	totalCost: number;
	sections: Section[];
	activeSection: string;
	onCancel: () => void;
	onRetry: () => void;
	onExport: () => void;
	onDownloadCode: () => void;
	onDelete: () => void;
	onNavigate: (id: string) => void;
}

let {
	job,
	provider,
	codeFootprint,
	totalTokens,
	totalCost,
	sections,
	activeSection,
	onCancel,
	onRetry,
	onExport,
	onDownloadCode,
	onDelete,
	onNavigate,
}: Props = $props();

const modeIcons: Record<string, any> = { custom: Pencil, scaffolding: Layers, copilot: Bot };
</script>

<!-- Breadcrumb -->
<nav aria-label="Breadcrumb" class="flex items-center gap-2 text-sm text-muted-foreground">
	<a href="/applications" class="hover:text-foreground transition-colors flex items-center gap-1">
		<ArrowLeft class="h-3.5 w-3.5" />
		<span class="font-medium text-foreground">Applications</span>
	</a>
	<span>/</span>
	<span class="text-muted-foreground truncate max-w-[300px]">{job.model_name ?? job.model_id_str ?? 'Unknown'}</span>
</nav>

<!-- Failed banner -->
{#if job.status === 'failed'}
	<div class="flex items-center gap-3 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3">
		<AlertTriangle class="h-5 w-5 text-red-400 shrink-0" />
		<div class="flex-1">
			<p class="text-sm font-medium text-red-400">Generation Failed</p>
			<p class="text-xs text-red-400/70 truncate">{job.error_message || 'Unknown error'}</p>
		</div>
		<Button variant="outline" size="sm" href="/applications/{job.id}/failure" class="border-red-500/30 text-red-400 hover:bg-red-500/10">
			View Details
		</Button>
	</div>
{/if}

<!-- Header Card -->
<Card.Root>
	<Card.Content class="p-5">
		<div class="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
			<div class="flex items-start gap-4">
				<div class="flex h-12 w-12 items-center justify-center rounded-md {modeColors[job.mode] ?? 'bg-zinc-500/15'}">
					{#if modeIcons[job.mode]}
						{@const Icon = modeIcons[job.mode]}
						<Icon class="h-6 w-6" />
					{/if}
				</div>
				<div>
					<h1 class="text-xl font-semibold">{job.model_name ?? 'Unknown Model'}</h1>
					<div class="flex flex-wrap items-center gap-2 mt-1.5">
						<Badge variant="outline" class="text-xs {modeColors[job.mode] ?? ''}">{job.mode}</Badge>
						<Badge variant="outline" class="text-xs {statusColors[job.status] ?? ''}">
							{#if job.status === 'running'}<span class="mr-1 h-1.5 w-1.5 rounded-full bg-amber-500 animate-pulse inline-block"></span>{/if}
							{job.status}
						</Badge>
						<Badge variant="outline" class="text-xs hidden sm:inline-flex">{provider}</Badge>
						{#if job.template_name}
							<Badge variant="outline" class="text-xs hidden sm:inline-flex">{job.template_name}</Badge>
						{/if}
						{#if job.scaffolding_name}
							<Badge variant="outline" class="text-xs hidden sm:inline-flex bg-purple-500/10 text-purple-400 border-purple-500/30">{job.scaffolding_name}</Badge>
						{/if}
						{#if job.batch_name}
							<Badge variant="outline" class="text-xs hidden sm:inline-flex">
								<GitBranch class="h-3 w-3 mr-1" />{job.batch_name}
							</Badge>
						{/if}
					</div>
					<p class="text-xs text-muted-foreground mt-1 font-mono truncate max-w-full">{job.model_id_str} · {job.id.substring(0, 8)}</p>
				</div>
			</div>
			<!-- Action buttons -->
			<div class="flex flex-wrap items-center gap-2 sm:shrink-0">
				<AppRuntimeControls jobId={job.id} jobStatus={job.status} />
				{#if job.status === 'pending' || job.status === 'running'}
					<Button variant="outline" size="sm" onclick={onCancel} title="Cancel job" class="border-amber-500/30 text-amber-400 hover:bg-amber-500/10">
						<Ban class="h-3.5 w-3.5 sm:mr-1.5" /><span class="hidden sm:inline">Cancel</span>
					</Button>
				{/if}
				{#if job.status === 'failed' || job.status === 'cancelled'}
					<Button variant="outline" size="sm" onclick={onRetry} title="Retry job" class="border-blue-500/30 text-blue-400 hover:bg-blue-500/10">
						<RotateCcw class="h-3.5 w-3.5 sm:mr-1.5" /><span class="hidden sm:inline">Retry</span>
					</Button>
				{/if}
				<Button variant="outline" size="sm" onclick={() => copyText(job.id, 'Copied Job ID')} title="Copy Job ID">
					<Copy class="h-3.5 w-3.5 sm:mr-1.5" /><span class="hidden sm:inline">ID</span>
				</Button>
				<Button variant="outline" size="sm" onclick={onExport} title="Export as JSON">
					<Download class="h-3.5 w-3.5 sm:mr-1.5" /><span class="hidden sm:inline">Export</span>
				</Button>
				<Button variant="outline" size="sm" onclick={onDownloadCode} title="Download generated code">
					<FileCode class="h-3.5 w-3.5 sm:mr-1.5" /><span class="hidden sm:inline">Code</span>
				</Button>
				<Button size="sm" href="/sample-generator" title="Generate with same settings">
					<RefreshCw class="h-3.5 w-3.5 sm:mr-1.5" /><span class="hidden sm:inline">Re-generate</span>
				</Button>
				{#if job.status !== 'running'}
					<Button variant="outline" size="sm" onclick={onDelete} title="Delete job" class="border-red-500/30 text-red-400 hover:bg-red-500/10">
						<Trash2 class="h-3.5 w-3.5 sm:mr-1.5" /><span class="hidden sm:inline">Delete</span>
					</Button>
				{/if}
			</div>
		</div>
	</Card.Content>
</Card.Root>

<!-- KPI Row -->
<div class="grid grid-cols-3 sm:grid-cols-3 md:grid-cols-6 gap-2 sm:gap-3">
	<div class="rounded-lg border bg-card p-2 sm:p-3 text-center">
		<div class="text-[10px] sm:text-xs text-muted-foreground mb-0.5 sm:mb-1">Duration</div>
		<div class="text-sm sm:text-lg font-semibold">{fmtDur(job.duration_seconds)}</div>
	</div>
	<div class="rounded-lg border bg-card p-2 sm:p-3 text-center">
		<div class="text-[10px] sm:text-xs text-muted-foreground mb-0.5 sm:mb-1">Tokens</div>
		<div class="text-sm sm:text-lg font-semibold">{fmt(totalTokens, 0)}</div>
	</div>
	<div class="rounded-lg border bg-card p-2 sm:p-3 text-center">
		<div class="text-[10px] sm:text-xs text-muted-foreground mb-0.5 sm:mb-1">Cost</div>
		<div class="text-sm sm:text-lg font-semibold">{fmtCost(totalCost)}</div>
	</div>
	<div class="rounded-lg border bg-card p-2 sm:p-3 text-center">
		<div class="text-[10px] sm:text-xs text-muted-foreground mb-0.5 sm:mb-1">Temperature</div>
		<div class="text-sm sm:text-lg font-semibold">{job.temperature}</div>
	</div>
	<div class="rounded-lg border bg-card p-2 sm:p-3 text-center">
		<div class="text-[10px] sm:text-xs text-muted-foreground mb-0.5 sm:mb-1">Max Tokens</div>
		<div class="text-sm sm:text-lg font-semibold">{fmt(job.max_tokens, 0)}</div>
	</div>
	<div class="rounded-lg border bg-card p-2 sm:p-3 text-center">
		<div class="text-[10px] sm:text-xs text-muted-foreground mb-0.5 sm:mb-1">Lines of Code</div>
		<div class="text-sm sm:text-lg font-semibold">{fmt(codeFootprint?.totalLines ?? 0, 0)}</div>
	</div>
</div>

<!-- Sticky Section Nav -->
<div class="sticky top-0 z-20 -mx-1 px-1 py-2 bg-background/95 backdrop-blur border-b">
	<div class="flex items-center gap-1 overflow-x-auto">
		{#each sections as sec}
			{@const SecIcon = sec.icon}
			<button
				class="flex items-center gap-1.5 px-3 py-2.5 sm:py-1.5 rounded-md text-xs font-medium transition-colors whitespace-nowrap {activeSection === sec.id ? 'bg-primary/10 text-primary border border-primary/30' : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'}"
				onclick={() => onNavigate(sec.id)}
			>
				<SecIcon class="h-3.5 w-3.5" />
				{sec.label}
			</button>
		{/each}
	</div>
</div>
