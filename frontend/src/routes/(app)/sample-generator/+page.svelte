<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { Badge } from '$lib/components/ui/badge';
	import {
		getModels,
		getScaffoldingTemplates,
		getTemplateBundles,
		getAppTemplates,
		createCustomJob,
		createScaffoldingBatch,
		createCopilotJob,
		getGenerationJobs,
		getGenerationJob,
		cancelGenerationJob,
		type LLMModelSummary,
		type ScaffoldingTemplate,
		type TemplateBundle,
		type AppRequirementTemplate,
		type GenerationJob,
		type PaginatedJobs,
	} from '$lib/api/client';
	import type { SampleGeneratorMode } from './+page';
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
	import OpenRouterKeyBanner from '$lib/components/sample-generator/OpenRouterKeyBanner.svelte';

	let { data } = $props();

	type TabId = SampleGeneratorMode;
	const activeTab = $derived(data.mode);

	let models = $state<LLMModelSummary[]>([]);
	let modelsLoading = $state(true);
	let customModelId = $state<number | ''>('');
	let copilotModelId = $state<number | ''>('');

	let scaffoldingTemplates = $state<ScaffoldingTemplate[]>([]);
	let templateBundles = $state<TemplateBundle[]>([]);
	let appTemplates = $state<AppRequirementTemplate[]>([]);
	let scaffoldingLoading = $state(true);
	let bundlesLoading = $state(true);

	let customSubmitting = $state(false);
	let customError = $state('');
	let customJob = $state<GenerationJob | null>(null);
	let customPolling = $state(false);

	let scaffoldingSubmitting = $state(false);
	let scaffoldingError = $state('');
	let scaffoldingResult = $state<{ batch_id: string; job_count: number; status: string } | null>(null);

	let copilotSubmitting = $state(false);
	let copilotError = $state('');
	let copilotJob = $state<GenerationJob | null>(null);
	let copilotPolling = $state(false);

	let historyData = $state<PaginatedJobs | null>(null);
	let historyLoading = $state(true);
	let historyPage = $state(1);
	let historyModeFilter = $state<string>(untrack(() => data.mode));
	let historyStatusFilter = $state('');

	let lastRestoredJobId = $state<string | null>(null);
	let lastAppliedModelSlug = $state<string | null>(null);

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

	function sampleGeneratorUrl(opts: {
		mode?: TabId;
		model?: string | null;
		job?: string | null;
	} = {}) {
		const params = new URLSearchParams();
		params.set('mode', opts.mode ?? activeTab);
		const model = opts.model !== undefined ? opts.model : data.modelSlug;
		const job = opts.job !== undefined ? opts.job : data.jobId;
		if (model) params.set('model', model);
		if (job) params.set('job', job);
		const qs = params.toString();
		return `/sample-generator${qs ? `?${qs}` : ''}`;
	}

	function switchTab(tab: TabId) {
		historyModeFilter = tab;
		historyPage = 1;
		loadHistory();
		goto(sampleGeneratorUrl({ mode: tab, job: null }), {
			replaceState: true,
			keepFocus: true,
			noScroll: true,
		});
	}

	function syncJobInUrl(job: GenerationJob | null) {
		if (!job) return;
		const url = sampleGeneratorUrl({ mode: job.mode as TabId, job: job.id });
		goto(url, { replaceState: true, keepFocus: true, noScroll: true });
	}

	function applyModelSlugFromUrl() {
		if (!data.modelSlug || models.length === 0) return;
		if (lastAppliedModelSlug === data.modelSlug) return;
		const m = models.find((x) => x.canonical_slug === data.modelSlug);
		if (!m) return;
		lastAppliedModelSlug = data.modelSlug;
		if (activeTab === 'custom') customModelId = m.id;
		else if (activeTab === 'copilot') copilotModelId = m.id;
	}

	$effect(() => {
		applyModelSlugFromUrl();
	});

	$effect(() => {
		if (modelsLoading || models.length === 0) return;
		if (activeTab === 'custom' && customModelId === '' && !data.modelSlug) {
			customModelId = models[0].id;
		}
	});

	async function restoreJobFromUrl(jobId: string) {
		if (lastRestoredJobId === jobId) return;
		try {
			const job = await getGenerationJob(jobId);
			lastRestoredJobId = jobId;
			if (job.mode !== activeTab) {
				goto(sampleGeneratorUrl({ mode: job.mode as TabId, job: jobId }), {
					replaceState: true,
					noScroll: true,
				});
				return;
			}
			if (job.mode === 'custom') {
				customJob = job;
				if (job.status === 'running' || job.status === 'pending') pollCustomJob(job.id);
			} else if (job.mode === 'copilot') {
				copilotJob = job;
				if (job.status === 'running' || job.status === 'pending') pollCopilotJob(job.id);
			}
		} catch {
			// invalid job id
		}
	}

	$effect(() => {
		if (data.jobId) restoreJobFromUrl(data.jobId);
		else {
			lastRestoredJobId = null;
		}
	});

	onMount(async () => {
		const [modelsRes] = await Promise.all([
			getModels({ per_page: 100 }),
			loadScaffoldingData(),
			loadHistory(),
		]);
		models = modelsRes.items;
		modelsLoading = false;
		applyModelSlugFromUrl();
		if (data.jobId) restoreJobFromUrl(data.jobId);
	});

	async function loadScaffoldingData() {
		try {
			const [scaffolds, bundles, apps] = await Promise.all([
				getScaffoldingTemplates(),
				getTemplateBundles(),
				getAppTemplates(),
			]);
			scaffoldingTemplates = scaffolds;
			templateBundles = bundles;
			appTemplates = apps;
		} finally {
			scaffoldingLoading = false;
			bundlesLoading = false;
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

	async function submitCustomJob(payload: CustomPayload) {
		customSubmitting = true;
		customError = '';
		customJob = null;
		try {
			const job = await createCustomJob(payload);
			customJob = job;
			syncJobInUrl(job);
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
					// ignore
				}
			});
			while (customPolling) {
				await new Promise((r) => setTimeout(r, 4000));
				if (!customPolling) break;
				const job = await getGenerationJob(id);
				customJob = job;
				if (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') {
					loadHistory();
					break;
				}
			}
		} finally {
			sseCleanup?.();
			customPolling = false;
		}
	}

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

	async function submitCopilotJob(payload: CopilotPayload) {
		copilotSubmitting = true;
		copilotError = '';
		copilotJob = null;
		try {
			const job = await createCopilotJob(payload);
			copilotJob = job;
			syncJobInUrl(job);
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
					// ignore
				}
			});
			while (copilotPolling) {
				await new Promise((r) => setTimeout(r, 5000));
				if (!copilotPolling) break;
				const job = await getGenerationJob(id);
				copilotJob = job;
				if (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') {
					loadHistory();
					break;
				}
			}
		} finally {
			sseCleanup?.();
			copilotPolling = false;
		}
	}

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

	function openJobInSidebar(mode: string, id: string) {
		goto(sampleGeneratorUrl({ mode: mode as TabId, job: id }), {
			keepFocus: true,
			noScroll: true,
		});
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
	<div class="page-header">
		<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
			<div class="min-w-0">
				<div class="flex flex-wrap items-center gap-2">
					<h1>Sample Generator</h1>
					<Badge variant="outline" class="text-[10px]">AI-Powered</Badge>
				</div>
				<p class="mt-1 text-sm text-muted-foreground max-w-2xl">
					Generate code with three modes: one-shot <strong>Custom</strong> prompts, matrix
					<strong>Scaffolding</strong> batches, or iterative <strong>Copilot + Aider</strong> in a git
					workspace (uses your stored OpenRouter key).
				</p>
			</div>
			<a
				href="/sample-generator/templates"
				class="shrink-0 self-start inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
			>
				<Layers class="h-3.5 w-3.5" /> Manage Templates
			</a>
		</div>
	</div>

	<div class="flex gap-1 rounded-lg bg-muted p-1 overflow-x-auto flex-nowrap">
		<button
			type="button"
			class="flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap {activeTab === 'custom' ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}"
			onclick={() => switchTab('custom')}
		>
			<Code class="h-4 w-4" />
			Custom
		</button>
		<button
			type="button"
			class="flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap {activeTab === 'scaffolding' ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}"
			onclick={() => switchTab('scaffolding')}
		>
			<Layers class="h-4 w-4" />
			Scaffolding
		</button>
		<button
			type="button"
			class="flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap {activeTab === 'copilot' ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}"
			onclick={() => switchTab('copilot')}
		>
			<Bot class="h-4 w-4" />
			Copilot
			<Badge variant="outline" class="text-[9px] ml-0.5">Aider</Badge>
		</button>
	</div>

	<div class="grid gap-6 {activeTab !== 'scaffolding' ? 'lg:grid-cols-[1fr_360px]' : ''}">
		<div class="space-y-4">
			{#if activeTab === 'copilot'}
				<OpenRouterKeyBanner />
			{/if}
			<GeneratorForm
				activeTab={activeTab}
				{models}
				{modelsLoading}
				{scaffoldingTemplates}
				{templateBundles}
				{appTemplates}
				{scaffoldingLoading}
				{bundlesLoading}
				{customSubmitting}
				{customError}
				{scaffoldingSubmitting}
				{scaffoldingError}
				{scaffoldingResult}
				{copilotSubmitting}
				{copilotError}
				bind:customModelId
				bind:copilotModelId
				onSubmitCustom={submitCustomJob}
				onSubmitScaffolding={submitScaffoldingBatch}
				onSubmitCopilot={submitCopilotJob}
			/>
		</div>

		{#if activeTab !== 'scaffolding'}
			<GenerationResults
				mode={activeTab === 'copilot' ? 'copilot' : 'custom'}
				job={activeTab === 'copilot' ? copilotJob : customJob}
				{statusColors}
				{formatDuration}
				onCancel={cancelJob}
			/>
		{/if}
	</div>

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
		onOpenJob={openJobInSidebar}
	/>
</div>
