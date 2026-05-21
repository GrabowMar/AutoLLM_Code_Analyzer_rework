<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import Eye from '@lucide/svelte/icons/eye';
import Wrench from '@lucide/svelte/icons/wrench';
import CircleCheck from '@lucide/svelte/icons/circle-check';
import Zap from '@lucide/svelte/icons/zap';
import type { GenerationJob } from '$lib/api/client';
import {
	statusColors,
	modeColors,
	fmt,
	fmtDur,
	fmtDateCompact,
	type CodeFootprint,
} from './utils';

interface FixesData { total: number; retry: number; autofix: number; llm: number }
interface FrameworkInfo { backend: string; frontend: string; database: string }
interface CostData { totalTokens: number }

interface Props {
	job: GenerationJob;
	provider: string;
	codeFootprint: CodeFootprint | null;
	fixesData: FixesData;
	frameworkInfo: FrameworkInfo;
	costData: CostData;
}

let { job, provider, codeFootprint, fixesData, frameworkInfo, costData }: Props = $props();
</script>

<section id="overview" class="space-y-4">
	<h2 class="text-lg font-semibold flex items-center gap-2"><Eye class="h-5 w-5" /> Overview</h2>

	<!-- 4-Card Grid -->
	<div class="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
		<!-- Card 1: Identity -->
		<Card.Root>
			<Card.Header class="pb-2"><Card.Title class="text-sm font-medium">Identity</Card.Title></Card.Header>
			<Card.Content class="space-y-2 text-sm">
				<div class="flex justify-between"><span class="text-muted-foreground">Model</span><span class="font-medium truncate max-w-[140px]" title={job.model_name ?? '—'}>{job.model_name ?? '—'}</span></div>
				<div class="flex justify-between"><span class="text-muted-foreground">Provider</span><Badge variant="outline" class="text-xs">{provider}</Badge></div>
				<div class="flex justify-between"><span class="text-muted-foreground">Application</span><span class="font-mono text-xs">{job.id.substring(0, 8)}</span></div>
				<div class="flex justify-between"><span class="text-muted-foreground">Mode</span><Badge variant="outline" class="text-xs {modeColors[job.mode] ?? ''}">{job.mode}</Badge></div>
			</Card.Content>
		</Card.Root>

		<!-- Card 2: Lifecycle -->
		<Card.Root>
			<Card.Header class="pb-2"><Card.Title class="text-sm font-medium">Lifecycle</Card.Title></Card.Header>
			<Card.Content class="space-y-2 text-sm">
				<div class="flex justify-between items-center">
					<span class="text-muted-foreground">Status</span>
					<Badge variant="outline" class="text-xs {statusColors[job.status] ?? ''}">
						<span class="mr-1.5 h-1.5 w-1.5 rounded-full inline-block {job.status === 'completed' ? 'bg-emerald-500' : job.status === 'failed' ? 'bg-red-500' : job.status === 'running' ? 'bg-amber-500 animate-pulse' : 'bg-zinc-500'}"></span>
						{job.status}
					</Badge>
				</div>
				<div class="flex justify-between items-center">
					<span class="text-muted-foreground">Generation</span>
					<Badge variant="outline" class="text-xs {job.status === 'completed' ? 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30' : job.status === 'failed' ? 'bg-red-500/15 text-red-400 border-red-500/30' : 'bg-amber-500/15 text-amber-500 border-amber-500/30'}">
						{job.status === 'completed' ? 'Completed' : job.status === 'failed' ? 'Failed' : job.status === 'running' ? 'Running' : 'Pending'}
					</Badge>
				</div>
				<div class="flex justify-between"><span class="text-muted-foreground">Created</span><span class="text-xs">{fmtDateCompact(job.created_at)}</span></div>
				<div class="flex justify-between"><span class="text-muted-foreground">Duration</span><span class="font-semibold">{fmtDur(job.duration_seconds)}</span></div>
			</Card.Content>
		</Card.Root>

		<!-- Card 3: Code Footprint -->
		<Card.Root>
			<Card.Header class="pb-2"><Card.Title class="text-sm font-medium">Code Footprint</Card.Title></Card.Header>
			<Card.Content>
				{#if codeFootprint && codeFootprint.totalLines > 0}
					<div class="flex items-center justify-around mb-3">
						<div class="text-center">
							<div class="text-2xl font-bold text-blue-400">{fmt(codeFootprint.totalLines, 0)}</div>
							<div class="text-[10px] text-muted-foreground uppercase">Lines</div>
						</div>
						<div class="text-center">
							<div class="text-2xl font-bold text-emerald-400">{codeFootprint.fileCount}</div>
							<div class="text-[10px] text-muted-foreground uppercase">Files</div>
						</div>
					</div>
					<div class="flex flex-wrap gap-1">
						{#each Object.entries(codeFootprint.languages) as [lang, count]}
							<Badge variant="outline" class="text-[10px]">{lang}: {count}</Badge>
						{/each}
					</div>
					{#if codeFootprint.truncated}
						<p class="text-[10px] text-amber-400 mt-1">⚠ Truncated</p>
					{/if}
				{:else}
					<p class="text-sm text-muted-foreground">No code generated</p>
				{/if}
			</Card.Content>
		</Card.Root>

		<!-- Card 4: Fixes Applied -->
		<Card.Root>
			<Card.Header class="pb-2"><Card.Title class="text-sm font-medium flex items-center gap-1.5"><Wrench class="h-3.5 w-3.5" /> Fixes Applied</Card.Title></Card.Header>
			<Card.Content>
				{#if job.mode === 'copilot' && fixesData.total > 0}
					<div class="text-2xl font-bold text-amber-400 mb-2">{fixesData.total}</div>
					<div class="space-y-1.5 text-sm">
						<div class="flex justify-between"><span class="text-muted-foreground">Retry</span><Badge variant="outline" class="text-xs">{fixesData.retry}</Badge></div>
						<div class="flex justify-between"><span class="text-muted-foreground">Auto-fix</span><Badge variant="outline" class="text-xs">{fixesData.autofix}</Badge></div>
						<div class="flex justify-between"><span class="text-muted-foreground">LLM</span><Badge variant="outline" class="text-xs">{fixesData.llm}</Badge></div>
					</div>
				{:else}
					<div class="flex items-center gap-2 text-emerald-500">
						<CircleCheck class="h-5 w-5" />
						<span class="text-sm font-medium">No fixes needed</span>
					</div>
				{/if}
			</Card.Content>
		</Card.Root>
	</div>

	<!-- Highlights Row -->
	<div class="flex items-center gap-2 flex-wrap text-sm">
		{#if job.batch_name}
			<Badge variant="outline" class="text-xs bg-amber-500/10 text-amber-400 border-amber-500/30">
				<Zap class="h-3 w-3 mr-1" />{job.batch_name}
			</Badge>
		{/if}
		{#if job.template_name}
			<Badge variant="outline" class="text-xs bg-sky-500/10 text-sky-400 border-sky-500/30">📋 {job.template_name}</Badge>
		{/if}
		{#if job.scaffolding_name}
			<Badge variant="outline" class="text-xs bg-purple-500/10 text-purple-400 border-purple-500/30">🏗️ {job.scaffolding_name}</Badge>
		{/if}
		{#if job.batch_name}
			<Badge variant="outline" class="text-xs">Batch run</Badge>
		{/if}
		{#if job.updated_at}
			<span class="text-xs text-muted-foreground ml-auto">Updated {fmtDateCompact(job.updated_at)}</span>
		{/if}
	</div>

	<!-- Generation Details -->
	<Card.Root>
		<Card.Header class="pb-2"><Card.Title class="text-sm font-medium">Generation Details</Card.Title></Card.Header>
		<Card.Content>
			<div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4 text-sm">
				<div><span class="text-xs text-muted-foreground block mb-0.5">Backend</span><span class="font-medium">{frameworkInfo.backend}</span></div>
				<div><span class="text-xs text-muted-foreground block mb-0.5">Frontend</span><span class="font-medium">{frameworkInfo.frontend}</span></div>
				<div><span class="text-xs text-muted-foreground block mb-0.5">Database</span><span class="font-medium">{frameworkInfo.database}</span></div>
				<div><span class="text-xs text-muted-foreground block mb-0.5">Duration</span><span class="font-medium">{fmtDur(job.duration_seconds)}</span></div>
			</div>
			<div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4 text-sm mt-3 pt-3 border-t">
				<div><span class="text-xs text-muted-foreground block mb-0.5">Template</span><span class="font-medium">{job.template_name ?? '—'}</span></div>
				<div><span class="text-xs text-muted-foreground block mb-0.5">Tokens</span><span class="font-medium">{fmt(costData.totalTokens, 0)}</span></div>
				<div><span class="text-xs text-muted-foreground block mb-0.5">Updated</span><span class="font-medium">{fmtDateCompact(job.updated_at)}</span></div>
				<div><span class="text-xs text-muted-foreground block mb-0.5">Type</span><Badge variant="outline" class="text-xs {modeColors[job.mode] ?? ''}">{job.mode}</Badge></div>
			</div>
		</Card.Content>
	</Card.Root>
</section>
