<script lang="ts">
import { onMount } from 'svelte';
import { goto } from '$app/navigation';
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import { Input } from '$lib/components/ui/input';
import { Label } from '$lib/components/ui/label';
import ArrowLeft from '@lucide/svelte/icons/arrow-left';
import Calendar from '@lucide/svelte/icons/calendar';
import Plus from '@lucide/svelte/icons/plus';
import Trash2 from '@lucide/svelte/icons/trash-2';
import RefreshCw from '@lucide/svelte/icons/refresh-cw';
import LoaderCircle from '@lucide/svelte/icons/loader-circle';
import {
	listSchedules,
	createSchedule,
	setScheduleEnabled,
	deleteSchedule,
	listPipelines,
	type ScheduleSummary,
	type PipelineListItem
} from '$lib/api/client';

const CRON_PRESETS = [
	{ label: 'Every hour', value: '0 * * * *' },
	{ label: 'Daily 9am', value: '0 9 * * *' },
	{ label: 'Daily midnight', value: '0 0 * * *' },
	{ label: 'Weekly Monday 9am', value: '0 9 * * 1' },
	{ label: 'Every 30 min', value: '*/30 * * * *' },
	{ label: 'Every 5 min', value: '*/5 * * * *' },
];

let schedules = $state<ScheduleSummary[]>([]);
let total = $state(0);
let loading = $state(true);
let error = $state('');
let showForm = $state(false);
let pipelines = $state<PipelineListItem[]>([]);
let newPipelineId = $state('');
let newCron = $state('0 * * * *');
let newEnabled = $state(true);
let saving = $state(false);
let formError = $state('');

// Map pipeline_id → name for display
let pipelineMap = $state<Record<string, string>>({});

async function load() {
	loading = true;
	try {
		const res = await listSchedules();
		schedules = res.items;
		total = res.total;
		error = '';
	} catch (e) {
		error = 'Failed to load schedules';
	} finally {
		loading = false;
	}
}

async function loadPipelines() {
	const res = await listPipelines({ per_page: 100 });
	pipelines = res.items;
	pipelineMap = Object.fromEntries(res.items.map((p) => [p.id, p.name]));
	if (pipelines.length > 0) newPipelineId = pipelines[0].id;
}

async function create() {
	if (!newPipelineId) { formError = 'Select a pipeline'; return; }
	saving = true;
	formError = '';
	try {
		await createSchedule({ pipeline_id: newPipelineId, cron_expression: newCron, enabled: newEnabled });
		showForm = false;
		await load();
	} catch (e: unknown) {
		const body = e as { errors?: string[]; detail?: string };
		formError = body?.errors?.[0] ?? body?.detail ?? 'Failed to create schedule';
	} finally {
		saving = false;
	}
}

async function toggle(sched: ScheduleSummary) {
	await setScheduleEnabled(sched.id, !sched.enabled);
	await load();
}

async function remove(id: string) {
	if (!confirm('Delete this schedule?')) return;
	await deleteSchedule(id);
	await load();
}

function fmt(s: string | null) { return s ? new Date(s).toLocaleString() : '—'; }

onMount(() => {
	load();
	loadPipelines();
});
</script>

<svelte:head><title>Schedules — LLM Eval Lab</title></svelte:head>

<div class="space-y-6">
	<nav aria-label="Breadcrumb" class="flex items-center gap-2 text-sm text-muted-foreground">
		<a href="/automation" class="hover:text-foreground transition-colors flex items-center gap-1">
			<ArrowLeft class="h-3.5 w-3.5" />
			<span class="font-medium text-foreground">Automation</span>
		</a>
		<span>/</span>
		<span>Schedules</span>
	</nav>
	<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
		<div class="page-header min-w-0">
			<h1>Schedules</h1>
			<p>{total} schedule{total !== 1 ? 's' : ''}</p>
		</div>
		<div class="flex flex-wrap items-center gap-2">
			<Button variant="outline" size="sm" onclick={load}><RefreshCw class="mr-2 h-4 w-4" />Refresh</Button>
			<Button size="sm" onclick={() => showForm = !showForm}><Plus class="mr-2 h-4 w-4" />New Schedule</Button>
		</div>
	</div>

	{#if showForm}
		<Card.Root>
			<Card.Header><Card.Title>New Schedule</Card.Title></Card.Header>
			<Card.Content class="space-y-4">
				<div class="space-y-2">
					<Label>Pipeline</Label>
					<select bind:value={newPipelineId} class="w-full rounded-md border bg-background px-3 py-2 text-sm">
						{#each pipelines as p}<option value={p.id}>{p.name}</option>{/each}
					</select>
					<p class="text-xs text-muted-foreground">Pipeline that will run on schedule.</p>
				</div>
				<div class="space-y-2">
					<Label>Cron Expression</Label>
					<!-- Presets -->
					<div class="flex flex-wrap gap-1.5">
						{#each CRON_PRESETS as preset}
							<button
								type="button"
								onclick={() => newCron = preset.value}
								class="rounded-full border px-2.5 py-0.5 text-xs transition-colors {newCron === preset.value ? 'bg-primary text-primary-foreground border-primary' : 'bg-background text-muted-foreground hover:border-primary/50'}"
							>{preset.label}</button>
						{/each}
					</div>
					<Input bind:value={newCron} placeholder="0 * * * *" class="font-mono" />
					<p class="text-xs text-muted-foreground">5-field cron: minute hour day month weekday — current: <strong class="font-mono">{newCron}</strong></p>
				</div>
				<div class="flex items-center gap-2">
					<input type="checkbox" id="enabled" bind:checked={newEnabled} class="rounded" />
					<Label for="enabled">Enabled immediately</Label>
				</div>
				{#if formError}<p class="text-sm text-destructive">{formError}</p>{/if}
				<div class="flex justify-end gap-2">
					<Button variant="outline" onclick={() => showForm = false}>Cancel</Button>
					<Button onclick={create} disabled={saving}>{saving ? 'Creating...' : 'Create'}</Button>
				</div>
			</Card.Content>
		</Card.Root>
	{/if}

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
	{:else if schedules.length === 0}
		<Card.Root>
			<Card.Content class="py-16 text-center">
				<Calendar class="mx-auto h-12 w-12 text-muted-foreground/50 mb-4" />
				<h3 class="text-lg font-medium mb-1">No schedules yet</h3>
				<p class="text-sm text-muted-foreground mb-4">Create a schedule to automatically run a pipeline on a cron expression.</p>
				<Button size="sm" onclick={() => showForm = true}>New Schedule</Button>
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
									<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Pipeline</th>
									<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Cron</th>
									<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Enabled</th>
									<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Next Run</th>
									<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Last Run</th>
									<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground whitespace-nowrap">Actions</th>
								</tr>
							</thead>
							<tbody>
								{#each schedules as sched, i (sched.id)}
									<tr class="border-b transition-colors hover:bg-muted/50 group {i % 2 === 0 ? '' : 'bg-muted/15'}">
										<td class="px-3 py-2 align-top">
											<button class="text-sm font-medium hover:underline text-left" onclick={() => goto(`/automation/${sched.pipeline_id}`)}>
												{pipelineMap[sched.pipeline_id] ?? sched.pipeline_id.slice(0, 8) + '…'}
											</button>
										</td>
										<td class="px-3 py-2 align-top font-mono text-xs">{sched.cron_expression}</td>
										<td class="px-3 py-2 align-top">
											<button onclick={() => toggle(sched)} title="Toggle enabled" class="cursor-pointer">
												<Badge variant="outline" class="text-[10px] {sched.enabled ? 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30' : 'bg-slate-500/15 text-slate-400 border-slate-500/30'}">
													{sched.enabled ? 'On' : 'Off'}
												</Badge>
											</button>
										</td>
										<td class="px-3 py-2 align-top text-sm text-muted-foreground">{fmt(sched.next_run_at)}</td>
										<td class="px-3 py-2 align-top text-sm text-muted-foreground">{fmt(sched.last_run_at)}</td>
										<td class="px-3 py-2">
											<div class="flex items-center justify-end gap-1">
												<Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="Delete" onclick={() => remove(sched.id)}>
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
			{#each schedules as sched (sched.id)}
				<div class="border rounded-lg p-3 bg-card">
					<div class="flex items-start justify-between gap-2 mb-2">
						<button class="text-sm font-medium hover:underline truncate text-left" onclick={() => goto(`/automation/${sched.pipeline_id}`)}>
							{pipelineMap[sched.pipeline_id] ?? sched.pipeline_id.slice(0, 8) + '…'}
						</button>
						<button onclick={() => toggle(sched)} title="Toggle enabled" class="cursor-pointer shrink-0">
							<Badge variant="outline" class="text-[10px] {sched.enabled ? 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30' : 'bg-slate-500/15 text-slate-400 border-slate-500/30'}">
								{sched.enabled ? 'On' : 'Off'}
							</Badge>
						</button>
					</div>
					<div class="text-xs space-y-1 mb-2">
						<div class="font-mono">{sched.cron_expression}</div>
						<div class="text-muted-foreground">Next: {fmt(sched.next_run_at)}</div>
						<div class="text-muted-foreground">Last: {fmt(sched.last_run_at)}</div>
					</div>
					<div class="flex items-center justify-end gap-1 border-t pt-2">
						<Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="Delete" onclick={() => remove(sched.id)}>
							<Trash2 class="h-3.5 w-3.5 text-destructive" />
						</Button>
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>
