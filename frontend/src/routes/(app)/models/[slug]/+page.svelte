<script lang="ts">
	import { page } from '$app/stores';
	import {
		getModel,
		getRelatedModels,
		refreshModelFromOpenRouter,
		type LLMModelDetail,
		type LLMModelSummary,
	} from '$lib/api/client';
		import { formatApiError } from '$lib/api/core';
	import { toast } from 'svelte-sonner';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import ModelHeader from '$lib/components/models/ModelHeader.svelte';
	import ModelStatsTabs from '$lib/components/models/ModelStatsTabs.svelte';
	import ModelComparisonChart from '$lib/components/models/ModelComparisonChart.svelte';
	import {
		costEfficiencyGrade,
		getArchitecture,
		getCapabilityMatrix,
		getDefaultParameters,
		getHuggingFaceId,
		getPerRequestLimits,
		getSupportedParameters,
	} from '$lib/components/models/helpers';

	const slug = $derived($page.params.slug ?? '');

	let model = $state<LLMModelDetail | null>(null);
	let loading = $state(true);
	let error = $state('');
	let refreshing = $state(false);
	let relatedModels = $state<LLMModelSummary[]>([]);
	let applicationCount = $state<number | undefined>(undefined);

	const meta = $derived(model ? (model.metadata || {}) as Record<string, unknown> : {} as Record<string, unknown>);
	const caps = $derived(model ? (model.capabilities_json || {}) as Record<string, unknown> : {} as Record<string, unknown>);
	const arch = $derived(model ? getArchitecture(model) : {} as Record<string, unknown>);
	const efficiency = $derived(model ? costEfficiencyGrade(model.cost_efficiency) : { grade: 'D', color: 'text-red-500' });
	const inputMods = $derived((arch.input_modalities || meta.architecture_input_modalities || []) as string[]);
	const outputMods = $derived((arch.output_modalities || meta.architecture_output_modalities || []) as string[]);
	const supportedParams = $derived(model ? getSupportedParameters(model) : [] as string[]);
	const perRequestLimits = $derived(model ? getPerRequestLimits(model) : {} as Record<string, number>);
	const capMatrix = $derived(model ? getCapabilityMatrix(model) : {} as Record<string, boolean>);
	const instructType = $derived((arch.instruct_type || meta.architecture_instruct_type || '') as string);
	const defaultParams = $derived(model ? getDefaultParameters(model) : {} as Record<string, unknown>);
	const hfId = $derived(model ? getHuggingFaceId(model) : null);

	async function load() {
		loading = true;
		error = '';
		relatedModels = [];
		try {
			model = await getModel(slug);
			getRelatedModels(slug, 12).then(r => { relatedModels = r; }).catch(() => {});
		} catch {
			error = 'Model not found.';
		} finally {
			loading = false;
		}
	}

	async function handleRefresh() {
		refreshing = true;
		try {
			model = await refreshModelFromOpenRouter(slug);
			getRelatedModels(slug, 12).then((result) => {
				relatedModels = result;
			}).catch(() => {});
			toast.success('Model metadata refreshed from OpenRouter.');
		} catch (caughtError) {
			toast.error(formatApiError(caughtError, 'Model refresh failed.'));
		} finally {
			refreshing = false;
		}
	}

	$effect(() => {
		if (slug) load();
	});
</script>

<svelte:head>
	<title>{model?.model_name ?? slug} - Models - LLM Lab</title>
</svelte:head>

<div class="space-y-5 min-w-0">
	<!-- Breadcrumb -->
	<nav aria-label="Breadcrumb" class="flex items-center gap-2 text-sm text-muted-foreground">
		<a href="/models" class="hover:text-foreground transition-colors flex items-center gap-1">
			<ArrowLeft class="h-3.5 w-3.5" />
			<span class="font-medium text-foreground">Models</span>
		</a>
		<span>/</span>
		<span class="text-muted-foreground truncate max-w-[300px]">{model?.model_name ?? slug}</span>
	</nav>

	{#if loading}
		<div class="flex items-center justify-center py-20">
			<LoaderCircle class="h-8 w-8 animate-spin text-muted-foreground" />
		</div>
	{:else if error}
		<div class="rounded-lg border border-destructive bg-destructive/10 p-4 text-sm text-destructive">
			{error}
		</div>
	{:else if model}
		<ModelHeader
			{model}
			{slug}
			{efficiency}
			{refreshing}
			applicationCount={applicationCount}
			onRefresh={handleRefresh}
		/>

		<ModelStatsTabs
			{model}
			{meta}
			{caps}
			{arch}
			{efficiency}
			{inputMods}
			{outputMods}
			{supportedParams}
			{perRequestLimits}
			{capMatrix}
			{instructType}
			{defaultParams}
			{hfId}
			{slug}
			onApplicationsLoaded={(n) => (applicationCount = n)}
		/>

		<ModelComparisonChart
			provider={model.provider}
			{relatedModels}
		/>
	{/if}
</div>
