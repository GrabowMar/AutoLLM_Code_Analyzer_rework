<script lang="ts">
import { page } from '$app/stores';
import { goto } from '$app/navigation';
import * as Card from '$lib/components/ui/card';
import { Button } from '$lib/components/ui/button';
import {
	getGenerationJob,
	getJobArtifacts,
	getCopilotIterations,
	exportGenerationJob,
	cancelGenerationJob,
	deleteGenerationJob,
	retryGenerationJob,
	type GenerationJob,
	type GenerationArtifact,
	type CopilotIteration,
} from '$lib/api/client';
import { onMount } from 'svelte';
import { toast } from 'svelte-sonner';

import Eye from '@lucide/svelte/icons/eye';
import Bot from '@lucide/svelte/icons/bot';
import FolderTree from '@lucide/svelte/icons/folder-tree';
import Terminal from '@lucide/svelte/icons/terminal';
import Package from '@lucide/svelte/icons/package';
import Shield from '@lucide/svelte/icons/shield';
import Database from '@lucide/svelte/icons/database';
import ChartBar from '@lucide/svelte/icons/chart-bar';
import AlertTriangle from '@lucide/svelte/icons/alert-triangle';
import LoaderCircle from '@lucide/svelte/icons/loader-circle';
import Server from '@lucide/svelte/icons/server';
import Wrench from '@lucide/svelte/icons/wrench';
import FileText from '@lucide/svelte/icons/file-text';

import ApplicationHeader from '$lib/components/applications/ApplicationHeader.svelte';
import ApplicationOverview from '$lib/components/applications/ApplicationOverview.svelte';
import ApplicationPrompts from '$lib/components/applications/ApplicationPrompts.svelte';
import ApplicationFiles from '$lib/components/applications/ApplicationFiles.svelte';
import ApplicationApiScan from '$lib/components/applications/ApplicationApiScan.svelte';
import ApplicationDependencies from '$lib/components/applications/ApplicationDependencies.svelte';
import ApplicationArtifacts from '$lib/components/applications/ApplicationArtifacts.svelte';
import ApplicationIterations from '$lib/components/applications/ApplicationIterations.svelte';
import ApplicationMetrics from '$lib/components/applications/ApplicationMetrics.svelte';
import ApplicationContainer from '$lib/components/applications/ApplicationContainer.svelte';
import ApplicationTools from '$lib/components/applications/ApplicationTools.svelte';
import ApplicationAnalyses from '$lib/components/applications/ApplicationAnalyses.svelte';
import ApplicationLogs from '$lib/components/applications/ApplicationLogs.svelte';
import {
	type CodeFootprint,
	type VirtualFile,
} from '$lib/components/applications/utils';

const jobId = $derived($page.params.id ?? '');
let loading = $state(true);
let job = $state<GenerationJob | null>(null);
let artifacts = $state<GenerationArtifact[]>([]);
let iterations = $state<CopilotIteration[]>([]);
let activeSection = $state('overview');

const sections = $derived(
	[
		{ id: 'overview', label: 'Overview', icon: Eye },
		{ id: 'prompts', label: 'Prompts', icon: Terminal },
		{ id: 'files', label: 'Files & Code', icon: FolderTree },
		{ id: 'container', label: 'Container', icon: Server },
		{ id: 'tools', label: 'Tools', icon: Wrench },
		{ id: 'analyses', label: 'Analyses', icon: ChartBar },
		{ id: 'logs', label: 'Logs', icon: FileText },
		job?.mode === 'scaffolding' && job?.result_data?.backend_scan
			? { id: 'scan', label: 'API Scan', icon: Shield }
			: null,
		{ id: 'dependencies', label: 'Dependencies', icon: Package },
		{ id: 'artifacts', label: 'Artifacts', icon: Database },
		job?.mode === 'copilot' ? { id: 'iterations', label: 'Copilot Iterations', icon: Bot } : null,
		{ id: 'metrics', label: 'Cost & Metrics', icon: ChartBar },
	].filter(Boolean) as { id: string; label: string; icon: any }[]
);

const provider = $derived(job?.model_id_str?.split('/')[0] ?? '—');

const codeFootprint = $derived.by<CodeFootprint | null>(() => {
	if (!job?.result_data) return null;
	const rd = job.result_data;
	let totalLines = 0;
	let totalChars = 0;
	const languages: Record<string, number> = {};
	const files: VirtualFile[] = [];

	if (job.mode === 'scaffolding') {
		if (rd.backend_code) {
			const lines = rd.backend_code.split('\n').length;
			totalLines += lines;
			totalChars += rd.backend_code.length;
			languages['Python'] = lines;
			files.push({ name: 'backend/app.py', code: rd.backend_code, lang: 'python' });
		}
		if (rd.frontend_code) {
			const lines = rd.frontend_code.split('\n').length;
			totalLines += lines;
			totalChars += rd.frontend_code.length;
			languages['JavaScript'] = lines;
			files.push({ name: 'frontend/App.jsx', code: rd.frontend_code, lang: 'javascript' });
		}
	} else {
		const content = rd.content ?? rd.raw_response ?? '';
		if (content) {
			totalLines = content.split('\n').length;
			totalChars = content.length;
			const blocks = content.match(/```(\w+)/g) ?? [];
			for (const b of blocks) {
				const lang = b.replace('```', '');
				const mapped = lang === 'py' ? 'Python' : lang === 'js' ? 'JavaScript' : lang === 'html' ? 'HTML' : lang === 'css' ? 'CSS' : lang.charAt(0).toUpperCase() + lang.slice(1);
				languages[mapped] = (languages[mapped] ?? 0) + 1;
			}
			files.push({ name: 'output.txt', code: content, lang: 'text' });
		}
	}

	return {
		totalLines,
		totalChars,
		languages,
		fileCount: (rd.backend_files ?? 0) + (rd.frontend_files ?? 0) || files.length,
		files,
		truncated: rd.backend_truncated || rd.frontend_truncated || rd.truncated || false,
	};
});

const virtualFiles = $derived<VirtualFile[]>(codeFootprint?.files ?? []);
const backendScan = $derived(job?.result_data?.backend_scan ?? null);
const backendDeps = $derived<(string | unknown)[]>(job?.result_data?.backend_dependencies ?? job?.result_data?.dependencies ?? []);

const costData = $derived.by(() => {
	let totalCost = 0;
	let totalPrompt = 0;
	let totalCompletion = 0;
	const byStage: Record<string, { cost: number; prompt: number; completion: number }> = {};

	for (const art of artifacts) {
		totalCost += art.total_cost ?? 0;
		totalPrompt += art.prompt_tokens ?? 0;
		totalCompletion += art.completion_tokens ?? 0;
		const s = art.stage ?? 'unknown';
		if (!byStage[s]) byStage[s] = { cost: 0, prompt: 0, completion: 0 };
		byStage[s].cost += art.total_cost ?? 0;
		byStage[s].prompt += art.prompt_tokens ?? 0;
		byStage[s].completion += art.completion_tokens ?? 0;
	}

	if (totalPrompt === 0 && job?.metrics) {
		totalPrompt = job.metrics.prompt_tokens ?? 0;
		totalCompletion = job.metrics.completion_tokens ?? 0;
	}

	const totalTokens = totalPrompt + totalCompletion;
	const tokensPerSec = job?.duration_seconds ? totalTokens / job.duration_seconds : 0;

	return { totalCost, totalPrompt, totalCompletion, totalTokens, tokensPerSec, byStage };
});

const fixesData = $derived.by(() => {
	if (!iterations || iterations.length === 0) return { total: 0, retry: 0, autofix: 0, llm: 0 };
	let retry = 0, autofix = 0, llm = 0;
	for (const it of iterations) {
		const action = (it.action ?? '').toLowerCase();
		if (action.includes('retry')) retry++;
		else if (action.includes('auto') || action.includes('autofix')) autofix++;
		else if (action.includes('llm')) llm++;
	}
	return { total: retry + autofix + llm, retry, autofix, llm };
});

const frameworkInfo = $derived.by(() => {
	const name = job?.scaffolding_name ?? '';
	let backend = '—', frontend = '—', database = '—';
	if (name) {
		const parts = name.split(/\s*\+\s*/);
		if (parts.length >= 2) {
			frontend = parts[0].trim();
			backend = parts[1].trim();
		} else if (parts.length === 1) {
			backend = parts[0].trim();
		}
		const lower = name.toLowerCase();
		if (lower.includes('postgres')) database = 'PostgreSQL';
		else if (lower.includes('mysql')) database = 'MySQL';
		else if (lower.includes('sqlite')) database = 'SQLite';
		else if (lower.includes('mongo')) database = 'MongoDB';
	}
	return { backend, frontend, database };
});

async function fetchData() {
	try {
		const [j, arts] = await Promise.all([
			getGenerationJob(jobId),
			getJobArtifacts(jobId),
		]);
		job = j;
		artifacts = arts;
		if (j.mode === 'copilot') {
			iterations = await getCopilotIterations(jobId);
		}
	} catch {
		toast.error('Failed to load job details');
	} finally {
		loading = false;
	}
}

async function handleExport() {
	try {
		const data = await exportGenerationJob(jobId);
		const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = `job-${jobId.substring(0, 8)}.json`;
		a.click();
		URL.revokeObjectURL(url);
		toast.success('Exported JSON');
	} catch {
		toast.error('Export failed');
	}
}

function downloadCode() {
	if (!codeFootprint || codeFootprint.files.length === 0) return;
	for (const f of codeFootprint.files) {
		const blob = new Blob([f.code], { type: 'text/plain' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = f.name.split('/').pop() ?? 'code.txt';
		a.click();
		URL.revokeObjectURL(url);
	}
	toast.success('Downloaded code files');
}

async function handleCancel() {
	if (!job) return;
	try {
		const result = await cancelGenerationJob(job.id);
		if (result.success) {
			toast.success('Job cancelled');
			job = { ...job, status: 'cancelled' };
		} else {
			toast.error('Cannot cancel this job');
		}
	} catch {
		toast.error('Failed to cancel job');
	}
}

async function handleDelete() {
	if (!job) return;
	if (!confirm('Are you sure you want to delete this job? This cannot be undone.')) return;
	try {
		const result = await deleteGenerationJob(job.id);
		if (result.success) {
			toast.success('Job deleted');
			goto('/applications');
		} else {
			toast.error('Cannot delete this job');
		}
	} catch {
		toast.error('Failed to delete job');
	}
}

async function handleRetry() {
	if (!job) return;
	try {
		const newJob = await retryGenerationJob(job.id);
		toast.success('Job retried — new job created');
		goto(`/applications/${newJob.id}`);
	} catch {
		toast.error('Failed to retry job');
	}
}

function scrollToSection(id: string) {
	activeSection = id;
	document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

onMount(fetchData);
</script>

<svelte:head>
	<title>{job?.model_name ?? 'Application'} - LLM Lab</title>
</svelte:head>

{#if loading}
	<div class="flex items-center justify-center py-32">
		<LoaderCircle class="h-8 w-8 animate-spin text-muted-foreground" />
		<span class="ml-3 text-muted-foreground">Loading application details...</span>
	</div>
{:else if !job}
	<Card.Root>
		<Card.Content class="py-16 text-center">
			<AlertTriangle class="mx-auto h-12 w-12 text-red-400 mb-4" />
			<h3 class="text-lg font-medium">Job not found</h3>
			<Button variant="outline" size="sm" href="/applications" class="mt-4">Back to Applications</Button>
		</Card.Content>
	</Card.Root>
{:else}
	<div class="space-y-5">
		<ApplicationHeader
			{job}
			{provider}
			{codeFootprint}
			{sections}
			{activeSection}
			totalTokens={costData.totalTokens}
			totalCost={costData.totalCost}
			onCancel={handleCancel}
			onRetry={handleRetry}
			onExport={handleExport}
			onDownloadCode={downloadCode}
			onDelete={handleDelete}
			onNavigate={scrollToSection}
		/>

		<ApplicationOverview
			{job}
			{provider}
			{codeFootprint}
			{fixesData}
			{frameworkInfo}
			{costData}
		/>

		<ApplicationPrompts {job} {artifacts} />

		<ApplicationFiles files={virtualFiles} />

		<ApplicationContainer jobId={job.id} jobStatus={job.status} />

		<ApplicationTools jobId={job.id} />

		<ApplicationAnalyses jobId={job.id} />

		<ApplicationLogs jobId={job.id} />

		{#if backendScan}
			<ApplicationApiScan scan={backendScan} />
		{/if}

		<ApplicationDependencies deps={backendDeps} mode={job.mode} />

		<ApplicationArtifacts {artifacts} />

		{#if job.mode === 'copilot' && iterations.length > 0}
			<ApplicationIterations {iterations} />
		{/if}

		<ApplicationMetrics
			{job}
			{costData}
			artifactCount={artifacts.length}
			iterationCount={iterations.length}
		/>
	</div>
{/if}
