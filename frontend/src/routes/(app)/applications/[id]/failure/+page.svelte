<script lang="ts">
import { formatDateTime as fmtDate, formatNumber as fmt } from '$lib/utils/formatters';
import { page } from '$app/state';
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import {
getGenerationJob,
getJobArtifacts,
getCopilotIterations,
type GenerationJob,
type GenerationArtifact,
type CopilotIteration,
} from '$lib/api/client';
import { onMount } from 'svelte';
import { toast } from 'svelte-sonner';

import Copy from '@lucide/svelte/icons/copy';
import AlertTriangle from '@lucide/svelte/icons/alert-triangle';
import CircleCheck from '@lucide/svelte/icons/circle-check';
import CircleX from '@lucide/svelte/icons/circle-x';
import Clock from '@lucide/svelte/icons/clock';
import RefreshCw from '@lucide/svelte/icons/refresh-cw';
import Database from '@lucide/svelte/icons/database';
import PageHeader from '$lib/components/PageHeader.svelte';
import StatusBadge from '$lib/components/StatusBadge.svelte';
import LoadingState from '$lib/components/LoadingState.svelte';
import EmptyState from '$lib/components/EmptyState.svelte';
import { copyText } from '$lib/components/applications/utils';

const jobId = $derived(page.params.id ?? '');
let loading = $state(true);
let job = $state<GenerationJob | null>(null);
let artifacts = $state<GenerationArtifact[]>([]);
let iterations = $state<CopilotIteration[]>([]);

const failedIterations = $derived(iterations.filter((it) => !it.build_success));
const allErrors = $derived.by(() => {
const errors: string[] = [];
for (const it of iterations) {
if (it.errors_detected) errors.push(...it.errors_detected);
}
return errors;
});


async function fetchData() {
try {
const [j, arts] = await Promise.all([
getGenerationJob(jobId),
getJobArtifacts(jobId),
]);
job = j;
artifacts = arts;
if (j.mode === 'copilot') {
iterations = await getCopilotIterations(jobId);
}
} catch {
toast.error('Failed to load job details');
} finally {
loading = false;
}
}

onMount(fetchData);
</script>

{#if loading}
<LoadingState label="loading failure details" />
{:else if !job}
<div class="space-y-6">
<PageHeader title="Failure Details" back={{ href: '/applications', label: 'Applications' }} />
<EmptyState icon={AlertTriangle} title="Job not found" description="It may have been deleted, or the link is wrong.">
{#snippet action()}
<Button variant="outline" size="sm" href="/applications">Back to Applications</Button>
{/snippet}
</EmptyState>
</div>
{:else}
<div class="space-y-6">
<PageHeader
title="Failure Details"
description="{job.model_name ?? 'Job'} — generation failed"
back={{ href: `/applications/${job.id}`, label: job.model_name ?? 'Job' }}
>
{#snippet meta()}<StatusBadge status={job!.status} />{/snippet}
{#snippet actions()}
<Button variant="outline" size="sm" onclick={() => copyText(job!.error_message || 'No error', 'Copied error')}>
<Copy class="h-3.5 w-3.5" />Copy error
</Button>
<Button variant="outline" size="sm" href="/applications/{job!.id}">View Full Detail</Button>
<Button size="sm" href="/sample-generator?from_job={job!.id}" title="New batch with this run's task, model, stack and presets">
<RefreshCw class="h-3.5 w-3.5" />Re-generate
</Button>
{/snippet}
</PageHeader>

<div class="grid lg:grid-cols-3 gap-4">
<!-- Left Column (2/3) -->
<div class="lg:col-span-2 space-y-4">
<!-- Quick Info -->
<Card.Root>
<Card.Content class="pt-6">
<dl class="grid grid-cols-2 sm:grid-cols-4 gap-4">
<div>
<dt class="section-label">Model</dt>
<dd class="text-sm font-medium mt-0.5">{job.model_name ?? '—'}</dd>
</div>
<div>
<dt class="section-label">Mode</dt>
<dd class="mt-0.5"><Badge variant="outline" class="text-xs capitalize">{job.mode}</Badge></dd>
</div>
<div>
<dt class="section-label">Temperature</dt>
<dd class="text-sm font-medium mt-0.5">{job.temperature}</dd>
</div>
<div>
<dt class="section-label">Max Tokens</dt>
<dd class="text-sm font-medium mt-0.5">{fmt(job.max_tokens)}</dd>
</div>
</dl>
</Card.Content>
</Card.Root>

<!-- Error Message -->
<Card.Root>
<Card.Header class="pb-3">
<Card.Title class="text-sm font-medium flex items-center gap-2">
<AlertTriangle class="h-4 w-4 text-destructive" />
Error Message
</Card.Title>
</Card.Header>
<Card.Content>
{#if job.error_message}
<div class="relative">
<pre class="text-xs font-mono bg-destructive/10 border border-destructive/30 rounded-lg p-4 pr-12 whitespace-pre-wrap break-words max-h-64 overflow-auto text-danger-fg">{job.error_message}</pre>
<Button variant="ghost" size="sm" class="absolute top-2 right-2 h-7" title="Copy error" aria-label="Copy error" onclick={() => copyText(job!.error_message, 'Copied error')}>
<Copy class="h-3.5 w-3.5" />
</Button>
</div>
{#if job.result_data?.error_status_code === 401 || /OpenRouter.*key/i.test(job.error_message)}
<div class="mt-3 flex flex-wrap items-center gap-3 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning-fg">
<span>This job failed because OpenRouter rejected the API key.</span>
<Button variant="outline" size="sm" href="/users/settings#api">
Open API Access settings
</Button>
</div>
{/if}
{:else}
<EmptyState compact title="No error message recorded" />
{/if}
</Card.Content>
</Card.Root>

<!-- Generation Errors List (from copilot iterations) -->
{#if allErrors.length > 0}
<Card.Root>
<Card.Header class="pb-3">
<Card.Title class="text-sm font-medium flex items-center gap-2">
Generation Errors
<Badge variant="outline" class="text-xs border-destructive/30 bg-destructive/10 text-danger-fg">{allErrors.length}</Badge>
</Card.Title>
</Card.Header>
<Card.Content>
<ol class="space-y-2">
{#each allErrors as err, i}
<li class="flex items-start gap-2 text-sm">
<span class="shrink-0 text-xs font-bold text-danger-fg bg-destructive/15 rounded px-1.5 py-0.5">{i + 1}</span>
<span class="text-danger-fg">{err}</span>
</li>
{/each}
</ol>
</Card.Content>
</Card.Root>
{/if}

<!-- Failed Iterations -->
{#if failedIterations.length > 0}
<Card.Root>
<Card.Header class="pb-3">
<Card.Title class="text-sm font-medium flex items-center gap-2">
Failed Iterations
<Badge variant="outline" class="text-xs border-destructive/30 bg-destructive/10 text-danger-fg">{failedIterations.length}</Badge>
</Card.Title>
</Card.Header>
<Card.Content class="space-y-4">
{#each failedIterations as it}
<div class="border border-destructive/30 rounded-lg p-3 bg-destructive/5">
<div class="flex items-center gap-2 mb-2">
<Badge variant="outline" class="text-xs">Iteration {it.iteration_number}</Badge>
<Badge variant="outline" class="text-xs">{it.action}</Badge>
<StatusBadge status={null} tone="danger" label="Build failed" />
</div>
{#if it.errors_detected && it.errors_detected.length > 0}
<div class="mb-2">
<div class="section-label mb-1">Errors</div>
<ul class="space-y-1">
{#each it.errors_detected as err}
<li class="flex items-start gap-1.5 text-xs text-danger-fg">
<CircleX class="h-3 w-3 shrink-0 mt-0.5" />{err}
</li>
{/each}
</ul>
</div>
{/if}
{#if it.build_output}
<div>
<div class="section-label mb-1">Build Output</div>
<pre class="text-xs font-mono bg-muted/40 rounded p-2 max-h-32 overflow-auto text-foreground/90">{it.build_output}</pre>
</div>
{/if}
</div>
{/each}
</Card.Content>
</Card.Root>
{/if}

<!-- Request Artifacts -->
{#if artifacts.length > 0}
<Card.Root>
<Card.Header class="pb-3">
<Card.Title class="text-sm font-medium flex items-center gap-2">
<Database class="h-4 w-4" />
Request Artifacts
<Badge variant="outline" class="text-xs">{artifacts.length}</Badge>
</Card.Title>
</Card.Header>
<Card.Content class="p-0">
<div class="overflow-x-auto">
<table class="w-full text-sm">
<thead>
<tr>
<th>Stage</th>
<th>Model</th>
<th>Prompt</th>
<th>Completion</th>
</tr>
</thead>
<tbody>
{#each artifacts as art}
<tr>
<td><Badge variant="outline" class="text-xs">{art.stage}</Badge></td>
<td class="font-mono text-xs">{art.request_payload?.model ?? '—'}</td>
<td>{fmt(art.prompt_tokens)}</td>
<td>{fmt(art.completion_tokens)}</td>
</tr>
{/each}
</tbody>
</table>
</div>
</Card.Content>
</Card.Root>
{/if}
</div>

<!-- Right Column (1/3) -->
<div class="space-y-4">
<!-- Timeline -->
<Card.Root>
<Card.Header class="pb-3"><Card.Title class="text-sm font-medium">Timeline</Card.Title></Card.Header>
<Card.Content>
<div class="space-y-4">
<div class="flex items-start gap-3">
<div class="mt-0.5 flex h-6 w-6 items-center justify-center rounded-full bg-success/15">
<CircleCheck class="h-3.5 w-3.5 text-success-fg" />
</div>
<div>
<p class="text-sm font-medium">Job Created</p>
<p class="text-xs text-muted-foreground">{fmtDate(job.created_at)}</p>
</div>
</div>
<div class="flex items-start gap-3">
{#if job.started_at}
<div class="mt-0.5 flex h-6 w-6 items-center justify-center rounded-full bg-success/15">
<CircleCheck class="h-3.5 w-3.5 text-success-fg" />
</div>
{:else}
<div class="mt-0.5 flex h-6 w-6 items-center justify-center rounded-full bg-muted">
<Clock class="h-3.5 w-3.5 text-muted-foreground" />
</div>
{/if}
<div>
<p class="text-sm font-medium">Generation Started</p>
<p class="text-xs text-muted-foreground">{fmtDate(job.started_at)}</p>
</div>
</div>
<div class="flex items-start gap-3">
<div class="mt-0.5 flex h-6 w-6 items-center justify-center rounded-full bg-destructive/15">
<CircleX class="h-3.5 w-3.5 text-destructive" />
</div>
<div>
<p class="text-sm font-medium text-danger-fg">Generation Failed</p>
<p class="text-xs text-muted-foreground">{fmtDate(job.completed_at)}</p>
</div>
</div>
</div>
</Card.Content>
</Card.Root>

<!-- Impact Assessment -->
<Card.Root>
<Card.Header class="pb-3"><Card.Title class="text-sm font-medium">Impact Assessment</Card.Title></Card.Header>
<Card.Content>
<div class="space-y-2.5">
{#each [
{ label: 'Job completed', ok: job.status === 'completed' },
{ label: 'Result data available', ok: !!(job.result_data && Object.keys(job.result_data).length > 0) },
{ label: 'Artifacts recorded', ok: artifacts.length > 0 },
{ label: 'Error captured', ok: !!job.error_message },
{ label: 'Can retry generation', ok: true },
] as check}
<div class="flex items-center gap-2 text-sm">
{#if check.ok}
<CircleCheck class="h-4 w-4 text-success-fg" />
{:else}
<CircleX class="h-4 w-4 text-destructive" />
{/if}
<span class={check.ok ? 'text-foreground' : 'text-muted-foreground'}>{check.label}</span>
</div>
{/each}
</div>
</Card.Content>
</Card.Root>

</div>
</div>
</div>
{/if}
