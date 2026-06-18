<script lang="ts">
	import { goto } from '$app/navigation';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Badge } from '$lib/components/ui/badge';
	import { onMount } from 'svelte';
	import Brain from '@lucide/svelte/icons/brain';
	import GitCompareArrows from '@lucide/svelte/icons/git-compare-arrows';
	import Wrench from '@lucide/svelte/icons/wrench';
	import TrendingUp from '@lucide/svelte/icons/trending-up';
	import Layers from '@lucide/svelte/icons/layers';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import {
		generateReport,
		getModels,
		getAppTemplates,
		type LLMModelSummary,
		type AppRequirementTemplate,
		type ReportType
	} from '$lib/api/client';

	const reportTypes: {
		key: ReportType;
		label: string;
		description: string;
		icon: typeof Brain;
	}[] = [
		{ key: 'model_analysis', label: 'Model Analysis', description: 'Aggregate generation, LOC, and findings for a single model.', icon: Brain },
		{ key: 'template_comparison', label: 'Template Comparison', description: 'Compare model performance on the same app template.', icon: GitCompareArrows },
		{ key: 'tool_analysis', label: 'Tool Analysis', description: 'Findings breakdown across analyzers / security tools.', icon: Wrench },
		{ key: 'generation_analytics', label: 'Generation Analytics', description: 'Throughput and success rate over a time window.', icon: TrendingUp },
		{ key: 'comprehensive', label: 'Comprehensive', description: 'Platform-wide rollup of generation, analysis, and tools.', icon: Layers }
	];

	let step = $state<1 | 2>(1);
	let selectedType = $state<ReportType | null>(null);
	let title = $state('');
	let description = $state('');
	let modelId = $state('');
	let templateSlug = $state('');
	let toolName = $state('');
	let days = $state(30);
	let submitting = $state(false);
	let error = $state('');

	let models = $state<LLMModelSummary[]>([]);
	let templates = $state<AppRequirementTemplate[]>([]);

	onMount(async () => {
		try {
			const [m, t] = await Promise.all([
				getModels({ per_page: 100 }),
				getAppTemplates()
			]);
			models = m.items;
			templates = t;
		} catch {
			// non-fatal
		}
	});

	function pickType(t: ReportType) {
		selectedType = t;
		step = 2;
	}

	async function submit() {
		if (!selectedType) return;
		const config: Record<string, unknown> = {};
		if (selectedType === 'model_analysis') {
			if (!modelId) {
				error = 'Pick a model';
				return;
			}
			config.model_id = modelId;
		}
		if (selectedType === 'template_comparison') {
			if (!templateSlug) {
				error = 'Pick a template';
				return;
			}
			config.template_slug = templateSlug;
		}
		if (selectedType === 'tool_analysis' && toolName) {
			config.tool_name = toolName;
		}
		if (selectedType === 'generation_analytics' || selectedType === 'comprehensive') {
			config.days = Number(days) || 30;
		}
		submitting = true;
		error = '';
		try {
			const r = await generateReport({
				report_type: selectedType,
				title: title || undefined,
				description: description || undefined,
				config
			});
			goto(`/reports/${r.report_id}`);
		} catch (e) {
			const obj = e as { detail?: string; message?: string };
			error = obj?.message || obj?.detail || 'Failed to generate report';
			submitting = false;
		}
	}
</script>

<svelte:head>
	<title>New report — LLM Eval Lab</title>
</svelte:head>

<div class="max-w-3xl space-y-6">
	<div class="flex items-center gap-3">
		<Button variant="ghost" size="sm" onclick={() => (step === 1 ? goto('/reports') : (step = 1))}>
			<ArrowLeft class="mr-1 h-4 w-4" />Back
		</Button>
		<h1 class="text-2xl font-bold">New report</h1>
		<Badge variant="outline">Step {step} of 2</Badge>
	</div>

	{#if step === 1}
		<div class="grid gap-3 sm:grid-cols-2">
			{#each reportTypes as t (t.key)}
				{@const TypeIcon = t.icon}
				<button
					type="button"
					onclick={() => pickType(t.key)}
					class="text-left rounded-lg border p-4 hover:border-primary hover:bg-muted/40 transition"
				>
					<div class="flex items-center gap-2 mb-1">
						<TypeIcon class="h-4 w-4 text-primary" />
						<span class="font-medium">{t.label}</span>
					</div>
					<p class="text-xs text-muted-foreground">{t.description}</p>
				</button>
			{/each}
		</div>
	{:else if selectedType}
		<Card.Root>
			<Card.Header>
				<Card.Title>{reportTypes.find((r) => r.key === selectedType)?.label}</Card.Title>
				<Card.Description>Configure the report and generate.</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-4">
				<div class="space-y-2">
					<label for="rep-title" class="text-sm font-medium">Title (optional)</label>
					<Input id="rep-title" bind:value={title} placeholder="Auto-generated if blank" />
					<p class="text-xs text-muted-foreground">Auto-generated if blank</p>
				</div>
				<div class="space-y-2">
					<label for="rep-desc" class="text-sm font-medium">Description (optional)</label>
					<Input id="rep-desc" bind:value={description} />
					<p class="text-xs text-muted-foreground">Optional description</p>
				</div>

				{#if selectedType === 'model_analysis'}
					<div class="space-y-2">
						<label for="rep-model" class="text-sm font-medium">Model</label>
						<select
							id="rep-model"
							bind:value={modelId}
							class="block w-full std-select py-2"
						>
							<option value="">— select a model —</option>
							{#each models as m (m.model_id)}
								<option value={m.model_id}>{m.model_name}</option>
							{/each}
						</select>
					</div>
				{:else if selectedType === 'template_comparison'}
					<div class="space-y-2">
						<label for="rep-tmpl" class="text-sm font-medium">Template</label>
						<select
							id="rep-tmpl"
							bind:value={templateSlug}
							class="block w-full std-select py-2"
						>
							<option value="">— select a template —</option>
							{#each templates as t (t.slug)}
								<option value={t.slug}>{t.name}</option>
							{/each}
						</select>
					</div>
				{:else if selectedType === 'tool_analysis'}
					<div class="space-y-2">
						<label for="rep-tool" class="text-sm font-medium">Tool name (optional)</label>
						<Input id="rep-tool" bind:value={toolName} placeholder="bandit, eslint, zap, … (blank = all)" />
						<p class="text-xs text-muted-foreground">Leave blank to aggregate all tools</p>
					</div>
				{:else if selectedType === 'generation_analytics' || selectedType === 'comprehensive'}
					<div class="space-y-2">
						<label for="rep-days" class="text-sm font-medium">Window (days)</label>
						<input
							id="rep-days"
							type="number"
							min="1"
							max="365"
							bind:value={days}
							class="block w-full std-select py-2"
						/>
						<p class="text-xs text-muted-foreground">1-365 days</p>
					</div>
				{/if}

				{#if error}
					<p class="text-sm text-red-400">{error}</p>
				{/if}
			</Card.Content>
			<Card.Footer class="gap-2 justify-end">
				<Button variant="outline" onclick={() => (step = 1)}>Back</Button>
				<Button onclick={submit} disabled={submitting}>
					{#if submitting}
						<LoaderCircle class="mr-2 h-4 w-4 animate-spin" />
					{/if}
					Generate report
				</Button>
			</Card.Footer>
		</Card.Root>
	{/if}
</div>
