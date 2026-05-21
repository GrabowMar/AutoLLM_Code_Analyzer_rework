<script lang="ts">
import { onMount } from 'svelte';
import { goto } from '$app/navigation';
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import { Separator } from '$lib/components/ui/separator';
import ArrowLeft from '@lucide/svelte/icons/arrow-left';
import ArrowRight from '@lucide/svelte/icons/arrow-right';
import Check from '@lucide/svelte/icons/check';
import Rocket from '@lucide/svelte/icons/rocket';
import Loader from '@lucide/svelte/icons/loader-circle';
import {
	getAnalyzers,
	getGenerationJobs,
	createAnalysisTask,
	type AnalyzerInfo,
	type GenerationJobList,
} from '$lib/api/client';
import AnalysisTargetForm from '$lib/components/analysis/AnalysisTargetForm.svelte';
import AnalyzerSelector from '$lib/components/analysis/AnalyzerSelector.svelte';
import AnalysisConfigureForm from '$lib/components/analysis/AnalysisConfigureForm.svelte';
import AnalysisReview from '$lib/components/analysis/AnalysisReview.svelte';

let step = $state(1);
const stepLabels = ['Select Source', 'Analyzers', 'Configure', 'Review'];

type SourceMode = 'job' | 'paste';
let sourceMode = $state<SourceMode>('job');
let jobsLoading = $state(true);
let jobsError = $state('');
let jobs = $state<GenerationJobList[]>([]);
let selectedJobId = $state<string | null>(null);
let jobSearch = $state('');
let pasteBackend = $state('');
let pasteFrontend = $state('');

const filteredJobs = $derived(
	jobs.filter(
		(j) =>
			!jobSearch ||
			(j.model_name ?? '').toLowerCase().includes(jobSearch.toLowerCase()) ||
			(j.template_name ?? '').toLowerCase().includes(jobSearch.toLowerCase()) ||
			j.id.toLowerCase().includes(jobSearch.toLowerCase()),
	),
);

const selectedJob = $derived(jobs.find((j) => j.id === selectedJobId));

const hasSource = $derived(
	sourceMode === 'job'
		? selectedJobId !== null
		: pasteBackend.trim().length > 0 || pasteFrontend.trim().length > 0,
);

let analyzersLoading = $state(true);
let analyzersError = $state('');
let analyzers = $state<AnalyzerInfo[]>([]);
let selectedAnalyzers = $state(new Set<string>());

function toggleAnalyzer(name: string) {
	const next = new Set(selectedAnalyzers);
	if (next.has(name)) next.delete(name);
	else next.add(name);
	selectedAnalyzers = next;
}

function selectAllAnalyzers() {
	selectedAnalyzers = new Set(analyzers.filter((a) => a.available).map((a) => a.name));
}

function clearAllAnalyzers() {
	selectedAnalyzers = new Set();
}

let taskName = $state('');
let autoStart = $state(true);
let liveTarget = $state(false);
let analyzerSettings = $state<Record<string, string>>({});
let settingsErrors = $state<Record<string, string>>({});

function getSettingsJson(analyzerName: string): Record<string, any> {
	const raw = analyzerSettings[analyzerName];
	if (!raw || !raw.trim()) {
		delete settingsErrors[analyzerName];
		return {};
	}
	try {
		const parsed = JSON.parse(raw);
		delete settingsErrors[analyzerName];
		return parsed;
	} catch (e: any) {
		settingsErrors[analyzerName] = e.message || 'Invalid JSON';
		return {};
	}
}

const selectedAnalyzersList = $derived(analyzers.filter((a) => selectedAnalyzers.has(a.name)));

let launching = $state(false);
let launchError = $state('');

async function handleLaunch() {
	launching = true;
	launchError = '';
	const hasErrors = Object.keys(settingsErrors).some((k) => settingsErrors[k]);
	if (hasErrors) {
		launchError = 'Please fix JSON configuration errors before launching.';
		launching = false;
		return;
	}
	try {
		const settingsMap: Record<string, any> = {};
		for (const a of selectedAnalyzersList) {
			settingsMap[a.name] = getSettingsJson(a.name);
		}

		const task = await createAnalysisTask({
			name: taskName || undefined,
			generation_job_id: sourceMode === 'job' ? selectedJobId : undefined,
			source_code: sourceMode === 'paste' ? { backend: pasteBackend, frontend: pasteFrontend } : {},
			analyzers: [...selectedAnalyzers],
			settings: settingsMap,
			auto_start: autoStart,
			live_target: sourceMode === 'job' && liveTarget ? true : undefined,
		});
		await goto(`/analysis/${task.id}`);
	} catch (err: any) {
		launchError = err?.detail ?? err?.message ?? 'Failed to create analysis task';
	} finally {
		launching = false;
	}
}

async function loadJobs() {
	jobsLoading = true;
	jobsError = '';
	try {
		const res = await getGenerationJobs({ status: 'completed', per_page: 100 });
		jobs = res.items;
	} catch (err: any) {
		jobsError = err?.detail ?? 'Failed to load generation jobs';
	} finally {
		jobsLoading = false;
	}
}

async function loadAnalyzers() {
	analyzersLoading = true;
	analyzersError = '';
	try {
		analyzers = await getAnalyzers();
	} catch (err: any) {
		analyzersError = err?.detail ?? 'Failed to load analyzers';
	} finally {
		analyzersLoading = false;
	}
}

onMount(() => {
	loadJobs();
	loadAnalyzers();
});

const canAdvance = $derived.by(() => {
	if (step === 1) return hasSource;
	if (step === 2) return selectedAnalyzers.size > 0;
	if (step === 3) return true;
	return true;
});
</script>

<svelte:head>
	<title>New Analysis - LLM Lab</title>
</svelte:head>

<div class="space-y-6">
	<div class="flex items-center gap-2 text-sm text-muted-foreground">
		<Button variant="ghost" size="sm" href="/analysis" class="gap-1.5 px-2">
			<ArrowLeft class="h-3.5 w-3.5" />
			Analysis Hub
		</Button>
		<span>/</span>
		<span class="text-foreground font-medium">New Analysis</span>
	</div>

	<div class="page-header">
		<h1>New Analysis</h1>
		<p>Configure and launch an analysis pipeline.</p>
	</div>

	<div class="grid gap-4 sm:gap-6 lg:grid-cols-4">
		<div class="space-y-6 lg:col-span-3">
			<div class="flex items-center gap-2 overflow-x-auto">
				{#each stepLabels as label, i}
					<button
						class="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors {step === i + 1 ? 'bg-primary/10 text-primary font-medium' : i + 1 < step ? 'text-emerald-500' : 'text-muted-foreground'}"
						onclick={() => { if (i + 1 <= step) step = i + 1; }}
					>
						<span class="flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold {step === i + 1 ? 'bg-primary text-primary-foreground' : i + 1 < step ? 'bg-emerald-500 text-white' : 'bg-muted text-muted-foreground'}">
							{#if i + 1 < step}
								<Check class="h-3 w-3" />
							{:else}
								{i + 1}
							{/if}
						</span>
						{label}
					</button>
					{#if i < stepLabels.length - 1}
						<div class="h-px flex-1 bg-border"></div>
					{/if}
				{/each}
			</div>

			{#if step === 1}
				<AnalysisTargetForm
					{sourceMode}
					{jobsLoading}
					{jobsError}
					{jobs}
					{filteredJobs}
					{selectedJobId}
					bind:jobSearch
					bind:pasteBackend
					bind:pasteFrontend
					onSourceModeChange={(m) => (sourceMode = m)}
					onSelectJob={(id) => (selectedJobId = id)}
					onRetryLoadJobs={loadJobs}
				/>
			{/if}

			{#if step === 2}
				<AnalyzerSelector
					{analyzersLoading}
					{analyzersError}
					{analyzers}
					{selectedAnalyzers}
					onToggleAnalyzer={toggleAnalyzer}
					onSelectAll={selectAllAnalyzers}
					onClearAll={clearAllAnalyzers}
					onReload={loadAnalyzers}
				/>
			{/if}

			{#if step === 3}
				<AnalysisConfigureForm
					bind:taskName
					bind:autoStart
					bind:liveTarget
					showLiveTargetOption={sourceMode === 'job' && selectedJobId !== null}
					{selectedAnalyzersList}
					bind:analyzerSettings
					{settingsErrors}
				/>
			{/if}

			{#if step === 4}
				<AnalysisReview
					{sourceMode}
					{selectedJob}
					{pasteBackend}
					{pasteFrontend}
					{taskName}
					{selectedAnalyzersList}
					{autoStart}
					{liveTarget}
					{launchError}
				/>
			{/if}
		</div>

		<div class="space-y-4">
			<Card.Root>
				<Card.Content class="p-4">
					<div class="mb-3 text-sm font-medium">Step {step} of {stepLabels.length}</div>
					<div class="mb-4 h-1.5 overflow-hidden rounded-full bg-muted">
						<div class="h-full rounded-full bg-primary transition-all" style="width: {(step / stepLabels.length) * 100}%"></div>
					</div>
					<div class="flex flex-col gap-2 sm:flex-row sm:justify-between">
						<Button variant="outline" size="sm" disabled={step === 1} onclick={() => step--}>
							<ArrowLeft class="mr-1.5 h-3.5 w-3.5" /> Back
						</Button>
						{#if step < 4}
							<Button size="sm" disabled={!canAdvance} onclick={() => step++}>
								Next <ArrowRight class="ml-1.5 h-3.5 w-3.5" />
							</Button>
						{:else}
							<Button size="sm" disabled={launching} onclick={handleLaunch}>
								{#if launching}
									<Loader class="mr-1.5 h-3.5 w-3.5 animate-spin" />
									Launching…
								{:else}
									<Rocket class="mr-1.5 h-3.5 w-3.5" /> Launch
								{/if}
							</Button>
						{/if}
					</div>
				</Card.Content>
			</Card.Root>

			<Card.Root>
				<Card.Header><Card.Title class="text-sm">Selections</Card.Title></Card.Header>
				<Card.Content class="space-y-3">
					<div>
						<div class="mb-1 text-xs font-medium uppercase text-muted-foreground">Source</div>
						{#if sourceMode === 'job' && selectedJob}
							<span class="text-sm">{selectedJob.model_name ?? selectedJob.id.slice(0, 8)}</span>
						{:else if sourceMode === 'paste' && (pasteBackend.trim() || pasteFrontend.trim())}
							<span class="text-sm">Pasted code</span>
						{:else}
							<span class="text-xs italic text-muted-foreground">Not selected</span>
						{/if}
					</div>
					<Separator />
					<div>
						<div class="mb-1 text-xs font-medium uppercase text-muted-foreground">
							Analyzers ({selectedAnalyzers.size})
						</div>
						{#if selectedAnalyzers.size > 0}
							<div class="flex flex-wrap gap-1">
								{#each selectedAnalyzersList as a}
									<Badge variant="secondary" class="text-[10px]">{a.display_name}</Badge>
								{/each}
							</div>
						{:else}
							<span class="text-xs italic text-muted-foreground">None selected</span>
						{/if}
					</div>
				</Card.Content>
			</Card.Root>

			<Card.Root>
				<Card.Header><Card.Title class="text-sm">Summary</Card.Title></Card.Header>
				<Card.Content>
					<dl class="grid grid-cols-2 gap-y-2 text-sm">
						<dt class="text-muted-foreground">Analyzers</dt>
						<dd class="font-mono">{selectedAnalyzers.size}</dd>
						<dt class="text-muted-foreground">Auto-start</dt>
						<dd class="font-mono">{autoStart ? 'Yes' : 'No'}</dd>
					</dl>
				</Card.Content>
			</Card.Root>
		</div>
	</div>
</div>
