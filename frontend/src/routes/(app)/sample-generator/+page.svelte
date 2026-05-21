<script lang="ts">
	import { onMount } from 'svelte';
	import { Badge } from '$lib/components/ui/badge';
	import { Separator } from '$lib/components/ui/separator';
	import {
		getModels,
		getScaffoldingTemplates,
		getAppTemplates,
		createCustomJob,
		createScaffoldingBatch,
		createCopilotJob,
		getGenerationJobs,
		getGenerationJob,
		cancelGenerationJob,
		type LLMModelSummary,
		type ScaffoldingTemplate,
		type AppRequirementTemplate,
		type GenerationJob,
		type PaginatedJobs,
	} from '$lib/api/client';
	import Layers from '@lucide/svelte/icons/layers';
	import Code from '@lucide/svelte/icons/code';
	import Bot from '@lucide/svelte/icons/bot';
	import { subscribe } from '$lib/api/sse';
	import GeneratorForm, {
		type CustomPayload,
		type ScaffoldingPayload,
		type CopilotPayload,
	} from '$lib/components/sample-generator/GeneratorForm.svelte';
	import GenerationResults from '$lib/components/sample-generator/GenerationResults.svelte';
	import GenerationHistory from '$lib/components/sample-generator/GenerationHistory.svelte';

	type TabId = 'custom' | 'scaffolding' | 'copilot';
	let activeTab = $state<TabId>('custom');
	let models = $state<LLMModelSummary[]>([]);
	let modelsLoading = $state(true);

	// Scaffolding/app templates
	let scaffoldingTemplates = $state<ScaffoldingTemplate[]>([]);
	let appTemplates = $state<AppRequirementTemplate[]>([]);
	let scaffoldingLoading = $state(true);

	// Custom job state
	let customSubmitting = $state(false);
	let customError = $state('');
	let customJob = $state<GenerationJob | null>(null);
	let customPolling = $state(false);

	// Scaffolding job state
	let scaffoldingSubmitting = $state(false);
	let scaffoldingError = $state('');
	let scaffoldingResult = $state<{ batch_id: string; job_count: number; status: string } | null>(null);

	// Copilot job state
	let copilotSubmitting = $state(false);
	let copilotError = $state('');
	let copilotJob = $state<GenerationJob | null>(null);
	let copilotPolling = $state(false);

	// History
	let historyData = $state<PaginatedJobs | null>(null);
	let historyLoading = $state(true);
	let historyPage = $state(1);
	let historyModeFilter = $state('');
	let historyStatusFilter = $state('');

	const statusColors: Record<string, string> = {
		completed: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30',
		running: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
		failed: 'bg-red-500/15 text-red-400 border-red-500/30',
		pending: 'bg-amber-500/15 text-amber-500 border-amber-500/30',
		cancelled: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30',
	};

	const modeLabels: Record<string, string> = {
		custom: 'Custom',
		scaffolding: 'Scaffolding',
		copilot: 'Copilot',
	};

	// --- Data loading ---
	onMount(async () => {
		const [modelsRes] = await Promise.all([
			getModels({ per_page: 100 }),
			loadScaffoldingData(),
			loadHistory(),
		]);
		models = modelsRes.items;
		modelsLoading = false;
	});

	async function loadScaffoldingData() {
		try {
			const [scaffolds, apps] = await Promise.all([
				getScaffoldingTemplates(),
				getAppTemplates(),
			]);
			scaffoldingTemplates = scaffolds;
			appTemplates = apps;
		} finally {
			scaffoldingLoading = false;
		}
	}

	async function loadHistory() {
		historyLoading = true;
		try {
			historyData = await getGenerationJobs({
				page: historyPage,
				per_page: 15,
				mode: historyModeFilter || undefined,
				status: historyStatusFilter || undefined,
			});
		} catch {
			historyData = null;
		} finally {
			historyLoading = false;
		}
	}

	// --- Custom mode actions ---
	async function submitCustomJob(payload: CustomPayload) {
		customSubmitting = true;
		customError = '';
		customJob = null;
		try {
			const job = await createCustomJob(payload);
			customJob = job;
			pollCustomJob(job.id);
			loadHistory();
		} catch (err: any) {
			const detail = err?.detail ?? err?.message ?? 'Failed to create job';
			const remediation = err?.remediation ? ` ${err.remediation}` : '';
			customError = detail + remediation;
		} finally {
			customSubmitting = false;
		}
	}

	async function pollCustomJob(id: string) {
		customPolling = true;
		let sseCleanup: (() => void) | null = null;
		try {
			sseCleanup = subscribe([`generation:${id}`], async () => {
				try {
					const job = await getGenerationJob(id);
					customJob = job;
					if (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') {
						loadHistory();
						sseCleanup?.();
						sseCleanup = null;
						customPolling = false;
					}
				} catch {
					// ignore transient errors
				}
			});
			while (customPolling) {
				await new Promise(r => setTimeout(r, 4000));
				if (!customPolling) break;
				const job = await getGenerationJob(id);
				customJob = job;
				if (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') {
					loadHistory();
					break;
				}
			}
		} catch {
			// polling stopped
		} finally {
			sseCleanup?.();
			customPolling = false;
		}
	}

	// --- Scaffolding mode actions ---
	async function submitScaffoldingBatch(payload: ScaffoldingPayload) {
		scaffoldingSubmitting = true;
		scaffoldingError = '';
		scaffoldingResult = null;
		try {
			const result = await createScaffoldingBatch(payload);
			scaffoldingResult = result;
			loadHistory();
		} catch (err: any) {
			const detail = err?.detail ?? err?.message ?? 'Failed to create batch';
			const remediation = err?.remediation ? ` ${err.remediation}` : '';
			scaffoldingError = detail + remediation;
		} finally {
			scaffoldingSubmitting = false;
		}
	}

	// --- Copilot mode actions ---
	async function submitCopilotJob(payload: CopilotPayload) {
		copilotSubmitting = true;
		copilotError = '';
		copilotJob = null;
		try {
			const job = await createCopilotJob(payload);
			copilotJob = job;
			pollCopilotJob(job.id);
			loadHistory();
		} catch (err: any) {
			const detail = err?.detail ?? err?.message ?? 'Failed to create copilot job';
			const remediation = err?.remediation ? ` ${err.remediation}` : '';
			copilotError = detail + remediation;
		} finally {
			copilotSubmitting = false;
		}
	}

	async function pollCopilotJob(id: string) {
		copilotPolling = true;
		let sseCleanup: (() => void) | null = null;
		try {
			sseCleanup = subscribe([`generation:${id}`], async () => {
				try {
					const job = await getGenerationJob(id);
					copilotJob = job;
					if (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') {
						loadHistory();
						sseCleanup?.();
						sseCleanup = null;
						copilotPolling = false;
					}
				} catch {
					// ignore transient errors
				}
			});
			while (copilotPolling) {
				await new Promise(r => setTimeout(r, 5000));
				if (!copilotPolling) break;
				const job = await getGenerationJob(id);
				copilotJob = job;
				if (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') {
					loadHistory();
					break;
				}
			}
		} catch {
			// polling stopped
		} finally {
			sseCleanup?.();
			copilotPolling = false;
		}
	}

	// --- History actions ---
	async function cancelJob(id: string) {
		try {
			await cancelGenerationJob(id);
			loadHistory();
		} catch {
			// ignore
		}
	}

	function onHistoryFilterChange() {
		historyPage = 1;
		loadHistory();
	}

	function onHistoryPageChange(page: number) {
		historyPage = page;
		loadHistory();
	}

	function formatDuration(seconds: number | null): string {
		if (seconds === null || seconds === undefined) return '—';
		if (seconds < 60) return `${seconds.toFixed(0)}s`;
		const m = Math.floor(seconds / 60);
		const s = Math.round(seconds % 60);
		return `${m}m ${s}s`;
	}

	function formatDate(dateStr: string | null): string {
		if (!dateStr) return '—';
		return new Date(dateStr).toLocaleString();
	}
</script>

<svelte:head>
	<title>Sample Generator - LLM Lab</title>
</svelte:head>

<div class="space-y-6">
	<!-- Header -->
	<div class="page-header">
		<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
			<div class="min-w-0">
				<div class="flex flex-wrap items-center gap-2">
					<h1>Sample Generator</h1>
					<Badge variant="outline" class="text-[10px]">AI-Powered</Badge>
				</div>
				<p>Generate code samples using LLMs with custom prompts, scaffolding templates, or AI copilot.</p>
			</div>
			<a href="/sample-generator/templates" class="shrink-0 self-start inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors">
				<Layers class="h-3.5 w-3.5" /> Manage Templates
			</a>
		</div>
	</div>

	<!-- Tabs -->
	<div class="flex gap-1 rounded-lg bg-muted p-1 overflow-x-auto flex-nowrap">
		<button
			class="flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap {activeTab === 'custom' ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}"
			onclick={() => activeTab = 'custom'}
		>
			<Code class="h-4 w-4" />
			Custom
		</button>
		<button
			class="flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap {activeTab === 'scaffolding' ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}"
			onclick={() => activeTab = 'scaffolding'}
		>
			<Layers class="h-4 w-4" />
			Scaffolding
		</button>
		<button
			class="flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap {activeTab === 'copilot' ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}"
			onclick={() => activeTab = 'copilot'}
		>
			<Bot class="h-4 w-4" />
			Copilot
		</button>
	</div>

	{#if activeTab === 'scaffolding'}
		<div class="space-y-4">
			<GeneratorForm
				{activeTab}
				{models}
				{modelsLoading}
				{scaffoldingTemplates}
				{appTemplates}
				{scaffoldingLoading}
				{customSubmitting}
				{customError}
				{scaffoldingSubmitting}
				{scaffoldingError}
				{scaffoldingResult}
				{copilotSubmitting}
				{copilotError}
				onSubmitCustom={submitCustomJob}
				onSubmitScaffolding={submitScaffoldingBatch}
				onSubmitCopilot={submitCopilotJob}
			/>
		</div>
	{:else}
		<div class="grid gap-6 lg:grid-cols-[1fr_360px]">
			<div class="space-y-4">
				<GeneratorForm
					{activeTab}
					{models}
					{modelsLoading}
					{scaffoldingTemplates}
					{appTemplates}
					{scaffoldingLoading}
					{customSubmitting}
					{customError}
					{scaffoldingSubmitting}
					{scaffoldingError}
					{scaffoldingResult}
					{copilotSubmitting}
					{copilotError}
					onSubmitCustom={submitCustomJob}
					onSubmitScaffolding={submitScaffoldingBatch}
					onSubmitCopilot={submitCopilotJob}
				/>
			</div>
			<GenerationResults
				mode={activeTab === 'copilot' ? 'copilot' : 'custom'}
				job={activeTab === 'copilot' ? copilotJob : customJob}
				{statusColors}
				{formatDuration}
				onCancel={cancelJob}
			/>
		</div>
	{/if}

	<Separator />

	<GenerationHistory
		{historyData}
		{historyLoading}
		bind:historyModeFilter
		bind:historyStatusFilter
		{statusColors}
		{modeLabels}
		{formatDuration}
		{formatDate}
		onRefresh={loadHistory}
		onFilterChange={onHistoryFilterChange}
		onPageChange={onHistoryPageChange}
		onCancelJob={cancelJob}
	/>
</div>
