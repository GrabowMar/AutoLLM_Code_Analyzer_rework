<script lang="ts">
	/**
	 * AppsTab — the task's app pool, de-blinded for the researcher, with hosting.
	 *
	 * - Hosting summary: running apps against the task and server caps, queue,
	 *   reachability; Start all (pins + starts every active app, the rest queue)
	 *   and Stop all (unpins + stops what the task started).
	 * - Pool table: blinded label, app spec, model / condition, functional
	 *   status, hosting state with Start / Stop / Pin / Unpin / Rebuild / Open,
	 *   rating counts, the active toggle, and (draft only) remove.
	 * - Draft only: add apps (AppPoolPicker).
	 * Hosting is polled every 5 s while any app is queued, building, starting or stopping.
	 *
	 * Props: `task`, `environment`, `onreload()` (refresh the page's counts).
	 */
	import { onDestroy, untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import AppPoolPicker from '$lib/components/human-eval/AppPoolPicker.svelte';
	import * as Card from '$lib/components/ui/card';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';
	import { Button } from '$lib/components/ui/button';
	import { Switch } from '$lib/components/ui/switch';
	import { TableToolbar, tableFrame } from '$lib/components/table';
	import { formatApiError } from '$lib/api/core';
	import {
		addTaskApps,
		appHostingAction,
		functionalSourceLabel,
		functionalStatusLabel,
		getTaskHosting,
		humanEvalErrorBody,
		listTaskApps,
		removeTaskApp,
		taskHostingAction,
		updateTaskApp,
		type AppHostingAction,
		type HumanEvalEnvironment,
		type ResearcherHostingInfo,
		type TaskApp,
		type TaskDetail,
		type TaskHosting,
	} from '$lib/api/human-eval';
	import { cn } from '$lib/utils';
	import {
		HOSTING_ACTION_DONE,
		HOSTING_ACTION_LABELS,
		HOSTING_ERROR_TEXT,
		anyTransitional,
		formatShare,
		hostingActions,
		hostingIsRunning,
		plural,
		repeatSuffix,
		safeHttpUrl,
		type ConfirmCopy,
	} from '../format';
	import HostingCell from './HostingCell.svelte';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import Hammer from '@lucide/svelte/icons/hammer';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Pin from '@lucide/svelte/icons/pin';
	import PinOff from '@lucide/svelte/icons/pin-off';
	import Play from '@lucide/svelte/icons/play';
	import Plus from '@lucide/svelte/icons/plus';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Square from '@lucide/svelte/icons/square';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import type { Component } from 'svelte';

	interface Props {
		task: TaskDetail;
		environment: HumanEvalEnvironment | null;
		onreload?: () => void;
	}

	let { task, environment, onreload }: Props = $props();

	const POLL_MS = 5000;
	const isDraft = $derived(task.status === 'draft');
	const frozen = $derived(!!task.frozen_at);
	const closedOrArchived = $derived(task.status === 'closed' || task.status === 'archived');

	let apps = $state.raw<TaskApp[]>([]);
	let hosting = $state.raw<TaskHosting | null>(null);
	let loading = $state(true);
	let loadError = $state('');

	let loadCtl: AbortController | null = null;
	async function load(quiet = false) {
		loadCtl?.abort();
		const mine = new AbortController();
		loadCtl = mine;
		if (!quiet) loading = true;
		try {
			const [list, summary] = await Promise.all([
				listTaskApps(task.id, { signal: mine.signal }),
				getTaskHosting(task.id, { signal: mine.signal }).catch((e) => {
					if (mine.signal.aborted) throw e;
					return null;
				}),
			]);
			apps = list;
			if (summary) hosting = summary;
			loadError = '';
		} catch (e) {
			if (mine.signal.aborted) return;
			if (!quiet || apps.length === 0) loadError = formatApiError(e, 'Could not load the apps.');
		} finally {
			if (loadCtl === mine) loading = false;
		}
	}

	$effect(() => {
		void task.id;
		untrack(() => void load());
	});

	// ── hosting: merged per-app state + polling ────────────────────
	const hostingById = $derived(new Map((hosting?.apps ?? []).map((h) => [h.app_id, h as ResearcherHostingInfo])));
	function infoOf(app: TaskApp): ResearcherHostingInfo {
		return hostingById.get(app.id) ?? app.hosting;
	}
	const infos = $derived(apps.map((a) => infoOf(a)));
	const transitional = $derived(anyTransitional(infos));
	const reachable = $derived(hosting?.apps_reachable ?? environment?.apps_reachable ?? true);

	let pollCtl: AbortController | null = null;
	async function pollHosting() {
		if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
		pollCtl?.abort();
		const mine = new AbortController();
		pollCtl = mine;
		try {
			hosting = await getTaskHosting(task.id, { signal: mine.signal });
		} catch {
			// Transient: the next tick retries.
		}
	}

	$effect(() => {
		if (!transitional) return;
		const timer = setInterval(() => void pollHosting(), POLL_MS);
		return () => clearInterval(timer);
	});

	onDestroy(() => {
		loadCtl?.abort();
		pollCtl?.abort();
	});

	function setInfo(appId: string, info: ResearcherHostingInfo) {
		if (!hosting) {
			apps = apps.map((a) => (a.id === appId ? { ...a, hosting: info } : a));
			return;
		}
		const label = apps.find((a) => a.id === appId)?.label ?? '';
		const exists = hosting.apps.some((h) => h.app_id === appId);
		hosting = {
			...hosting,
			apps: exists
				? hosting.apps.map((h) => (h.app_id === appId ? { ...info, app_id: appId, label: h.label } : h))
				: [...hosting.apps, { ...info, app_id: appId, label }],
		};
	}

	function errorText(e: unknown, fallback: string): string {
		const body = humanEvalErrorBody(e);
		if (body) return HOSTING_ERROR_TEXT[body.code] ?? body.detail ?? fallback;
		return formatApiError(e, fallback);
	}

	// ── per-app actions ─────────────────────────────────────────────
	let busy = $state<Record<string, string>>({});
	function setBusy(appId: string, what: string | null) {
		const next = { ...busy };
		if (what) next[appId] = what;
		else delete next[appId];
		busy = next;
	}

	async function act(app: TaskApp, action: AppHostingAction) {
		setBusy(app.id, action);
		try {
			const info = await appHostingAction(task.id, app.id, action);
			setInfo(app.id, info);
			toast.success(`${app.label}: ${HOSTING_ACTION_DONE[action]}`);
			void pollHosting();
		} catch (e) {
			toast.error(`${app.label}: ${errorText(e, `Could not ${action} the app.`)}`);
		} finally {
			setBusy(app.id, null);
		}
	}

	function patchApp(appId: string, patch: Partial<TaskApp>) {
		apps = apps.map((a) => (a.id === appId ? { ...a, ...patch } : a));
	}

	async function toggleActive(app: TaskApp, next: boolean) {
		setBusy(app.id, 'active');
		// Optimistic, so a failure flips the switch back through the prop.
		patchApp(app.id, { is_active: next });
		try {
			const updated = await updateTaskApp(task.id, app.id, { is_active: next });
			apps = apps.map((a) => (a.id === app.id ? updated : a));
			toast.success(
				next
					? `${app.label} is active again`
					: frozen
						? `${app.label} deactivated: no new assignments; open ones may finish`
						: `${app.label} deactivated`
			);
			onreload?.();
		} catch (e) {
			patchApp(app.id, { is_active: !next });
			toast.error(errorText(e, 'Could not update the app.'));
		} finally {
			setBusy(app.id, null);
		}
	}

	// ── confirmations ───────────────────────────────────────────────
	type Pending = { kind: 'app'; app: TaskApp; action: AppHostingAction } | { kind: 'remove'; app: TaskApp } | { kind: 'stop_all' } | null;
	let confirmOpen = $state(false);
	let confirming = $state<Pending>(null);
	let confirmCopy = $state<ConfirmCopy>({ title: '', description: '', label: '' });
	let confirmBusy = $state(false);

	function askApp(app: TaskApp, action: AppHostingAction) {
		const needs = action === 'rebuild' || (action === 'stop' && !isDraft && app.stats.in_progress > 0);
		if (!needs) {
			void act(app, action);
			return;
		}
		confirming = { kind: 'app', app, action };
		confirmCopy =
			action === 'rebuild'
				? {
						title: `Rebuild ${app.label}?`,
						description:
							'Removes its container and builds a new one from the generated code. Data stored in the running app is lost, and raters before and after the rebuild used different builds (recorded per assignment). Participants working on it lose the app until the build finishes.',
						label: 'Rebuild',
						destructive: true,
					}
				: {
						title: `Stop ${app.label}?`,
						description: `${plural(app.stats.in_progress, 'participant')} ${app.stats.in_progress === 1 ? 'is' : 'are'} working on this app right now. Stopping it interrupts them until it is started again (on demand or by you).`,
						label: 'Stop app',
						destructive: true,
					};
		confirmOpen = true;
	}

	function askRemove(app: TaskApp) {
		confirming = { kind: 'remove', app };
		confirmCopy = {
			title: `Remove ${app.label} from the pool?`,
			description: 'The app leaves this draft task. You can add it again before opening.',
			label: 'Remove',
			destructive: true,
		};
		confirmOpen = true;
	}

	function askStopAll() {
		confirming = { kind: 'stop_all' };
		confirmCopy = {
			title: 'Stop all apps?',
			description:
				'Unpins every app and stops every container this task started. Participants who are testing right now lose their app until it is started again (on demand, if enabled).',
			label: 'Stop all',
			destructive: true,
		};
		confirmOpen = true;
	}

	async function onConfirm() {
		const c = confirming;
		if (!c) return;
		confirmBusy = true;
		try {
			if (c.kind === 'app') {
				confirmOpen = false;
				await act(c.app, c.action);
			} else if (c.kind === 'remove') {
				await removeTaskApp(task.id, c.app.id);
				apps = apps.filter((a) => a.id !== c.app.id);
				confirmOpen = false;
				toast.success(`${c.app.label} removed`);
				onreload?.();
			} else {
				await taskAction('stop_all');
				confirmOpen = false;
			}
		} catch (e) {
			confirmOpen = false;
			toast.error(errorText(e, 'The action failed.'));
		} finally {
			confirmBusy = false;
		}
	}

	// ── task-wide hosting ───────────────────────────────────────────
	let taskBusy = $state<'start_all' | 'stop_all' | null>(null);
	async function taskAction(action: 'start_all' | 'stop_all') {
		taskBusy = action;
		try {
			hosting = await taskHostingAction(task.id, action);
			toast.success(
				action === 'start_all'
					? `Pinned and starting the active apps: ${hosting.running} running, ${hosting.queued} queued`
					: 'Unpinned all apps and stopped the ones this task started'
			);
		} catch (e) {
			toast.error(errorText(e, action === 'start_all' ? 'Could not start the apps.' : 'Could not stop the apps.'));
		} finally {
			taskBusy = null;
		}
	}

	// ── add apps (draft) ────────────────────────────────────────────
	let addOpen = $state(false);
	let addSelected = $state<string[]>([]);
	let addStudy = $state<string | null>(null);
	let adding = $state(false);
	const existingRunIds = $derived(apps.map((a) => a.run_id).filter((id): id is string => !!id));

	function openAdd() {
		addSelected = [];
		addStudy = task.study?.id ?? null;
		addOpen = true;
	}

	async function addApps(whole: boolean) {
		adding = true;
		try {
			const before = apps.length;
			await addTaskApps(task.id, whole && addStudy ? { study_id: addStudy, current_only: true } : { run_ids: addSelected });
			await load(true);
			const added = apps.length - before;
			toast.success(added > 0 ? `Added ${plural(added, 'app')}` : 'Those apps were already in the pool');
			addOpen = false;
			onreload?.();
		} catch (e) {
			toast.error(errorText(e, 'Could not add the apps.'));
		} finally {
			adding = false;
		}
	}

	// ── display ─────────────────────────────────────────────────────
	const ACTION_ICONS: Record<AppHostingAction, Component> = {
		start: Play,
		stop: Square,
		pin: Pin,
		unpin: PinOff,
		rebuild: Hammer,
	};
	const ACTION_TITLES: Record<AppHostingAction, string> = {
		start: 'Start the app (counts against the running-app caps)',
		stop: 'Stop the app (also containers the task did not start)',
		pin: 'Pin: keep it running (never idle-stopped or evicted)',
		unpin: 'Unpin: idle auto-stop applies again',
		rebuild: 'Remove the container and build it again',
	};
	const groupCount = $derived(new Set(apps.map((a) => a.group_key)).size);
	const activeCount = $derived(apps.filter((a) => a.is_active).length);
</script>

<div class="space-y-4">
	{#if !reachable}
		<Alert class="border-warning/40 bg-warning/5">
			<TriangleAlert class="text-warning-fg" />
			<AlertDescription>
				Participants can't open apps in the current routing mode ({environment?.routing_mode ?? 'path'}). Apps still run for
				you, but the portal can only show them with subdomain (or Traefik) routing.
			</AlertDescription>
		</Alert>
	{/if}

	<Card.Root class="gap-0 py-0">
		<div class="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
			<div class="min-w-0 space-y-1">
				<Card.Title>Hosting</Card.Title>
				{#if hosting}
					<div class="header-stats">
						<span class="header-stat" title="Running apps of this task / the task's limit">
							<b>{hosting.running}/{hosting.task_max_running}</b> running
						</span>
						<span class="header-stat" title="Apps waiting for a free slot or build slot"><b>{hosting.queued}</b> queued</span>
						<span class="header-stat" title="Task-started apps across all tasks / the server-wide limit">
							<b>{hosting.global_running}/{hosting.global_max_running}</b> server-wide
						</span>
						<span class="header-stat">
							idle stop after <b>{task.settings.hosting.idle_stop_minutes}</b> min
						</span>
						<span class="header-stat">on demand <b class="font-sans">{task.settings.hosting.on_demand ? 'on' : 'off'}</b></span>
						<span class="header-stat">isolation <b class="font-sans">{task.settings.hosting.isolation}</b></span>
						{#if task.settings.hosting.reset === 'per_assignment'}
							<span class="header-stat"><b class="font-sans">reset</b> per assignment</span>
						{/if}
						{#if transitional}
							<span class="header-stat text-info-fg"><LoaderCircle class="size-3 animate-spin motion-reduce:animate-none" aria-hidden="true" />updating every 5 s</span>
						{/if}
					</div>
				{:else if !loading}
					<p class="text-xs text-muted-foreground">Hosting status unavailable.</p>
				{/if}
			</div>
			<div class="flex flex-wrap items-center gap-2">
				<Button variant="ghost" size="sm" onclick={() => load(true)} aria-label="Refresh apps and hosting">
					<RefreshCw />
				</Button>
				<Button variant="outline" size="sm" disabled={taskBusy !== null || apps.length === 0} onclick={askStopAll}>
					{#if taskBusy === 'stop_all'}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Square />{/if}Stop all
				</Button>
				<Button
					size="sm"
					disabled={taskBusy !== null || activeCount === 0 || closedOrArchived}
					title={closedOrArchived ? 'The task is closed' : 'Pin and start every active app; apps beyond capacity wait in the queue'}
					onclick={() => taskAction('start_all')}
				>
					{#if taskBusy === 'start_all'}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Play />{/if}Start all
				</Button>
			</div>
		</div>
	</Card.Root>

	{#if loadError}
		<ErrorAlert message={loadError} onretry={() => load()} />
	{:else if loading && apps.length === 0}
		<LoadingState label="loading apps" />
	{:else if apps.length === 0}
		<EmptyState icon={AppWindow} title="No apps in the pool" description={isDraft ? 'Add generated apps to evaluate.' : 'This task has no apps.'}>
			{#snippet action()}
				{#if isDraft}<Button size="sm" onclick={openAdd}><Plus />Add apps</Button>{/if}
			{/snippet}
		</EmptyState>
	{:else}
		<Card.Root class="gap-0 overflow-clip py-0">
			<TableToolbar>
				{#snippet summary()}
					{plural(apps.length, 'app')} · {activeCount} active · {plural(groupCount, 'spec group')}
					{#if !frozen} · labels are provisional and shuffled when the task opens{/if}
				{/snippet}
				{#snippet controls()}
					{#if isDraft}<Button size="sm" class="h-7 text-xs" onclick={openAdd}><Plus />Add apps</Button>{/if}
				{/snippet}
			</TableToolbar>
			<div class="table-frame" {@attach tableFrame}>
				<table class="min-w-[64rem]">
					<thead>
						<tr>
							<th class="col-sticky">App</th>
							<th>Model · condition</th>
							<th title="Automated functional check of the generated app (the EvalSuite tester); not measured is never a pass or a 0">Functional</th>
							<th>Hosting</th>
							<th class="num" title="Submitted / in progress / skipped (platform unavailable)">Ratings</th>
							<th title="Inactive apps get no new assignments">Active</th>
							<th class="text-right">Actions</th>
						</tr>
					</thead>
					<tbody>
						{#each apps as app (app.id)}
							{@const info = infoOf(app)}
							{@const url = hostingIsRunning(info) ? safeHttpUrl(info.url) : null}
							{@const working = busy[app.id]}
							<tr class={cn(!app.is_active && 'opacity-60')}>
								<td class="col-sticky min-w-44">
									<span class="font-mono font-medium">{app.label}</span>
									<span class="cell-sub block text-xs text-muted-foreground" title="Spec group: {app.group_key || 'none'}">
										{app.app_name || app.app_slug || 'no spec'}{repeatSuffix(app.repeat_index)}
									</span>
									{#if app.run_id}
										<a class="cell-sub block text-[11px] text-muted-foreground hover:text-primary" href="/samples/{app.run_id}">run {app.run_id.slice(0, 8)}</a>
									{/if}
								</td>
								<td class="max-w-64">
									<span class="block truncate" title={app.model_name}>{app.model_name || '—'}</span>
									{#if app.condition_label}
										<span class="cell-sub block truncate text-xs text-muted-foreground" title={app.condition_label}>{app.condition_label}</span>
									{/if}
								</td>
								<td>
									{#if app.functional.status}
										<StatusBadge status={app.functional.status} label={functionalStatusLabel(app.functional.status)} />
									{:else}
										<span class="text-xs text-muted-foreground">not measured</span>
									{/if}
									<span class="cell-sub block text-xs text-muted-foreground">
										{#if app.functional.pass_rate != null}{formatShare(app.functional.pass_rate)} pass{/if}
										{#if app.functional.deployed === false} · not deployed{:else if app.functional.deployed === true} · deployed{/if}
										{#if functionalSourceLabel(app.functional.source)} · {functionalSourceLabel(app.functional.source)}{/if}
									</span>
								</td>
								<td class="min-w-48"><HostingCell {info} {reachable} /></td>
								<td class="num text-xs">
									<span title="submitted">{app.stats.submitted}</span>
									<span class="text-muted-foreground"> / <span title="in progress">{app.stats.in_progress}</span> / <span title="skipped">{app.stats.skipped}</span></span>
									{#if app.stats.unavailable_skips > 0}
										<span class="block text-[11px] text-warning-fg" title="Skips because the platform could not provide the app">{app.stats.unavailable_skips} unavailable</span>
									{/if}
								</td>
								<td>
									<Switch
										size="sm"
										checked={app.is_active}
										disabled={!!working || task.status === 'archived'}
										label="{app.is_active ? 'Deactivate' : 'Activate'} {app.label}"
										onCheckedChange={(v) => toggleActive(app, v)}
									/>
								</td>
								<td>
									<div class="flex items-center justify-end gap-0.5">
										{#each hostingActions(info) as action (action)}
											{@const Icon = ACTION_ICONS[action]}
											{@const disabled = !!working || !app.run_id || (action === 'start' && closedOrArchived)}
											<Button
												variant="ghost"
												size="sm"
												class="size-8 p-0"
												{disabled}
												title={ACTION_TITLES[action]}
												aria-label="{HOSTING_ACTION_LABELS[action]} {app.label}"
												onclick={() => askApp(app, action)}
											>
												{#if working === action}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Icon />{/if}
											</Button>
										{/each}
										{#if url}
											<Button
												variant="ghost"
												size="sm"
												class="size-8 p-0"
												href={url}
												target="_blank"
												rel="noopener noreferrer"
												title="Open the running app in a new tab"
												aria-label="Open {app.label}"
											>
												<ExternalLink />
											</Button>
										{/if}
										{#if isDraft}
											<Button
												variant="ghost"
												size="sm"
												class="size-8 p-0 text-danger-fg"
												disabled={!!working}
												title="Remove from the pool"
												aria-label="Remove {app.label}"
												onclick={() => askRemove(app)}
											>
												<Trash2 />
											</Button>
										{/if}
									</div>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</Card.Root>
		<p class="text-xs text-muted-foreground">
			Model and condition are shown to you only; participants see the blinded label. Deactivating an app after opening stops new
			assignments for it (its items are deactivated); work already started may finish.
		</p>
	{/if}
</div>

<ConfirmDialog
	bind:open={confirmOpen}
	title={confirmCopy.title}
	description={confirmCopy.description}
	confirmLabel={confirmCopy.label}
	destructive={confirmCopy.destructive}
	busy={confirmBusy}
	{onConfirm}
/>

<Dialog.Root bind:open={addOpen}>
	<Dialog.Content class="max-h-[90vh] max-w-4xl overflow-y-auto">
		<Dialog.Header>
			<Dialog.Title>Add apps</Dialog.Title>
			<Dialog.Description>Pick completed app runs, or add a whole study. Apps already in the pool are skipped.</Dialog.Description>
		</Dialog.Header>
		<div class="py-2">
			<AppPoolPicker bind:selected={addSelected} bind:studyId={addStudy} excludeRunIds={existingRunIds} />
		</div>
		<Dialog.Footer class="flex-wrap gap-2">
			<Button variant="outline" size="sm" disabled={adding} onclick={() => (addOpen = false)}>Cancel</Button>
			{#if addStudy}
				<Button variant="outline" size="sm" disabled={adding} onclick={() => addApps(true)} title="The current run of every main-stage cell of the study">
					Add whole study
				</Button>
			{/if}
			<Button size="sm" disabled={adding || addSelected.length === 0} aria-busy={adding} onclick={() => addApps(false)}>
				{#if adding}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}
				Add {addSelected.length > 0 ? plural(addSelected.length, 'app') : 'apps'}
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
