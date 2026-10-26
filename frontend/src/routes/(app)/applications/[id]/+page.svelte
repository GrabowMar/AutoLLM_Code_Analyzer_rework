<script lang="ts">
// Job detail. Data reloads whenever the route id changes (retry/replay/rerun
// navigate to the new job in place); while the job is pending or running it
// follows the job's SSE channel, with a slow poll as a safety net.
import { untrack } from 'svelte';
import { page } from '$app/state';
import { goto } from '$app/navigation';
import * as Tabs from '$lib/components/ui/tabs';
import PageHeader from '$lib/components/PageHeader.svelte';
import LoadingState from '$lib/components/LoadingState.svelte';
import EmptyState from '$lib/components/EmptyState.svelte';
import ErrorAlert from '$lib/components/ErrorAlert.svelte';
import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
import { Button } from '$lib/components/ui/button';
import {
	getGenerationJob,
	getJobArtifacts,
	getCopilotIterations,
	exportGenerationJob,
	cancelGenerationJob,
	deleteGenerationJob,
	retryGenerationJob,
	replayGenerationJob,
	rerunGenerationJob,
	type GenerationJob,
	type GenerationArtifact,
	type CopilotIteration,
	type JobPhase,
} from '$lib/api/generation';
import { formatApiError } from '$lib/api/core';
import { subscribe } from '$lib/api/sse';
import { isNotFoundError } from '$lib/components/generator/batches';
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
import AgentRunSummary from '$lib/components/applications/AgentRunSummary.svelte';
import JobProvenance from '$lib/components/applications/JobProvenance.svelte';
import GitHubExportDialog from '$lib/components/integrations/GitHubExportDialog.svelte';
import { jobDescription } from '$lib/components/applications/utils';
import {
	type CodeFootprint,
	type VirtualFile,
} from '$lib/components/applications/utils';

const LIVE_POLL_MS = 15_000;

const jobId = $derived(page.params.id ?? '');
let githubExportOpen = $state(false);
let loading = $state(true);
let notFound = $state(false);
let loadError = $state('');
let job = $state<GenerationJob | null>(null);
let artifacts = $state<GenerationArtifact[]>([]);
let iterations = $state<CopilotIteration[]>([]);
let activeSection = $state('overview');
let deleteOpen = $state(false);
let deleting = $state(false);
let cloneBusy = $state<string | null>(null);
let seq = 0;

function sectionsFor(j: GenerationJob | null): { id: string; label: string; icon: any }[] {
	return [
		{ id: 'overview', label: 'Overview', icon: Eye },
		j?.mode === 'agent' ? { id: 'agent', label: 'Agent Run', icon: Bot } : null,
		{ id: 'prompts', label: 'Prompts', icon: Terminal },
		{ id: 'files', label: 'Files & Code', icon: FolderTree },
		{ id: 'container', label: 'Container', icon: Server },
		{ id: 'tools', label: 'Tools', icon: Wrench },
		{ id: 'analyses', label: 'Analyses', icon: ChartBar },
		{ id: 'logs', label: 'Logs', icon: FileText },
		j?.mode === 'scaffolding' && j?.result_data?.backend_scan
			? { id: 'scan', label: 'API Scan', icon: Shield }
			: null,
		{ id: 'dependencies', label: 'Dependencies', icon: Package },
		{ id: 'artifacts', label: 'Artifacts', icon: Database },
		j?.mode === 'copilot' ? { id: 'iterations', label: 'Copilot Iterations', icon: Bot } : null,
		{ id: 'metrics', label: 'Cost & Metrics', icon: ChartBar },
	].filter(Boolean) as { id: string; label: string; icon: any }[];
}

const sections = $derived(sectionsFor(job));

const provider = $derived(job?.model_id_str?.split('/')[0] ?? '—');

const codeFootprint = $derived.by<CodeFootprint | null>(() => {
	if (!job?.result_data) return null;
	const rd = job.result_data;
	let totalLines = 0;
	let totalChars = 0;
	const languages: Record<string, number> = {};
	const files: VirtualFile[] = [];

	const fileMap = rd.files && typeof rd.files === 'object' ? (rd.files as Record<string, string>) : null;
	if (job.mode === 'agent' && fileMap) {
		// Agent runs: the whole collected workspace, path for path.
		const langByExt: Record<string, [string, string]> = {
			py: ['Python', 'python'], js: ['JavaScript', 'javascript'], jsx: ['JavaScript', 'javascript'],
			ts: ['TypeScript', 'typescript'], tsx: ['TypeScript', 'typescript'], vue: ['Vue', 'html'],
			html: ['HTML', 'html'], css: ['CSS', 'css'], json: ['JSON', 'json'], md: ['Markdown', 'markdown'],
		};
		for (const [path, code] of Object.entries(fileMap).sort(([a], [b]) => a.localeCompare(b))) {
			if (typeof code !== 'string') continue;
			const lines = code.split('\n').length;
			const [label, lang] = langByExt[path.split('.').pop()?.toLowerCase() ?? ''] ?? ['Other', 'text'];
			totalLines += lines;
			totalChars += code.length;
			languages[label] = (languages[label] ?? 0) + lines;
			files.push({ name: path, code, lang });
		}
	} else if (job.mode === 'scaffolding') {
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
	// Agent runs log no per-stage artifacts; the gateway's cost is in metrics.
	if (totalCost === 0 && typeof job?.metrics?.cost === 'number') {
		totalCost = job.metrics.cost;
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

async function fetchData(id: string, silent = false) {
	const mine = ++seq;
	try {
		const [j, arts] = await Promise.all([getGenerationJob(id), getJobArtifacts(id)]);
		if (mine !== seq) return;
		job = j;
		artifacts = arts;
		notFound = false;
		loadError = '';
		if (!sectionsFor(j).some((sec) => sec.id === activeSection)) activeSection = 'overview';
		if (j.mode === 'copilot') {
			const its = await getCopilotIterations(id);
			if (mine === seq) iterations = its;
		}
	} catch (e) {
		if (mine !== seq) return;
		if (isNotFoundError(e)) {
			notFound = true;
			job = null;
		} else if (!silent || !job) {
			loadError = formatApiError(e, 'Failed to load job details.');
		}
		// A failed live refresh keeps the current render; the next event or poll retries.
	} finally {
		if (mine === seq) loading = false;
	}
}

// Route id → data (also after retry/replay/rerun navigate to the new job).
$effect(() => {
	const id = jobId;
	untrack(() => {
		job = null;
		artifacts = [];
		iterations = [];
		loading = true;
		notFound = false;
		loadError = '';
		deleteOpen = false;
		fetchData(id);
	});
});

const liveId = $derived(job && (job.status === 'pending' || job.status === 'running') ? job.id : null);

$effect(() => {
	const id = liveId;
	if (!id) return;
	let timer: ReturnType<typeof setTimeout> | null = null;
	// Events come in bursts (phase + status); one refetch per burst is enough.
	const schedule = () => {
		if (timer) return;
		timer = setTimeout(() => {
			timer = null;
			fetchData(id, true);
		}, 750);
	};
	const stop = subscribe([`generation:${id}`], (event) => {
		const phase = event.data.phase;
		if (typeof phase === 'string' && job?.id === id) job.phase = phase as JobPhase;
		schedule();
	});
	// Covers events missed while the stream (re)connects.
	const poll = setInterval(() => {
		if (!document.hidden) schedule();
	}, LIVE_POLL_MS);
	return () => {
		stop();
		clearInterval(poll);
		if (timer) clearTimeout(timer);
	};
});

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
	const id = job.id;
	try {
		const result = await cancelGenerationJob(id);
		if (result.success) {
			toast.success('Job cancelled');
			await fetchData(id, true);
		} else {
			toast.error(result.message || 'This job can no longer be cancelled');
		}
	} catch (e) {
		toast.error(formatApiError(e, 'Failed to cancel job'));
	}
}

async function confirmDelete() {
	if (!job) return;
	deleting = true;
	try {
		const result = await deleteGenerationJob(job.id);
		if (result.success) {
			toast.success('Job deleted');
			deleteOpen = false;
			goto(job.batch_id ? `/sample-generator/batches/${job.batch_id}` : '/applications');
		} else {
			toast.error(result.message || 'Cannot delete this job');
		}
	} catch (e) {
		toast.error(formatApiError(e, 'Failed to delete job'));
	} finally {
		deleting = false;
	}
}

async function handleRetry() {
	if (!job) return;
	try {
		const newJob = await retryGenerationJob(job.id);
		toast.success('Job retried — new job created');
		goto(`/applications/${newJob.id}`);
	} catch (e) {
		toast.error(formatApiError(e, 'Failed to retry job'));
	}
}

async function handleClone(kind: 'replay' | 'rerun') {
	if (!job || cloneBusy) return;
	cloneBusy = kind;
	try {
		const created = kind === 'replay' ? await replayGenerationJob(job.id) : await rerunGenerationJob(job.id);
		toast.success(kind === 'replay' ? 'Replay started — same snapshot and seed' : 'Another trial started — new seed');
		goto(`/applications/${created.id}`);
	} catch (e) {
		toast.error(formatApiError(e, kind === 'replay' ? 'Could not replay this run' : 'Could not start another trial'));
	} finally {
		cloneBusy = null;
	}
}
</script>

<!-- Section tabs for the header's nav strip (inside the page's Tabs.Root). -->
{#snippet sectionTabs()}
	<Tabs.List aria-label="Application sections">
		{#each sections as sec (sec.id)}
			{@const SecIcon = sec.icon}
			<Tabs.Trigger value={sec.id}><SecIcon />{sec.label}</Tabs.Trigger>
		{/each}
	</Tabs.List>
{/snippet}

{#if loading}
	<LoadingState label="loading application" />
{:else if !job && !notFound}
	<div class="space-y-4">
		<PageHeader title="Application" back={{ href: '/applications', label: 'Applications' }} />
		<ErrorAlert message={loadError || 'Failed to load job details.'} onretry={() => {
				loading = true;
				fetchData(jobId);
			}} />
	</div>
{:else if !job}
	<div class="space-y-4">
		<PageHeader title="Application" back={{ href: '/applications', label: 'Applications' }} />
		<EmptyState icon={AlertTriangle} title="Job not found" description="It may have been deleted, or the link is wrong.">
			{#snippet action()}
				<Button variant="outline" size="sm" href="/applications">Back to Applications</Button>
			{/snippet}
		</EmptyState>
	</div>
{:else}
	<Tabs.Root bind:value={activeSection} class="space-y-4">
		<ApplicationHeader
			{job}
			{provider}
			{codeFootprint}
			totalTokens={costData.totalTokens}
			totalCost={costData.totalCost}
			onCancel={handleCancel}
			onRetry={handleRetry}
			onExport={handleExport}
			onDownloadCode={downloadCode}
			onDelete={() => (deleteOpen = true)}
			onExportGithub={() => (githubExportOpen = true)}
			nav={sectionTabs}
		/>

		<Tabs.Content value="overview" class="pt-0">
			<div class="space-y-4">
				<ApplicationOverview {job} {provider} {codeFootprint} {fixesData} />
				<JobProvenance
					{job}
					busy={cloneBusy}
					onReplay={() => handleClone('replay')}
					onRerun={() => handleClone('rerun')}
				/>
			</div>
		</Tabs.Content>
		{#if job.mode === 'agent'}
			<Tabs.Content value="agent" class="pt-0"><AgentRunSummary {job} /></Tabs.Content>
		{/if}
		<Tabs.Content value="prompts" class="pt-0"><ApplicationPrompts {job} {artifacts} /></Tabs.Content>
		<Tabs.Content value="files" class="pt-0"><ApplicationFiles files={virtualFiles} /></Tabs.Content>
		<Tabs.Content value="container" class="pt-0"><ApplicationContainer jobId={job.id} jobStatus={job.status} /></Tabs.Content>
		<Tabs.Content value="tools" class="pt-0"><ApplicationTools jobId={job.id} /></Tabs.Content>
		<Tabs.Content value="analyses" class="pt-0"><ApplicationAnalyses jobId={job.id} /></Tabs.Content>
		<Tabs.Content value="logs" class="pt-0"><ApplicationLogs jobId={job.id} /></Tabs.Content>
		{#if backendScan}
			<Tabs.Content value="scan" class="pt-0"><ApplicationApiScan scan={backendScan} /></Tabs.Content>
		{/if}
		<Tabs.Content value="dependencies" class="pt-0"><ApplicationDependencies deps={backendDeps} mode={job.mode} /></Tabs.Content>
		<Tabs.Content value="artifacts" class="pt-0"><ApplicationArtifacts {artifacts} /></Tabs.Content>
		{#if job.mode === 'copilot'}
			<Tabs.Content value="iterations" class="pt-0"><ApplicationIterations {iterations} /></Tabs.Content>
		{/if}
		<Tabs.Content value="metrics" class="pt-0">
			<ApplicationMetrics {job} {costData} artifactCount={artifacts.length} iterationCount={iterations.length} />
		</Tabs.Content>
	</Tabs.Root>

	<ConfirmDialog
		bind:open={deleteOpen}
		title="Delete this job?"
		description="Deletes the job, its generated files and its run record. This cannot be undone."
		confirmLabel="Delete job"
		destructive
		busy={deleting}
		onConfirm={confirmDelete}
	/>

	<GitHubExportDialog bind:open={githubExportOpen} applicationId={job.id} defaultName={jobDescription(job)} />
{/if}
