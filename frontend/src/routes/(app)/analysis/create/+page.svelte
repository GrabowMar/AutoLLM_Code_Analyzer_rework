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
import RefreshCw from '@lucide/svelte/icons/refresh-cw';
import AlertCircle from '@lucide/svelte/icons/alert-circle';
import Store from '@lucide/svelte/icons/store';
import { toast } from 'svelte-sonner';
import { createRun } from '$lib/api/runs';
import { getToolCatalog, type AnalyzerTool } from '$lib/api/analyzers';
import { getGenerationJobs, type GenerationJobList } from '$lib/api/client';
import AnalysisTargetForm from '$lib/components/analysis/AnalysisTargetForm.svelte';

let step = $state(1);
const stepLabels = ['Select Source', 'Tools', 'Review'];

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

// Tools
let toolsLoading = $state(true);
let toolsError = $state('');
let tools = $state<AnalyzerTool[]>([]);
let selectedTools = $state(new Set<string>());

const categoryLabels: Record<string, string> = {
	security: 'Security',
	lint: 'Lint & Quality',
	secrets: 'Secrets',
	performance: 'Performance',
	ai: 'AI Review',
};
const categoryOrder = ['security', 'lint', 'secrets', 'performance', 'ai'];

const groupedTools = $derived(
	categoryOrder
		.map((cat) => ({ category: cat, items: tools.filter((t) => t.category === cat) }))
		.filter((g) => g.items.length > 0),
);
const selectedToolsList = $derived(tools.filter((t) => selectedTools.has(t.slug)));
const installedSelectedCount = $derived(
	selectedToolsList.filter((t) => t.installed).length,
);

function toggleTool(slug: string) {
	const next = new Set(selectedTools);
	if (next.has(slug)) next.delete(slug);
	else next.add(slug);
	selectedTools = next;
}

// Config
let runName = $state('');
let autoStart = $state(true);

// Launch
let launching = $state(false);
let launchError = $state('');

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

async function loadTools() {
	toolsLoading = true;
	toolsError = '';
	try {
		tools = await getToolCatalog();
	} catch (err: any) {
		toolsError = err?.detail ?? 'Failed to load analyzers';
	} finally {
		toolsLoading = false;
	}
}

async function handleLaunch() {
	launching = true;
	launchError = '';
	try {
		const run = await createRun({
			name: runName || undefined,
			tool_slugs: [...selectedTools],
			generation_job_id: sourceMode === 'job' ? (selectedJobId ?? undefined) : undefined,
			source_code:
				sourceMode === 'paste'
					? { backend_code: pasteBackend, frontend_code: pasteFrontend }
					: undefined,
			auto_start: autoStart,
		});
		toast.success(autoStart ? 'Analysis started' : 'Analysis run created');
		await goto(`/analysis/${run.id}`);
	} catch (err: any) {
		const detail = err?.detail;
		launchError = Array.isArray(detail)
			? detail.join('; ')
			: (detail ?? err?.message ?? 'Failed to create analysis run');
		toast.error(launchError);
	} finally {
		launching = false;
	}
}

onMount(() => {
	loadJobs();
	loadTools();
});

const canAdvance = $derived.by(() => {
	if (step === 1) return hasSource;
	if (step === 2) return installedSelectedCount > 0;
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
			Analysis
		</Button>
		<span>/</span>
		<span class="text-foreground font-medium">New Analysis</span>
	</div>

	<div class="page-header">
		<h1>New Analysis</h1>
		<p>Pick a source, choose installed analyzers, and launch a run.</p>
	</div>

	<div class="grid gap-4 sm:gap-6 lg:grid-cols-4">
		<div class="space-y-6 lg:col-span-3">
			<!-- Step progress -->
			<div class="flex items-center gap-2 overflow-x-auto">
				{#each stepLabels as label, i}
					<button
						class="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors {step === i + 1 ? 'bg-primary/10 text-primary font-medium' : i + 1 < step ? 'text-emerald-500' : 'text-muted-foreground'}"
						aria-current={step === i + 1 ? 'step' : undefined}
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
				<div class="space-y-4">
					<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
						<p class="text-sm text-muted-foreground">
							Only <strong>installed</strong> analyzers can be run. Install more from the
							<a href="/analyzers" class="text-primary underline-offset-2 hover:underline">tool shop</a>.
						</p>
						<div class="flex items-center gap-2">
							<Button variant="outline" size="sm" href="/analyzers">
								<Store class="mr-1.5 h-3 w-3" /> Shop
							</Button>
							<Button variant="outline" size="sm" onclick={loadTools}>
								<RefreshCw class="mr-1.5 h-3 w-3" /> Refresh
							</Button>
							<Badge variant="outline">{installedSelectedCount} selected</Badge>
						</div>
					</div>

					{#if toolsLoading}
						<div class="grid grid-cols-1 gap-4 md:grid-cols-2">
							{#each Array(4) as _}
								<div class="animate-pulse motion-reduce:animate-none rounded-lg border border-border p-4">
									<div class="mb-3 h-4 w-28 rounded bg-muted"></div>
									{#each Array(3) as _}
										<div class="mb-2 flex items-center gap-2.5">
											<div class="h-4 w-4 rounded bg-muted"></div>
											<div class="h-3 w-40 rounded bg-muted"></div>
										</div>
									{/each}
								</div>
							{/each}
						</div>
					{:else if toolsError}
						<div class="flex items-center gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
							<AlertCircle class="h-4 w-4 shrink-0" />
							{toolsError}
							<Button variant="outline" size="sm" class="ml-auto" onclick={loadTools}>Retry</Button>
						</div>
					{:else}
						<div class="grid grid-cols-1 gap-4 md:grid-cols-2">
							{#each groupedTools as group}
								<Card.Root>
									<Card.Header>
										<Card.Title class="text-sm">{categoryLabels[group.category] ?? group.category}</Card.Title>
										<Card.Description>
											{group.items.filter((t) => t.installed).length}/{group.items.length} installed
										</Card.Description>
									</Card.Header>
									<Card.Content>
										<div class="space-y-2">
											{#each group.items as tool}
												<label class="flex items-center gap-2.5 rounded-md px-2 py-1.5 transition-colors focus-within:ring-2 focus-within:ring-ring {tool.installed ? 'cursor-pointer hover:bg-muted/30' : 'cursor-not-allowed opacity-70'}">
													<input
														type="checkbox"
														class="rounded"
														checked={selectedTools.has(tool.slug)}
														disabled={!tool.installed}
														onchange={() => toggleTool(tool.slug)}
													/>
													<div class="min-w-0 flex-1">
														<div class="flex items-center gap-2">
															<span class="text-sm font-medium">{tool.name}</span>
															{#if tool.kind === 'ai'}
																<Badge variant="outline" class="text-[10px]">AI</Badge>
															{/if}
															{#if tool.installed}
																<Badge variant="outline" class="text-[10px] border-emerald-500/30 bg-emerald-500/10 text-emerald-500">
																	<Check class="mr-0.5 h-2.5 w-2.5" /> Installed
																</Badge>
															{:else}
																<Badge variant="outline" class="text-[10px] border-amber-500/30 bg-amber-500/10 text-amber-500">Not installed</Badge>
																<a href="/analyzers" class="text-[10px] text-amber-500 underline-offset-2 hover:underline">Install →</a>
															{/if}
														</div>
														<div class="text-xs text-muted-foreground line-clamp-2">{tool.description}</div>
													</div>
												</label>
											{/each}
										</div>
									</Card.Content>
								</Card.Root>
							{/each}
						</div>
					{/if}
				</div>
			{/if}

			{#if step === 3}
				<Card.Root>
					<Card.Header>
						<Card.Title class="text-sm">Run Settings</Card.Title>
					</Card.Header>
					<Card.Content class="space-y-4">
						<div>
							<label for="run-name" class="mb-1 block text-sm font-medium">Run name (optional)</label>
							<input
								id="run-name"
								type="text"
								class="h-9 w-full max-w-sm rounded-md border border-input bg-background px-3 text-sm"
								placeholder="Analysis run"
								bind:value={runName}
							/>
						</div>
						<label class="flex items-center gap-2 text-sm">
							<input type="checkbox" class="rounded" bind:checked={autoStart} />
							Start immediately after creation
						</label>
					</Card.Content>
				</Card.Root>

				<Card.Root>
					<Card.Header><Card.Title class="text-sm">Review</Card.Title></Card.Header>
					<Card.Content class="space-y-3 text-sm">
						<div class="flex justify-between">
							<span class="text-muted-foreground">Source</span>
							<span class="font-medium">
								{#if sourceMode === 'job' && selectedJob}
									{selectedJob.model_name ?? selectedJob.id.slice(0, 8)}
								{:else if sourceMode === 'paste'}
									Pasted code
								{:else}
									—
								{/if}
							</span>
						</div>
						<Separator />
						<div>
							<div class="mb-1 text-muted-foreground">Analyzers ({installedSelectedCount})</div>
							<div class="flex flex-wrap gap-1">
								{#each selectedToolsList.filter((t) => t.installed) as t}
									<Badge variant="secondary" class="text-[10px]">{t.name}</Badge>
								{/each}
							</div>
						</div>
						{#if launchError}
							<div class="flex items-center gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-destructive">
								<AlertCircle class="h-4 w-4 shrink-0" />
								{launchError}
							</div>
						{/if}
					</Card.Content>
				</Card.Root>
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
						{#if step < 3}
							<Button size="sm" disabled={!canAdvance} onclick={() => step++}>
								Next <ArrowRight class="ml-1.5 h-3.5 w-3.5" />
							</Button>
						{:else}
							<Button size="sm" disabled={launching || installedSelectedCount === 0} onclick={handleLaunch}>
								{#if launching}
									<Loader class="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" /> Launching…
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
							Analyzers ({installedSelectedCount})
						</div>
						{#if installedSelectedCount > 0}
							<div class="flex flex-wrap gap-1">
								{#each selectedToolsList.filter((t) => t.installed) as t}
									<Badge variant="secondary" class="text-[10px]">{t.name}</Badge>
								{/each}
							</div>
						{:else}
							<span class="text-xs italic text-muted-foreground">None selected</span>
						{/if}
					</div>
				</Card.Content>
			</Card.Root>
		</div>
	</div>
</div>
