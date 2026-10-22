<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import StatusBadge from '$lib/components/StatusBadge.svelte';
import Wrench from '@lucide/svelte/icons/wrench';
import CircleCheck from '@lucide/svelte/icons/circle-check';
import type { GenerationJob } from '$lib/api/client';
import { jobDisplayStatus, jobPhaseTones } from '$lib/components/generator/batches';
import {
	fmt,
	fmtDur,
	fmtDateCompact,
	type CodeFootprint,
} from './utils';

interface FixesData { total: number; retry: number; autofix: number; llm: number }

interface Props {
	job: GenerationJob;
	provider: string;
	codeFootprint: CodeFootprint | null;
	fixesData: FixesData;
}

let { job, provider, codeFootprint, fixesData }: Props = $props();
</script>

<section id="overview" class="space-y-4">
	<div class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
		<!-- Card 1: Identity -->
		<Card.Root>
			<Card.Header class="pb-2"><Card.Title class="text-sm font-medium">Identity</Card.Title></Card.Header>
			<Card.Content class="space-y-2 text-sm">
				<div class="flex justify-between"><span class="text-muted-foreground">Model</span><span class="font-medium truncate max-w-[140px]" title={job.model_name ?? '—'}>{job.model_name ?? '—'}</span></div>
				<div class="flex justify-between"><span class="text-muted-foreground">Provider</span><Badge variant="outline" class="text-xs">{provider}</Badge></div>
				<div class="flex justify-between"><span class="text-muted-foreground">Application</span><span class="font-mono text-xs">{job.id.substring(0, 8)}</span></div>
				<div class="flex justify-between"><span class="text-muted-foreground">Mode</span><Badge variant="outline" class="text-xs capitalize">{job.mode}</Badge></div>
			</Card.Content>
		</Card.Root>

		<!-- Card 2: Lifecycle -->
		<Card.Root>
			<Card.Header class="pb-2"><Card.Title class="text-sm font-medium">Lifecycle</Card.Title></Card.Header>
			<Card.Content class="space-y-2 text-sm">
				<div class="flex justify-between items-center">
					<span class="text-muted-foreground">Status</span>
					<StatusBadge status={jobDisplayStatus(job)} tones={jobPhaseTones} />
				</div>
				<div class="flex justify-between"><span class="text-muted-foreground">Created</span><span class="text-xs">{fmtDateCompact(job.created_at)}</span></div>
				<div class="flex justify-between"><span class="text-muted-foreground">Updated</span><span class="text-xs">{fmtDateCompact(job.updated_at)}</span></div>
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
							<div class="text-2xl font-bold font-display tabular-nums">{fmt(codeFootprint.totalLines, 0)}</div>
							<div class="section-label">Lines</div>
						</div>
						<div class="text-center">
							<div class="text-2xl font-bold font-display tabular-nums">{codeFootprint.fileCount}</div>
							<div class="section-label">Files</div>
						</div>
					</div>
					<div class="flex flex-wrap gap-1">
						{#each Object.entries(codeFootprint.languages) as [lang, count]}
							<Badge variant="outline" class="text-[11px]">{lang}: {count}</Badge>
						{/each}
					</div>
					{#if codeFootprint.truncated}
						<p class="mt-1 text-xs text-warning-fg">Output truncated</p>
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
					<div class="text-2xl font-bold font-display tabular-nums mb-2">{fixesData.total}</div>
					<div class="space-y-1.5 text-sm">
						<div class="flex justify-between"><span class="text-muted-foreground">Retry</span><Badge variant="outline" class="text-xs">{fixesData.retry}</Badge></div>
						<div class="flex justify-between"><span class="text-muted-foreground">Auto-fix</span><Badge variant="outline" class="text-xs">{fixesData.autofix}</Badge></div>
						<div class="flex justify-between"><span class="text-muted-foreground">LLM</span><Badge variant="outline" class="text-xs">{fixesData.llm}</Badge></div>
					</div>
				{:else}
					<div class="flex items-center gap-2 text-success-fg">
						<CircleCheck class="h-5 w-5" />
						<span class="text-sm font-medium">No fixes needed</span>
					</div>
				{/if}
			</Card.Content>
		</Card.Root>
	</div>
</section>
