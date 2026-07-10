<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Badge } from '$lib/components/ui/badge';
	import ModelSelector from '$lib/components/sample-generator/ModelSelector.svelte';
	import BundlePicker from '$lib/components/sample-generator/BundlePicker.svelte';
	import { getModels } from '$lib/api/models';
	import { getTemplateBundles, type TemplateBundle } from '$lib/api/generation';
	import type { LLMModelSummary } from '$lib/api/client';
	import {
		getExperiment,
		listConditions,
		createCondition,
		deleteCondition,
		previewExperiment,
		launchExperiment,
		getExperimentStatus,
		exportExperiment,
		archiveExperiment,
		type Experiment,
		type ExperimentCondition,
		type ExperimentPreview,
		type ExperimentStatusReport,
	} from '$lib/api/experiments';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import Rocket from '@lucide/svelte/icons/rocket';
	import Calculator from '@lucide/svelte/icons/calculator';
	import Download from '@lucide/svelte/icons/download';
	import Archive from '@lucide/svelte/icons/archive';

	const experimentId = $derived(page.params.id!);

	let experiment = $state<Experiment | null>(null);
	let conditions = $state<ExperimentCondition[]>([]);
	let loading = $state(true);
	let error = $state('');

	let models = $state<LLMModelSummary[]>([]);
	let bundles = $state<TemplateBundle[]>([]);
	let newConditionModelId = $state<number | ''>('');
	let newConditionBundleId = $state<number | ''>('');
	let addingCondition = $state(false);

	let previewing = $state(false);
	let previewResult = $state<ExperimentPreview | null>(null);
	let launching = $state(false);
	let launchError = $state('');

	let status = $state<ExperimentStatusReport | null>(null);
	let statusPolling = $state(false);

	const statusColors: Record<string, string> = {
		pending: 'bg-amber-500/15 text-amber-500 border-amber-500/30',
		running: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
		completed: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30',
		failed: 'bg-red-500/15 text-red-400 border-red-500/30',
		cancelled: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30',
	};

	onMount(load);

	async function load() {
		loading = true;
		error = '';
		try {
			const [exp, conds, modelsRes, bundlesRes] = await Promise.all([
				getExperiment(experimentId),
				listConditions(experimentId),
				getModels({ per_page: 100 }),
				getTemplateBundles(),
			]);
			experiment = exp;
			conditions = conds;
			models = modelsRes.items;
			bundles = bundlesRes;
			if (exp.status !== 'draft') {
				await refreshStatus();
			}
		} catch (err: any) {
			error = err?.detail ?? err?.message ?? 'Failed to load experiment';
		} finally {
			loading = false;
		}
	}

	async function refreshStatus() {
		statusPolling = true;
		try {
			status = await getExperimentStatus(experimentId);
		} finally {
			statusPolling = false;
		}
	}

	async function addCondition() {
		if (!newConditionModelId || !newConditionBundleId) return;
		addingCondition = true;
		error = '';
		try {
			const cond = await createCondition(experimentId, {
				model_id: newConditionModelId as number,
				template_bundle_id: newConditionBundleId as number,
			});
			conditions = [...conditions, cond];
			newConditionModelId = '';
			newConditionBundleId = '';
			previewResult = null;
		} catch (err: any) {
			error = err?.detail ?? err?.message ?? 'Failed to add condition';
		} finally {
			addingCondition = false;
		}
	}

	async function removeCondition(id: number) {
		try {
			await deleteCondition(experimentId, id);
			conditions = conditions.filter((c) => c.id !== id);
			previewResult = null;
		} catch (err: any) {
			error = err?.detail ?? err?.message ?? 'Failed to remove condition';
		}
	}

	async function runPreview() {
		previewing = true;
		error = '';
		try {
			previewResult = await previewExperiment(experimentId);
		} catch (err: any) {
			error = err?.detail ?? err?.message ?? 'Failed to preview experiment';
		} finally {
			previewing = false;
		}
	}

	async function doLaunch() {
		launching = true;
		launchError = '';
		try {
			await launchExperiment(experimentId);
			experiment = await getExperiment(experimentId);
			await refreshStatus();
		} catch (err: any) {
			const detail = err?.detail ?? err?.message ?? 'Failed to launch experiment';
			const remediation = err?.remediation ? ` ${err.remediation}` : '';
			launchError = detail + remediation;
		} finally {
			launching = false;
		}
	}

	async function doArchive() {
		try {
			experiment = await archiveExperiment(experimentId);
		} catch (err: any) {
			error = err?.detail ?? err?.message ?? 'Failed to archive experiment';
		}
	}

	async function doExport() {
		const data = await exportExperiment(experimentId);
		const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = `experiment-${experiment?.slug ?? experimentId}.json`;
		a.click();
		URL.revokeObjectURL(url);
	}
</script>

<div class="mx-auto max-w-4xl space-y-6 p-6">
	<Button variant="ghost" size="sm" onclick={() => goto('/experiments')}>
		<ArrowLeft class="h-4 w-4" />
		Back to experiments
	</Button>

	{#if loading}
		<div class="flex justify-center py-16"><LoaderCircle class="h-6 w-6 animate-spin text-muted-foreground" /></div>
	{:else if error && !experiment}
		<div class="rounded-md bg-red-500/10 border border-red-500/30 px-4 py-3 text-sm text-red-400">{error}</div>
	{:else if experiment}
		<div class="flex items-start justify-between gap-4">
			<div>
				<div class="flex items-center gap-2">
					<h1 class="text-2xl font-semibold tracking-tight">{experiment.name}</h1>
					<Badge variant="outline" class="text-[10px]">{experiment.status}</Badge>
				</div>
				{#if experiment.description}
					<p class="mt-1 text-sm text-muted-foreground">{experiment.description}</p>
				{/if}
				{#if experiment.hypothesis}
					<p class="mt-2 text-sm italic text-muted-foreground">"{experiment.hypothesis}"</p>
				{/if}
			</div>
			<div class="flex shrink-0 gap-2">
				<Button variant="outline" size="sm" onclick={doExport}>
					<Download class="h-3.5 w-3.5" />
					Export
				</Button>
				{#if experiment.status !== 'archived'}
					<Button variant="outline" size="sm" onclick={doArchive}>
						<Archive class="h-3.5 w-3.5" />
						Archive
					</Button>
				{/if}
			</div>
		</div>

		<div class="flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted-foreground">
			<span>{experiment.app_requirement_ids.length} apps</span>
			<span>{experiment.repeats}× repeats per cell</span>
			<span>temp {experiment.temperature}</span>
			<span>max_tokens {experiment.max_tokens}</span>
			{#if experiment.base_seed !== null}
				<span>base seed {experiment.base_seed}</span>
			{:else}
				<span>random seed per run</span>
			{/if}
		</div>

		{#if error}
			<div class="rounded-md bg-red-500/10 border border-red-500/30 px-4 py-3 text-sm text-red-400">{error}</div>
		{/if}

		<Card.Root>
			<Card.Header class="pb-3">
				<div class="flex items-center justify-between">
					<Card.Title>Conditions</Card.Title>
					<span class="text-xs text-muted-foreground">model × prompt bundle</span>
				</div>
			</Card.Header>
			<Card.Content class="space-y-3">
				{#if conditions.length > 0}
					<div class="space-y-1.5">
						{#each conditions as cond (cond.id)}
							<div class="flex items-center justify-between rounded-md border px-3 py-2 text-sm">
								<span>
									<span class="font-medium">{cond.model_name ?? `model #${cond.model}`}</span>
									<span class="text-muted-foreground"> × </span>
									<span class="font-mono text-xs">{cond.bundle_slug}@{cond.bundle_version}</span>
								</span>
								{#if experiment.status === 'draft'}
									<button
										type="button"
										class="text-muted-foreground hover:text-red-400"
										onclick={() => removeCondition(cond.id)}
										aria-label="Remove condition"
									>
										<Trash2 class="h-3.5 w-3.5" />
									</button>
								{/if}
							</div>
						{/each}
					</div>
				{:else}
					<p class="text-sm text-muted-foreground">No conditions yet — add at least one model × bundle pair.</p>
				{/if}

				{#if experiment.status === 'draft'}
					<div class="grid gap-3 rounded-md border bg-muted/20 p-3 sm:grid-cols-[1fr_1fr_auto]">
						<div class="space-y-1">
							<span class="text-xs text-muted-foreground">Model</span>
							<ModelSelector {models} mode="single" bind:selectedId={newConditionModelId} maxHeight="max-h-48" />
						</div>
						<div class="space-y-1">
							<span class="text-xs text-muted-foreground">Prompt bundle</span>
							<BundlePicker {bundles} bind:selectedId={newConditionBundleId} />
						</div>
						<div class="flex items-end">
							<Button
								size="sm"
								disabled={!newConditionModelId || !newConditionBundleId || addingCondition}
								onclick={addCondition}
							>
								<Plus class="h-3.5 w-3.5" />
								Add
							</Button>
						</div>
					</div>
				{/if}
			</Card.Content>
		</Card.Root>

		{#if experiment.status === 'draft'}
			<Card.Root>
				<Card.Header class="pb-3">
					<Card.Title>Preview &amp; launch</Card.Title>
				</Card.Header>
				<Card.Content class="space-y-3">
					<div class="flex items-center gap-2">
						<Button variant="outline" size="sm" onclick={runPreview} disabled={previewing}>
							{#if previewing}<LoaderCircle class="h-3.5 w-3.5 animate-spin" />{:else}<Calculator class="h-3.5 w-3.5" />{/if}
							Preview
						</Button>
						<Button
							size="sm"
							onclick={doLaunch}
							disabled={launching || conditions.length === 0 || experiment.app_requirement_ids.length === 0}
						>
							{#if launching}<LoaderCircle class="h-3.5 w-3.5 animate-spin" />{:else}<Rocket class="h-3.5 w-3.5" />{/if}
							Launch
						</Button>
					</div>

					{#if previewResult}
						<div class="rounded-md border bg-muted/20 p-3 text-sm">
							<div class="flex flex-wrap gap-x-6 gap-y-1">
								<span><span class="font-medium">{previewResult.total_jobs}</span> jobs</span>
								<span><span class="font-medium">{previewResult.conditions}</span> conditions</span>
								<span><span class="font-medium">{previewResult.apps}</span> apps</span>
								<span>
									est. cost
									<span class="font-medium">${previewResult.estimated_cost.toFixed(4)}</span>
								</span>
							</div>
						</div>
					{/if}

					{#if launchError}
						<div class="rounded-md bg-red-500/10 border border-red-500/30 px-3 py-2 text-xs text-red-400">
							{launchError}
						</div>
					{/if}
				</Card.Content>
			</Card.Root>
		{:else}
			<Card.Root>
				<Card.Header class="pb-3">
					<div class="flex items-center justify-between">
						<Card.Title>Progress</Card.Title>
						<Button variant="ghost" size="sm" onclick={refreshStatus} disabled={statusPolling}>
							{#if statusPolling}<LoaderCircle class="h-3.5 w-3.5 animate-spin" />{:else}Refresh{/if}
						</Button>
					</div>
				</Card.Header>
				<Card.Content class="space-y-3">
					{#if status}
						<div class="flex flex-wrap gap-2">
							{#each Object.entries(status.by_status) as [s, count] (s)}
								<Badge variant="outline" class="text-[10px] {statusColors[s] ?? ''}">{s}: {count}</Badge>
							{/each}
							<span class="text-xs text-muted-foreground">
								{status.jobs_created} / {status.total_cells} cells · running cost ${status.running_cost.toFixed(4)}
							</span>
						</div>
						<div class="grid gap-1.5 sm:grid-cols-2">
							{#each status.grid as cell (cell.condition_id + ':' + cell.app_id)}
								<div class="rounded-md border px-2.5 py-1.5 text-xs">
									<div class="flex flex-wrap gap-1">
										{#each cell.runs as run (run.job_id)}
											<a
												href={`/sample-generator?job=${run.job_id}`}
												class="rounded border px-1.5 py-0.5 {statusColors[run.status] ?? ''}"
												title={`repeat ${run.repeat_index}`}
											>
												{run.repeat_index}
											</a>
										{/each}
									</div>
								</div>
							{/each}
						</div>
					{/if}
				</Card.Content>
			</Card.Root>
		{/if}
	{/if}
</div>
