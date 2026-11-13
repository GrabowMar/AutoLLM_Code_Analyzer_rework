<script lang="ts">
import { onMount, onDestroy } from 'svelte';
import { page } from '$app/state';
import { goto } from '$app/navigation';
import * as Card from '$lib/components/ui/card';
import * as Tabs from '$lib/components/ui/tabs';
import { Button } from '$lib/components/ui/button';
import Lock from '@lucide/svelte/icons/lock';
import LoaderCircle from '@lucide/svelte/icons/loader-circle';
import PageHeader from '$lib/components/PageHeader.svelte';
import StatusBadge from '$lib/components/StatusBadge.svelte';
import ErrorAlert from '$lib/components/ErrorAlert.svelte';
import LoadingState from '$lib/components/LoadingState.svelte';
import EmptyState from '$lib/components/EmptyState.svelte';
import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
import AppsFootprint from '$lib/components/system/AppsFootprint.svelte';
import { formatBytes, formatSpan } from '$lib/components/system/format';
import { tableFrame } from '$lib/components/table';
import { containerTones, type Tone } from '$lib/constants/colors';
import { formatApiError } from '$lib/api/core';
import {
getMe,
getSystemSnapshot,
clearStuckAnalysis,
clearStuckGeneration,
purgeOrphanContainers,
clearCaches,
type SystemSnapshot,
} from '$lib/api/client';

const TABS = [
{ value: 'footprint', label: 'Apps & footprint' },
{ value: 'health', label: 'Health' },
{ value: 'maintenance', label: 'Maintenance' },
] as const;
type Tab = (typeof TABS)[number]['value'];
const DEFAULT_TAB: Tab = 'footprint';

const tab = $derived<Tab>(
(TABS.find((t) => t.value === page.url.searchParams.get('tab'))?.value ?? DEFAULT_TAB) as Tab,
);

function setTab(next: string) {
const url = new URL(page.url);
if (next === DEFAULT_TAB) url.searchParams.delete('tab');
else url.searchParams.set('tab', next);
goto(`${url.pathname}${url.search}`, { replaceState: true, keepFocus: true, noScroll: true });
}

let isStaff = $state(false);
let checking = $state(true);
let snapshot = $state<SystemSnapshot | null>(null);
let snapshotError = $state<string | null>(null);
let refreshInterval: ReturnType<typeof setInterval> | undefined;

async function loadSnapshot() {
try {
snapshot = await getSystemSnapshot();
snapshotError = null;
} catch (err: unknown) {
snapshotError = formatApiError(err);
}
}

onMount(async () => {
try {
const me = await getMe();
isStaff = me.is_staff ?? false;
} catch {
isStaff = false;
}
checking = false;
if (isStaff) {
await loadSnapshot();
refreshInterval = setInterval(loadSnapshot, 10_000);
}
});

onDestroy(() => {
if (refreshInterval) clearInterval(refreshInterval);
});

// Typed accessors from the snapshot
const host = $derived((snapshot?.host ?? {}) as Record<string, unknown>);
const memory = $derived((host.memory ?? {}) as Record<string, number>);
const loadAvg = $derived(Object.values((host.load_avg ?? {}) as Record<string, number>));
const disks = $derived((host.disks ?? []) as Record<string, unknown>[]);
const containers = $derived((snapshot?.containers ?? []) as Record<string, unknown>[]);
const redis = $derived((snapshot?.redis ?? {}) as Record<string, unknown>);
const celery = $derived((snapshot?.celery ?? {}) as Record<string, unknown>);
const celeryQueues = $derived(Object.entries((celery.queue_lengths ?? {}) as Record<string, number>));
const db = $derived((snapshot?.db ?? {}) as Record<string, unknown>);
const dbStats = $derived((db.stats ?? {}) as Record<string, number>);
const topTables = $derived((db.top_tables_by_size ?? []) as Record<string, unknown>[]);
const appStats = $derived((snapshot?.app_stats ?? {}) as Record<string, Record<string, unknown>>);
const rootDisk = $derived(disks[0]);

function byStatus(key: string): [string, number][] {
return Object.entries((appStats[key]?.by_status ?? {}) as Record<string, number>);
}

// Raw Docker states (not ContainerInstance statuses) need a few extra tones.
const dockerTones: Partial<Record<string, Tone>> = { ...containerTones, exited: 'danger', stopped: 'danger', dead: 'danger', paused: 'warning' };

const modelSections = [
{ key: 'analysis_runs', label: 'Analysis runs' },
{ key: 'generation_runs', label: 'Generation runs' },
{ key: 'reports', label: 'Reports' },
{ key: 'container_instances', label: 'Container instances' },
];

// Maintenance
interface MaintenanceAction {
id: string;
label: string;
desc: string;
fn: () => Promise<unknown>;
summary: (r: Record<string, unknown>) => string;
}
const maintenanceActions: MaintenanceAction[] = [
{
id: 'clear-stuck-analysis',
label: 'Clear stuck analysis runs',
desc: 'Mark pending/running analysis runs older than 60 min as failed.',
fn: () => clearStuckAnalysis(60),
summary: (r) => `${r.updated ?? 0} marked failed.`,
},
{
id: 'clear-stuck-generation',
label: 'Clear stuck generation runs',
desc: 'Fail runs never queued, or whose executor lease expired, more than 60 min ago (infra_error).',
fn: () => clearStuckGeneration(60),
summary: (r) => `${r.updated ?? 0} marked failed.`,
},
{
id: 'purge-orphan-containers',
label: 'Purge orphan containers',
desc: 'Mark app records whose Docker container is gone as removed, and reap leftover agent sandboxes.',
fn: () => purgeOrphanContainers(),
summary: (r) => `${r.purged ?? 0} purged.`,
},
{
id: 'clear-caches',
label: 'Clear all caches',
desc: 'Flush the Django cache framework (also resets app idle timers).',
fn: () => clearCaches(),
summary: (r) => (r.success ? 'Caches cleared.' : `Failed: ${r.error ?? 'unknown error'}`),
},
];
let maintResults = $state<Record<string, { ok: boolean; text: string }>>({});
let maintRunning = $state<string | null>(null);
let maintConfirmOpen = $state(false);
let maintPending = $state<MaintenanceAction | null>(null);

async function runMaintenance() {
const action = maintPending;
if (!action) return;
maintRunning = action.id;
try {
const result = (await action.fn()) as Record<string, unknown>;
maintResults[action.id] = { ok: true, text: action.summary(result) };
} catch (err: unknown) {
maintResults[action.id] = { ok: false, text: formatApiError(err) };
} finally {
maintRunning = null;
maintConfirmOpen = false;
maintPending = null;
}
}
</script>

{#snippet meta()}
{#if snapshot}
<span title="Auto-refreshing every 10s"><StatusBadge status="running" tone="info" label="Live" /></span>
{/if}
{/snippet}

{#snippet nav()}
<Tabs.List aria-label="System sections">
{#each TABS as t (t.value)}
<Tabs.Trigger value={t.value}>{t.label}</Tabs.Trigger>
{/each}
</Tabs.List>
{/snippet}

<Tabs.Root value={tab} onValueChange={(v) => setTab(v)} class="space-y-4">
<PageHeader
title="System"
description="Platform health, preview-app footprint and maintenance tools."
{meta}
nav={isStaff ? nav : undefined}
>
{#if isStaff && snapshot}
<div class="header-stats">
<span class="header-stat"><b>{host.cpu_percent as number}%</b> CPU</span>
<span class="header-stat"><b>{memory.percent}%</b> memory · {formatBytes(memory.used)} / {formatBytes(memory.total)}</span>
{#if rootDisk}
<span class="header-stat"><b>{rootDisk.percent as number}%</b> disk · {formatBytes(rootDisk.free as number)} free</span>
{/if}
<span class="header-stat">load <b>{loadAvg.map((v) => v.toFixed(2)).join(' / ')}</b></span>
<span class="header-stat">up <b>{formatSpan(host.uptime_seconds as number)}</b></span>
</div>
{/if}
</PageHeader>

{#if checking}
<LoadingState label="checking access" />
{:else if !isStaff}
<EmptyState icon={Lock} title="Staff access required" description="This page is only available to admin users." />
{:else}
<Tabs.Content value="footprint" class="pt-0">
{#if tab === 'footprint'}<AppsFootprint />{/if}
</Tabs.Content>

<Tabs.Content value="health" class="space-y-4 pt-0">
{#if snapshotError}
<ErrorAlert message={snapshotError} onretry={loadSnapshot} />
{:else if !snapshot}
<LoadingState label="loading system snapshot" />
{:else}
<!-- HOST -->
<div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
<div class="kpi-card space-y-1">
<p class="kpi-label">CPU</p>
<p class="kpi-value">{host.cpu_percent as number}%</p>
<p class="text-xs text-muted-foreground">{host.cpu_count as number} logical cores</p>
<span class="block h-1.5 rounded-sm bg-muted" aria-hidden="true"><span class="block h-1.5 rounded-sm bg-[var(--chart-1)]" style="width: {host.cpu_percent}%"></span></span>
</div>
<div class="kpi-card space-y-1">
<p class="kpi-label">Memory</p>
<p class="kpi-value">{memory.percent}%</p>
<p class="text-xs text-muted-foreground">{formatBytes(memory.used)} / {formatBytes(memory.total)} · {formatBytes(memory.available)} available</p>
<span class="block h-1.5 rounded-sm bg-muted" aria-hidden="true"><span class="block h-1.5 rounded-sm bg-[var(--chart-1)]" style="width: {memory.percent}%"></span></span>
</div>
<div class="kpi-card space-y-1">
<p class="kpi-label">Load (1m / 5m / 15m)</p>
<p class="kpi-value text-lg">{loadAvg.map((v) => v.toFixed(2)).join(' / ')}</p>
<p class="text-xs text-muted-foreground">up {formatSpan(host.uptime_seconds as number)}</p>
</div>
<div class="kpi-card space-y-1">
<p class="kpi-label">Disk</p>
{#each disks as d (d.mountpoint)}
<div class="flex items-center gap-2 text-xs">
<span class="w-14 truncate font-mono text-muted-foreground" title={d.mountpoint as string}>{d.mountpoint}</span>
<span class="block h-1.5 flex-1 rounded-sm bg-muted" aria-hidden="true"><span class="block h-1.5 rounded-sm bg-[var(--chart-1)]" style="width: {d.percent}%"></span></span>
<span class="w-24 text-right font-mono tabular-nums">{formatBytes(d.free as number)} free</span>
</div>
{:else}
<p class="text-xs text-muted-foreground">—</p>
{/each}
</div>
</div>

<!-- SERVICES -->
<div class="grid grid-cols-1 gap-4 lg:grid-cols-3">
<Card.Root>
<Card.Header class="pb-2"><Card.Title>Redis</Card.Title></Card.Header>
<Card.Content>
{#if redis.reachable}
<dl class="space-y-1 text-sm">
<div class="flex justify-between"><dt class="text-muted-foreground">Latency</dt><dd class="font-mono text-xs">{redis.ping_latency_ms} ms</dd></div>
<div class="flex justify-between"><dt class="text-muted-foreground">Clients</dt><dd class="font-mono text-xs">{redis.connected_clients}</dd></div>
<div class="flex justify-between"><dt class="text-muted-foreground">Memory</dt><dd class="font-mono text-xs">{redis.used_memory_human}</dd></div>
<div class="flex justify-between"><dt class="text-muted-foreground">Commands</dt><dd class="font-mono text-xs">{(redis.total_commands_processed as number)?.toLocaleString()}</dd></div>
<div class="flex justify-between"><dt class="text-muted-foreground">Version</dt><dd class="font-mono text-xs">{redis.redis_version}</dd></div>
</dl>
{:else}
<p class="text-sm text-danger-fg">Unreachable: {redis.error}</p>
{/if}
</Card.Content>
</Card.Root>

<Card.Root>
<Card.Header class="pb-2"><Card.Title>Celery</Card.Title></Card.Header>
<Card.Content class="space-y-3">
{#if celery.reachable}
<dl class="space-y-1 text-sm">
<div class="flex justify-between"><dt class="text-muted-foreground">Workers</dt><dd class="font-mono text-xs">{celery.worker_count}</dd></div>
<div class="flex justify-between"><dt class="text-muted-foreground">Active tasks</dt><dd class="font-mono text-xs">{celery.active_tasks}</dd></div>
<div class="flex justify-between"><dt class="text-muted-foreground">Scheduled</dt><dd class="font-mono text-xs">{celery.scheduled_tasks}</dd></div>
<div class="flex justify-between"><dt class="text-muted-foreground">Reserved</dt><dd class="font-mono text-xs">{celery.reserved_tasks}</dd></div>
</dl>
{#if celeryQueues.length > 0}
<div>
<p class="section-label mb-1">Queue lengths</p>
{#each celeryQueues as [q, len] (q)}
<div class="flex justify-between text-sm"><span>{q}</span><span class="font-mono text-xs">{len}</span></div>
{/each}
</div>
{/if}
{:else}
<p class="text-sm text-muted-foreground">No workers reachable</p>
{/if}
</Card.Content>
</Card.Root>

<Card.Root>
<Card.Header class="pb-2"><Card.Title>Database</Card.Title></Card.Header>
<Card.Content class="space-y-3">
<dl class="space-y-1 text-sm">
<div class="flex justify-between"><dt class="text-muted-foreground">Backends</dt><dd class="font-mono text-xs">{dbStats.numbackends}</dd></div>
<div class="flex justify-between"><dt class="text-muted-foreground">Commits</dt><dd class="font-mono text-xs">{dbStats.xact_commit?.toLocaleString()}</dd></div>
<div class="flex justify-between"><dt class="text-muted-foreground">Rollbacks</dt><dd class="font-mono text-xs">{dbStats.xact_rollback?.toLocaleString()}</dd></div>
<div class="flex justify-between"><dt class="text-muted-foreground">Deadlocks</dt><dd class="font-mono text-xs {dbStats.deadlocks > 0 ? 'text-danger-fg' : ''}">{dbStats.deadlocks}</dd></div>
</dl>
{#if topTables.length > 0}
<div>
<p class="section-label mb-1">Largest tables</p>
{#each topTables.slice(0, 5) as t (t.table)}
<div class="flex items-center justify-between gap-2 py-0.5 text-sm">
<span class="truncate font-mono text-xs">{t.table}</span>
<span class="shrink-0 text-xs text-muted-foreground">{t.total_human}</span>
</div>
{/each}
</div>
{/if}
</Card.Content>
</Card.Root>
</div>

<!-- APP STATS -->
<Card.Root class="gap-0 overflow-clip py-0">
<Card.Header class="border-b border-border py-3"><Card.Title>Records</Card.Title></Card.Header>
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
{#each modelSections as model (model.key)}
<tr>
<td class="text-sm font-medium">{model.label}</td>
<td class="num">{appStats[model.key]?.last_24h ?? '—'}</td>
<td class="text-xs text-muted-foreground">
<span class="flex flex-wrap gap-x-3 gap-y-0.5">
{#each byStatus(model.key) as [status, count] (status)}
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

<!-- DOCKER CONTAINERS (apps daemon) -->
<Card.Root class="gap-0 overflow-clip py-0">
<Card.Header class="border-b border-border py-3">
<Card.Title>Apps-daemon containers</Card.Title>
<Card.Description>Everything the apps daemon runs, managed or not.</Card.Description>
</Card.Header>
<Card.Content class="p-0">
{#if containers.length === 0}
<div class="p-4"><EmptyState compact title="No containers found" /></div>
{:else if containers[0]?.error}
<div class="p-4"><ErrorAlert message={`Apps daemon unreachable: ${containers[0].error}`} /></div>
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
{#each containers as c (c.id ?? c.name)}
<tr>
<td class="font-mono text-xs">{c.name}</td>
<td class="max-w-[28rem] truncate font-mono text-xs text-muted-foreground" title={c.image as string}>{c.image}</td>
<td class="whitespace-nowrap"><StatusBadge status={c.status as string} tones={dockerTones} /></td>
<td class="text-xs text-muted-foreground">{c.health && c.health !== 'N/A' ? c.health : '—'}</td>
</tr>
{/each}
</tbody>
</table>
</div>
{/if}
</Card.Content>
</Card.Root>
{/if}
</Tabs.Content>

<Tabs.Content value="maintenance" class="pt-0">
<Card.Root class="gap-0 py-0">
<Card.Header class="border-b border-border py-3">
<Card.Title>Maintenance</Card.Title>
<Card.Description>Repair stuck records and caches. App and image cleanup lives under “Apps &amp; footprint”.</Card.Description>
</Card.Header>
<Card.Content class="divide-y divide-border p-0">
{#each maintenanceActions as action (action.id)}
<div class="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:gap-4">
<div class="min-w-0 flex-1">
<p class="text-sm font-medium">{action.label}</p>
<p class="text-xs text-muted-foreground">{action.desc}</p>
{#if maintResults[action.id]}
<p class="mt-1 text-xs {maintResults[action.id].ok ? 'text-foreground' : 'text-danger-fg'}" role="status">{maintResults[action.id].text}</p>
{/if}
</div>
<Button
variant="outline"
size="sm"
class="sm:w-28"
disabled={maintRunning !== null}
onclick={() => {
maintPending = action;
maintConfirmOpen = true;
}}
>
{#if maintRunning === action.id}<LoaderCircle class="size-3.5 animate-spin" />{/if}
Run
</Button>
</div>
{/each}
</Card.Content>
</Card.Root>
</Tabs.Content>
{/if}
</Tabs.Root>

<ConfirmDialog
bind:open={maintConfirmOpen}
title={maintPending?.label ?? ''}
description={maintPending?.desc ?? ''}
confirmLabel="Run"
busy={maintRunning !== null}
onConfirm={runMaintenance}
/>
