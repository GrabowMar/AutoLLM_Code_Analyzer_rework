<script lang="ts">
import * as Card from '$lib/components/ui/card';
import ProviderIcon from '$lib/components/ProviderIcon.svelte';
import { shortModelName } from '$lib/components/table';
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

// Fix counts only exist for the legacy copilot loop; other modes have nothing to show.
const showFixes = $derived(job.mode === 'copilot');
</script>

<section id="overview" class="grid grid-cols-1 gap-4 {showFixes ? 'lg:grid-cols-3' : 'md:grid-cols-2'}">
	<!-- Run summary: identity + lifecycle as one description list -->
	<Card.Root>
		<Card.Header class="pb-0"><Card.Title class="text-base">Run</Card.Title></Card.Header>
		<Card.Content>
			<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2.5 text-sm">
				<dt class="text-muted-foreground">Model</dt>
				<dd class="min-w-0">
					<span class="flex items-center gap-1.5" title={job.model_name ?? undefined}>
						{#if provider && provider !== '—'}<ProviderIcon {provider} size={14} class="shrink-0" />{/if}
						<span class="break-words font-medium">{shortModelName(job.model_name) || '—'}</span>
					</span>
					{#if job.model_id_str}<span class="block truncate font-mono text-[11px] text-muted-foreground" title={job.model_id_str}>{job.model_id_str}</span>{/if}
				</dd>
				<dt class="text-muted-foreground">Mode</dt>
				<dd class="capitalize">{job.mode}{#if job.agent_config?.harness}<span class="normal-case text-muted-foreground"> · <span class="font-mono">{job.agent_config.harness}</span></span>{/if}</dd>
				<dt class="text-muted-foreground">Status</dt>
				<dd><StatusBadge status={jobDisplayStatus(job)} tones={jobPhaseTones} /></dd>
				<dt class="text-muted-foreground">Created</dt>
				<dd class="font-mono text-xs tabular-nums">{fmtDateCompact(job.created_at)}</dd>
				<dt class="text-muted-foreground">{job.completed_at ? 'Finished' : 'Updated'}</dt>
				<dd class="font-mono text-xs tabular-nums">{fmtDateCompact(job.completed_at ?? job.updated_at)}</dd>
				<dt class="text-muted-foreground">Duration</dt>
				<dd class="font-mono text-xs tabular-nums">{fmtDur(job.duration_seconds)}</dd>
			</dl>
		</Card.Content>
	</Card.Root>

	<!-- Code footprint: totals + language share -->
	<Card.Root>
		<Card.Header class="pb-0"><Card.Title class="text-base">Code footprint</Card.Title></Card.Header>
		<Card.Content>
			{#if codeFootprint && codeFootprint.totalLines > 0}
				{@const langs = Object.entries(codeFootprint.languages).sort((x, y) => y[1] - x[1])}
				{@const langTotal = langs.reduce((n, [, c]) => n + c, 0) || 1}
				<div class="flex items-baseline gap-6">
					<div>
						<div class="font-mono text-2xl font-medium tabular-nums">{fmt(codeFootprint.totalLines, 0)}</div>
						<div class="section-label">Lines</div>
					</div>
					<div>
						<div class="font-mono text-2xl font-medium tabular-nums">{codeFootprint.fileCount}</div>
						<div class="section-label">Files</div>
					</div>
				</div>
				{#if langs.length}
					<div class="mt-4 flex h-2 w-full gap-px overflow-hidden rounded-full bg-muted" role="img" aria-label="Lines by language: {langs.map(([l, c]) => `${l} ${c}`).join(', ')}">
						{#each langs as [lang, count], i (lang)}
							<span class="h-full" style:width="{(count / langTotal) * 100}%" style:background-color="var(--chart-{(i % 5) + 1})" title="{lang}: {count} lines"></span>
						{/each}
					</div>
					<ul class="mt-2.5 space-y-1 text-xs">
						{#each langs as [lang, count], i (lang)}
							<li class="flex items-center gap-2">
								<span class="size-2 shrink-0 rounded-sm" style:background-color="var(--chart-{(i % 5) + 1})" aria-hidden="true"></span>
								<span class="text-foreground">{lang}</span>
								<span class="ml-auto font-mono tabular-nums text-muted-foreground">{fmt(count, 0)}</span>
								<span class="w-10 text-right font-mono tabular-nums text-muted-foreground">{Math.round((count / langTotal) * 100)}%</span>
							</li>
						{/each}
					</ul>
				{/if}
				{#if codeFootprint.truncated}
					<p class="mt-2 text-xs text-warning-fg">Output truncated</p>
				{/if}
			{:else}
				<p class="text-sm text-muted-foreground">No code generated</p>
			{/if}
		</Card.Content>
	</Card.Root>

	{#if showFixes}
	<!-- Fixes -->
	<Card.Root>
		<Card.Header class="pb-0"><Card.Title class="flex items-center gap-1.5 text-base"><Wrench class="size-4 text-muted-foreground" aria-hidden="true" />Fixes applied</Card.Title></Card.Header>
		<Card.Content>
			{#if job.mode === 'copilot' && fixesData.total > 0}
				<div class="font-mono text-2xl font-medium tabular-nums">{fixesData.total}</div>
				<dl class="mt-3 grid grid-cols-[1fr_auto] gap-y-1.5 text-sm">
					<dt class="text-muted-foreground">Retry</dt><dd class="font-mono tabular-nums">{fixesData.retry}</dd>
					<dt class="text-muted-foreground">Auto-fix</dt><dd class="font-mono tabular-nums">{fixesData.autofix}</dd>
					<dt class="text-muted-foreground">LLM</dt><dd class="font-mono tabular-nums">{fixesData.llm}</dd>
				</dl>
			{:else if job.mode === 'copilot'}
				<div class="flex items-center gap-2 text-success-fg">
					<CircleCheck class="size-5" aria-hidden="true" />
					<span class="text-sm font-medium">No fixes needed</span>
				</div>
			{:else}
				<p class="text-sm text-muted-foreground">Not tracked for {job.mode} runs — fix counts come from the legacy copilot loop.</p>
			{/if}
		</Card.Content>
	</Card.Root>
	{/if}
</section>
