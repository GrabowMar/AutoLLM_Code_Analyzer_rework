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
import BookmarkCheck from '@lucide/svelte/icons/bookmark-check';
import X from '@lucide/svelte/icons/x';
import {
	getAnalyzers,
	getAnalysisProfiles,
	createAnalysisTask,
	type AnalyzerInfo,
	type AnalysisProfile,
} from '$lib/api/analysis';
import { getGenerationJobs, type GenerationJobList } from '$lib/api/client';
import AnalysisTargetForm from '$lib/components/analysis/AnalysisTargetForm.svelte';
import AnalyzerSelector from '$lib/components/analysis/AnalyzerSelector.svelte';
import AnalysisConfigureForm from '$lib/components/analysis/AnalysisConfigureForm.svelte';
import AnalysisReview from '$lib/components/analysis/AnalysisReview.svelte';
import ProfileSelector from '$lib/components/analysis/ProfileSelector.svelte';
import AnalyzerConfigForm from '$lib/components/analysis/AnalyzerConfigForm.svelte';

let step = $state(1);
const stepLabels = ['Select Source', 'Analyzers', 'Configure', 'Review'];

// Profile
let selectedProfile = $state<AnalysisProfile | null>(null);
let appliedProfileBanner = $state(false);

// Source
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

// Analyzers
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

// Configuration — typed (replaces raw JSON strings)
let taskName = $state('');
let autoStart = $state(true);
let liveTarget = $state(false);
let analyzerConfigs = $state<Record<string, Record<string, unknown>>>({});
let thresholds = $state<Record<string, number | ''>>({});
let expandedConfigs = $state(new Set<string>());

const selectedAnalyzersList = $derived(analyzers.filter((a) => selectedAnalyzers.has(a.name)));

function handleConfigChange(analyzerName: string, key: string, value: unknown) {
	analyzerConfigs = {
		...analyzerConfigs,
		[analyzerName]: { ...(analyzerConfigs[analyzerName] ?? {}), [key]: value },
	};
}

function toggleConfigExpand(name: string) {
	const next = new Set(expandedConfigs);
	if (next.has(name)) next.delete(name);
	else next.add(name);
	expandedConfigs = next;
}

const severities = ['critical', 'high', 'medium', 'low'] as const;

// Apply profile
function applyProfile(profile: AnalysisProfile | null) {
	selectedProfile = profile;
	if (profile) {
		selectedAnalyzers = new Set(profile.analyzers);
		analyzerConfigs = Object.fromEntries(
			Object.entries(profile.settings).map(([k, v]) => [k, { ...v }])
		);
		appliedProfileBanner = true;
	} else {
		appliedProfileBanner = false;
	}
}

// Launch
let launching = $state(false);
let launchError = $state('');

async function handleLaunch() {
	launching = true;
	launchError = '';
	try {
		// Build thresholds (only include values that are actually numbers)
		const resolvedThresholds: Record<string, number> = {};
		for (const [sev, val] of Object.entries(thresholds)) {
			if (val !== '' && val !== undefined) resolvedThresholds[sev] = Number(val);
		}

		const task = await createAnalysisTask({
			name: taskName || undefined,
			generation_job_id: sourceMode === 'job' ? (selectedJobId ?? undefined) : undefined,
			source_code: sourceMode === 'paste' ? { backend: pasteBackend, frontend: pasteFrontend } : {},
			analyzers: [...selectedAnalyzers],
			settings: analyzerConfigs,
			auto_start: autoStart,
			live_target: sourceMode === 'job' && liveTarget ? true : undefined,
			profile_id: selectedProfile?.id ?? null,
			thresholds: Object.keys(resolvedThresholds).length ? resolvedThresholds : undefined,
		});
		await goto(`/analysis/${task.id}`);
	} catch (err: any) {
		const detail = err?.detail;
		launchError = Array.isArray(detail)
			? detail.join('; ')
			: (detail ?? err?.message ?? 'Failed to create analysis task');
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
			<!-- Step progress -->
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
				<!-- Profile selector above source -->
				<ProfileSelector
					selectedProfileId={selectedProfile?.id ?? null}
					onSelect={applyProfile}
				/>

				{#if appliedProfileBanner && selectedProfile}
					<div class="flex items-center gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-400">
						<BookmarkCheck class="h-4 w-4 shrink-0" />
						<span>Profile <strong>{selectedProfile.name}</strong> applied — {selectedProfile.analyzers.length} analyzer{selectedProfile.analyzers.length !== 1 ? 's' : ''} pre-selected.</span>
						<button class="ml-auto" onclick={() => { appliedProfileBanner = false; }}>
							<X class="h-3.5 w-3.5" />
						</button>
					</div>
				{/if}

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
				<!-- Task-level configuration (keep AnalysisConfigureForm for name/auto-start/live-target) -->
				<AnalysisConfigureForm
					bind:taskName
					bind:autoStart
					bind:liveTarget
					showLiveTargetOption={sourceMode === 'job' && selectedJobId !== null}
				/>

				<!-- Typed per-analyzer config -->
				{#if selectedAnalyzersList.length > 0}
					<Card.Root>
						<Card.Header>
							<Card.Title class="text-sm">Analyzer Configuration</Card.Title>
							<Card.Description>Expand any analyzer to adjust its settings.</Card.Description>
						</Card.Header>
						<Card.Content class="space-y-2">
							{#each selectedAnalyzersList as analyzer}
								<details
									open={expandedConfigs.has(analyzer.name)}
									class="rounded-md border border-border"
									ontoggle={(e) => { if ((e.target as HTMLDetailsElement).open) expandedConfigs.add(analyzer.name); else expandedConfigs.delete(analyzer.name); expandedConfigs = new Set(expandedConfigs); }}
								>
									<summary class="flex cursor-pointer items-center justify-between px-3 py-2 text-sm font-medium hover:bg-muted/30 select-none">
										<span>{analyzer.display_name}</span>
										<Badge variant="outline" class="text-xs">{analyzer.type}</Badge>
									</summary>
									<div class="border-t border-border px-3 py-3">
										<AnalyzerConfigForm
											{analyzer}
											config={analyzerConfigs[analyzer.name] ?? {}}
											onConfigChange={(key, val) => handleConfigChange(analyzer.name, key, val)}
										/>
									</div>
								</details>
							{/each}
						</Card.Content>
					</Card.Root>
				{/if}

				<!-- Threshold configuration -->
				<Card.Root>
					<Card.Header>
						<Card.Title class="text-sm">Pass / Fail Thresholds</Card.Title>
						<Card.Description>Optionally fail the task if finding counts exceed these limits. Leave blank to skip.</Card.Description>
					</Card.Header>
					<Card.Content>
						<div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
							{#each severities as sev}
								<div>
									<label class="mb-1 block text-xs font-medium capitalize text-muted-foreground">{sev}</label>
									<input
										type="number"
										min="0"
										class="h-8 w-full rounded-md border border-input bg-background px-2 text-sm"
										placeholder="—"
										value={thresholds[sev] ?? ''}
										oninput={(e) => {
											const v = (e.target as HTMLInputElement).value;
											thresholds = { ...thresholds, [sev]: v === '' ? '' : Number(v) };
										}}
									/>
								</div>
							{/each}
						</div>
					</Card.Content>
				</Card.Root>
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

				{#if selectedProfile}
					<div class="flex items-center gap-2 rounded-md border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-sm">
						<BookmarkCheck class="h-4 w-4 text-emerald-500" />
						Profile: <strong>{selectedProfile.name}</strong>
					</div>
				{/if}

				{#if Object.values(thresholds).some((v) => v !== '')}
					<Card.Root>
						<Card.Header class="pb-2"><Card.Title class="text-sm">Thresholds</Card.Title></Card.Header>
						<Card.Content>
							<div class="flex flex-wrap gap-2">
								{#each severities as sev}
									{#if thresholds[sev] !== '' && thresholds[sev] !== undefined}
										<Badge variant="outline" class="text-xs capitalize">{sev} ≤ {thresholds[sev]}</Badge>
									{/if}
								{/each}
							</div>
						</Card.Content>
					</Card.Root>
				{/if}
			{/if}
		</div>

		<!-- Sidebar -->
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
					{#if selectedProfile}
						<div>
							<div class="mb-1 text-xs font-medium uppercase text-muted-foreground">Profile</div>
							<span class="flex items-center gap-1.5 text-sm text-emerald-400">
								<BookmarkCheck class="h-3.5 w-3.5" /> {selectedProfile.name}
							</span>
						</div>
						<Separator />
					{/if}
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
