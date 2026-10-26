<script lang="ts">
import { onMount, onDestroy } from 'svelte';
import * as Card from '$lib/components/ui/card';
import { Button } from '$lib/components/ui/button';
import Lock from '@lucide/svelte/icons/lock';
import PageHeader from '$lib/components/PageHeader.svelte';
import StatusBadge from '$lib/components/StatusBadge.svelte';
import ErrorAlert from '$lib/components/ErrorAlert.svelte';
import LoadingState from '$lib/components/LoadingState.svelte';
import EmptyState from '$lib/components/EmptyState.svelte';
import { tableFrame } from '$lib/components/table';
import { containerTones, type Tone } from '$lib/constants/colors';
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

// Raw Docker states (not ContainerInstance statuses) need a few extra tones.
const dockerTones: Partial<Record<string, Tone>> = { ...containerTones, exited: 'danger', stopped: 'danger', dead: 'danger', paused: 'warning' };

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

<div class="space-y-4">
<PageHeader title="System Monitor" description="Real-time metrics and maintenance tools.">
{#snippet meta()}
{#if snapshot}
<span title="Auto-refreshing every 10s"><StatusBadge status="running" tone="info" label="Live" /></span>
{/if}
{/snippet}
</PageHeader>

{#if loading}
<LoadingState label="loading system snapshot" />
{:else if !isStaff}
<EmptyState icon={Lock} title="Staff access required" description="This page is only available to admin users." />
{:else if error}
<ErrorAlert message={error} onretry={loadSnapshot} />
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
<div class="h-full bg-info rounded" style="width: {memory().percent}%"></div>
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
<p class="section-label mb-2">Disk</p>
<div class="space-y-2">
{#each disks() as d}
<div class="flex items-center gap-3">
<span class="w-16 truncate text-xs text-muted-foreground">{d.mountpoint}</span>
<div class="flex-1 h-2 rounded bg-muted overflow-hidden">
<div class="h-full bg-warning rounded" style="width: {d.percent}%"></div>
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
<Card.Root class="gap-0 overflow-clip py-0">
<Card.Header class="border-b border-border py-3"><Card.Title>Containers</Card.Title></Card.Header>
<Card.Content class="p-0">
{#if containers().length === 0}
<div class="p-4"><EmptyState compact title="No containers found" /></div>
{:else}
<div class="table-frame" {@attach tableFrame}>
<table>
<thead>
<tr>
<th>Name</th>
<th>Image</th>
<th>Status</th>
<th>Health</th>
</tr>
</thead>
<tbody>
{#each containers() as c}
<tr>
<td class="font-mono text-xs">{c.name}</td>
<td class="max-w-[28rem] truncate font-mono text-xs text-muted-foreground" title={c.image as string}>{c.image}</td>
<td class="whitespace-nowrap">
<StatusBadge status={c.status as string} tones={dockerTones} />
</td>
<td class="text-xs text-muted-foreground">{c.health || '—'}</td>
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
<p class="text-sm text-danger-fg">Unreachable: {redis().error}</p>
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
<Card.Root class="gap-0 py-0">
<Card.Header class="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border py-3">
<Card.Title>Database</Card.Title>
<div class="header-stats">
<span class="header-stat"><b>{dbStats().numbackends}</b> backends</span>
<span class="header-stat"><b>{dbStats().xact_commit?.toLocaleString()}</b> commits</span>
<span class="header-stat"><b>{dbStats().xact_rollback}</b> rollbacks</span>
<span class="header-stat"><b class={dbStats().deadlocks > 0 ? 'text-danger-fg' : ''}>{dbStats().deadlocks}</b> deadlocks</span>
</div>
</Card.Header>
{#if topTables().length > 0}
<Card.Content class="px-4 py-3">
<p class="section-label mb-1">Top Tables by Size</p>
<div class="divide-y">
{#each topTables() as t}
<div class="flex items-center justify-between py-1.5 text-sm">
<span class="font-mono text-xs">{t.table}</span>
<span class="text-muted-foreground text-xs">{t.total_human}</span>
</div>
{/each}
</div>
</Card.Content>
{/if}
</Card.Root>

<!-- APP STATS: one row per model instead of a tile each -->
<Card.Root class="gap-0 overflow-clip py-0">
<Card.Header class="border-b border-border py-3"><Card.Title>Application Stats</Card.Title></Card.Header>
<Card.Content class="p-0">
<div class="table-frame" {@attach tableFrame}>
<table>
<thead>
<tr>
<th>Model</th>
<th class="num">Last 24h</th>
<th>By status</th>
</tr>
</thead>
<tbody>
{#each modelSections as model}
<tr>
<td class="text-sm font-medium">{model.label}</td>
<td class="num font-mono text-sm">{modelStat(model.key).last_24h}</td>
<td class="text-xs text-muted-foreground">
<span class="flex flex-wrap gap-x-3 gap-y-0.5">
{#each byStatus(model.key) as [status, count]}
<span class="whitespace-nowrap"><span class="capitalize">{status}</span> <b class="font-mono font-medium text-foreground tabular-nums">{count}</b></span>
{:else}
—
{/each}
</span>
</td>
</tr>
{/each}
</tbody>
</table>
</div>
</Card.Content>
</Card.Root>

<!-- MAINTENANCE -->
<Card.Root>
<Card.Header class="pb-2">
<Card.Title>Maintenance Actions</Card.Title>
<Card.Description>Each action asks for confirmation before it runs.</Card.Description>
</Card.Header>
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
