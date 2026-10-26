<script lang="ts">
	// One experiment: header with lifecycle actions, state banners (digest
	// drift, executor, offline endpoints, legacy rows) and five tabs —
	// Overview (live monitor), Results, Runs, Design, Automation. `?tab=` keeps
	// the tab in the URL. Live data comes from one ExperimentLive per launched
	// experiment (SSE with a polling fallback).
	import { onDestroy, onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { toast } from 'svelte-sonner';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import * as Tabs from '$lib/components/ui/tabs';
	import {
		archiveExperiment,
		cancelExperiment,
		deleteExperiment,
		experimentErrorBody,
		exportExperiment,
		getExperiment,
		getPreview,
		launchExperiment,
		pauseExperiment,
		requeueLegacy,
		resumeExperiment,
		type ExperimentDetail,
		type ExperimentPreviewV2,
	} from '$lib/api/experiments';
	import { formatApiError } from '$lib/api/core';
	import { loadAppSpecCatalog } from '$lib/components/generator';
	import type { AppRequirementTemplate } from '$lib/api/generation';
	import { ExperimentLive } from '$lib/components/experiments/live.svelte';
	import {
		MODE_LABELS,
		activeCells,
		downloadBlob,
		effectivePolicyLabel,
		experimentTones,
		formatHours,
		isActive,
		isTerminal,
		pauseReasonLabel,
		plural,
	} from '$lib/components/experiments/format';
	import OverviewTab from '$lib/components/experiments/tabs/OverviewTab.svelte';
	import ResultsTab from '$lib/components/experiments/tabs/ResultsTab.svelte';
	import RunsTab from '$lib/components/experiments/tabs/RunsTab.svelte';
	import DesignTab from '$lib/components/experiments/tabs/DesignTab.svelte';
	import AutomationTab from '$lib/components/experiments/tabs/AutomationTab.svelte';
	import Archive from '@lucide/svelte/icons/archive';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import Copy from '@lucide/svelte/icons/copy';
	import Download from '@lucide/svelte/icons/download';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Pause from '@lucide/svelte/icons/pause';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Play from '@lucide/svelte/icons/play';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import Rocket from '@lucide/svelte/icons/rocket';
	import Square from '@lucide/svelte/icons/square';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import type { PageData } from './$types';
	import type { ExperimentTab } from './+page';

	let { data }: { data: PageData } = $props();

	const experimentId = $derived(data.id);

	let experiment = $state<ExperimentDetail | null>(null);
	let loading = $state(true);
	let loadError = $state('');
	let notFound = $state(false);
	let apps = $state<AppRequirementTemplate[]>([]);
	let live = $state<ExperimentLive | null>(null);
	let refreshSignal = $state(0);
	let tickSignal = $state(0);
	let postRunSignal = $state(0);

	const appNames = $derived(new Map(apps.map((a) => [a.id, a.name])));
	const appName = (id: number | null) => (id == null ? '—' : (appNames.get(id) ?? `App #${id}`));

	let loadSeq = 0;
	async function load(id: string, silent = false) {
		const mine = ++loadSeq;
		if (!silent) loading = true;
		try {
			const detail = await getExperiment(id);
			if (mine !== loadSeq) return;
			experiment = detail;
			loadError = '';
			notFound = false;
			ensureLive(detail);
		} catch (e) {
			if (mine !== loadSeq) return;
			const status = (e as { httpStatus?: number })?.httpStatus;
			if (status === 404) notFound = true;
			else if (!silent || !experiment) loadError = formatApiError(e, 'Failed to load the experiment.');
			else toast.error(formatApiError(e, 'Could not refresh the experiment.'));
		} finally {
			if (mine === loadSeq) loading = false;
		}
	}

	/** Runs were queued here (top-up, schedule "Run now"): it may have reopened. */
	function relaunched() {
		if (!experiment) return;
		void load(experiment.id, true).then(() => live?.restart());
	}

	function ensureLive(detail: ExperimentDetail) {
		if (detail.status === 'draft') {
			live?.destroy();
			live = null;
			return;
		}
		if (live && live.id === detail.id) return;
		live?.destroy();
		const next = new ExperimentLive(detail.id, {
			onState: () => {
				refreshSignal += 1;
				void load(detail.id, true);
			},
			onTick: () => (tickSignal += 1),
			onPostRun: () => (postRunSignal += 1),
		});
		live = next;
		void next.start();
	}

	// Reload when navigating between experiments (the component is reused).
	$effect(() => {
		const id = experimentId;
		untrack(() => {
			live?.destroy();
			live = null;
			experiment = null;
			void load(id);
		});
	});

	onMount(() => {
		loadAppSpecCatalog()
			.then((list) => (apps = list))
			.catch(() => {});
	});
	onDestroy(() => live?.destroy());

	// ── tabs ────────────────────────────────────────────────────────
	const defaultTab = $derived<ExperimentTab>(experiment?.status === 'draft' ? 'design' : 'overview');
	const tab = $derived<ExperimentTab>(data.tab ?? defaultTab);

	function setTab(next: string) {
		const url = new URL(page.url);
		if (next === defaultTab) url.searchParams.delete('tab');
		else url.searchParams.set('tab', next);
		goto(`${url.pathname}${url.search}`, { replaceState: true, keepFocus: true, noScroll: true });
	}

	// ── lifecycle actions ───────────────────────────────────────────
	type Pending = 'launch' | 'pause' | 'resume' | 'cancel' | 'archive' | 'delete' | 'topup' | 'export' | 'requeue' | null;
	let pending = $state<Pending>(null);
	let confirm = $state<'launch' | 'cancel' | 'archive' | 'delete' | 'topup' | 'drift' | null>(null);
	let confirmOpen = $state(false);
	let launchPreview = $state.raw<ExperimentPreviewV2 | null>(null);
	let actionError = $state('');

	function ask(kind: NonNullable<typeof confirm>) {
		confirm = kind;
		confirmOpen = true;
	}

	async function askLaunch(kind: 'launch' | 'topup') {
		if (!experiment) return;
		actionError = '';
		pending = kind;
		try {
			launchPreview = await getPreview(experiment.id);
			const blocking = (launchPreview.issues ?? []).filter((i) => i.blocking);
			if (blocking.length > 0) {
				actionError = `Launch blocked: ${blocking.map((i) => i.detail).join('; ')}`;
				return;
			}
			ask(kind);
		} catch (e) {
			actionError = formatApiError(e, 'Could not plan the launch.');
		} finally {
			pending = null;
		}
	}

	async function run(kind: NonNullable<Pending>, fn: () => Promise<unknown>, success: string) {
		if (!experiment) return;
		pending = kind;
		actionError = '';
		try {
			await fn();
			toast.success(success);
			confirmOpen = false;
			await load(experiment.id, true);
			refreshSignal += 1;
			await live?.restart();
		} catch (e) {
			confirmOpen = false;
			const body = experimentErrorBody(e);
			actionError = body
				? `${body.detail}${body.errors.length ? ` — ${body.errors.map((x) => x.detail).join('; ')}` : ''}`
				: formatApiError(e, `Could not ${kind} the experiment.`);
		} finally {
			pending = null;
		}
	}

	async function onConfirm() {
		const exp = experiment;
		if (!exp) return;
		switch (confirm) {
			case 'launch':
				return run('launch', () => launchExperiment(exp.id, { mode: 'launch' }), 'Experiment launched');
			case 'topup':
				return run('topup', () => launchExperiment(exp.id, { mode: 'top_up' }), 'Failed cells queued again');
			case 'cancel':
				return run('cancel', () => cancelExperiment(exp.id, { scope: 'all' }), 'Experiment cancelled');
			case 'archive':
				return run('archive', () => archiveExperiment(exp.id), 'Experiment archived');
			case 'drift':
				return run('resume', () => resumeExperiment(exp.id, { accept_digest_drift: true }), 'Resumed with the current weights');
			case 'delete':
				pending = 'delete';
				try {
					await deleteExperiment(exp.id);
					toast.success('Experiment deleted');
					await goto('/experiments');
				} catch (e) {
					confirmOpen = false;
					actionError = formatApiError(e, 'Could not delete the experiment.');
				} finally {
					pending = null;
				}
		}
	}

	async function doExport(format: 'json' | 'csv') {
		if (!experiment) return;
		pending = 'export';
		try {
			const blob = await exportExperiment(experiment.id, format);
			downloadBlob(blob, `experiment-${experiment.slug}.${format}`);
		} catch (e) {
			toast.error(formatApiError(e, 'Export failed.'));
		} finally {
			pending = null;
		}
	}

	const status = $derived(live?.status ?? null);
	const counts = $derived(status?.counts ?? experiment?.progress);
	const failedCells = $derived(counts ? counts.failed + counts.cancelled : 0);
	const drift = $derived(experiment?.status === 'paused' && experiment.pause_reason === 'digest_changed');
	const offlineEndpoints = $derived(
		experiment && isActive(experiment.status) ? (status?.endpoints ?? []).filter((e) => !e.online && e.kind !== 'openrouter') : []
	);
	const legacyPending = $derived(status?.legacy_pending ?? 0);

	const confirmCopy = $derived.by((): { title: string; description: string; label: string; destructive?: boolean } => {
		const exp = experiment;
		const p = launchPreview;
		switch (confirm) {
			case 'launch': {
				const eps = (p?.endpoints ?? []).map((e) => `${e.name || e.slug}${e.online ? '' : ' (offline)'}`).join(', ');
				return {
					title: `Launch ${plural(p?.total_jobs ?? exp?.total_cells ?? 0, 'cell')}?`,
					description: `${p ? `${p.conditions} conditions × ${p.apps} apps × ${p.repeats} repeats` : ''}${eps ? ` on ${eps}` : ''}${
						p ? ` · estimated ${formatHours(p.estimate.total_hours)}` : ''
					}. The design is frozen once it launches.`,
					label: 'Launch',
				};
			}
			case 'topup':
				return {
					title: `Re-run ${plural(p?.pending_jobs ?? failedCells, 'failed cell')}?`,
					description: `New runs reuse each cell's original seed. ${exp && isTerminal(exp.status) ? 'The experiment reopens until they finish. ' : ''}Completed runs are kept.`,
					label: 'Top up',
				};
			case 'cancel':
				return {
					title: 'Cancel this experiment?',
					description: 'Queued runs are dropped and running ones stopped. Runs being evaluated finish and are graded.',
					label: 'Cancel experiment',
					destructive: true,
				};
			case 'archive':
				return { title: 'Archive this experiment?', description: 'It leaves the active list; runs and results are kept.', label: 'Archive' };
			case 'delete':
				return { title: 'Delete this experiment?', description: 'The draft and its conditions are removed. This cannot be undone.', label: 'Delete', destructive: true };
			case 'drift':
				return {
					title: 'Resume with the new weights?',
					description:
						'A model was updated on its endpoint. Remaining runs use the new weights; results will show mixed fingerprints for the affected conditions. Clone the experiment instead for a clean comparison.',
					label: 'Resume anyway',
				};
			default:
				return { title: '', description: '', label: '' };
		}
	});
</script>

<div class="space-y-6">
	<PageHeader title={experiment?.name ?? 'Experiment'} back={{ href: '/experiments', label: 'Experiments' }}>
		{#snippet meta()}
			{#if experiment}
				<StatusBadge status={live?.status?.status ?? experiment.status} tones={experimentTones} />
				<Badge variant="outline">{MODE_LABELS[experiment.mode] ?? experiment.mode}</Badge>
				<Badge variant="outline" title="Run order">
					{effectivePolicyLabel(status?.policy_effective ?? null, experiment.scheduling_policy)}
				</Badge>
			{/if}
		{/snippet}
		{#snippet actions()}
			{#if experiment}
				{@const s = experiment.status}
				<span class="inline-flex">
					<Button variant="outline" size="sm" class="rounded-r-none" disabled={pending === 'export'} onclick={() => doExport('json')} title="Export as JSON">
						{#if pending === 'export'}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Download />{/if}JSON
					</Button>
					<Button variant="outline" size="sm" class="-ml-px rounded-l-none" disabled={pending === 'export'} onclick={() => doExport('csv')} title="Export the runs as CSV">CSV</Button>
				</span>
				{#if s === 'draft'}
					<Button variant="outline" size="sm" onclick={() => ask('delete')}><Trash2 />Delete</Button>
					<Button variant="outline" size="sm" href="/experiments/new?edit={experiment.id}"><Pencil />Edit</Button>
					<Button size="sm" disabled={pending === 'launch'} onclick={() => askLaunch('launch')}>
						{#if pending === 'launch'}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Rocket />{/if}Launch
					</Button>
				{:else if s === 'running'}
					<Button variant="outline" size="sm" onclick={() => ask('cancel')}><Square />Cancel</Button>
					<Button size="sm" disabled={pending === 'pause'} onclick={() => run('pause', () => pauseExperiment(experiment!.id), 'Experiment paused')}>
						{#if pending === 'pause'}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Pause />{/if}Pause
					</Button>
				{:else if s === 'paused'}
					<Button variant="outline" size="sm" onclick={() => ask('cancel')}><Square />Cancel</Button>
					{#if !drift}
						<Button size="sm" disabled={pending === 'resume'} onclick={() => run('resume', () => resumeExperiment(experiment!.id), 'Experiment resumed')}>
							{#if pending === 'resume'}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Play />{/if}Resume
						</Button>
					{/if}
				{:else if s === 'completed' || s === 'cancelled'}
					<Button variant="outline" size="sm" onclick={() => ask('archive')}><Archive />Archive</Button>
					<Button variant="outline" size="sm" href="/experiments/new?from={experiment.id}"><Copy />Clone</Button>
					<Button size="sm" disabled={failedCells === 0 || pending === 'topup'} onclick={() => askLaunch('topup')}>
						{#if pending === 'topup'}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<RotateCcw />{/if}Top up failed
					</Button>
				{:else}
					<Button variant="outline" size="sm" href="/experiments/new?from={experiment.id}"><Copy />Clone</Button>
				{/if}
			{/if}
		{/snippet}
		{#if experiment?.hypothesis}
			<p class="text-sm italic text-muted-foreground">“{experiment.hypothesis}”</p>
		{/if}
		{#if experiment?.launched_at}
			<p class="text-[11px] text-muted-foreground">
				Launched {new Date(experiment.launched_at).toLocaleString()}{experiment.cloned_from ? ' · ' : ''}
				{#if experiment.cloned_from}clone of <a class="underline" href="/experiments/{experiment.cloned_from.id}">{experiment.cloned_from.name}</a>{/if}
			</p>
		{/if}
	</PageHeader>

	{#if loading && !experiment}
		<LoadingState label="loading experiment" />
	{:else if notFound}
		<EmptyState icon={FlaskConical} title="Experiment not found" description="It was deleted, or it belongs to someone else.">
			{#snippet action()}<Button size="sm" href="/experiments">All experiments</Button>{/snippet}
		</EmptyState>
	{:else if loadError}
		<ErrorAlert message={loadError} onretry={() => load(experimentId)} />
	{:else if experiment}
		{#if actionError}
			<ErrorAlert message={actionError} />
		{/if}

		{#if drift}
			<Alert class="border-warning/40 bg-warning/5">
				<TriangleAlert class="text-warning-fg" />
				<div class="flex flex-wrap items-start justify-between gap-2">
					<AlertDescription class="min-w-0 flex-1">
						<span class="font-medium text-foreground">Paused: {pauseReasonLabel(experiment.pause_reason)}.</span>
						A model's weights changed on its endpoint since launch, so new runs would not be comparable with finished ones.
					</AlertDescription>
					<span class="flex gap-2">
						<Button variant="outline" size="sm" href="/experiments/new?from={experiment.id}"><Copy />Clone with current digest</Button>
						<Button variant="outline" size="sm" onclick={() => ask('drift')}>Resume anyway</Button>
					</span>
				</div>
			</Alert>
		{:else if experiment.status === 'paused' && experiment.pause_reason}
			<Alert>
				<Pause />
				<AlertDescription>Paused: {pauseReasonLabel(experiment.pause_reason)}. No new runs start until you resume.</AlertDescription>
			</Alert>
		{/if}

		{#if status && isActive(experiment.status) && (!status.executor.online || status.executor.code_stale || status.executor.draining)}
			<Alert variant={status.executor.online ? 'default' : 'destructive'}>
				<CircleAlert />
				<AlertDescription>
					{#if !status.executor.online}
						The executor is offline: nothing runs until it is back. Queued runs keep their place.
					{:else if status.executor.draining}
						The executor is draining for a restart: running jobs finish, new ones wait.
					{:else}
						The executor runs older code than the web app. Restart it when idle (drain first) to pick up changes.
					{/if}
				</AlertDescription>
			</Alert>
		{/if}

		{#if offlineEndpoints.length > 0}
			<Alert class="border-warning/40 bg-warning/5">
				<TriangleAlert class="text-warning-fg" />
				<AlertDescription>
					{offlineEndpoints.map((e) => e.name || e.slug).join(', ')}
					{offlineEndpoints.length === 1 ? 'is' : 'are'} offline. Its runs wait until a relay connects —
					<a class="underline" href="/endpoints/{offlineEndpoints[0].id}">check the endpoint</a>.
				</AlertDescription>
			</Alert>
		{/if}

		{#if legacyPending > 0}
			<Alert>
				<CircleAlert />
				<div class="flex flex-wrap items-center justify-between gap-2">
					<AlertDescription>
						{plural(legacyPending, 'run')} from before the executor never started. Requeue them to let the executor run them.
					</AlertDescription>
					<Button
						variant="outline"
						size="sm"
						disabled={pending === 'requeue'}
						onclick={() => run('requeue', () => requeueLegacy(experiment!.id), 'Legacy runs requeued')}
					>
						Requeue
					</Button>
				</div>
			</Alert>
		{/if}

		<Tabs.Root value={tab} onValueChange={(v) => setTab(v)}>
			<Tabs.List class="w-full justify-start overflow-x-auto sm:w-auto">
				<Tabs.Trigger value="overview" disabled={experiment.status === 'draft'}>Overview</Tabs.Trigger>
				<Tabs.Trigger value="results" disabled={experiment.status === 'draft'}>Results</Tabs.Trigger>
				<Tabs.Trigger value="runs" disabled={experiment.status === 'draft'}>
					Runs{#if counts && activeCells(counts) > 0}<span class="ml-1 font-mono text-[11px] text-muted-foreground">{activeCells(counts)}</span>{/if}
				</Tabs.Trigger>
				<Tabs.Trigger value="design">Design</Tabs.Trigger>
				<Tabs.Trigger value="automation">Automation</Tabs.Trigger>
			</Tabs.List>
			<Tabs.Content value="overview" class="pt-4">
				{#if live}
					<OverviewTab {experiment} {live} {appName} />
				{:else}
					<EmptyState title="Not launched yet" description="Launch the experiment to watch it run here." />
				{/if}
			</Tabs.Content>
			<Tabs.Content value="results" class="pt-4">
				{#if tab === 'results'}
					<ResultsTab {experiment} {refreshSignal} {tickSignal} />
				{/if}
			</Tabs.Content>
			<Tabs.Content value="runs" class="pt-4">
				{#if tab === 'runs'}
					<RunsTab {experiment} {appName} {counts} {refreshSignal} {tickSignal} onlaunched={relaunched} />
				{/if}
			</Tabs.Content>
			<Tabs.Content value="design" class="pt-4">
				{#if tab === 'design'}
					<DesignTab
						{experiment}
						appName={(id) => appName(id)}
						policyEffective={status?.policy_effective ?? null}
						onupdated={(detail) => (experiment = detail)}
					/>
				{/if}
			</Tabs.Content>
			<Tabs.Content value="automation" class="pt-4">
				{#if tab === 'automation'}
					<AutomationTab {experiment} {postRunSignal} onlaunched={relaunched} />
				{/if}
			</Tabs.Content>
		</Tabs.Root>
	{/if}
</div>

<ConfirmDialog
	bind:open={confirmOpen}
	title={confirmCopy.title}
	description={confirmCopy.description}
	confirmLabel={confirmCopy.label}
	destructive={confirmCopy.destructive}
	busy={pending !== null}
	onConfirm={onConfirm}
/>
