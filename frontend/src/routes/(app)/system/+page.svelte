<script lang="ts">
import { onMount, onDestroy } from 'svelte';
import * as Card from '$lib/components/ui/card';
import { Button } from '$lib/components/ui/button';
import LoaderCircle from '@lucide/svelte/icons/loader-circle';
import {
getMe,
getSystemSnapshot,
clearStuckAnalysis,
clearStuckGeneration,
purgeOrphanContainers,
clearCaches,
type SystemSnapshot,
} from '$lib/api/client';

let isStaff = $state(false);
let loading = $state(true);
let snapshot = $state<SystemSnapshot | null>(null);
let error = $state<string | null>(null);

let maintResults = $state<Record<string, string>>({});
let maintLoading = $state<Record<string, boolean>>({});
let confirmPending = $state<string | null>(null);

let refreshInterval: ReturnType<typeof setInterval>;

async function loadSnapshot() {
try {
snapshot = await getSystemSnapshot();
error = null;
} catch (err: unknown) {
error = String(err);
}
}

onMount(async () => {
try {
const me = await getMe();
isStaff = me.is_staff ?? false;
} catch {
isStaff = false;
}
if (isStaff) {
await loadSnapshot();
loading = false;
refreshInterval = setInterval(loadSnapshot, 10_000);
} else {
loading = false;
}
});

onDestroy(() => {
if (refreshInterval) clearInterval(refreshInterval);
});

// Typed accessors from snapshot
function host(): Record<string, unknown> {
return (snapshot?.host ?? {}) as Record<string, unknown>;
}
function memory(): Record<string, unknown> {
return (host().memory ?? {}) as Record<string, unknown>;
}
function loadAvg(): number[] {
const la = host().load_avg as Record<string, number> | undefined;
return la ? Object.values(la) : [];
}
function disks(): Record<string, unknown>[] {
return (host().disks ?? []) as Record<string, unknown>[];
}
function containers(): Record<string, unknown>[] {
return (snapshot?.containers ?? []) as Record<string, unknown>[];
}
function redis(): Record<string, unknown> {
return (snapshot?.redis ?? {}) as Record<string, unknown>;
}
function celery(): Record<string, unknown> {
return (snapshot?.celery ?? {}) as Record<string, unknown>;
}
function celeryQueues(): [string, number][] {
const q = celery().queue_lengths as Record<string, number> | undefined;
return q ? (Object.entries(q) as [string, number][]) : [];
}
function db(): Record<string, unknown> {
return (snapshot?.db ?? {}) as Record<string, unknown>;
}
function dbStats(): Record<string, number> {
return (db().stats ?? {}) as Record<string, number>;
}
function topTables(): Record<string, unknown>[] {
return (db().top_tables_by_size ?? []) as Record<string, unknown>[];
}
function appStats(): Record<string, unknown> {
return (snapshot?.app_stats ?? {}) as Record<string, unknown>;
}
function modelStat(key: string): Record<string, unknown> {
return (appStats()[key] ?? {}) as Record<string, unknown>;
}
function byStatus(key: string): [string, number][] {
const s = modelStat(key).by_status as Record<string, number> | undefined;
return s ? (Object.entries(s) as [string, number][]) : [];
}

async function runMaintenance(action: string, fn: () => Promise<Record<string, unknown>>) {
maintLoading[action] = true;
confirmPending = null;
try {
const result = await fn();
maintResults[action] = JSON.stringify(result);
} catch (err: unknown) {
maintResults[action] = `Error: ${String(err)}`;
} finally {
maintLoading[action] = false;
}
}

function confirmAction(action: string) {
confirmPending = action;
}
function cancelConfirm() {
confirmPending = null;
}

function formatBytes(b: number): string {
const units = ['B', 'KB', 'MB', 'GB', 'TB'];
let v = b;
for (const u of units) {
if (v < 1024) return `${v.toFixed(1)} ${u}`;
v /= 1024;
}
return `${v.toFixed(1)} PB`;
}

function formatUptime(seconds: number): string {
const d = Math.floor(seconds / 86400);
const h = Math.floor((seconds % 86400) / 3600);
const m = Math.floor((seconds % 3600) / 60);
if (d > 0) return `${d}d ${h}h ${m}m`;
if (h > 0) return `${h}h ${m}m`;
return `${m}m`;
}

function statusBadgeClass(status: string): string {
const s = status.toLowerCase();
if (s === 'running') return 'bg-emerald-500/15 text-emerald-500 border border-emerald-500/30';
if (s === 'exited' || s === 'stopped' || s === 'dead') return 'bg-red-500/15 text-red-400 border border-red-500/30';
if (s === 'paused') return 'bg-amber-500/15 text-amber-500 border border-amber-500/30';
return 'bg-zinc-500/15 text-zinc-400 border border-zinc-500/30';
}

const modelSections = [
{ key: 'analysis_tasks', label: 'Analysis Tasks' },
{ key: 'generation_jobs', label: 'Generation Jobs' },
{ key: 'reports', label: 'Reports' },
{ key: 'container_instances', label: 'Container Instances' },
];

const maintenanceActions = [
{
id: 'clear-stuck-analysis',
label: 'Clear Stuck Analysis Tasks',
desc: 'Mark pending/running tasks older than 60m as failed',
fn: () => clearStuckAnalysis(60) as Promise<Record<string, unknown>>,
},
{
id: 'clear-stuck-generation',
label: 'Clear Stuck Generation Jobs',
desc: 'Mark pending/running jobs older than 60m as failed',
fn: () => clearStuckGeneration(60) as Promise<Record<string, unknown>>,
},
{
id: 'purge-orphan-containers',
label: 'Purge Orphan Containers',
desc: 'Remove container instances with no matching Docker container',
fn: () => purgeOrphanContainers() as Promise<Record<string, unknown>>,
},
{
id: 'clear-caches',
label: 'Clear All Caches',
desc: 'Flush the Django cache framework',
fn: () => clearCaches() as Promise<Record<string, unknown>>,
},
];
</script>

<svelte:head>
	<title>System - LLM Lab</title>
</svelte:head>

<div class="space-y-6">
<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
<div class="page-header">
<h1>System Monitor</h1>
<p>Real-time metrics and maintenance tools</p>
</div>
{#if snapshot}
<span class="text-xs text-muted-foreground animate-pulse">Auto-refreshing every 10s</span>
{/if}
</div>

{#if loading}
<Card.Root>
<Card.Content class="flex items-center justify-center py-20">
<LoaderCircle class="h-8 w-8 animate-spin text-muted-foreground" />
</Card.Content>
</Card.Root>
{:else if !isStaff}
<div class="rounded-lg border border-destructive/30 bg-destructive/10 p-6 text-center">
<p class="text-lg font-semibold text-destructive">403 — Staff access required</p>
<p class="mt-1 text-sm text-muted-foreground">This page is only available to admin users.</p>
</div>
{:else if error}
<div class="rounded-lg border border-destructive/30 bg-destructive/10 p-4">
<p class="text-sm text-destructive">{error}</p>
</div>
{:else if snapshot}
<!-- HOST METRICS -->
<Card.Root>
<Card.Header class="pb-2"><Card.Title>Host Metrics</Card.Title></Card.Header>
<Card.Content class="space-y-4">
<div class="grid grid-cols-2 gap-4 sm:grid-cols-4">
<div class="space-y-1">
<p class="text-xs text-muted-foreground">CPU</p>
<p class="text-2xl font-bold">{host().cpu_percent as number}%</p>
<div class="h-2 rounded bg-muted overflow-hidden">
<div class="h-full bg-primary rounded" style="width: {host().cpu_percent}%"></div>
</div>
</div>
<div class="space-y-1">
<p class="text-xs text-muted-foreground">Memory</p>
<p class="text-2xl font-bold">{memory().percent as number}%</p>
<div class="h-2 rounded bg-muted overflow-hidden">
<div class="h-full bg-blue-500 rounded" style="width: {memory().percent}%"></div>
</div>
<p class="text-xs text-muted-foreground">
{formatBytes(memory().used as number)} / {formatBytes(memory().total as number)}
</p>
</div>
<div class="space-y-1">
<p class="text-xs text-muted-foreground">Uptime</p>
<p class="text-lg font-semibold">{formatUptime(host().uptime_seconds as number)}</p>
</div>
<div class="space-y-1">
<p class="text-xs text-muted-foreground">Load Avg (1m / 5m / 15m)</p>
<p class="text-sm font-medium">{loadAvg().map((v) => v.toFixed(2)).join(' / ')}</p>
</div>
</div>
{#if disks().length > 0}
<div>
<p class="mb-2 text-xs font-medium text-muted-foreground uppercase">Disk</p>
<div class="space-y-2">
{#each disks() as d}
<div class="flex items-center gap-3">
<span class="w-16 truncate text-xs text-muted-foreground">{d.mountpoint}</span>
<div class="flex-1 h-2 rounded bg-muted overflow-hidden">
<div class="h-full bg-orange-400 rounded" style="width: {d.percent}%"></div>
</div>
<span class="w-12 text-right text-xs">{d.percent}%</span>
<span class="text-xs text-muted-foreground">{formatBytes(d.used as number)} / {formatBytes(d.total as number)}</span>
</div>
{/each}
</div>
</div>
{/if}
</Card.Content>
</Card.Root>

<!-- CONTAINERS -->
<Card.Root>
<Card.Header class="pb-2"><Card.Title>Containers</Card.Title></Card.Header>
<Card.Content class="space-y-3">
{#if containers().length === 0}
<p class="text-sm text-muted-foreground">No containers found.</p>
{:else}
<div class="overflow-x-auto">
<table class="w-full text-sm">
<thead>
<tr class="border-b bg-muted/40 sticky top-0 z-10">
<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Name</th>
<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Image</th>
<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Status</th>
<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Health</th>
</tr>
</thead>
<tbody>
{#each containers() as c, i}
<tr class="border-b transition-colors hover:bg-muted/50 {i % 2 === 0 ? '' : 'bg-muted/15'}">
<td class="px-3 py-2 align-top font-mono text-xs">{c.name}</td>
<td class="px-3 py-2 align-top text-xs text-muted-foreground">{c.image}</td>
<td class="px-3 py-2 align-top">
<span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium {statusBadgeClass(c.status as string)}">
{c.status}
</span>
</td>
<td class="px-3 py-2 align-top text-xs text-muted-foreground">{c.health}</td>
</tr>
{/each}
</tbody>
</table>
</div>
{/if}
</Card.Content>
</Card.Root>

<!-- REDIS + CELERY -->
<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
<Card.Root>
<Card.Header class="pb-2"><Card.Title>Redis</Card.Title></Card.Header>
<Card.Content class="space-y-3">
{#if redis().reachable}
<dl class="space-y-1 text-sm">
<div class="flex justify-between"><dt class="text-muted-foreground">Latency</dt><dd>{redis().ping_latency_ms} ms</dd></div>
<div class="flex justify-between"><dt class="text-muted-foreground">Clients</dt><dd>{redis().connected_clients}</dd></div>
<div class="flex justify-between"><dt class="text-muted-foreground">Memory</dt><dd>{redis().used_memory_human}</dd></div>
<div class="flex justify-between"><dt class="text-muted-foreground">Commands</dt><dd>{(redis().total_commands_processed as number)?.toLocaleString()}</dd></div>
<div class="flex justify-between"><dt class="text-muted-foreground">Version</dt><dd>{redis().redis_version}</dd></div>
</dl>
{:else}
<p class="text-sm text-destructive">Unreachable: {redis().error}</p>
{/if}
</Card.Content>
</Card.Root>

<Card.Root>
<Card.Header class="pb-2"><Card.Title>Celery</Card.Title></Card.Header>
<Card.Content class="space-y-3">
{#if celery().reachable}
<dl class="space-y-1 text-sm">
<div class="flex justify-between"><dt class="text-muted-foreground">Workers</dt><dd>{celery().worker_count}</dd></div>
<div class="flex justify-between"><dt class="text-muted-foreground">Active tasks</dt><dd>{celery().active_tasks}</dd></div>
<div class="flex justify-between"><dt class="text-muted-foreground">Scheduled</dt><dd>{celery().scheduled_tasks}</dd></div>
<div class="flex justify-between"><dt class="text-muted-foreground">Reserved</dt><dd>{celery().reserved_tasks}</dd></div>
</dl>
{#if celeryQueues().length > 0}
<div>
<p class="text-xs font-medium text-muted-foreground mb-1">Queue lengths</p>
{#each celeryQueues() as [q, len]}
<div class="flex justify-between text-sm"><span>{q}</span><span>{len}</span></div>
{/each}
</div>
{/if}
{:else}
<p class="text-sm text-muted-foreground">No workers reachable</p>
{/if}
</Card.Content>
</Card.Root>
</div>

<!-- DB STATS -->
<Card.Root>
<Card.Header class="pb-2"><Card.Title>Database</Card.Title></Card.Header>
<Card.Content class="space-y-3">
<div class="grid grid-cols-2 gap-3 sm:grid-cols-4 text-sm">
<div class="kpi-card">
<div class="text-xs text-muted-foreground uppercase tracking-wider">Backends</div>
<div class="text-2xl font-semibold font-mono tabular-nums">{dbStats().numbackends}</div>
</div>
<div class="kpi-card">
<div class="text-xs text-muted-foreground uppercase tracking-wider">Commits</div>
<div class="text-2xl font-semibold font-mono tabular-nums">{dbStats().xact_commit?.toLocaleString()}</div>
</div>
<div class="kpi-card">
<div class="text-xs text-muted-foreground uppercase tracking-wider">Rollbacks</div>
<div class="text-2xl font-semibold font-mono tabular-nums">{dbStats().xact_rollback}</div>
</div>
<div class="kpi-card">
<div class="text-xs text-muted-foreground uppercase tracking-wider">Deadlocks</div>
<div class="text-2xl font-semibold font-mono tabular-nums {dbStats().deadlocks > 0 ? 'text-destructive' : ''}">{dbStats().deadlocks}</div>
</div>
</div>
{#if topTables().length > 0}
<div>
<p class="mb-2 text-xs font-medium text-muted-foreground uppercase">Top Tables by Size</p>
<div class="divide-y">
{#each topTables() as t}
<div class="flex items-center justify-between py-1.5 text-sm">
<span class="font-mono text-xs">{t.table}</span>
<span class="text-muted-foreground text-xs">{t.total_human}</span>
</div>
{/each}
</div>
</div>
{/if}
</Card.Content>
</Card.Root>

<!-- APP STATS -->
<Card.Root>
<Card.Header class="pb-2"><Card.Title>Application Stats</Card.Title></Card.Header>
<Card.Content class="space-y-3">
<div class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
{#each modelSections as model}
<div class="rounded-md border p-3 space-y-2">
<p class="text-xs font-medium text-muted-foreground">{model.label}</p>
<p class="text-sm">
<span class="font-semibold">{modelStat(model.key).last_24h}</span>
<span class="text-muted-foreground text-xs"> in last 24h</span>
</p>
<div class="space-y-0.5">
{#each byStatus(model.key) as [status, count]}
<div class="flex justify-between text-xs">
<span class="capitalize text-muted-foreground">{status}</span>
<span class="font-medium">{count}</span>
</div>
{/each}
</div>
</div>
{/each}
</div>
</Card.Content>
</Card.Root>

<!-- MAINTENANCE -->
<Card.Root>
<Card.Header class="pb-2"><Card.Title>Maintenance Actions</Card.Title></Card.Header>
<Card.Content class="space-y-4">
<div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
{#each maintenanceActions as action}
<div class="rounded-md border p-4 space-y-2">
<div>
<p class="text-sm font-medium">{action.label}</p>
<p class="text-xs text-muted-foreground">{action.desc}</p>
</div>
{#if maintResults[action.id]}
<p class="text-xs font-mono bg-muted rounded p-2 break-all">{maintResults[action.id]}</p>
{/if}
{#if confirmPending === action.id}
<div class="flex gap-2">
<Button variant="destructive" size="sm" class="flex-1" onclick={() => runMaintenance(action.id, action.fn)}>
{maintLoading[action.id] ? 'Running…' : 'Confirm'}
</Button>
<Button variant="outline" size="sm" class="flex-1" onclick={cancelConfirm}>Cancel</Button>
</div>
{:else}
<Button variant="outline" size="sm" class="w-full" onclick={() => confirmAction(action.id)} disabled={maintLoading[action.id]}>
{maintLoading[action.id] ? 'Running…' : 'Run Action'}
</Button>
{/if}
</div>
{/each}
</div>
</Card.Content>
</Card.Root>
{/if}
</div>
