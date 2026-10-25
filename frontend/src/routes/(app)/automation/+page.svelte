<script lang="ts">
	import { onMount } from 'svelte';
	import { goto, replaceState } from '$app/navigation';
	import { page } from '$app/state';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Segmented } from '$lib/components/ui/segmented';
	import Plus from '@lucide/svelte/icons/plus';
	import Edit from '@lucide/svelte/icons/pencil';
	import Copy from '@lucide/svelte/icons/copy';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import Play from '@lucide/svelte/icons/play';
	import Workflow from '@lucide/svelte/icons/workflow';
	import GitCompareArrows from '@lucide/svelte/icons/git-compare-arrows';
	import Search from '@lucide/svelte/icons/search';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { listPipelines, deletePipeline, clonePipeline, triggerPipelineRun, type PipelineListItem } from '$lib/api/automation';
	import { listExperiments, type Experiment } from '$lib/api/experiments';
	import { formatApiError } from '$lib/api/core';

	type AutomationItem =
		| { kind: 'workflow'; record: PipelineListItem }
		| { kind: 'experiment'; record: Experiment };

	const PAGE_SIZE = 20;
	const typeItems = [
		{ value: 'all', label: 'All' },
		{ value: 'experiment', label: 'Experiments', icon: GitCompareArrows },
		{ value: 'workflow', label: 'Workflows', icon: Workflow }
	];
	let pipelines = $state<PipelineListItem[]>([]);
	let experiments = $state<Experiment[]>([]);
	let workflowsLoading = $state(true);
	let experimentsLoading = $state(true);
	let workflowsError = $state('');
	let experimentsError = $state('');
	let actionError = $state('');
	let busyId = $state<string | null>(null);

	const search = $derived(page.url.searchParams.get('search') ?? '');
	const typeFilter = $derived(
		['experiment', 'workflow'].includes(page.url.searchParams.get('type') ?? '')
			? page.url.searchParams.get('type')!
			: 'all'
	);
	const statusFilter = $derived(page.url.searchParams.get('status') ?? '');
	const loading = $derived(
		(typeFilter !== 'experiment' && workflowsLoading) ||
		(typeFilter !== 'workflow' && experimentsLoading)
	);
	const hasError = $derived(
		(typeFilter !== 'experiment' && !!workflowsError) ||
		(typeFilter !== 'workflow' && !!experimentsError)
	);
	const createHref = $derived(typeFilter === 'all' ? '/automation/create' : `/automation/create?type=${typeFilter}`);
	const items = $derived.by(() => {
		const combined: AutomationItem[] = [
			...pipelines.map((record) => ({ kind: 'workflow' as const, record })),
			...experiments.map((record) => ({ kind: 'experiment' as const, record }))
		];
		const query = search.trim().toLowerCase();
		return combined.filter((item) => {
			if (typeFilter !== 'all' && item.kind !== typeFilter) return false;
			if (!statusFilter && item.record.status === 'archived') return false;
			if (statusFilter && statusFilter !== 'all' && item.record.status !== statusFilter) return false;
			const text = [item.record.name, item.record.description,
				...(item.kind === 'workflow' ? item.record.tags : [])].join(' ').toLowerCase();
			return !query || text.includes(query);
		}).sort((a, b) =>
			Date.parse(b.record.updated_at) - Date.parse(a.record.updated_at) || a.record.name.localeCompare(b.record.name)
		);
	});
	const pages = $derived(Math.max(1, Math.ceil(items.length / PAGE_SIZE)));
	const currentPage = $derived(Math.min(pages, Math.max(1,
		Math.floor(Number(page.url.searchParams.get('page'))) || 1)));
	const visible = $derived(items.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE));

	function setFilters(values: Record<string, string | null>) {
		const url = new URL(page.url);
		url.searchParams.delete('page');
		for (const [key, value] of Object.entries(values)) {
			if (value && (value !== 'all' || key === 'status')) url.searchParams.set(key, value);
			else url.searchParams.delete(key);
		}
		replaceState(url, page.state);
	}

	async function loadWorkflows() {
		workflowsLoading = true;
		workflowsError = '';
		try {
			// Search and paginate the complete collection, including later API pages.
			const first = await listPipelines({ page: 1, per_page: 100 });
			const all = [...first.items];
			for (let next = 2; next <= first.pages; next++) {
				const result = await listPipelines({ page: next, per_page: 100 });
				all.push(...result.items);
			}
			pipelines = all;
		} catch (e) {
			workflowsError = formatApiError(e, 'Failed to load workflows.');
		} finally {
			workflowsLoading = false;
		}
	}

	async function loadExperiments() {
		experimentsLoading = true;
		experimentsError = '';
		try {
			experiments = await listExperiments();
		} catch (e) {
			experimentsError = formatApiError(e, 'Failed to load experiments.');
		} finally {
			experimentsLoading = false;
		}
	}

	async function act(action: 'run' | 'clone' | 'delete', pipeline: PipelineListItem) {
		if (busyId) return;
		if (action === 'delete' && !confirm('Delete this workflow?')) return;
		const newName = action === 'clone' ? prompt('New workflow name:', `${pipeline.name} (copy)`) : null;
		if (action === 'clone' && !newName?.trim()) return;
		busyId = pipeline.id;
		actionError = '';
		try {
			if (action === 'run') {
				const run = await triggerPipelineRun(pipeline.id);
				await goto(`/automation/runs/${run.id}`);
			} else if (action === 'clone') {
				const copy = await clonePipeline(pipeline.id, newName!.trim());
				await goto(`/automation/${copy.id}`);
			} else {
				await deletePipeline(pipeline.id);
				pipelines = pipelines.filter((p) => p.id !== pipeline.id);
			}
		} catch (e) {
			actionError = formatApiError(e, `Failed to ${action} workflow.`);
		} finally {
			busyId = null;
		}
	}

	onMount(() => {
		void loadWorkflows();
		void loadExperiments();
	});
</script>

<div class="space-y-6">
	<PageHeader title="Automation" description="Compare models with experiments, or build workflows to generate, analyze, and report on apps.">
		{#snippet actions()}
			<Button size="sm" href={createHref}><Plus class="h-4 w-4" />New automation</Button>
		{/snippet}
	</PageHeader>

	<div class="space-y-3">
		<Segmented aria-label="Automation type" value={typeFilter} items={typeItems} onValueChange={(type) => setFilters({ type, status: null })} />
		<div class="flex flex-col gap-2 sm:flex-row">
			<div class="relative flex-1">
				<Search class="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
				<Input value={search} oninput={(e) => setFilters({ search: e.currentTarget.value })} placeholder="Search experiments and workflows…" aria-label="Search automations" class="h-9 pl-9" />
			</div>
			<select value={statusFilter} onchange={(e) => setFilters({ status: e.currentTarget.value })} class="std-select" aria-label="Filter by status">
				<option value="">Hide archived</option>
				<option value="all">All statuses</option>
				<option value="draft">Draft</option>
				{#if typeFilter !== 'experiment'}<option value="active">Active</option>{/if}
				{#if typeFilter !== 'workflow'}
					<option value="running">Running</option>
					<option value="completed">Completed</option>
				{/if}
				<option value="archived">Archived</option>
			</select>
		</div>
	</div>

	{#if actionError}<ErrorAlert message={actionError} />{/if}
	{#if typeFilter !== 'experiment' && workflowsError}
		<ErrorAlert title="Workflows unavailable" message={workflowsError} onretry={loadWorkflows} />
	{/if}
	{#if typeFilter !== 'workflow' && experimentsError}
		<ErrorAlert title="Experiments unavailable" message={experimentsError} onretry={loadExperiments} />
	{/if}
	{#if loading}<LoadingState label="loading automations" />{/if}

	{#if visible.length > 0}
		<div class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
			{#each visible as item (`${item.kind}-${item.record.id}`)}
				{@const record = item.record}
				{@const href = item.kind === 'workflow' ? `/automation/${record.id}` : `/experiments/${record.id}`}
				<div class="card-lift group relative flex flex-col rounded-md border bg-card focus-within:ring-2 focus-within:ring-ring">
					<div class="flex-1 space-y-3 p-4">
						<div class="flex items-center gap-1.5 text-xs text-muted-foreground">
							{#if item.kind === 'experiment'}<GitCompareArrows class="h-3.5 w-3.5" />Experiment
							{:else}<Workflow class="h-3.5 w-3.5" />Workflow{/if}
						</div>
						<div class="flex items-start justify-between gap-2">
							<h2 class="min-w-0 break-words text-sm font-medium leading-tight group-hover:text-primary">
								<a {href} class="after:absolute after:inset-0 focus-visible:outline-none">{record.name}</a>
							</h2>
							<StatusBadge status={record.status} />
						</div>
						{#if record.description}<p class="line-clamp-2 text-xs text-muted-foreground">{record.description}</p>{/if}
						{#if item.kind === 'experiment'}
							<div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
								<Badge variant="outline">{item.record.mode === 'agent' ? 'Agent' : 'Template scaffolding'}</Badge>
								<span>{item.record.app_requirement_ids.length} {item.record.app_requirement_ids.length === 1 ? 'app' : 'apps'}</span>
								<span>{item.record.condition_count} {item.record.condition_count === 1 ? 'condition' : 'conditions'}</span>
								<span>{item.record.repeats}× repeats</span>
								{#if item.record.stack_slug}<span class="font-mono">{item.record.stack_slug}</span>{/if}
								{#if item.record.base_seed !== null}<span>seed {item.record.base_seed}</span>{/if}
							</div>
						{:else if item.record.tags.length > 0}
							<div class="flex flex-wrap gap-1">
								{#each item.record.tags.slice(0, 4) as tag (tag)}<Badge variant="secondary" class="px-1.5 py-0 text-[11px]">{tag}</Badge>{/each}
								{#if item.record.tags.length > 4}<Badge variant="secondary" class="px-1.5 py-0 text-[11px]">+{item.record.tags.length - 4}</Badge>{/if}
							</div>
						{/if}
					</div>
					<div class="flex items-center justify-between border-t bg-muted/30 px-4 py-2 text-[11px] text-muted-foreground">
						<span>Updated {new Date(record.updated_at).toLocaleDateString()}</span>
						{#if item.kind === 'workflow'}<span class="font-mono">v{item.record.version}</span>{/if}
					</div>
					{#if item.kind === 'workflow'}
						<div class="relative z-10 flex items-center gap-0.5 border-t px-2 py-2">
							<Button variant="ghost" size="sm" class="h-10 text-success-fg hover:text-success-fg hover:bg-success/10" disabled={busyId !== null} onclick={() => act('run', item.record)} aria-label="Run workflow {record.name}"><Play class="h-4 w-4" />Run</Button>
							<Button variant="ghost" size="sm" class="h-10 w-10 p-0" href="/automation/{record.id}/edit" title="Edit" aria-label="Edit {record.name}"><Edit class="h-4 w-4" /></Button>
							<Button variant="ghost" size="sm" class="h-10 w-10 p-0" disabled={busyId !== null} onclick={() => act('clone', item.record)} title="Clone" aria-label="Clone {record.name}"><Copy class="h-4 w-4" /></Button>
							<Button variant="ghost" size="sm" class="ml-auto h-10 w-10 p-0 text-destructive hover:text-destructive" disabled={busyId !== null} onclick={() => act('delete', item.record)} title="Delete" aria-label="Delete {record.name}"><Trash2 class="h-4 w-4" /></Button>
						</div>
					{/if}
				</div>
			{/each}
		</div>
		<PaginationBar resultsText="{items.length} automation{items.length === 1 ? '' : 's'}" page={currentPage} {pages} onGoToPage={(next) => setFilters({ page: String(next) })} class="rounded-md border border-border" />
	{:else if !loading && !hasError}
		<EmptyState
			icon={Workflow}
			title={search || statusFilter || typeFilter !== 'all' ? 'No matching automations' : 'No automations yet'}
			description={search || statusFilter || typeFilter !== 'all' ? 'Try another search, type, or status filter, or create an automation.' : 'Create an experiment to compare models, or a workflow to automate generation, analysis, and reporting.'}
		>
			{#snippet action()}
				<Button size="sm" href={createHref}><Plus class="h-4 w-4" />New automation</Button>
				{#if search || statusFilter || typeFilter !== 'all'}
					<Button size="sm" variant="outline" onclick={() => setFilters({ search: null, type: null, status: null })}>Clear filters</Button>
				{/if}
			{/snippet}
		</EmptyState>
	{/if}
</div>
