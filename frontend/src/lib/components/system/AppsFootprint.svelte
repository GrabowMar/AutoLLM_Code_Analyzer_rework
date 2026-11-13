<script lang="ts">
	/**
	 * AppsFootprint — the System page's "Apps & footprint" tab: what the preview
	 * apps cost right now (memory, apps-daemon disk), one-click bulk actions
	 * (stop all, remove, prune images, apply the memory cap) and the standing
	 * rules that keep the footprint small (idle stop, running cap, per-app
	 * memory limit, periodic prune). Polls every 10 s while mounted.
	 */
	import { onMount, onDestroy } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import NumberInput from '$lib/components/slots/NumberInput.svelte';
	import { Switch } from '$lib/components/ui/switch';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { tableFrame } from '$lib/components/table';
	import { containerTones } from '$lib/constants/colors';
	import { formatApiError } from '$lib/api/core';
	import {
		getFootprint,
		updateFootprintSettings,
		stopAllApps,
		removeApps,
		stopIdleApps,
		applyAppMemLimit,
		pruneAppsDaemon,
		stopContainer,
		removeContainer,
		type FootprintOverview,
		type FootprintSettings,
	} from '$lib/api/client';
	import { formatBytes, formatSpan, parseDockerSize } from './format';
	import Square from '@lucide/svelte/icons/square';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import Clock from '@lucide/svelte/icons/clock';
	import HardDrive from '@lucide/svelte/icons/hard-drive';
	import Eraser from '@lucide/svelte/icons/eraser';
	import Gauge from '@lucide/svelte/icons/gauge';
	import Bomb from '@lucide/svelte/icons/bomb';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Boxes from '@lucide/svelte/icons/boxes';

	let data = $state<FootprintOverview | null>(null);
	let error = $state<string | null>(null);
	let loading = $state(true);
	let timer: ReturnType<typeof setInterval> | undefined;

	// Settings draft (edited locally, saved explicitly).
	type NumKey = 'idle_stop_minutes' | 'max_running_apps' | 'app_mem_limit_mb' | 'auto_prune_interval_hours';
	type Draft = Omit<FootprintSettings, NumKey> & Record<NumKey, number | null>;
	let draft = $state<Draft | null>(null);
	let saving = $state(false);
	let saveMsg = $state<{ ok: boolean; text: string } | null>(null);

	// Quick actions.
	let idleMinutes = $state<number | null>(60);
	let running = $state<string | null>(null);
	let results = $state<Record<string, { ok: boolean; text: string }>>({});
	let confirmOpen = $state(false);
	let pending = $state<QuickAction | null>(null);
	let rowBusy = $state<Record<string, boolean>>({});

	async function load() {
		try {
			const next = await getFootprint();
			data = next;
			error = null;
			if (!draft) {
				draft = { ...next.settings };
				idleMinutes = next.settings.idle_stop_minutes;
			}
		} catch (e) {
			error = formatApiError(e);
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		load();
		timer = setInterval(load, 10_000);
	});
	onDestroy(() => timer && clearInterval(timer));

	const settings = $derived(data?.settings);
	const apps = $derived(data?.apps ?? []);
	const runningApps = $derived(apps.filter((a) => a.status === 'running'));
	const stoppedApps = $derived(apps.filter((a) => a.status === 'stopped' || a.status === 'failed'));
	const disk = $derived(data?.daemon.disk);
	const budget = $derived(parseDockerSize(data?.daemon.mem_budget));
	const appsMem = $derived(data?.daemon.apps_mem_bytes ?? 0);
	const otherMem = $derived(data?.daemon.other_mem_bytes ?? 0);
	const draftValid = $derived(
		!!draft &&
			draft.idle_stop_minutes != null &&
			draft.idle_stop_minutes >= 5 &&
			draft.max_running_apps != null &&
			draft.max_running_apps >= 0 &&
			draft.app_mem_limit_mb != null &&
			draft.app_mem_limit_mb >= 64 &&
			draft.app_mem_limit_mb <= 8192 &&
			draft.auto_prune_interval_hours != null &&
			draft.auto_prune_interval_hours >= 1,
	);
	const dirty = $derived(
		!!draft &&
			!!settings &&
			(
				[
					'idle_stop_enabled',
					'idle_stop_minutes',
					'max_running_apps',
					'app_mem_limit_mb',
					'auto_prune_enabled',
					'auto_prune_interval_hours',
				] as const
			).some((k) => draft![k] !== settings[k]),
	);

	interface QuickAction {
		id: string;
		label: string;
		desc: string;
		icon: typeof Square;
		destructive?: boolean;
		button: string;
		confirm: string;
		disabled?: () => boolean;
		run: () => Promise<string>;
	}

	function plural(n: number, word: string) {
		return `${n} ${word}${n === 1 ? '' : 's'}`;
	}

	const actions: QuickAction[] = [
		{
			id: 'stop-all',
			button: 'Stop all',
			label: 'Stop all running apps',
			desc: 'Frees their memory at once. Images stay cached, so any app starts again in seconds.',
			icon: Square,
			confirm: 'Every running preview app is stopped. Apps with an action in progress are skipped.',
			disabled: () => runningApps.length === 0,
			run: async () => {
				const r = await stopAllApps();
				return `Stopping ${plural(r.stopping, 'app')}${r.skipped ? ` · ${r.skipped} skipped (busy)` : ''}.`;
			},
		},
		{
			id: 'stop-idle',
			button: 'Stop idle',
			label: 'Stop idle apps',
			desc: 'Stops apps nobody opened (or started) within the window below. Human-eval apps are left to their task.',
			icon: Clock,
			confirm: 'Running apps unused for the chosen number of minutes are stopped.',
			disabled: () => runningApps.length === 0 || !idleMinutes || idleMinutes < 1,
			run: async () => {
				const r = await stopIdleApps(idleMinutes ?? 60);
				return r.idle ? `Stopping ${plural(r.idle, 'idle app')}.` : 'No app was idle that long.';
			},
		},
		{
			id: 'remove-stopped',
			button: 'Remove',
			label: 'Remove stopped apps',
			desc: 'Deletes stopped and failed app containers and frees their ports and disk. Running apps are kept.',
			icon: Trash2,
			confirm: 'Stopped and failed preview apps are removed. They can be rebuilt from their run later.',
			disabled: () => stoppedApps.length === 0,
			run: async () => {
				const r = await removeApps(false);
				return `Removing ${plural(r.removing, 'app')}${r.skipped ? ` · ${r.skipped} skipped (busy)` : ''}.`;
			},
		},
		{
			id: 'prune',
			button: 'Prune',
			label: 'Prune unused images',
			desc: 'Old tool-image versions, images of removed apps, dangling layers, build cache and expired sample-cache entries. Current images are kept.',
			icon: Eraser,
			confirm: 'Safe prune of the apps daemon. Nothing a queued or running run needs is removed.',
			run: async () => pruneSummary(await pruneAppsDaemon(false)),
		},
		{
			id: 'apply-mem',
			button: 'Apply',
			label: 'Apply memory limit now',
			desc: 'Pushes the per-app memory limit below onto the apps that are already running (no restart).',
			icon: Gauge,
			confirm: 'Running apps get the configured memory limit. An app using more than the new limit keeps its old one.',
			disabled: () => runningApps.length === 0,
			run: async () => {
				const r = await applyAppMemLimit();
				return `${plural(r.updated, 'app')} capped at ${r.limit_mb} MB${r.failed.length ? ` · ${r.failed.length} failed (${r.failed[0].error})` : ''}.`;
			},
		},
	];

	const dangerActions: QuickAction[] = [
		{
			id: 'remove-all',
			button: 'Remove all',
			label: 'Remove all apps',
			desc: 'Stops and deletes every preview app, running ones included.',
			icon: Trash2,
			destructive: true,
			confirm: 'Every preview app is removed, including running ones and apps open in human-eval tasks. Analyzer workspaces are not touched.',
			disabled: () => apps.length === 0,
			run: async () => {
				const r = await removeApps(true);
				return `Removing ${plural(r.removing, 'app')}${r.skipped ? ` · ${r.skipped} skipped (busy)` : ''}.`;
			},
		},
		{
			id: 'prune-deep',
			button: 'Deep prune',
			label: 'Deep prune',
			desc: 'The safe prune plus every other unused image (node, python, golang… base images are pulled again on the next build).',
			icon: Bomb,
			destructive: true,
			confirm: 'All unused images except the current tool images and the sample cache are removed. The next builds will be slower.',
			run: async () => pruneSummary(await pruneAppsDaemon(true)),
		},
	];

	function pruneSummary(r: Awaited<ReturnType<typeof pruneAppsDaemon>>): string {
		const parts = [`Reclaimed ${formatBytes(r.reclaimed_bytes)}`, plural(r.removed_images, 'image')];
		if (r.removed_containers) parts.push(plural(r.removed_containers, 'stray container'));
		if (r.sample_cache_removed) parts.push(`${r.sample_cache_removed} from sample cache`);
		if (r.in_use) parts.push(`${r.in_use} in use, kept`);
		if (r.errors.length) parts.push(`${plural(r.errors.length, 'error')}: ${r.errors[0]}`);
		return parts.join(' · ') + '.';
	}

	function ask(action: QuickAction) {
		pending = action;
		confirmOpen = true;
	}

	async function confirmRun() {
		const action = pending;
		if (!action) return;
		running = action.id;
		try {
			results[action.id] = { ok: true, text: await action.run() };
		} catch (e) {
			results[action.id] = { ok: false, text: formatApiError(e) };
		} finally {
			running = null;
			confirmOpen = false;
			pending = null;
			setTimeout(load, 1500);
		}
	}

	async function save() {
		if (!draft || !draftValid) return;
		saving = true;
		saveMsg = null;
		try {
			const next = await updateFootprintSettings({
				idle_stop_enabled: draft.idle_stop_enabled,
				idle_stop_minutes: draft.idle_stop_minutes!,
				max_running_apps: draft.max_running_apps!,
				app_mem_limit_mb: draft.app_mem_limit_mb!,
				auto_prune_enabled: draft.auto_prune_enabled,
				auto_prune_interval_hours: draft.auto_prune_interval_hours!,
			});
			draft = { ...next };
			if (data) data.settings = next;
			saveMsg = { ok: true, text: 'Saved. The sweep applies it within two minutes; new apps get the memory limit at start.' };
		} catch (e) {
			saveMsg = { ok: false, text: formatApiError(e) };
		} finally {
			saving = false;
		}
	}

	async function rowAction(id: string, kind: 'stop' | 'remove') {
		rowBusy[id] = true;
		try {
			await (kind === 'stop' ? stopContainer(id) : removeContainer(id));
			setTimeout(load, 1500);
		} catch (e) {
			error = formatApiError(e);
		} finally {
			rowBusy[id] = false;
		}
	}

	function sweepSummary(): string {
		const s = settings;
		if (!s?.last_sweep_at) return 'The sweep has not run yet.';
		const r = s.last_sweep_result ?? {};
		const bits: string[] = [];
		if (typeof r.idle === 'number' && r.idle) bits.push(`${r.idle} idle stopped`);
		if (typeof r.over_cap === 'number' && r.over_cap) bits.push(`${r.over_cap} over the cap stopped`);
		if (typeof r.pruned_bytes === 'number') bits.push(`pruned ${formatBytes(r.pruned_bytes)}`);
		const ago = formatSpan((Date.now() - new Date(s.last_sweep_at).getTime()) / 1000);
		return `Last sweep ${ago} ago${bits.length ? ` — ${bits.join(', ')}` : ' — nothing to do'}.`;
	}

	function memPct(used: number | null, limit: number | null): number | null {
		return used != null && limit ? Math.min(100, (used / limit) * 100) : null;
	}
</script>

{#if loading}
	<LoadingState label="loading app footprint" />
{:else if !data}
	<ErrorAlert message={error ?? 'Could not load the app footprint.'} onretry={load} />
{:else}
	<div class="space-y-4">
		{#if error}<ErrorAlert message={error} onretry={load} />{/if}
		{#if !data.daemon.reachable}
			<ErrorAlert message={`Apps daemon unreachable: ${data.daemon.error ?? 'unknown error'}`} onretry={load} />
		{/if}

		<!-- KPIs -->
		<div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
			<div class="kpi-card space-y-1">
				<p class="kpi-label">Running apps</p>
				<p class="kpi-value">{runningApps.length}</p>
				<p class="text-xs text-muted-foreground">
					{settings?.max_running_apps ? `cap ${settings.max_running_apps}` : 'no cap'} · {stoppedApps.length} stopped
				</p>
			</div>
			<div class="kpi-card space-y-1">
				<p class="kpi-label">App memory</p>
				<p class="kpi-value">{formatBytes(appsMem)}</p>
				<p class="text-xs text-muted-foreground">
					{budget ? `of ${formatBytes(budget)} apps-daemon budget` : 'apps-daemon budget unknown'}
				</p>
				{#if budget}
					<span class="block h-1.5 rounded-sm bg-muted" aria-hidden="true">
						<span class="block h-1.5 rounded-sm bg-[var(--chart-1)]" style="width: {Math.min(100, ((appsMem + otherMem) / budget) * 100)}%"></span>
					</span>
				{/if}
			</div>
			<div class="kpi-card space-y-1">
				<p class="kpi-label">Other containers</p>
				<p class="kpi-value">{formatBytes(otherMem)}</p>
				<p class="text-xs text-muted-foreground">
					{plural(data.daemon.other_running?.length ?? 0, 'container')} (analyzers, sandboxes, builders)
				</p>
			</div>
			<div class="kpi-card space-y-1">
				<p class="kpi-label">Apps-daemon images</p>
				<p class="kpi-value">{formatBytes(disk?.images_bytes)}</p>
				<p class="text-xs text-muted-foreground">
					{#if disk}{disk.images_count} images · {formatBytes(disk.images_unused_bytes)} in {disk.images_unused_count} unused{:else}—{/if}
				</p>
			</div>
		</div>

		<!-- QUICK ACTIONS -->
		<Card.Root class="gap-0 py-0">
			<Card.Header class="border-b border-border py-3">
				<Card.Title>Quick cleanup</Card.Title>
				<Card.Description>Each action asks for confirmation. Stop and remove go through the normal app actions, so status and routes stay consistent.</Card.Description>
			</Card.Header>
			<Card.Content class="divide-y divide-border p-0">
				{#each actions as action (action.id)}
					{@const Icon = action.icon}
					<div class="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:gap-4">
						<Icon class="hidden size-4 shrink-0 text-muted-foreground sm:block" aria-hidden="true" />
						<div class="min-w-0 flex-1">
							<p class="text-sm font-medium">{action.label}</p>
							<p class="text-xs text-muted-foreground">{action.desc}</p>
							{#if results[action.id]}
								<p class="mt-1 text-xs {results[action.id].ok ? 'text-foreground' : 'text-danger-fg'}" role="status">{results[action.id].text}</p>
							{/if}
						</div>
						{#if action.id === 'stop-idle'}
							<label class="flex items-center gap-2 text-xs text-muted-foreground">
								unused for
								<NumberInput min={1} class="h-8 w-20" bind:value={idleMinutes} aria-label="Idle minutes" />
								min
							</label>
						{/if}
						<Button
							variant="outline"
							size="sm"
							class="sm:w-36"
							disabled={running !== null || (action.disabled?.() ?? false)}
							onclick={() => ask(action)}
						>
							{#if running === action.id}<LoaderCircle class="size-3.5 animate-spin" />{/if}
							{action.button}
						</Button>
					</div>
				{/each}
			</Card.Content>
			<div class="border-t border-border bg-destructive/5 px-4 py-2">
				<p class="section-label">Danger zone</p>
			</div>
			<div class="divide-y divide-border">
				{#each dangerActions as action (action.id)}
					{@const Icon = action.icon}
					<div class="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:gap-4">
						<Icon class="hidden size-4 shrink-0 text-danger-fg sm:block" aria-hidden="true" />
						<div class="min-w-0 flex-1">
							<p class="text-sm font-medium">{action.label}</p>
							<p class="text-xs text-muted-foreground">{action.desc}</p>
							{#if results[action.id]}
								<p class="mt-1 text-xs {results[action.id].ok ? 'text-foreground' : 'text-danger-fg'}" role="status">{results[action.id].text}</p>
							{/if}
						</div>
						<Button
							variant="destructive"
							size="sm"
							class="sm:w-36"
							disabled={running !== null || (action.disabled?.() ?? false)}
							onclick={() => ask(action)}
						>
							{#if running === action.id}<LoaderCircle class="size-3.5 animate-spin" />{/if}
							{action.button}
						</Button>
					</div>
				{/each}
			</div>
		</Card.Root>

		<!-- STANDING RULES -->
		{#if draft}
			<Card.Root class="gap-0 py-0">
				<Card.Header class="border-b border-border py-3">
					<Card.Title>Keep the footprint small</Card.Title>
					<Card.Description>Standing rules, applied by a sweep every two minutes. {sweepSummary()}</Card.Description>
				</Card.Header>
				<Card.Content class="divide-y divide-border p-0">
					<div class="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
						<div class="min-w-0">
							<p class="text-sm font-medium">Stop idle apps automatically</p>
							<p class="text-xs text-muted-foreground">An app is idle when nobody opened it through the app proxy, started or rebuilt it for this long. Human-eval apps follow their task's own idle rule.</p>
						</div>
						<div class="flex items-center gap-3">
							<Switch bind:checked={draft.idle_stop_enabled} label="Stop idle apps automatically" />
							<NumberInput min={5} class="h-8 w-24" bind:value={draft.idle_stop_minutes} disabled={!draft.idle_stop_enabled} aria-label="Idle minutes before stop" />
							<span class="text-xs text-muted-foreground">min</span>
						</div>
					</div>
					<div class="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
						<div class="min-w-0">
							<p class="text-sm font-medium">Max running apps</p>
							<p class="text-xs text-muted-foreground">Above this, the least recently used apps are stopped. 0 = no cap.</p>
						</div>
						<NumberInput min={0} class="h-8 w-24" bind:value={draft.max_running_apps} aria-label="Max running apps" />
					</div>
					<div class="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
						<div class="min-w-0">
							<p class="text-sm font-medium">Memory limit per app</p>
							<p class="text-xs text-muted-foreground">Hard cap for each new preview app (64–8192 MB). Use “Apply memory limit now” for apps already running.</p>
						</div>
						<div class="flex items-center gap-2">
							<NumberInput min={64} max={8192} step={64} class="h-8 w-24" bind:value={draft.app_mem_limit_mb} aria-label="Memory limit per app in MB" />
							<span class="text-xs text-muted-foreground">MB</span>
						</div>
					</div>
					<div class="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
						<div class="min-w-0">
							<p class="text-sm font-medium">Prune unused images automatically</p>
							<p class="text-xs text-muted-foreground">
								Runs the safe prune on this interval.
								{#if settings?.last_auto_prune_at}Last run {formatSpan((Date.now() - new Date(settings.last_auto_prune_at).getTime()) / 1000)} ago.{/if}
							</p>
						</div>
						<div class="flex items-center gap-3">
							<Switch bind:checked={draft.auto_prune_enabled} label="Prune unused images automatically" />
							<span class="text-xs text-muted-foreground">every</span>
							<NumberInput min={1} class="h-8 w-20" bind:value={draft.auto_prune_interval_hours} disabled={!draft.auto_prune_enabled} aria-label="Prune interval in hours" />
							<span class="text-xs text-muted-foreground">h</span>
						</div>
					</div>
				</Card.Content>
				<div class="flex flex-wrap items-center justify-end gap-3 border-t border-border px-4 py-2.5">
					{#if saveMsg}
						<p class="mr-auto text-xs {saveMsg.ok ? 'text-muted-foreground' : 'text-danger-fg'}" role="status">{saveMsg.text}</p>
					{/if}
					<Button variant="outline" size="sm" disabled={!dirty || saving} onclick={() => settings && (draft = { ...settings })}>Reset</Button>
					<Button size="sm" disabled={!dirty || saving || !draftValid} onclick={save}>
						{#if saving}<LoaderCircle class="size-3.5 animate-spin" />{/if}
						Save rules
					</Button>
				</div>
			</Card.Root>
		{/if}

		<!-- APPS TABLE -->
		<Card.Root class="gap-0 overflow-clip py-0">
			<Card.Header class="border-b border-border py-3">
				<Card.Title>Preview apps</Card.Title>
				<Card.Description>
					{Object.entries(data.counts).map(([status, n]) => `${n} ${status}`).join(' · ') || 'none'} · memory excludes reclaimable page cache
				</Card.Description>
			</Card.Header>
			<Card.Content class="p-0">
				{#if apps.length === 0}
					<div class="p-4"><EmptyState compact icon={Boxes} title="No preview apps" description="Nothing is using the apps daemon besides tools." /></div>
				{:else}
					<div class="table-frame" {@attach tableFrame}>
						<table>
							<thead>
								<tr>
									<th>App</th>
									<th>Status</th>
									<th class="num">Memory</th>
									<th class="num">Idle</th>
									<th>Owner</th>
									<th><span class="sr-only">Actions</span></th>
								</tr>
							</thead>
							<tbody>
								{#each apps as app (app.id)}
									{@const pct = memPct(app.mem_usage, app.mem_limit)}
									<tr>
										<td>
											<a href={`/runtime/${app.id}`} class="font-mono text-xs hover:underline">{app.name}</a>
											{#if app.human_eval}<span class="ml-2 rounded-full border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">human eval</span>{/if}
											{#if app.busy}<span class="ml-2 rounded-full border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">action running</span>{/if}
										</td>
										<td class="whitespace-nowrap"><StatusBadge status={app.status} tones={containerTones} /></td>
										<td class="num whitespace-nowrap">
											{#if app.mem_usage != null}
												<span>{formatBytes(app.mem_usage)}</span>
												<span class="text-muted-foreground"> / {formatBytes(app.mem_limit)}</span>
												{#if pct != null}
													<span class="mt-1 ml-auto block h-1 w-20 rounded-sm bg-muted" aria-hidden="true">
														<span class="block h-1 rounded-sm bg-[var(--chart-1)]" style="width: {pct}%"></span>
													</span>
												{/if}
											{:else}
												<span class="text-muted-foreground">—</span>
											{/if}
										</td>
										<td class="num whitespace-nowrap">
											{#if app.status === 'running'}{formatSpan(app.idle_seconds)}{:else}<span class="text-muted-foreground">—</span>{/if}
										</td>
										<td class="text-xs text-muted-foreground">{app.owner || '—'}</td>
										<td class="text-right whitespace-nowrap">
											{#if app.status === 'running'}
												<Button variant="ghost" size="sm" disabled={rowBusy[app.id] || app.busy} onclick={() => rowAction(app.id, 'stop')}>
													<Square class="size-3.5" />Stop
												</Button>
											{:else}
												<Button variant="ghost" size="sm" disabled={rowBusy[app.id] || app.busy} onclick={() => rowAction(app.id, 'remove')}>
													<Trash2 class="size-3.5" />Remove
												</Button>
											{/if}
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			</Card.Content>
		</Card.Root>

		{#if (data.daemon.other_running?.length ?? 0) > 0 || disk}
			<div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
				{#if (data.daemon.other_running?.length ?? 0) > 0}
					<Card.Root class="gap-0 py-0">
						<Card.Header class="border-b border-border py-3"><Card.Title>Other running containers</Card.Title></Card.Header>
						<Card.Content class="divide-y divide-border px-4 py-1">
							{#each data.daemon.other_running ?? [] as c (c.name)}
								<div class="flex items-center justify-between gap-3 py-1.5 text-sm">
									<span class="truncate font-mono text-xs">{c.name}</span>
									<span class="flex items-center gap-3">
										{#if c.kind}<span class="text-xs text-muted-foreground">{c.kind}</span>{/if}
										<span class="font-mono text-xs tabular-nums">{formatBytes(c.mem_usage)}</span>
									</span>
								</div>
							{/each}
						</Card.Content>
					</Card.Root>
				{/if}
				{#if disk}
					<Card.Root class="gap-0 py-0">
						<Card.Header class="border-b border-border py-3">
							<Card.Title class="flex items-center gap-2"><HardDrive class="size-4 text-muted-foreground" />Apps-daemon disk</Card.Title>
						</Card.Header>
						<Card.Content class="px-4 py-2">
							<dl class="divide-y divide-border text-sm">
								<div class="flex justify-between py-1.5"><dt class="text-muted-foreground">Images ({disk.images_count})</dt><dd class="font-mono text-xs tabular-nums">{formatBytes(disk.images_bytes)}</dd></div>
								<div class="flex justify-between py-1.5"><dt class="text-muted-foreground">Unused images ({disk.images_unused_count})</dt><dd class="font-mono text-xs tabular-nums">{formatBytes(disk.images_unused_bytes)}</dd></div>
								<div class="flex justify-between py-1.5"><dt class="text-muted-foreground">Containers ({disk.containers_count}, writable layers)</dt><dd class="font-mono text-xs tabular-nums">{formatBytes(disk.containers_bytes)}</dd></div>
								<div class="flex justify-between py-1.5"><dt class="text-muted-foreground">Volumes</dt><dd class="font-mono text-xs tabular-nums">{formatBytes(disk.volumes_bytes)}</dd></div>
								<div class="flex justify-between py-1.5"><dt class="text-muted-foreground">Build cache</dt><dd class="font-mono text-xs tabular-nums">{formatBytes(disk.build_cache_bytes)}</dd></div>
							</dl>
						</Card.Content>
					</Card.Root>
				{/if}
			</div>
		{/if}
	</div>
{/if}

<ConfirmDialog
	bind:open={confirmOpen}
	title={pending?.label ?? ''}
	description={pending?.confirm ?? ''}
	confirmLabel={pending?.button ?? 'Confirm'}
	destructive={pending?.destructive ?? false}
	busy={running !== null}
	onConfirm={confirmRun}
/>
