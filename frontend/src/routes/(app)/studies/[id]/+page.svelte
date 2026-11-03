<script lang="ts">
	// The study record (spec 10.3), laid out like a paper: masthead, then the numbered sections
	// Abstract · Method · Results · Patterns (slot) · Data · Operations (`?tab=`; old names keep working
	// through `study/tabs.ts`). Live data comes from `StudyLive` (SSE `study:<id>` + polling fallback).
	import { onDestroy, untrack, type Component } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import * as Tabs from '$lib/components/ui/tabs';
	import { Button } from '$lib/components/ui/button';
	import { formatApiError } from '$lib/api/core';
	import { cancelStudy, getStudyResults, pauseStudy, resumeStudy } from '$lib/api/v3/studies';
	import type { StudyResultsOut } from '$lib/api/v3/types';
	import { StudyLive } from '$lib/components/studies/monitor/live.svelte';
	import StudyMasthead from '$lib/components/studies/study/StudyMasthead.svelte';
	import { resolveStudyTab, tabsFor, type StudyTab } from '$lib/components/studies/study/tabs';
	import LiveSpendStrip from '$lib/components/studies/monitor/LiveSpendStrip.svelte';
	import AbstractSection from '$lib/components/studies/record/AbstractSection.svelte';
	import MethodSection from '$lib/components/studies/record/MethodSection.svelte';
	import ResultsSection from '$lib/components/studies/results/ResultsSection.svelte';
	import DataSection from '$lib/components/studies/record/DataSection.svelte';
	import OperationsSection from '$lib/components/studies/record/OperationsSection.svelte';
	import StudySection from '$lib/components/studies/study/StudySection.svelte';
	import { refreshSpendChip } from '$lib/components/studies/spend';
	import { isEditable, isTerminal } from '$lib/components/studies/format';
	import Pause from '@lucide/svelte/icons/pause';
	import Play from '@lucide/svelte/icons/play';
	import Square from '@lucide/svelte/icons/square';
	import Pencil from '@lucide/svelte/icons/pencil';

	// WP-16 owns the Patterns content (`PatternsTab { studyId }`); it is mounted when it exists.
	const patternModules = import.meta.glob<{ default: Component<{ studyId: string }> }>('/src/lib/components/patterns/PatternsTab.svelte');

	const id = $derived(page.params.id ?? '');
	let live = $state<StudyLive | null>(null);
	let results = $state<StudyResultsOut | null>(null);
	let resultsSignal = $state(0);
	let actionError = $state('');
	let confirmCancel = $state(false);
	let busy = $state(false);
	let PatternsTab = $state<Component<{ studyId: string }> | null>(null);
	let lastTickRefresh = 0;

	const study = $derived(live?.study ?? null);
	const tabs = $derived(tabsFor(study?.mode ?? 'app'));
	const tab = $derived<StudyTab>(resolveStudyTab(page.url.searchParams.get('tab'), study?.mode ?? 'slot') ?? 'summary');
	const numberOf = (value: StudyTab) => String(tabs.find((t) => t.value === value)?.number ?? '');

	async function loadResults() {
		try {
			results = await getStudyResults(id);
		} catch {
			// The Results section shows its own error.
		}
	}

	$effect(() => {
		const studyId = id;
		untrack(() => {
			live?.destroy();
			results = null;
			const next = new StudyLive(studyId, {
				onState: () => {
					resultsSignal++;
					void loadResults();
				},
				onTick: () => {
					// Results refetch at most every 30 s while runs finish.
					const now = Date.now();
					if (now - lastTickRefresh > 30_000) {
						lastTickRefresh = now;
						resultsSignal++;
						void loadResults();
					}
				},
			});
			live = next;
			void next.start().then(() => {
				if (next.study && next.study.status === 'draft') void goto(`/studies/new?draft=${studyId}`, { replaceState: true });
			});
			void loadResults();
		});
	});

	$effect(() => {
		if (study?.mode !== 'slot' || PatternsTab) return;
		const loader = patternModules['/src/lib/components/patterns/PatternsTab.svelte'];
		if (loader) void loader().then((m) => (PatternsTab = m.default));
	});

	onDestroy(() => live?.destroy());

	function setTab(value: string) {
		const url = new URL(page.url);
		url.searchParams.set('tab', value);
		void goto(url, { replaceState: true, keepFocus: true, noScroll: true });
	}

	async function act(fn: () => Promise<unknown>, fallback: string) {
		busy = true;
		actionError = '';
		try {
			await fn();
			live?.restart();
			refreshSpendChip();
		} catch (e) {
			actionError = formatApiError(e, fallback);
		} finally {
			busy = false;
		}
	}

	// Progress of the PLANNED sample: main-stage cells that are not replicates (`beyond_plan`). The payload
	// lists every stage and the replicate cells too, so `counts.done` is not this number.
	const plannedMain = $derived(live?.cells?.cells.filter((c) => c.stage === 'main' && !c.beyond_plan) ?? []);
	const cellsDone = $derived(plannedMain.filter((c) => c.state === 'done').length);
	const cellsTotal = $derived(plannedMain.length);
	const conditions = $derived((results?.leaderboard ?? []).map((r) => ({ index: r.index, label: r.label })));
</script>

<div class="space-y-6">
	{#if live?.error && !study}
		<ErrorAlert message={live.error} onretry={() => live?.resync()} />
	{:else if !study}
		<LoadingState label="loading the study" />
	{:else}
		<StudyMasthead {study}>
			{#snippet actions()}
				{#if isEditable(study.status, study.locked)}
					<Button size="sm" href="/studies/new?draft={study.id}"><Pencil />Continue designing</Button>
				{/if}
				{#if study.status === 'running' || study.status === 'piloting'}
					<Button variant="outline" size="sm" disabled={busy} onclick={() => act(() => pauseStudy(study.id), 'Could not pause.')}><Pause />Pause</Button>
				{:else if study.status === 'paused'}
					<Button variant="outline" size="sm" disabled={busy} onclick={() => act(() => resumeStudy(study.id), 'Could not resume.')}><Play />Resume</Button>
				{/if}
				{#if !isTerminal(study.status) && study.status !== 'draft'}
					<Button variant="ghost" size="sm" disabled={busy} onclick={() => (confirmCancel = true)}><Square />Cancel</Button>
				{/if}
			{/snippet}
			{#snippet progress()}
				<div class="flex flex-wrap items-center justify-between gap-2 text-xs">
					<span><b class="font-mono tabular-nums">{cellsDone}</b> / {cellsTotal} main cells done</span>
					<LiveSpendStrip budget={live?.budget ?? null} liveBurnRate={live?.liveBurnRate ?? null} compact />
				</div>
			{/snippet}
			{#snippet nav()}
				<Tabs.Root value={tab} onValueChange={(v) => setTab(v)}>
					<Tabs.List>
						{#each tabs as t (t.value)}
							<Tabs.Trigger value={t.value}><span class="mr-1 font-mono text-[11px] text-muted-foreground">{t.number}</span>{t.label}</Tabs.Trigger>
						{/each}
					</Tabs.List>
				</Tabs.Root>
			{/snippet}
		</StudyMasthead>

		{#if actionError}<ErrorAlert message={actionError} />{/if}

		{#if tab === 'summary'}
			<AbstractSection {study} {results} {cellsDone} {cellsTotal} />
		{:else if tab === 'method'}
			<MethodSection {study} {conditions} number={numberOf('method')} />
		{:else if tab === 'results'}
			<ResultsSection studyId={study.id} refreshSignal={resultsSignal} number={numberOf('results')} />
		{:else if tab === 'patterns'}
			<StudySection id="patterns" number={numberOf('patterns')} title="Patterns" lead="Prevalence of the coded features in the regenerated parts, clusters of equivalent variants and comparisons between conditions.">
				{#if PatternsTab}
					<PatternsTab studyId={study.id} />
				{:else}
					<p class="text-xs text-muted-foreground">The pattern analysis view is not available in this build.</p>
				{/if}
			</StudySection>
		{:else if tab === 'data'}
			<DataSection studyId={study.id} number={numberOf('data')} refreshSignal={resultsSignal} />
		{:else if live}
			<OperationsSection {live} number={numberOf('operations')} onchanged={() => (resultsSignal++)} />
		{/if}
	{/if}
</div>

<ConfirmDialog
	bind:open={confirmCancel}
	title="Cancel the study?"
	description="Pending runs are cancelled; running runs finish. Cancelled cells are reported as excluded, never as results."
	confirmLabel="Cancel the study"
	destructive
	{busy}
	onConfirm={async () => {
		if (study) await act(() => cancelStudy(study.id), 'Could not cancel.');
		confirmCancel = false;
	}}
/>
