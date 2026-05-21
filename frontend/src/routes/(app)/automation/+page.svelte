<script lang="ts">
import { onMount } from 'svelte';
import { goto } from '$app/navigation';
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import { Input } from '$lib/components/ui/input';
import Plus from '@lucide/svelte/icons/plus';
import Eye from '@lucide/svelte/icons/eye';
import Edit from '@lucide/svelte/icons/pencil';
import Copy from '@lucide/svelte/icons/copy';
import Trash2 from '@lucide/svelte/icons/trash-2';
import Play from '@lucide/svelte/icons/play';
import RefreshCw from '@lucide/svelte/icons/refresh-cw';
import Calendar from '@lucide/svelte/icons/calendar';
import Layers from '@lucide/svelte/icons/layers';
import LoaderCircle from '@lucide/svelte/icons/loader-circle';
import ChevronLeft from '@lucide/svelte/icons/chevron-left';
import ChevronRight from '@lucide/svelte/icons/chevron-right';
import {
listPipelines,
deletePipeline,
clonePipeline,
triggerPipelineRun,
type PipelineListItem
} from '$lib/api/client';

let pipelines = $state<PipelineListItem[]>([]);
let total = $state(0);
let page = $state(1);
let pages = $state(1);
let loading = $state(true);
let error = $state('');
let search = $state('');
let statusFilter = $state('');

const statusColors: Record<string, string> = {
draft: 'bg-slate-500/15 text-slate-400 border-slate-500/30',
active: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30',
archived: 'bg-orange-500/15 text-orange-400 border-orange-500/30'
};

async function load() {
loading = true;
error = '';
try {
const res = await listPipelines({
page,
per_page: 20,
search: search || undefined,
status: statusFilter || undefined
});
pipelines = res.items;
total = res.total;
pages = res.pages;
} catch (e) {
error = (e as Error)?.message || 'Failed to load pipelines';
} finally {
loading = false;
}
}

async function remove(id: string) {
if (!confirm('Delete this pipeline?')) return;
try {
await deletePipeline(id);
await load();
} catch (e) {
alert('Failed to delete pipeline');
}
}

async function clone(id: string, name: string) {
const newName = prompt('New pipeline name:', `${name} (copy)`);
if (!newName) return;
try {
const p = await clonePipeline(id, newName);
goto(`/automation/${p.id}`);
} catch (e) {
alert('Failed to clone pipeline');
}
}

async function run(id: string) {
if (!confirm('Trigger a new run?')) return;
try {
const r = await triggerPipelineRun(id);
goto(`/automation/runs/${r.id}`);
} catch (e) {
alert('Failed to trigger run');
}
}

function formatDate(s: string) {
return new Date(s).toLocaleDateString();
}

$effect(() => {
void search;
void statusFilter;
page = 1;
load();
});

onMount(load);
</script>

<svelte:head>
<title>Automation — LLM Eval Lab</title>
</svelte:head>

<div class="space-y-6">
<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
<div class="page-header min-w-0">
<h1>Automation Pipelines</h1>
<p>{total} pipeline{total !== 1 ? 's' : ''}</p>
</div>
<div class="flex flex-wrap items-center gap-2">
<Button variant="outline" size="sm" onclick={() => goto('/automation/batches')}>
<Layers class="mr-2 h-4 w-4" />Batches
</Button>
<Button variant="outline" size="sm" onclick={() => goto('/automation/schedules')}>
<Calendar class="mr-2 h-4 w-4" />Schedules
</Button>
<Button variant="outline" size="sm" onclick={load}>
<RefreshCw class="mr-2 h-4 w-4" />Refresh
</Button>
<Button size="sm" onclick={() => goto('/automation/create')}>
<Plus class="mr-2 h-4 w-4" />New Pipeline
</Button>
</div>
</div>

<!-- Filters -->
<div class="flex flex-col gap-2 sm:flex-row sm:gap-3">
<Input placeholder="Search pipelines..." bind:value={search} class="sm:max-w-xs" />
<select
bind:value={statusFilter}
class="rounded-md border bg-background px-3 py-2 text-sm"
>
<option value="">All statuses</option>
<option value="draft">Draft</option>
<option value="active">Active</option>
<option value="archived">Archived</option>
</select>
</div>

{#if loading}
<Card.Root>
<Card.Content class="flex items-center justify-center py-20">
<LoaderCircle class="h-8 w-8 animate-spin text-muted-foreground" />
</Card.Content>
</Card.Root>
{:else if error}
<Card.Root>
<Card.Content class="pt-6 text-center text-destructive">{error}</Card.Content>
</Card.Root>
{:else if pipelines.length === 0}
<Card.Root>
<Card.Content class="py-16 text-center">
<Layers class="mx-auto h-12 w-12 text-muted-foreground/50 mb-4" />
<h3 class="text-lg font-medium mb-1">No pipelines found</h3>
<p class="text-sm text-muted-foreground mb-4">Create your first automation pipeline to get started.</p>
<Button size="sm" href="/automation/create">Create Pipeline</Button>
</Card.Content>
</Card.Root>
{:else}
<!-- Table (desktop) -->
<div class="hidden md:block">
<Card.Root>
<Card.Content class="p-0">
<div class="overflow-x-auto">
<table class="w-full">
<thead>
<tr class="border-b bg-muted/40 sticky top-0 z-10">
<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Name</th>
<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Status</th>
<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Version</th>
<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Tags</th>
<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Updated</th>
<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground whitespace-nowrap">Actions</th>
</tr>
</thead>
<tbody>
{#each pipelines as p, i (p.id)}
<tr class="border-b transition-colors hover:bg-muted/50 group {i % 2 === 0 ? '' : 'bg-muted/15'}">
<td class="px-3 py-2 align-top">
<button
class="text-left text-sm font-medium hover:underline"
onclick={() => goto(`/automation/${p.id}`)}
>{p.name}</button>
</td>
<td class="px-3 py-2 align-top">
<Badge variant="outline" class="text-[10px] {statusColors[p.status] ?? ''}">{p.status}</Badge>
</td>
<td class="px-3 py-2 align-top text-sm text-muted-foreground">v{p.version}</td>
<td class="px-3 py-2 align-top">
<div class="flex flex-wrap gap-1">
{#each p.tags as tag}
<Badge variant="secondary" class="text-[10px]">{tag}</Badge>
{/each}
</div>
</td>
<td class="px-3 py-2 align-top text-sm text-muted-foreground">{formatDate(p.updated_at)}</td>
<td class="px-3 py-2">
<div class="flex items-center justify-end gap-1">
<Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="View" onclick={() => goto(`/automation/${p.id}`)}>
<Eye class="h-3.5 w-3.5" />
</Button>
<Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="Edit" onclick={() => goto(`/automation/${p.id}/edit`)}>
<Edit class="h-3.5 w-3.5" />
</Button>
<Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="Clone" onclick={() => clone(p.id, p.name)}>
<Copy class="h-3.5 w-3.5" />
</Button>
<Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="Run" onclick={() => run(p.id)}>
<Play class="h-3.5 w-3.5" />
</Button>
<Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="Delete" onclick={() => remove(p.id)}>
<Trash2 class="h-3.5 w-3.5 text-destructive" />
</Button>
</div>
</td>
</tr>
{/each}
</tbody>
</table>
</div>
</Card.Content>
</Card.Root>
</div>

<!-- Cards (mobile) -->
<div class="md:hidden space-y-3">
{#each pipelines as p (p.id)}
<div class="border rounded-lg p-3 bg-card">
<div class="flex items-start justify-between gap-2 mb-2">
<button class="text-left text-sm font-medium hover:underline truncate" onclick={() => goto(`/automation/${p.id}`)}>{p.name}</button>
<Badge variant="outline" class="shrink-0 text-[10px] {statusColors[p.status] ?? ''}">{p.status}</Badge>
</div>
<div class="flex items-center justify-between text-xs text-muted-foreground mb-2">
<span>v{p.version} · {formatDate(p.updated_at)}</span>
</div>
{#if p.tags.length > 0}
<div class="flex flex-wrap gap-1 mb-2">
{#each p.tags as tag}
<Badge variant="secondary" class="text-[10px]">{tag}</Badge>
{/each}
</div>
{/if}
<div class="flex items-center justify-end gap-1 border-t pt-2">
<Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="View" onclick={() => goto(`/automation/${p.id}`)}>
<Eye class="h-3.5 w-3.5" />
</Button>
<Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="Edit" onclick={() => goto(`/automation/${p.id}/edit`)}>
<Edit class="h-3.5 w-3.5" />
</Button>
<Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="Clone" onclick={() => clone(p.id, p.name)}>
<Copy class="h-3.5 w-3.5" />
</Button>
<Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="Run" onclick={() => run(p.id)}>
<Play class="h-3.5 w-3.5" />
</Button>
<Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="Delete" onclick={() => remove(p.id)}>
<Trash2 class="h-3.5 w-3.5 text-destructive" />
</Button>
</div>
</div>
{/each}
</div>

<!-- Pagination -->
{#if pages > 1}
<div class="flex justify-center gap-2">
<Button variant="outline" size="sm" disabled={page <= 1} onclick={() => { page--; load(); }}>
<ChevronLeft class="h-4 w-4" />
</Button>
<span class="px-3 py-2 text-sm">Page {page} / {pages}</span>
<Button variant="outline" size="sm" disabled={page >= pages} onclick={() => { page++; load(); }}>
<ChevronRight class="h-4 w-4" />
</Button>
</div>
{/if}
{/if}
</div>
