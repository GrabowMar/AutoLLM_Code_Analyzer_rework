<script lang="ts">
	/**
	 * New report: pick a type, configure it, generate. v3 configs: the task comparison
	 * (`template_comparison`) takes `task_slug` (+ optional `task_version`), the study conditions
	 * report (`experiment_report`) a `study_id`, the study report an optional `study_id` (else every
	 * app of yours). The task comparison, study conditions and study report offer the
	 * exclude-truncated sensitivity; `exclude_truncated` is sent only when ticked, so an untouched
	 * report stays intention-to-treat (or follows a locked analysis plan) without an override note.
	 *
	 * Deep link: `?type=<report type>&study_id=<uuid>` (from a study's Results section).
	 */
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
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import Microscope from '@lucide/svelte/icons/microscope';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { getModels, type LLMModelSummary } from '$lib/api/models';
	import {
		REPORT_TYPE_LABELS,
		excludeTruncatedConfig,
		generateReport,
		type ReportType,
		type StudyConditionsConfig,
		type StudyReportConfig,
		type TaskComparisonConfig
	} from '$lib/api/reports';
	import { formatApiError } from '$lib/api/core';
	import { listAllLibrary } from '$lib/api/v3/library';
	import { listStudies } from '$lib/api/v3/studies';
	import type { LibraryItemOut, StudyListOut } from '$lib/api/v3/types';
	import { page } from '$app/state';
	import * as Select from '$lib/components/ui/select';

	const reportTypes: {
		key: ReportType;
		label: string;
		description: string;
		icon: typeof Brain;
	}[] = [
		{ key: 'model_analysis', label: REPORT_TYPE_LABELS.model_analysis, description: 'Aggregate generation, LOC, and findings for a single model.', icon: Brain },
		{ key: 'template_comparison', label: REPORT_TYPE_LABELS.template_comparison, description: 'Compare models on the same library task (one row per model and treatment).', icon: GitCompareArrows },
		{ key: 'tool_analysis', label: REPORT_TYPE_LABELS.tool_analysis, description: 'Findings breakdown across analyzers / security tools.', icon: Wrench },
		{ key: 'generation_analytics', label: REPORT_TYPE_LABELS.generation_analytics, description: 'Throughput and success rate over a time window.', icon: TrendingUp },
		{ key: 'comprehensive', label: REPORT_TYPE_LABELS.comprehensive, description: 'Platform-wide rollup of generation, analysis, and tools.', icon: Layers },
		{ key: 'experiment_report', label: REPORT_TYPE_LABELS.experiment_report, description: "Leaderboard and paired comparisons against the pre-registered baseline condition of one study (current runs of the main stage).", icon: FlaskConical },
		{ key: 'study_report', label: REPORT_TYPE_LABELS.study_report, description: 'Paper-ready tables: outcome funnel, deduplicated issues, correlations and rankings with uncertainty, validity threats. Exports to Markdown and LaTeX.', icon: Microscope }
	];

	let step = $state<1 | 2>(1);
	let selectedType = $state<ReportType | null>(null);
	let title = $state('');
	let description = $state('');
	let modelId = $state('');
	let taskSlug = $state('');
	/** '' = every version of the task. */
	let taskVersion = $state('');
	let toolName = $state('');
	let days = $state(30);
	let toolDays = $state<number | null>(null);
	let studyId = $state('');
	let includeAi = $state(false);
	let bootstrap = $state(true);
	let excludeTruncated = $state(false);
	let seed = $state(0);
	let submitting = $state(false);
	let error = $state('');

	let models = $state<LLMModelSummary[]>([]);
	let tasks = $state<LibraryItemOut<'task'>[]>([]);
	let studies = $state<StudyListOut[]>([]);

	// Deep link from a study's Results section: ?type=experiment_report&study_id=…
	{
		const type = page.url.searchParams.get('type');
		if (type && reportTypes.some((t) => t.key === type)) {
			selectedType = type as ReportType;
			step = 2;
		}
		studyId = page.url.searchParams.get('study_id') ?? '';
	}

	onMount(async () => {
		// Each list is optional for the others: one failing does not empty the rest.
		const [m, t, st] = await Promise.allSettled([
			getModels({ per_page: 100 }),
			listAllLibrary('task'),
			listStudies({ page_size: 100 })
		]);
		if (m.status === 'fulfilled') models = m.value.items;
		if (t.status === 'fulfilled') tasks = t.value.filter((x) => !x.is_archived);
		if (st.status === 'fulfilled') studies = st.value.items.filter((x) => x.status !== 'draft');
	});

	const studyItems = $derived(
		studies.map((x) => ({
			value: x.id,
			label: x.name,
			description: `${x.status} · ${x.mode} mode · ${x.kind} · ${x.cells_done}/${x.cells_total} cells`
		}))
	);
	const selectedTask = $derived(tasks.find((t) => t.slug === taskSlug) ?? null);
	/** Versions 1..latest of the picked task (the list holds the latest version per slug). */
	const versionItems = $derived(
		selectedTask
			? Array.from({ length: selectedTask.version }, (_, i) => selectedTask.version - i).map((v) => ({
					value: String(v),
					label: `v${v}${v === selectedTask.version ? ' (latest)' : ''}`
				}))
			: []
	);

	function pickType(t: ReportType) {
		selectedType = t;
		step = 2;
	}

	async function submit() {
		if (!selectedType) return;
		let config: Record<string, unknown> = {};
		if (selectedType === 'model_analysis') {
			if (!modelId) {
				error = 'Pick a model';
				return;
			}
			config.model_id = modelId;
		}
		if (selectedType === 'template_comparison') {
			if (!taskSlug) {
				error = 'Pick a task';
				return;
			}
			const c: TaskComparisonConfig = { task_slug: taskSlug };
			if (taskVersion) c.task_version = Number(taskVersion);
			config = { ...c, ...excludeTruncatedConfig(excludeTruncated) };
		}
		if (selectedType === 'tool_analysis') {
			if (toolName) config.tool_name = toolName;
			// Blank window: all time.
			if (toolDays) config.days = Number(toolDays);
		}
		if (selectedType === 'generation_analytics' || selectedType === 'comprehensive') {
			config.days = Number(days) || 30;
		}
		if (selectedType === 'experiment_report') {
			if (!studyId) {
				error = 'Pick a study';
				return;
			}
			const c: StudyConditionsConfig = { study_id: studyId, ...excludeTruncatedConfig(excludeTruncated) };
			config = { ...c };
		}
		if (selectedType === 'study_report') {
			// No study: every app you own.
			// Unset unless ticked: an explicit value differing from a locked plan adds a validity note.
			const c: StudyReportConfig = excludeTruncatedConfig(excludeTruncated);
			if (studyId) c.study_id = studyId;
			config = { ...c, include_ai: includeAi, bootstrap, seed: Number(seed) || 0 };
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
			error = formatApiError(e, 'Failed to generate report');
			submitting = false;
		}
	}
</script>

{#snippet excludeTruncatedOption(planNote: string)}
	<label class="flex items-start gap-2 text-sm">
		<input type="checkbox" class="mt-1" bind:checked={excludeTruncated} />
		<span>
			Exclude truncated runs (sensitivity analysis)
			<span class="block text-xs text-muted-foreground">
				Off: intention-to-treat, truncated runs (budget, wall clock, idle, max turns) stay in the sample.
				{planNote}
			</span>
		</span>
	</label>
{/snippet}

<div class="max-w-3xl space-y-4">
	<PageHeader
		title="New report"
		description="Pick a report type, then configure and generate it."
		back={{ href: '/reports', label: 'Reports' }}
	>
		{#snippet meta()}<Badge variant="outline">Step {step} of 2</Badge>{/snippet}
	</PageHeader>

	{#if step === 1}
		<Card.Root>
			<Card.Header>
				<Card.Title>1. Report type</Card.Title>
			</Card.Header>
			<Card.Content>
		<div class="grid gap-3 sm:grid-cols-2">
			{#each reportTypes as t (t.key)}
				{@const TypeIcon = t.icon}
				<button
					type="button"
					onclick={() => pickType(t.key)}
					aria-pressed={selectedType === t.key}
					class="card-lift rounded-lg border p-4 text-left hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring {selectedType === t.key ? 'border-primary bg-primary/5' : ''}"
				>
					<div class="flex items-center gap-2 mb-1">
						<TypeIcon class="h-4 w-4 text-primary" />
						<span class="font-medium">{t.label}</span>
					</div>
					<p class="text-xs text-muted-foreground">{t.description}</p>
				</button>
			{/each}
		</div>
			</Card.Content>
		</Card.Root>
	{:else if selectedType}
		<Card.Root>
			<Card.Header>
				<Card.Title>2. Configure · {reportTypes.find((r) => r.key === selectedType)?.label}</Card.Title>
			</Card.Header>
			<Card.Content class="space-y-4">
				<div class="space-y-2">
					<label for="rep-title" class="text-sm font-medium">Title (optional)</label>
					<Input id="rep-title" bind:value={title} placeholder="Auto-generated if blank" />
				</div>
				<div class="space-y-2">
					<label for="rep-desc" class="text-sm font-medium">Description (optional)</label>
					<Input id="rep-desc" bind:value={description} />
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
						<label for="rep-task" class="text-sm font-medium">Task</label>
						<Select.Simple
							id="rep-task"
							value={taskSlug}
							placeholder="Select a library task"
							items={tasks.map((t) => ({ value: t.slug, label: t.name || t.slug, description: `${t.slug} · latest v${t.version}` }))}
							onValueChange={(v) => {
								taskSlug = v;
								taskVersion = '';
							}}
						/>
					</div>
					{#if selectedTask}
						<div class="space-y-2">
							<label for="rep-task-version" class="text-sm font-medium">Task version (optional)</label>
							<Select.Simple
								id="rep-task-version"
								bind:value={taskVersion}
								placeholder="Every version"
								clearLabel="Every version"
								items={versionItems}
							/>
							<p class="text-xs text-muted-foreground">Versions of a task are different stimuli; pick one to compare like with like.</p>
						</div>
					{/if}
					{@render excludeTruncatedOption('')}
				{:else if selectedType === 'tool_analysis'}
					<div class="space-y-2">
						<label for="rep-tool" class="text-sm font-medium">Tool name (optional)</label>
						<Input id="rep-tool" bind:value={toolName} placeholder="bandit, eslint, zap, … (blank = all)" />
						<p class="text-xs text-muted-foreground">Leave blank to aggregate all tools</p>
					</div>
					<div class="space-y-2">
						<label for="rep-tool-days" class="text-sm font-medium">Window (days, optional)</label>
						<input
							id="rep-tool-days"
							type="number"
							min="1"
							max="365"
							bind:value={toolDays}
							placeholder="all time"
							class="block w-full std-select py-2"
						/>
						<p class="text-xs text-muted-foreground">Only runs created in the last N days; blank for all runs</p>
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
				{:else if selectedType === 'experiment_report'}
					<div class="space-y-2">
						<label for="rep-study" class="text-sm font-medium">Study</label>
						<Select.Simple id="rep-study" bind:value={studyId} placeholder="Select a study" items={studyItems} />
						<p class="text-xs text-muted-foreground">
							Uses the current run of every main-stage cell; pilot, beyond-plan and superseded runs are left out and counted.
						</p>
					</div>
					{@render excludeTruncatedOption("If the study's analysis plan is locked, its truncated-runs rule applies instead.")}
				{:else if selectedType === 'study_report'}
					<div class="space-y-2">
						<label for="rep-study-scope" class="text-sm font-medium">Study (optional)</label>
						<Select.Simple
							id="rep-study-scope"
							bind:value={studyId}
							placeholder="All my apps"
							items={[{ value: '', label: 'All my apps' }, ...studyItems]}
						/>
						<p class="text-xs text-muted-foreground">
							With a study, its locked analysis plan decides which comparisons are confirmatory (and its exclusions win over the option below).
						</p>
					</div>
					{@render excludeTruncatedOption("With a study whose analysis plan is locked, the plan's truncated-runs rule applies; leave this off to follow it.")}
					<div class="space-y-2">
						<label for="rep-seed" class="text-sm font-medium">Random seed</label>
						<input id="rep-seed" type="number" min="0" bind:value={seed} class="block w-full std-select py-2" />
						<p class="text-xs text-muted-foreground">Fixes every bootstrap and permutation draw, so the report regenerates identically.</p>
					</div>
					<label class="flex items-center gap-2 text-sm">
						<input type="checkbox" bind:checked={bootstrap} />
						Bootstrap rank intervals over apps (slower)
					</label>
					<label class="flex items-center gap-2 text-sm">
						<input type="checkbox" bind:checked={includeAi} />
						Count AI-reviewer findings as issues
					</label>
				{/if}

				{#if error}
					<ErrorAlert message={error} />
				{/if}
			</Card.Content>
			<Card.Footer class="gap-2 justify-end">
				<Button variant="outline" onclick={() => (step = 1)}>
					<ArrowLeft class="mr-1 h-4 w-4" />Change type
				</Button>
				<Button onclick={submit} disabled={submitting}>
					{#if submitting}
						<LoaderCircle class="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
					{/if}
					Generate report
				</Button>
			</Card.Footer>
		</Card.Root>
	{/if}
</div>
