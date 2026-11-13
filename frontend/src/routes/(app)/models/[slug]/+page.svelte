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
	import Activity from '@lucide/svelte/icons/activity';
	import '$lib/components/models/detail.css';
	import ModelHeader from '$lib/components/models/ModelHeader.svelte';
	import ModelOverviewTab from '$lib/components/models/ModelOverviewTab.svelte';
	import ModelResultsTab from '$lib/components/models/ModelResultsTab.svelte';
	import ModelBenchmarksTab from '$lib/components/models/ModelBenchmarksTab.svelte';
	import ModelCapabilitiesTab from '$lib/components/models/ModelCapabilitiesTab.svelte';
	import ModelPricingTab from '$lib/components/models/ModelPricingTab.svelte';
	import ModelDataTab from '$lib/components/models/ModelDataTab.svelte';
	import ModelApplicationsSection from '$lib/components/models/ModelApplicationsSection.svelte';
	import ModelGenerationDataTab from '$lib/components/models/ModelGenerationDataTab.svelte';

	const slug = $derived($page.params.slug ?? '');

	const sections = [
		{ id: 'overview', label: 'Overview', icon: Info },
		{ id: 'results', label: 'Results', icon: FlaskConical },
		{ id: 'generation', label: 'Behaviour', icon: Activity },
		{ id: 'benchmarks', label: 'Benchmarks', icon: Trophy },
		{ id: 'capabilities', label: 'Specs', icon: Zap },
		{ id: 'pricing', label: 'Pricing', icon: DollarSign },
		{ id: 'applications', label: 'Apps', icon: AppWindow },
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

	const applicationCount = $derived(insights?.generation.runs_total);
	/** Small count on a tab, when the section has one. */
	const tabCount = $derived<Partial<Record<SectionId, number>>>({
		results: applicationCount,
		applications: applicationCount,
	});

	// Keep ?tab= in the URL so a section can be linked and survives reload.
	/** After a tab switch from far down the page, bring the new section's top up under the sticky tab bar. */
	function scrollToContent() {
		const header = document.querySelector<HTMLElement>('header.mh');
		if (!header) return;
		const target = header.getBoundingClientRect().bottom + window.scrollY - 48;
		if (window.scrollY > target) window.scrollTo({ top: target, behavior: 'instant' });
	}

	function syncTabParam(tab: string) {
		scrollToContent();
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
			<Tabs.Trigger value={sec.id}>
				<SecIcon />{sec.label}
				{#if tabCount[sec.id]}<span class="tab-count">{tabCount[sec.id]?.toLocaleString()}</span>{/if}
			</Tabs.Trigger>
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
		<ModelHeader
			{model}
			{slug}
			{refreshing}
			{insights}
			{insightsError}
			onRefresh={isStaff ? handleRefresh : undefined}
			onOpenTab={(t) => openSection(t as SectionId)}
			nav={sectionTabs}
		/>

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
		<Tabs.Content value="generation">
			<ModelGenerationDataTab
				modelId={model.id}
				twinModelId={model.is_local ? (model.twin?.model?.model_id ?? null) : null}
				twinSlug={model.is_local ? (model.twin?.model?.canonical_slug ?? null) : null}
				runsTotal={insights ? insights.generation.runs_total : null}
				{slug}
			/>
		</Tabs.Content>
		<Tabs.Content value="benchmarks">
			<ModelBenchmarksTab {model} {insights} />
		</Tabs.Content>
		<Tabs.Content value="capabilities">
			<ModelCapabilitiesTab {model} {slug} />
		</Tabs.Content>
		<Tabs.Content value="pricing">
			<ModelPricingTab {model} {slug} {insights} />
		</Tabs.Content>
		<Tabs.Content value="applications" class="space-y-4">
			<ModelApplicationsSection modelId={model.model_id} {slug} />
		</Tabs.Content>
		<Tabs.Content value="data">
			<ModelDataTab {model} />
		</Tabs.Content>
	</Tabs.Root>
{/if}

<style>
	.tab-count {
		border-radius: 9999px;
		background: var(--muted);
		padding: 0 0.375rem;
		font-family: var(--font-mono);
		font-size: 0.625rem;
		line-height: 1rem;
		color: var(--muted-foreground);
		font-variant-numeric: tabular-nums;
	}
</style>
