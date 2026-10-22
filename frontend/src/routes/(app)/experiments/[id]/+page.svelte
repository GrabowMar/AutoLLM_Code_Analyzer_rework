<script lang="ts">
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { toneClasses, statusTone } from '$lib/constants/colors';
	import { Button } from '$lib/components/ui/button';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { Badge } from '$lib/components/ui/badge';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import ModelSelector from '$lib/components/sample-generator/ModelSelector.svelte';
	import ProfilePicker from '$lib/components/sample-generator/ProfilePicker.svelte';
	import LLMParamsEditor from '$lib/components/sample-generator/LLMParamsEditor.svelte';
	import { getModels } from '$lib/api/models';
	import {
		getAgentHarnesses,
		getGenerationProfiles,
		type AgentHarness,
		type GenerationProfile,
		type LLMParams,
	} from '$lib/api/generation';
	import { Label } from '$lib/components/ui/label';
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
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import Rocket from '@lucide/svelte/icons/rocket';
	import Calculator from '@lucide/svelte/icons/calculator';
	import Download from '@lucide/svelte/icons/download';
	import Archive from '@lucide/svelte/icons/archive';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import GitCompareArrows from '@lucide/svelte/icons/git-compare-arrows';

	const experimentId = $derived(page.params.id!);

	let experiment = $state<Experiment | null>(null);
	let conditions = $state<ExperimentCondition[]>([]);
	let loading = $state(true);
	let error = $state('');

	let models = $state<LLMModelSummary[]>([]);
	let profiles = $state<GenerationProfile[]>([]);
	let newConditionModelId = $state<number | ''>('');
	let newConditionProfileId = $state<number | ''>('');
	let newConditionOverrides = $state<LLMParams>({});
	let newConditionHarness = $state('');
	let harnesses = $state<AgentHarness[]>([]);
	const selectedHarness = $derived(harnesses.find((h) => h.name === newConditionHarness));
	// A tool-calling harness can only drive tool-calling models.
	const conditionModels = $derived(
		selectedHarness && !selectedHarness.requires_tool_calling ? models : models.filter((m) => m.supports_function_calling),
	);
	let showConditionOverrides = $state(false);
	let addingCondition = $state(false);

	let previewing = $state(false);
	let previewResult = $state<ExperimentPreview | null>(null);
	let launching = $state(false);
	let launchError = $state('');

	let status = $state<ExperimentStatusReport | null>(null);
	let statusPolling = $state(false);

	onMount(load);

	async function load() {
		loading = true;
		error = '';
		try {
			const [exp, conds, modelsRes, profilesRes, harnessList] = await Promise.all([
				getExperiment(experimentId),
				listConditions(experimentId),
				getModels({ per_page: 100 }),
				getGenerationProfiles(),
				getAgentHarnesses().catch(() => [] as AgentHarness[]),
			]);
			harnesses = harnessList;
			experiment = exp;
			conditions = conds;
			models = modelsRes.items;
			profiles = profilesRes;
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
		if (!newConditionModelId || !newConditionProfileId) return;
		addingCondition = true;
		error = '';
		try {
			const cond = await createCondition(experimentId, {
				model_id: newConditionModelId as number,
				profile_id: newConditionProfileId as number,
				harness: experiment?.mode === 'agent' ? newConditionHarness : '',
				param_overrides: newConditionOverrides,
			});
			conditions = [...conditions, cond];
			newConditionModelId = '';
			newConditionProfileId = '';
			newConditionOverrides = {};
			newConditionHarness = '';
			showConditionOverrides = false;
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

<div class="mx-auto max-w-4xl space-y-6">
	<PageHeader
		title={experiment?.name ?? 'Experiment'}
		description={experiment?.description || undefined}
		back={{ href: '/experiments', label: 'Experiments' }}
	>
		{#snippet meta()}
			{#if experiment}
				<StatusBadge status={experiment.status} />
				<Badge variant="outline">{experiment.mode === 'agent' ? 'Agent (OpenCode)' : 'Scaffolding'}</Badge>
			{/if}
		{/snippet}
		{#snippet actions()}
			{#if experiment}
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
			{/if}
		{/snippet}
		{#if experiment}
			{#if experiment.hypothesis}
				<p class="text-sm italic text-muted-foreground">"{experiment.hypothesis}"</p>
			{/if}
			<div class="flex flex-wrap gap-x-6 gap-y-1 pt-1 text-xs text-muted-foreground">
				<span>{experiment.app_requirement_ids.length} apps</span>
				<span>{experiment.repeats}× repeats per cell</span>
				{#if Object.keys(experiment.llm_defaults ?? {}).length > 0}
					<span class="font-mono">{Object.entries(experiment.llm_defaults).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(' ')}</span>
				{:else}
					<span>default sampling</span>
				{/if}
				{#if experiment.base_seed !== null}
					<span>base seed {experiment.base_seed}</span>
				{:else}
					<span>random seed per run</span>
				{/if}
			</div>
		{/if}
	</PageHeader>

	{#if loading}
		<LoadingState label="loading experiment" />
	{:else if error && !experiment}
		<ErrorAlert message={error} onretry={load} />
	{:else if experiment}
		{#if error}
			<ErrorAlert message={error} />
		{/if}

		<Card.Root>
			<Card.Header>
				<Card.Title>Conditions</Card.Title>
				<Card.Description>
					{#if experiment.mode === 'agent'}
						Each condition is a model × harness pair (the profile supplies only the stack and LLM config); every app runs under every condition with the same brief, budget and seed per cell.
					{:else}
						Each condition is a model × profile pair; every app runs under every condition.
					{/if}
				</Card.Description>
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
									{#if experiment.mode === 'agent'}
										<span class="text-muted-foreground"> × </span>
										<Badge variant="outline" class="font-mono">{cond.harness || 'default harness'}</Badge>
									{/if}
								</span>
								{#if experiment.status === 'draft'}
									<Button
										variant="ghost"
										size="icon"
										class="h-7 w-7 text-muted-foreground hover:text-destructive"
										onclick={() => removeCondition(cond.id)}
										aria-label="Remove condition {cond.model_name ?? `model #${cond.model}`} × {cond.bundle_slug}"
									>
										<Trash2 class="h-3.5 w-3.5" />
									</Button>
								{/if}
							</div>
						{/each}
					</div>
				{:else}
					<EmptyState
						compact
						icon={GitCompareArrows}
						title="No conditions yet"
						description={experiment.status === 'draft' ? 'Add at least one model × profile pair below.' : undefined}
					/>
				{/if}

				{#if experiment.status === 'draft'}
					<div class="space-y-3 rounded-md border bg-muted/30 p-3">
						<p class="section-label">Add condition</p>
						{#if experiment.mode === 'agent'}
							<div class="space-y-1">
								<Label class="text-xs" for="cond-harness">Harness</Label>
								<select id="cond-harness" bind:value={newConditionHarness} class="std-select h-9 w-full px-2">
									<option value="">Experiment default</option>
									{#each harnesses as h (h.name)}
										<option value={h.name}>{h.label} {h.version}{h.requires_tool_calling ? '' : ' (no tool calling needed)'}</option>
									{/each}
								</select>
							</div>
						{/if}
						<div class="grid gap-3 sm:grid-cols-[1fr_1fr_auto]">
							<div class="space-y-1">
								<span class="text-xs font-medium">Model</span>
								<ModelSelector
									models={experiment.mode === 'agent' ? conditionModels : models}
									mode="single"
									bind:selectedId={newConditionModelId}
									maxHeight="max-h-48"
								/>
							</div>
							<div class="space-y-1">
								<span class="text-xs font-medium">Profile</span>
								<ProfilePicker {profiles} bind:selectedId={newConditionProfileId} />
							</div>
							<div class="flex items-end">
								<Button
									size="sm"
									disabled={!newConditionModelId || !newConditionProfileId || addingCondition}
									onclick={addCondition}
								>
									<Plus class="h-3.5 w-3.5" />
									Add
								</Button>
							</div>
						</div>
						<button
							type="button"
							class="text-xs text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
							onclick={() => (showConditionOverrides = !showConditionOverrides)}
							aria-expanded={showConditionOverrides}
						>
							{showConditionOverrides ? 'Hide' : 'Show'} per-condition param overrides
							{#if Object.keys(newConditionOverrides).length > 0}
								<span class="text-primary">({Object.keys(newConditionOverrides).length} set)</span>
							{/if}
						</button>
						{#if showConditionOverrides}
							<LLMParamsEditor
								bind:params={newConditionOverrides}
								inherited={experiment.llm_defaults ?? {}}
								idPrefix="cond-overrides"
							/>
						{/if}
					</div>
				{/if}
			</Card.Content>
		</Card.Root>

		{#if experiment.status === 'draft'}
			<Card.Root>
				<Card.Header>
					<Card.Title>Preview &amp; launch</Card.Title>
					<Card.Description>Preview the job count and estimated cost before launching the full matrix.</Card.Description>
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
						<div class="rounded-md border bg-muted/30 p-3 text-sm tabular-nums">
							<div class="flex flex-wrap gap-x-6 gap-y-1">
								<span><span class="font-medium">{previewResult.total_jobs}</span> jobs</span>
								<span><span class="font-medium">{previewResult.conditions}</span> conditions</span>
								<span><span class="font-medium">{previewResult.apps}</span> apps</span>
								<span>
									{previewResult.cost_estimate_kind === 'budget_upper_bound' ? 'cost cap' : 'est. cost'}
									<span class="font-medium">${previewResult.estimated_cost.toFixed(4)}</span>
								</span>
							</div>
						</div>
						{#if previewResult.comparability}
							{@const comp = previewResult.comparability}
							<div class="space-y-2">
								<p class="section-label">Comparability</p>
								{#if comp.has_problems}
									<div class="rounded-md border px-3 py-2 text-xs {toneClasses.warning}" role="status">
										Some conditions will not run exactly as configured. Fix these before launching, or report them with the results.
									</div>
								{/if}
								{#if comp.differs_on.length}
									<p class="text-xs text-muted-foreground">Conditions differ on: <span class="font-mono">{comp.differs_on.join(', ')}</span></p>
								{/if}
								<div class="overflow-x-auto rounded-md border">
									<table class="w-full text-xs">
										<thead class="bg-muted/40 text-left text-muted-foreground">
											<tr>
												<th class="px-2 py-1.5 font-medium">Model</th>
												<th class="px-2 py-1.5 font-medium">Harness</th>
												<th class="px-2 py-1.5 font-medium">Sampling</th>
												<th class="px-2 py-1.5 font-medium">Forced params</th>
												<th class="px-2 py-1.5 font-medium">Seed</th>
												<th class="px-2 py-1.5 font-medium">Problems</th>
											</tr>
										</thead>
										<tbody class="divide-y">
											{#each comp.conditions as row (row.condition_id)}
												<tr>
													<td class="px-2 py-1.5 font-mono">{row.model}</td>
													<td class="px-2 py-1.5 font-mono">{row.harness}@{row.harness_version}</td>
													<td class="px-2 py-1.5">{row.sampling_policy.replace(/_/g, ' ')}</td>
													<td class="px-2 py-1.5 font-mono">{JSON.stringify(row.forced_params)}</td>
													<td class="px-2 py-1.5">{row.seed_forwarded ? 'forwarded' : 'not supported'}</td>
													<td class="px-2 py-1.5 {row.problems.length ? 'text-warning-fg' : 'text-muted-foreground'}">
														{row.problems.length ? row.problems.join('; ') : '—'}
													</td>
												</tr>
											{/each}
										</tbody>
									</table>
								</div>
							</div>
						{/if}
					{/if}

					{#if launchError}
						<ErrorAlert title="Launch failed" message={launchError} />
					{/if}
				</Card.Content>
			</Card.Root>
		{:else}
			<Card.Root>
				<Card.Header class="flex flex-row items-start justify-between gap-2 space-y-0">
					<div class="space-y-1">
						<Card.Title>Progress</Card.Title>
						<Card.Description>Each square is one run; open it in the sample generator.</Card.Description>
					</div>
					<Button variant="outline" size="sm" onclick={refreshStatus} disabled={statusPolling}>
						{#if statusPolling}<LoaderCircle class="h-3.5 w-3.5 animate-spin" />{:else}<RefreshCw class="h-3.5 w-3.5" />{/if}
						Refresh
					</Button>
				</Card.Header>
				<Card.Content class="space-y-3">
					{#if !status}
						<EmptyState compact title="No progress yet" description="Refresh to fetch the latest run status." />
					{:else}
						<div class="flex flex-wrap gap-2">
							{#each Object.entries(status.by_status) as [s, count] (s)}
								<StatusBadge status={s} label="{s.replace(/_/g, ' ')}: {count}" />
							{/each}
							<span class="text-xs text-muted-foreground tabular-nums">
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
												class="rounded border px-1.5 py-0.5 tabular-nums {toneClasses[statusTone(run.status)]}"
												title={`repeat ${run.repeat_index} · ${run.status}`}
												aria-label={`Repeat ${run.repeat_index}, ${run.status}`}
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
