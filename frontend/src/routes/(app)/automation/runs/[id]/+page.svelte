<script lang="ts">
	import { formatDateTime as fmt } from '$lib/utils/formatters';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { onMount, onDestroy } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import * as Tabs from '$lib/components/ui/tabs';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import Workflow from '@lucide/svelte/icons/workflow';
	import ListChecks from '@lucide/svelte/icons/list-checks';
	import ScrollText from '@lucide/svelte/icons/scroll-text';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import XCircle from '@lucide/svelte/icons/x-circle';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import CheckCircle from '@lucide/svelte/icons/check-circle';
	import AlertCircle from '@lucide/svelte/icons/alert-circle';
	import Clock from '@lucide/svelte/icons/clock';
	import { getRun, getPipeline, cancelRun, retryRun, getRunLogs, type PipelineRunDetail, type PipelineDetail } from '$lib/api/client';
	import { subscribe } from '$lib/api/sse';
	import type { Edge } from '@xyflow/svelte';
	import WorkflowCanvas from '$lib/components/workflow/WorkflowCanvas.svelte';
	import { pipelineToFlow, type PipelineConfig, type WorkflowNode } from '$lib/utils/pipeline-flow';

	const id = page.params.id as string;

	let run = $state<PipelineRunDetail | null>(null);
	let pipeline = $state<PipelineDetail | null>(null);
	let loading = $state(true);
	let error = $state('');
	let activeTab = $state<string>('canvas');
	let logs = $state('');
	let logsLoading = $state(false);
	let logsError = $state('');
	let retrying = $state(false);

	let nodes = $state<WorkflowNode[]>([]);
	let edges = $state<Edge[]>([]);
	let selectedNodeId = $state<string | null>(null);

	const stepIdToName = $derived.by(() => {
		const map = new Map<string, string>();
		pipeline?.steps?.forEach((s) => map.set(s.id, s.name));
		return map;
	});

	const runStatusMap = $derived.by(() => {
		const map: Record<string, string> = {};
		run?.step_runs?.forEach((sr) => {
			if (sr.step_id) {
				const name = stepIdToName.get(sr.step_id);
				if (name) map[name] = sr.status;
			}
		});
		return map;
	});

	async function load() {
		loading = true;
		try {
			run = await getRun(id);
			error = '';
			if (run && (!pipeline || pipeline.id !== run.pipeline_id)) {
				try {
					pipeline = await getPipeline(run.pipeline_id);
					const cfg = (pipeline.config as unknown as PipelineConfig | undefined) ?? { steps: [] };
					const flow = pipelineToFlow(cfg);
					nodes = flow.nodes;
					edges = flow.edges;
				} catch {
					/* pipeline may be deleted; canvas just shows nothing */
				}
			}
		} catch {
			error = 'Failed to load run';
		} finally {
			loading = false;
		}
	}

	async function cancel() {
		if (!confirm('Cancel this run?')) return;
		run = await cancelRun(id);
	}

	async function retry() {
		retrying = true;
		try {
			const newRun = await retryRun(id);
			goto(`/automation/runs/${newRun.id}`);
		} catch (e: unknown) {
			const body = e as { status?: number; detail?: string };
			if (body?.status === 404) {
				alert('Retry endpoint not yet available (7b not deployed)');
			} else {
				alert(body?.detail ?? 'Failed to retry run');
			}
		} finally {
			retrying = false;
		}
	}

	async function loadLogs() {
		logsLoading = true;
		logsError = '';
		try {
			const data = await getRunLogs(id);
			logs = data.logs ?? '';
		} catch {
			if (run?.step_runs?.length) {
				logs = run.step_runs.map((sr) => {
					const name = sr.step_id ? stepIdToName.get(sr.step_id) ?? sr.step_id.slice(0, 8) : 'unknown';
					const parts = [`=== Step ${name} [${sr.status}] ===`];
					if (sr.error) parts.push(`Error: ${sr.error}`);
					if (Object.keys(sr.output).length > 0) parts.push(`Output: ${JSON.stringify(sr.output, null, 2)}`);
					return parts.join('\n');
				}).join('\n\n');
				logsError = 'Live log endpoint not available — showing step output data.';
			} else {
				logsError = 'Logs not available yet.';
			}
		} finally {
			logsLoading = false;
		}
	}

	function duration(a: string | null, b: string | null): string | null {
		if (!a) return null;
		const end = b ? new Date(b).getTime() : Date.now();
		const ms = end - new Date(a).getTime();
		if (ms < 1000) return `${ms}ms`;
		if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
		return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
	}

	let unsub: (() => void) | null = null;

	onMount(async () => {
		await load();
		unsub = subscribe([`automation:${id}`], async () => {
			try {
				run = await getRun(id);
			} catch {
				/* ignore transient */
			}
		});
	});

	onDestroy(() => { unsub?.(); });

	$effect(() => {
		if (activeTab === 'logs') loadLogs();
	});
</script>

<div class="space-y-6">
	<PageHeader
		title="Workflow run"
		documentTitle="Run {id.slice(0, 8)}"
		back={run ? { href: `/automation/${run.pipeline_id}`, label: pipeline?.name ?? 'Workflow' } : { href: '/automation', label: 'Workflows' }}
	>
		{#snippet meta()}
			{#if run}<StatusBadge status={run.status} />{/if}
		{/snippet}
		{#snippet actions()}
			<Button variant="outline" size="sm" onclick={load}><RefreshCw class="h-4 w-4" />Refresh</Button>
			{#if run && (run.status === 'failed' || run.status === 'cancelled')}
				<Button variant="outline" size="sm" onclick={retry} disabled={retrying}>
					<RotateCcw class="h-4 w-4" />{retrying ? 'Retrying…' : 'Retry'}
				</Button>
			{/if}
			{#if run && (run.status === 'pending' || run.status === 'running')}
				<Button variant="destructive" size="sm" onclick={cancel}><XCircle class="h-4 w-4" />Cancel</Button>
			{/if}
		{/snippet}
		<p class="font-mono text-xs text-muted-foreground">{id}</p>
	</PageHeader>

	{#if loading}
		<LoadingState label="loading run" />
	{:else if error}
		<ErrorAlert message={error} onretry={load} />
	{:else if run}
		<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
			<Card.Root>
				<Card.Header><Card.Title>Run info</Card.Title></Card.Header>
				<Card.Content class="space-y-2 text-sm">
					<div class="flex justify-between">
						<span class="text-muted-foreground">Workflow</span>
						<a class="hover:underline font-mono text-xs" href="/automation/{run.pipeline_id}">{pipeline?.name ?? `${run.pipeline_id.slice(0, 8)}…`}</a>
					</div>
					<div class="flex justify-between"><span class="text-muted-foreground">Started</span><span>{fmt(run.started_at)}</span></div>
					<div class="flex justify-between"><span class="text-muted-foreground">Completed</span><span>{fmt(run.completed_at)}</span></div>
					<div class="flex justify-between">
						<span class="text-muted-foreground">Duration</span>
						<span>{duration(run.started_at, run.completed_at) ?? '—'}</span>
					</div>
					{#if run.error}
						<div class="pt-2">
							<p class="section-label">Error</p>
							<p class="text-danger-fg text-xs mt-1 font-mono break-words">{run.error}</p>
						</div>
					{/if}
				</Card.Content>
			</Card.Root>

			<Card.Root>
				<Card.Header><Card.Title>Parameters</Card.Title></Card.Header>
				<Card.Content>
					<pre class="text-xs font-mono bg-muted/50 rounded p-2 overflow-auto max-h-40">{JSON.stringify(run.params, null, 2)}</pre>
				</Card.Content>
			</Card.Root>
		</div>

		<Tabs.Root bind:value={activeTab}>
			<Tabs.List>
				<Tabs.Trigger value="canvas"><Workflow />Workflow canvas</Tabs.Trigger>
				<Tabs.Trigger value="timeline"><ListChecks />Step timeline</Tabs.Trigger>
				<Tabs.Trigger value="logs"><ScrollText />Logs</Tabs.Trigger>
			</Tabs.List>

			<Tabs.Content value="canvas">
				<Card.Root>
					<Card.Header><Card.Title>Workflow status</Card.Title></Card.Header>
					<Card.Content class="p-0">
						{#if nodes.length === 0}
							<EmptyState compact icon={Workflow} title="Workflow not available" description="The parent workflow may have been deleted." />
						{:else}
							<div class="h-[500px] border-t">
								<WorkflowCanvas bind:nodes bind:edges bind:selectedNodeId readOnly runStatusMap={runStatusMap} />
							</div>
							<div class="px-4 py-2 border-t flex items-center gap-3 text-[11px] text-muted-foreground flex-wrap">
								<span class="flex items-center gap-1"><span class="inline-block h-2 w-2 rounded-full ring-1 ring-border"></span>pending</span>
								<span class="flex items-center gap-1"><span class="inline-block h-2 w-2 rounded-full ring-2 ring-info animate-pulse motion-reduce:animate-none"></span>running</span>
								<span class="flex items-center gap-1"><span class="inline-block h-2 w-2 rounded-full ring-2 ring-success"></span>succeeded</span>
								<span class="flex items-center gap-1"><span class="inline-block h-2 w-2 rounded-full ring-2 ring-destructive"></span>failed</span>
								<span class="flex items-center gap-1"><span class="inline-block h-2 w-2 rounded-full ring-2 ring-warning"></span>cancelled</span>
							</div>
						{/if}
					</Card.Content>
				</Card.Root>
			</Tabs.Content>

			<Tabs.Content value="timeline">
				<Card.Root>
					<Card.Header>
						<Card.Title>Step runs</Card.Title>
						<Card.Description>{run.step_runs.length} recorded</Card.Description>
					</Card.Header>
					<Card.Content>
						{#if run.step_runs.length === 0}
							<EmptyState compact icon={ListChecks} title="No step runs yet" description="Steps appear here as the run progresses." />
						{:else}
							<div class="space-y-0">
								{#each run.step_runs as sr, idx}
									{@const dur = duration(sr.started_at, sr.completed_at)}
									{@const stepName = sr.step_id ? stepIdToName.get(sr.step_id) ?? sr.step_id.slice(0, 8) : 'unknown'}
									<div class="flex items-start gap-3">
										<div class="flex flex-col items-center shrink-0 pt-1">
											{#if sr.status === 'succeeded'}
												<CheckCircle class="h-5 w-5 text-success-fg shrink-0" aria-hidden="true" />
											{:else if sr.status === 'failed'}
												<AlertCircle class="h-5 w-5 text-danger-fg shrink-0" aria-hidden="true" />
											{:else if sr.status === 'running'}
												<LoaderCircle class="h-5 w-5 text-info-fg animate-spin shrink-0" aria-hidden="true" />
											{:else if sr.status === 'cancelled'}
												<XCircle class="h-5 w-5 text-warning-fg shrink-0" aria-hidden="true" />
											{:else}
												<Clock class="h-5 w-5 text-muted-foreground shrink-0" aria-hidden="true" />
											{/if}
											{#if idx < run.step_runs.length - 1}
												<div class="w-px flex-1 bg-border my-1 min-h-[1.5rem]"></div>
											{/if}
										</div>
										<div class="flex-1 min-w-0 rounded-md border p-3 mb-2">
											<div class="flex items-start justify-between gap-2">
												<div class="min-w-0">
													<p class="font-medium text-sm font-mono">{stepName}</p>
													<p class="text-xs text-muted-foreground">Attempt {sr.attempt} · {fmt(sr.started_at)}</p>
												</div>
												<div class="flex items-center gap-2 shrink-0">
													{#if dur}<span class="text-xs text-muted-foreground">{dur}</span>{/if}
													<StatusBadge status={sr.status} />
												</div>
											</div>
											{#if sr.error}
												<p class="text-xs text-danger-fg mt-2 font-mono break-words">{sr.error}</p>
											{/if}
											{#if Object.keys(sr.output).length > 0}
												<details class="mt-2">
													<summary class="text-xs text-muted-foreground cursor-pointer hover:text-foreground">Output</summary>
													<pre class="text-xs font-mono bg-muted/50 rounded p-2 mt-1 overflow-auto max-h-32">{JSON.stringify(sr.output, null, 2)}</pre>
												</details>
											{/if}
										</div>
									</div>
								{/each}
							</div>
						{/if}
					</Card.Content>
				</Card.Root>
			</Tabs.Content>

			<Tabs.Content value="logs">
				<Card.Root>
					<Card.Header><Card.Title>Run logs</Card.Title></Card.Header>
					<Card.Content>
						{#if logsLoading}
							<LoadingState class="py-8" label="loading logs" />
						{:else}
							{#if logsError}
								<p class="text-xs text-muted-foreground mb-2">{logsError}</p>
							{/if}
							{#if logs}
								<pre class="text-xs font-mono bg-muted/50 rounded p-3 overflow-auto max-h-[60vh] whitespace-pre-wrap">{logs}</pre>
							{:else}
								<EmptyState compact icon={ScrollText} title="No logs available" />
							{/if}
						{/if}
					</Card.Content>
				</Card.Root>
			</Tabs.Content>
		</Tabs.Root>
	{/if}
</div>
