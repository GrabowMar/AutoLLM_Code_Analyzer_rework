<script lang="ts">
	// Sample (generation run) view, spec 10.4. One `SampleLive` follows `run:<id>` (SSE + polling fallback);
	// the tabs read from it. `/applications/[id]` redirects here.
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import * as Tabs from '$lib/components/ui/tabs';
	import { Button } from '$lib/components/ui/button';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { scrollHint } from '$lib/utils/scroll-hint';
	import { toast } from 'svelte-sonner';
	import Scissors from '@lucide/svelte/icons/scissors';
	import RotateCw from '@lucide/svelte/icons/rotate-cw';
	import Square from '@lucide/svelte/icons/square';
	import Download from '@lucide/svelte/icons/download';
	import { formatApiError } from '$lib/api/core';
	import { cancelSample, retrySample, sampleEvidenceUrl } from '$lib/api/v3/samples';
	import {
		CallsTab,
		EvaluationTab,
		FilesTab,
		ManifestPanel,
		OverviewTab,
		RunHeader,
		SampleLive,
		TrajectoryTab,
		canBeParent,
		isActiveStatus,
	} from '$lib/components/runs';
	import ApplicationAnalyses from '$lib/components/applications/ApplicationAnalyses.svelte';
	import AppRuntimeControls from '$lib/components/applications/AppRuntimeControls.svelte';
	import ApplicationLogs from '$lib/components/applications/ApplicationLogs.svelte';

	const TABS = [
		{ value: 'overview', label: 'Overview' },
		{ value: 'manifest', label: 'Manifest' },
		{ value: 'files', label: 'Files' },
		{ value: 'trajectory', label: 'Trajectory' },
		{ value: 'evaluation', label: 'Evaluation' },
		{ value: 'calls', label: 'Calls' },
		{ value: 'analyses', label: 'Analyses' },
		{ value: 'runtime', label: 'Runtime' },
	] as const;
	type Tab = (typeof TABS)[number]['value'];
	const DEFAULT_TAB: Tab = 'overview';

	const live = new SampleLive();
	const runId = $derived(page.params.id ?? '');
	const tab = $derived.by((): Tab => {
		const value = page.url.searchParams.get('tab');
		return TABS.some((t) => t.value === value) ? (value as Tab) : DEFAULT_TAB;
	});

	$effect(() => {
		const id = runId;
		if (!id) return;
		live.start(id);
		return () => live.stop();
	});

	function setTab(next: string) {
		const url = new URL(page.url);
		if (next === DEFAULT_TAB) url.searchParams.delete('tab');
		else url.searchParams.set('tab', next);
		goto(`${url.pathname}${url.search}`, { replaceState: true, keepFocus: true, noScroll: true });
	}

	const run = $derived(live.run);
	// A single-shot slot call has no harness, so no trajectory.
	const disabledTabs = $derived(new Set<string>(run?.kind === 'slot_single_shot' ? ['trajectory'] : []));

	let confirmCancel = $state(false);
	let busy = $state(false);

	async function retry() {
		if (!run) return;
		busy = true;
		try {
			const out = await retrySample(run.id);
			toast.success(`Attempt ${out.attempt} queued (same manifest and seed)`);
			await live.refresh();
		} catch (e) {
			toast.error(formatApiError(e, 'Could not retry the run.'));
		} finally {
			busy = false;
		}
	}

	async function cancel() {
		if (!run) return;
		busy = true;
		try {
			await cancelSample(run.id);
			toast.success('Run cancelled');
			confirmCancel = false;
			await live.refresh();
		} catch (e) {
			toast.error(formatApiError(e, 'Could not cancel the run.'));
		} finally {
			busy = false;
		}
	}
</script>

{#snippet actions()}
	{#if run}
		<Button variant="outline" size="sm" href={sampleEvidenceUrl(run.id)} title="Zip of the run's evidence directory" download>
			<Download class="mr-1.5 h-3.5 w-3.5" />Evidence
		</Button>
		{#if isActiveStatus(run.status)}
			<Button variant="outline" size="sm" disabled={busy} onclick={() => (confirmCancel = true)}>
				<Square class="mr-1.5 h-3.5 w-3.5" />Cancel
			</Button>
		{/if}
		{#if run.outcome === 'infra_error'}
			<Button variant="outline" size="sm" disabled={busy} onclick={retry} title="Infra errors only: a new attempt with the same manifest and seed">
				<RotateCw class="mr-1.5 h-3.5 w-3.5" />Retry
			</Button>
		{/if}
		{#if canBeParent(run)}
			<Button size="sm" href={`/slots/new?parent=${encodeURIComponent(run.id)}`} title="Freeze this app and regenerate one of its parts N times">
				<Scissors class="mr-1.5 h-3.5 w-3.5" />Regenerate a part…
			</Button>
		{/if}
	{/if}
{/snippet}

{#snippet nav()}
	<div
		class="scroll-hint no-scrollbar min-w-0 flex-1 overflow-x-auto"
		{@attach scrollHint({ edges: 'both', focusable: false, follow: '[role="tab"][data-state="active"]' })}
	>
		<Tabs.List aria-label="Sample sections" class="w-max min-w-full overflow-x-visible">
			{#each TABS as section (section.value)}
				<Tabs.Trigger value={section.value} disabled={disabledTabs.has(section.value)}>{section.label}</Tabs.Trigger>
			{/each}
		</Tabs.List>
	</div>
{/snippet}

<Tabs.Root value={tab} onValueChange={(v) => setTab(v)} class="space-y-4">
	{#if run}
		<RunHeader {run} {actions} {nav} stream={live.stream} />

		{#if live.error}
			<ErrorAlert message={live.error} onretry={() => live.refresh()} />
		{/if}

		<Tabs.Content value="overview" class="pt-0">
			{#if tab === 'overview'}<OverviewTab {run} />{/if}
		</Tabs.Content>
		<Tabs.Content value="manifest" class="pt-0">
			{#if tab === 'manifest'}<ManifestPanel runId={run.id} />{/if}
		</Tabs.Content>
		<Tabs.Content value="files" class="pt-0">
			{#if tab === 'files'}<FilesTab {run} version={live.version} />{/if}
		</Tabs.Content>
		<Tabs.Content value="trajectory" class="pt-0">
			{#if tab === 'trajectory'}
				<TrajectoryTab {run} liveSteps={live.liveSteps} liveStepCount={live.liveStepCount} version={live.version} />
			{/if}
		</Tabs.Content>
		<Tabs.Content value="evaluation" class="pt-0">
			{#if tab === 'evaluation'}<EvaluationTab {run} version={live.version} oncreated={() => live.refresh()} />{/if}
		</Tabs.Content>
		<Tabs.Content value="calls" class="pt-0">
			{#if tab === 'calls'}<CallsTab {run} version={live.version} />{/if}
		</Tabs.Content>
		<Tabs.Content value="analyses" class="pt-0">
			{#if tab === 'analyses'}<ApplicationAnalyses jobId={run.id} />{/if}
		</Tabs.Content>
		<Tabs.Content value="runtime" class="pt-0">
			{#if tab === 'runtime'}
				<div class="space-y-4">
					<AppRuntimeControls jobId={run.id} jobStatus={run.status} />
					<ApplicationLogs jobId={run.id} />
				</div>
			{/if}
		</Tabs.Content>
	{:else if live.loading}
		<PageHeader title="Sample" back={{ href: '/applications', label: 'Applications' }} />
		<LoadingState label="loading sample" />
	{:else if live.notFound}
		<PageHeader title="Sample not found" back={{ href: '/applications', label: 'Applications' }} />
		<EmptyState title="No such sample" description="It does not exist, or it belongs to someone else's experiment." />
	{:else}
		<PageHeader title="Sample" back={{ href: '/applications', label: 'Applications' }} />
		<ErrorAlert message={live.error || 'Could not load the sample.'} onretry={() => live.refresh()} />
	{/if}
</Tabs.Root>

<ConfirmDialog
	bind:open={confirmCancel}
	title="Cancel this run?"
	description="The run stops and is excluded with reason cancelled. A cancelled cell can be topped up later."
	confirmLabel="Cancel run"
	destructive
	{busy}
	onConfirm={cancel}
/>
