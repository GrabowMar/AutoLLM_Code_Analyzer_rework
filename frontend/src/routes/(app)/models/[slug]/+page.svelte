<script lang="ts">
	import { page } from '$app/stores';
	import { replaceState } from '$app/navigation';
	import {
		getModel,
		getModelInsights,
		getRelatedModels,
		refreshModelFromOpenRouter,
		type LLMModelDetail,
		type LLMModelSummary,
		type ModelInsights,
	} from '$lib/api/client';
	import { formatApiError } from '$lib/api/core';
	import { getMe } from '$lib/api/users';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import * as Tabs from '$lib/components/ui/tabs';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import Info from '@lucide/svelte/icons/info';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import Trophy from '@lucide/svelte/icons/trophy';
	import Zap from '@lucide/svelte/icons/zap';
	import DollarSign from '@lucide/svelte/icons/dollar-sign';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import Braces from '@lucide/svelte/icons/braces';
	import ModelHeader from '$lib/components/models/ModelHeader.svelte';
	import ModelOverviewTab from '$lib/components/models/ModelOverviewTab.svelte';
	import ModelResultsTab from '$lib/components/models/ModelResultsTab.svelte';
	import ModelBenchmarksTab from '$lib/components/models/ModelBenchmarksTab.svelte';
	import ModelCapabilitiesTab from '$lib/components/models/ModelCapabilitiesTab.svelte';
	import ModelPricingTab from '$lib/components/models/ModelPricingTab.svelte';
	import ModelDataTab from '$lib/components/models/ModelDataTab.svelte';
	import ModelApplicationsSection from '$lib/components/models/ModelApplicationsSection.svelte';
	import ModelUsageSection from '$lib/components/models/ModelUsageSection.svelte';
	import { costEfficiencyGrade } from '$lib/components/models/helpers';

	const slug = $derived($page.params.slug ?? '');

	const sections = [
		{ id: 'overview', label: 'Overview', icon: Info },
		{ id: 'results', label: 'Results', icon: FlaskConical },
		{ id: 'benchmarks', label: 'Benchmarks', icon: Trophy },
		{ id: 'capabilities', label: 'Capabilities', icon: Zap },
		{ id: 'pricing', label: 'Pricing & providers', icon: DollarSign },
		{ id: 'applications', label: 'Applications', icon: AppWindow },
		{ id: 'data', label: 'Raw data', icon: Braces },
	] as const;
	type SectionId = (typeof sections)[number]['id'];

	function initialSection(): SectionId {
		const t = $page.url.searchParams.get('tab');
		return sections.some((s) => s.id === t) ? (t as SectionId) : 'overview';
	}

	let activeSection = $state<SectionId>(initialSection());
	let model = $state<LLMModelDetail | null>(null);
	let loading = $state(true);
	let error = $state('');
	let refreshing = $state(false);
	// Refreshing model metadata is staff-only on the API; hide the control for everyone else.
	let isStaff = $state(false);
	onMount(async () => {
		try {
			isStaff = (await getMe()).is_staff ?? false;
		} catch {
			isStaff = false;
		}
	});
	let relatedModels = $state<LLMModelSummary[]>([]);
	let insights = $state<ModelInsights | null>(null);
	let insightsError = $state('');

	const efficiency = $derived(model ? costEfficiencyGrade(model.cost_efficiency) : { grade: 'D', color: 'text-danger-fg' });
	const applicationCount = $derived(insights?.generation.jobs_total);

	// Keep ?tab= in the URL so a section can be linked and survives reload.
	function syncTabParam(tab: string) {
		const url = new URL(window.location.href);
		if (tab === 'overview') url.searchParams.delete('tab');
		else url.searchParams.set('tab', tab);
		replaceState(url, {});
	}

	function openSection(tab: SectionId) {
		activeSection = tab;
		syncTabParam(tab);
	}

	async function loadInsights() {
		insightsError = '';
		try {
			insights = await getModelInsights(slug);
		} catch (e) {
			insightsError = formatApiError(e, 'Could not load measurements.');
		}
	}

	async function load() {
		loading = true;
		error = '';
		relatedModels = [];
		insights = null;
		try {
			model = await getModel(slug);
			getRelatedModels(slug, 12).then((r) => (relatedModels = r)).catch(() => {});
			loadInsights();
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
			loadInsights();
			toast.success('Model metadata refreshed from OpenRouter, models.dev and Hugging Face.');
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

{#snippet sectionTabs()}
	<Tabs.List aria-label="Model sections">
		{#each sections as sec (sec.id)}
			{@const SecIcon = sec.icon}
			<Tabs.Trigger value={sec.id}><SecIcon />{sec.label}</Tabs.Trigger>
		{/each}
	</Tabs.List>
{/snippet}

{#if loading}
	<div class="space-y-4">
		<PageHeader title={slug} back={{ href: '/models', label: 'Models' }} />
		<LoadingState label="loading model" />
	</div>
{:else if error || !model}
	<div class="space-y-4">
		<PageHeader title={slug} back={{ href: '/models', label: 'Models' }} />
		<ErrorAlert message={error || 'Model not found.'} onretry={load} />
	</div>
{:else}
	<Tabs.Root bind:value={activeSection} onValueChange={syncTabParam} class="min-w-0 space-y-4">
		<ModelHeader {model} {slug} {efficiency} {refreshing} {applicationCount} onRefresh={isStaff ? handleRefresh : undefined} nav={sectionTabs} />

		<Tabs.Content value="overview">
			<ModelOverviewTab
				{model}
				{slug}
				{insights}
				{insightsError}
				onRetryInsights={loadInsights}
				{relatedModels}
				onOpenTab={(t) => openSection(t as SectionId)}
			/>
		</Tabs.Content>
		<Tabs.Content value="results">
			<ModelResultsTab {insights} error={insightsError} onRetry={loadInsights} {slug} />
		</Tabs.Content>
		<Tabs.Content value="benchmarks">
			<ModelBenchmarksTab {model} {insights} />
		</Tabs.Content>
		<Tabs.Content value="capabilities">
			<ModelCapabilitiesTab {model} />
		</Tabs.Content>
		<Tabs.Content value="pricing">
			<ModelPricingTab {model} {slug} {insights} />
		</Tabs.Content>
		<Tabs.Content value="applications" class="space-y-4">
			<ModelApplicationsSection modelId={model.model_id} {slug} />
			<ModelUsageSection modelId={model.model_id} />
		</Tabs.Content>
		<Tabs.Content value="data">
			<ModelDataTab {model} />
		</Tabs.Content>
	</Tabs.Root>
{/if}
