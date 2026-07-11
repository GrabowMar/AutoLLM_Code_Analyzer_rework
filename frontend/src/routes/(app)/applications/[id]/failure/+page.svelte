<script lang="ts">
import { formatDateTime as fmtDate, formatNumber as fmt } from '$lib/utils/formatters';
import { page } from '$app/stores';
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

import ArrowLeft from '@lucide/svelte/icons/arrow-left';
import Copy from '@lucide/svelte/icons/copy';
import AlertTriangle from '@lucide/svelte/icons/alert-triangle';
import CircleCheck from '@lucide/svelte/icons/circle-check';
import CircleX from '@lucide/svelte/icons/circle-x';
import Clock from '@lucide/svelte/icons/clock';
import LoaderCircle from '@lucide/svelte/icons/loader-circle';
import Skull from '@lucide/svelte/icons/skull';
import RefreshCw from '@lucide/svelte/icons/refresh-cw';
import Database from '@lucide/svelte/icons/database';
import Bot from '@lucide/svelte/icons/bot';
import Layers from '@lucide/svelte/icons/layers';
import Pencil from '@lucide/svelte/icons/pencil';
import Shield from '@lucide/svelte/icons/shield';

const jobId = $derived($page.params.id ?? '');
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

const modeColors: Record<string, string> = {
custom: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
scaffolding: 'bg-purple-500/15 text-purple-400 border-purple-500/30',
copilot: 'bg-teal-500/15 text-teal-400 border-teal-500/30',
};
const modeIcons: Record<string, any> = { custom: Pencil, scaffolding: Layers, copilot: Bot };

function copyText(text: string, label = 'Copied') {
navigator.clipboard.writeText(text);
toast.success(label);
}

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

<svelte:head>
<title>Failure Details - LLM Lab</title>
</svelte:head>

{#if loading}
<div class="flex items-center justify-center py-32">
<LoaderCircle class="h-8 w-8 animate-spin text-muted-foreground" />
<span class="ml-3 text-muted-foreground">Loading failure details...</span>
</div>
{:else if !job}
<Card.Root>
<Card.Content class="py-16 text-center">
<AlertTriangle class="mx-auto h-12 w-12 text-destructive mb-4" />
<h3 class="text-lg font-medium">Job not found</h3>
<Button variant="outline" size="sm" href="/applications" class="mt-4">Back to Applications</Button>
</Card.Content>
</Card.Root>
{:else}
<div class="space-y-5">
<!-- Breadcrumb -->
<nav aria-label="Breadcrumb" class="flex items-center gap-2 text-sm text-muted-foreground">
<a href="/applications" class="hover:text-foreground transition-colors flex items-center gap-1">
<ArrowLeft class="h-3.5 w-3.5" />
<span>Applications</span>
</a>
<span>/</span>
<a href="/applications/{job.id}" class="hover:text-foreground transition-colors">{job.model_name ?? 'Job'}</a>
<span>/</span>
<span class="text-destructive font-medium">Failure Details</span>
</nav>

<!-- Header -->
<div class="flex items-center gap-3 rounded-lg border border-red-500/30 bg-red-500/10 px-5 py-4">
<div class="flex h-10 w-10 items-center justify-center rounded-md bg-red-500/20">
<Skull class="h-5 w-5 text-destructive" />
</div>
<div class="flex-1">
<h1 class="text-lg font-semibold text-destructive">Failure Details</h1>
<p class="text-sm text-destructive/70">{job.model_name} — Generation Failed</p>
</div>
<div class="flex gap-2">
<Button variant="outline" size="sm" href="/applications/{job.id}" class="border-zinc-600">
View Full Detail
</Button>
<Button size="sm" href="/sample-generator" class="bg-amber-600 hover:bg-amber-700">
<RefreshCw class="h-3.5 w-3.5 mr-1.5" />Retry Generation
</Button>
</div>
</div>

<div class="grid lg:grid-cols-3 gap-5">
<!-- Left Column (2/3) -->
<div class="lg:col-span-2 space-y-5">
<!-- Quick Info -->
<Card.Root>
<Card.Content class="p-4">
<div class="grid grid-cols-2 sm:grid-cols-4 gap-4">
<div>
<dt class="text-xs text-muted-foreground">Model</dt>
<dd class="text-sm font-medium mt-0.5">{job.model_name ?? '—'}</dd>
</div>
<div>
<dt class="text-xs text-muted-foreground">Mode</dt>
<dd class="mt-0.5"><Badge variant="outline" class="text-xs {modeColors[job.mode] ?? ''}">{job.mode}</Badge></dd>
</div>
<div>
<dt class="text-xs text-muted-foreground">Temperature</dt>
<dd class="text-sm font-medium mt-0.5">{job.temperature}</dd>
</div>
<div>
<dt class="text-xs text-muted-foreground">Max Tokens</dt>
<dd class="text-sm font-medium mt-0.5">{fmt(job.max_tokens)}</dd>
</div>
</div>
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
<pre class="text-xs font-mono bg-red-500/10 border border-red-500/30 rounded-lg p-4 whitespace-pre-wrap break-words max-h-64 overflow-auto text-red-300">{job.error_message}</pre>
<Button variant="ghost" size="sm" class="absolute top-2 right-2 h-7" onclick={() => copyText(job!.error_message, 'Copied error')}>
<Copy class="h-3.5 w-3.5" />
</Button>
</div>
{#if job.result_data?.error_status_code === 401 || /OpenRouter.*key/i.test(job.error_message)}
<div class="mt-3 flex flex-wrap items-center gap-3 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
<span>This job failed because OpenRouter rejected the API key.</span>
<Button variant="outline" size="sm" href="/users/settings#api">
Open API Access settings
</Button>
</div>
{/if}
{:else}
<p class="text-sm text-muted-foreground">No error message recorded</p>
{/if}
</Card.Content>
</Card.Root>

<!-- Generation Errors List (from copilot iterations) -->
{#if allErrors.length > 0}
<Card.Root>
<Card.Header class="pb-3">
<Card.Title class="text-sm font-medium flex items-center gap-2">
Generation Errors
<Badge variant="outline" class="text-xs bg-red-500/15 text-red-400 border-red-500/30">{allErrors.length}</Badge>
</Card.Title>
</Card.Header>
<Card.Content>
<ol class="space-y-2">
{#each allErrors as err, i}
<li class="flex items-start gap-2 text-sm">
<span class="shrink-0 text-xs font-bold text-destructive bg-red-500/20 rounded px-1.5 py-0.5">{i + 1}</span>
<span class="text-red-300/90">{err}</span>
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
<Badge variant="outline" class="text-xs bg-red-500/15 text-red-400 border-red-500/30">{failedIterations.length}</Badge>
</Card.Title>
</Card.Header>
<Card.Content class="space-y-4">
{#each failedIterations as it}
<div class="border border-red-500/20 rounded-lg p-3 bg-red-500/5">
<div class="flex items-center gap-2 mb-2">
<Badge variant="outline" class="text-xs">Iteration {it.iteration_number}</Badge>
<Badge variant="outline" class="text-xs">{it.action}</Badge>
<Badge variant="outline" class="text-xs bg-red-500/15 text-red-400 border-red-500/30">Build Failed</Badge>
</div>
{#if it.errors_detected && it.errors_detected.length > 0}
<div class="mb-2">
<div class="text-xs text-muted-foreground mb-1">Errors:</div>
<ul class="space-y-1">
{#each it.errors_detected as err}
<li class="flex items-start gap-1.5 text-xs text-destructive/90">
<CircleX class="h-3 w-3 shrink-0 mt-0.5" />{err}
</li>
{/each}
</ul>
</div>
{/if}
{#if it.build_output}
<div>
<div class="text-xs text-muted-foreground mb-1">Build Output:</div>
<pre class="text-xs font-mono bg-zinc-950 rounded p-2 max-h-32 overflow-auto text-zinc-300">{it.build_output}</pre>
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
<table class="w-full text-sm">
<thead>
<tr class="border-b bg-muted/30">
<th class="px-4 py-2 text-left text-xs font-medium text-muted-foreground">Stage</th>
<th class="px-4 py-2 text-left text-xs font-medium text-muted-foreground">Model</th>
<th class="px-4 py-2 text-left text-xs font-medium text-muted-foreground">Prompt</th>
<th class="px-4 py-2 text-left text-xs font-medium text-muted-foreground">Completion</th>
</tr>
</thead>
<tbody class="divide-y">
{#each artifacts as art}
<tr class="hover:bg-muted/30">
<td class="px-4 py-2"><Badge variant="outline" class="text-xs">{art.stage}</Badge></td>
<td class="px-4 py-2 font-mono text-xs">{art.request_payload?.model ?? '—'}</td>
<td class="px-4 py-2">{fmt(art.prompt_tokens)}</td>
<td class="px-4 py-2">{fmt(art.completion_tokens)}</td>
</tr>
{/each}
</tbody>
</table>
</Card.Content>
</Card.Root>
{/if}
</div>

<!-- Right Column (1/3) -->
<div class="space-y-5">
<!-- Status Card -->
<Card.Root>
<Card.Content class="py-8 text-center">
<div class="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-md bg-red-500/20">
<Skull class="h-8 w-8 text-destructive" />
</div>
<h3 class="text-lg font-semibold text-destructive">Failed</h3>
<p class="text-sm text-muted-foreground mt-1">Generation did not complete successfully</p>
</Card.Content>
</Card.Root>

<!-- Timeline -->
<Card.Root>
<Card.Header class="pb-3"><Card.Title class="text-sm font-medium">Timeline</Card.Title></Card.Header>
<Card.Content>
<div class="space-y-4">
<div class="flex items-start gap-3">
<div class="mt-0.5 flex h-6 w-6 items-center justify-center rounded-full bg-emerald-500/20">
<CircleCheck class="h-3.5 w-3.5 text-emerald-500" />
</div>
<div>
<p class="text-sm font-medium">Job Created</p>
<p class="text-xs text-muted-foreground">{fmtDate(job.created_at)}</p>
</div>
</div>
<div class="flex items-start gap-3">
{#if job.started_at}
<div class="mt-0.5 flex h-6 w-6 items-center justify-center rounded-full bg-emerald-500/20">
<CircleCheck class="h-3.5 w-3.5 text-emerald-500" />
</div>
{:else}
<div class="mt-0.5 flex h-6 w-6 items-center justify-center rounded-full bg-zinc-500/20">
<Clock class="h-3.5 w-3.5 text-zinc-400" />
</div>
{/if}
<div>
<p class="text-sm font-medium">Generation Started</p>
<p class="text-xs text-muted-foreground">{fmtDate(job.started_at)}</p>
</div>
</div>
<div class="flex items-start gap-3">
<div class="mt-0.5 flex h-6 w-6 items-center justify-center rounded-full bg-red-500/20">
<CircleX class="h-3.5 w-3.5 text-destructive" />
</div>
<div>
<p class="text-sm font-medium text-destructive">Generation Failed</p>
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
<CircleCheck class="h-4 w-4 text-emerald-500" />
{:else}
<CircleX class="h-4 w-4 text-destructive" />
{/if}
<span class={check.ok ? 'text-foreground' : 'text-muted-foreground'}>{check.label}</span>
</div>
{/each}
</div>
</Card.Content>
</Card.Root>

<!-- Quick Actions -->
<Card.Root>
<Card.Header class="pb-3"><Card.Title class="text-sm font-medium">Actions</Card.Title></Card.Header>
<Card.Content class="space-y-2">
<Button variant="outline" size="sm" class="w-full justify-start" href="/sample-generator">
<RefreshCw class="h-3.5 w-3.5 mr-2" />Retry with same settings
</Button>
<Button variant="outline" size="sm" class="w-full justify-start" href="/applications/{job.id}">
<Shield class="h-3.5 w-3.5 mr-2" />View full job detail
</Button>
<Button variant="outline" size="sm" class="w-full justify-start" onclick={() => copyText(job!.error_message || 'No error', 'Copied error')}>
<Copy class="h-3.5 w-3.5 mr-2" />Copy error message
</Button>
</Card.Content>
</Card.Root>
</div>
</div>
</div>
{/if}
