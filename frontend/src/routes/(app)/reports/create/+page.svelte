<script lang="ts">
	/**
	 * New report, on one page: 1 the report type (research reports vs platform rollups), 2 its scope
	 * and options, 3 an optional title and note; a sticky preview on the right shows the title it will
	 * get, what it will contain and what is still missing, with the Generate button.
	 *
	 * v3 configs: the task comparison (`template_comparison`) takes `task_slug` (+ optional
	 * `task_version`), the study conditions report (`experiment_report`) a `study_id`, the study
	 * report an optional `study_id` (else every app of yours). The task comparison, study conditions
	 * and study report offer the exclude-truncated sensitivity; `exclude_truncated` is sent only when
	 * ticked, so an untouched report stays intention-to-treat (or follows a locked analysis plan)
	 * without an override note.
	 *
	 * Deep link: `?type=<report type>&study_id=<uuid>` (from a study's Results section).
	 */
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import * as Select from '$lib/components/ui/select';
	import Check from '@lucide/svelte/icons/check';
	import FileText from '@lucide/svelte/icons/file-text';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import CircleDashed from '@lucide/svelte/icons/circle-dashed';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import ComposerSection from '$lib/components/studies/builder/ComposerSection.svelte';
	import NumberField from '$lib/components/studies/shared/NumberField.svelte';
	import { getModels, type LLMModelSummary } from '$lib/api/models';
	import {
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
	import { cn } from '$lib/utils';
	import { EXPORT_FORMATS, REPORT_GROUPS, REPORT_TYPES, REPORT_TYPE_INFO } from '$lib/components/reports/catalog';

	let selectedType = $state<ReportType | null>(null);
	let title = $state('');
	let description = $state('');
	let modelId = $state('');
	let taskSlug = $state('');
	/** '' = every version of the task. */
	let taskVersion = $state('');
	let toolName = $state('');
	let days = $state<number | null>(30);
	let toolDays = $state<number | null>(null);
	let studyId = $state('');
	let includeAi = $state(false);
	let countScratch = $state(false);
	let countTests = $state(false);
	let bootstrap = $state(true);
	let excludeTruncated = $state(false);
	let seed = $state<number | null>(0);
	let daysInvalid = $state(false);
	let toolDaysInvalid = $state(false);
	let seedInvalid = $state(false);
	let submitting = $state(false);
	let error = $state('');
	let detailsOpen = $state(false);

	let models = $state<LLMModelSummary[]>([]);
	let tasks = $state<LibraryItemOut<'task'>[]>([]);
	let studies = $state<StudyListOut[]>([]);
	let listsLoaded = $state(false);

	// Deep link from a study's Results section: ?type=experiment_report&study_id=…
	{
		const type = page.url.searchParams.get('type');
		if (type && REPORT_TYPES.some((t) => t.key === type)) selectedType = type as ReportType;
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
		listsLoaded = true;
	});

	const info = $derived(selectedType ? REPORT_TYPE_INFO[selectedType] : null);
	const studyItems = $derived(
		studies.map((x) => ({
			value: x.id,
			label: x.name,
			description: `${x.status} · ${x.mode} mode · ${x.kind} · ${x.cells_done}/${x.cells_total} cells`
		}))
	);
	const modelItems = $derived(models.map((m) => ({ value: m.model_id, label: m.model_name, description: m.model_id })));
	const selectedTask = $derived(tasks.find((t) => t.slug === taskSlug) ?? null);
	const selectedStudy = $derived(studies.find((s) => s.id === studyId) ?? null);
	const selectedModel = $derived(models.find((m) => m.model_id === modelId) ?? null);
	/** Versions 1..latest of the picked task (the list holds the latest version per slug). */
	const versionItems = $derived(
		selectedTask
			? Array.from({ length: selectedTask.version }, (_, i) => selectedTask.version - i).map((v) => ({
					value: String(v),
					label: `v${v}${v === selectedTask.version ? ' (latest)' : ''}`
				}))
			: []
	);

	/** What still has to be picked, in words ("" when the report can run). */
	const missing = $derived.by(() => {
		if (!selectedType) return 'Choose a report type';
		if (selectedType === 'model_analysis' && !modelId) return 'Pick a model';
		if (selectedType === 'template_comparison' && !taskSlug) return 'Pick a library task';
		if (selectedType === 'experiment_report' && !studyId) return 'Pick an experiment';
		if (daysInvalid || toolDaysInvalid || seedInvalid) return 'Fix the highlighted field';
		return '';
	});
	const scopeDone = $derived(!!selectedType && !missing);

	/** The title the server will give the report when none is typed (`_default_title`). */
	const autoTitle = $derived.by(() => {
		switch (selectedType) {
			case 'model_analysis':
				return `Model analysis: ${selectedModel?.model_name ?? modelId ?? '…'}`;
			case 'template_comparison':
				return `Task comparison: ${taskSlug || '…'}`;
			case 'tool_analysis':
				return `Tool analysis: ${toolName || 'all tools'}`;
			case 'generation_analytics':
				return `Generation analytics (${days ?? 30}d)`;
			case 'comprehensive':
				return `Comprehensive report (${days ?? 30}d)`;
			case 'experiment_report':
				return `Study conditions: ${selectedStudy?.name ?? (studyId || '…')}`;
			case 'study_report':
				return `Study report: ${selectedStudy?.name ?? 'all my apps'}`;
			default:
				return 'Untitled report';
		}
	});

	/** One line per scope setting for the preview. */
	const scopeLines = $derived.by(() => {
		const out: [string, string][] = [];
		switch (selectedType) {
			case 'model_analysis':
				out.push(['Model', selectedModel?.model_name ?? '—']);
				break;
			case 'template_comparison':
				out.push(['Task', selectedTask?.name || taskSlug || '—']);
				out.push(['Version', taskVersion ? `v${taskVersion}` : 'every version']);
				break;
			case 'tool_analysis':
				out.push(['Tools', toolName || 'all']);
				out.push(['Window', toolDays ? `last ${toolDays} days` : 'all time']);
				break;
			case 'generation_analytics':
			case 'comprehensive':
				out.push(['Window', `last ${days ?? 30} days`]);
				break;
			case 'experiment_report':
				out.push(['Experiment', selectedStudy?.name ?? '—']);
				break;
			case 'study_report':
				out.push(['Scope', selectedStudy?.name ?? 'all my apps']);
				out.push(['Seed', String(seed ?? 0)]);
				break;
		}
		if (selectedType === 'template_comparison' || selectedType === 'experiment_report' || selectedType === 'study_report') {
			out.push(['Analysis', excludeTruncated ? 'truncated runs excluded' : 'intention-to-treat']);
		}
		return out;
	});

	function pickType(t: ReportType) {
		if (selectedType !== t) error = '';
		selectedType = t;
	}

	/** Arrow keys move between the type cards (a radiogroup). */
	function onTypeKey(e: KeyboardEvent) {
		if (!['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight'].includes(e.key)) return;
		e.preventDefault();
		const i = REPORT_TYPES.findIndex((t) => t.key === selectedType);
		const step = e.key === 'ArrowDown' || e.key === 'ArrowRight' ? 1 : -1;
		const next = REPORT_TYPES[(Math.max(i, 0) + step + REPORT_TYPES.length) % REPORT_TYPES.length];
		pickType(next.key);
		document.getElementById(`rt-${next.key}`)?.focus();
	}

	async function submit() {
		if (!selectedType) return;
		if (missing) {
			error = missing;
			return;
		}
		let config: Record<string, unknown> = {};
		if (selectedType === 'model_analysis') config.model_id = modelId;
		if (selectedType === 'template_comparison') {
			const c: TaskComparisonConfig = { task_slug: taskSlug };
			if (taskVersion) c.task_version = Number(taskVersion);
			config = { ...c, ...excludeTruncatedConfig(excludeTruncated) };
		}
		if (selectedType === 'tool_analysis') {
			if (toolName.trim()) config.tool_name = toolName.trim();
			// Blank window: all time.
			if (toolDays) config.days = toolDays;
		}
		if (selectedType === 'generation_analytics' || selectedType === 'comprehensive') {
			config.days = days || 30;
		}
		if (selectedType === 'experiment_report') {
			const c: StudyConditionsConfig = { study_id: studyId, ...excludeTruncatedConfig(excludeTruncated) };
			config = { ...c };
		}
		if (selectedType === 'study_report') {
			// No study: every app you own.
			// Unset unless ticked: an explicit value differing from a locked plan adds a validity note.
			const c: StudyReportConfig = excludeTruncatedConfig(excludeTruncated);
			if (studyId) c.study_id = studyId;
			// Unset by default: tests and the agent's scratch scripts are counted apart from the app.
			if (countTests) c.code_scope = 'all';
			else if (countScratch) c.code_scope = 'with_scratch';
			config = { ...c, include_ai: includeAi, bootstrap, seed: seed ?? 0 };
		}
		submitting = true;
		error = '';
		try {
			const r = await generateReport({
				report_type: selectedType,
				title: title.trim() || undefined,
				description: description.trim() || undefined,
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
	<label class="flex cursor-pointer items-start gap-2.5 rounded-md border border-border p-3 transition-colors hover:bg-muted/40">
		<Checkbox bind:checked={excludeTruncated} class="mt-0.5" />
		<span class="space-y-0.5">
			<span class="block text-sm font-medium">Exclude truncated runs <span class="font-normal text-muted-foreground">(sensitivity analysis)</span></span>
			<span class="block text-xs leading-relaxed text-muted-foreground">
				Off: intention-to-treat, truncated runs (budget, wall clock, idle, max turns) stay in the sample.
				{planNote}
			</span>
		</span>
	</label>
{/snippet}

{#snippet field(id: string, label: string, hint: string)}
	<!-- (label + hint only; the control follows in the caller) -->
	<Label for={id}>{label}</Label>
	{#if hint}<p class="text-xs text-muted-foreground">{hint}</p>{/if}
{/snippet}

<div class="space-y-4">
	<PageHeader
		title="New report"
		description="Choose what to report on and its scope. Reports are kept 30 days and download as Markdown, LaTeX or JSON."
		back={{ href: '/reports', label: 'Reports' }}
	/>

	<div class="grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem] lg:items-start">
		<div class="min-w-0 space-y-4">
			<!-- 1 · Report type -->
			<ComposerSection step={1} id="type" title="Report type" description="What the report answers." done={!!selectedType} meta={info?.label}>
				<div role="radiogroup" aria-label="Report type" tabindex="-1" class="space-y-5" onkeydown={onTypeKey}>
					{#each REPORT_GROUPS as group (group.key)}
						<div class="space-y-2.5">
							<SectionHeading size="sm" title={group.label} lead={group.lead} />
							<div class="grid gap-2.5 @lg:grid-cols-2">
								{#each REPORT_TYPES.filter((t) => t.group === group.key) as t (t.key)}
									{@const Icon = t.icon}
									{@const on = selectedType === t.key}
									<button
										type="button"
										id="rt-{t.key}"
										role="radio"
										aria-checked={on}
										tabindex={on || (!selectedType && t.key === REPORT_TYPES[0].key) ? 0 : -1}
										onclick={() => pickType(t.key)}
										class={cn(
											'group relative flex cursor-pointer items-start gap-3 rounded-md border p-3 text-left transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
											on ? 'border-primary bg-primary/[0.06]' : 'border-border hover:border-foreground/30 hover:bg-muted/40'
										)}
									>
										<span
											class={cn(
												'flex size-8 shrink-0 items-center justify-center rounded-md border transition-colors',
												on ? 'border-primary/40 bg-primary/10 text-primary' : 'border-border text-muted-foreground group-hover:text-foreground'
											)}
										>
											<Icon class="size-4" aria-hidden="true" />
										</span>
										<span class="min-w-0 flex-1 space-y-1">
											<span class="flex items-center gap-2">
												<span class="font-medium">{t.label}</span>
												{#if on}<Check class="ml-auto size-4 text-primary" aria-hidden="true" />{/if}
											</span>
											<span class="block text-xs leading-relaxed text-muted-foreground">{t.purpose}</span>
											{#if t.needs}
												<span class="block font-mono text-[11px] text-muted-foreground">needs {t.needs}</span>
											{/if}
										</span>
									</button>
								{/each}
							</div>
						</div>
					{/each}
				</div>
			</ComposerSection>

			<!-- 2 · Scope -->
			<ComposerSection
				step={2}
				id="scope"
				title="Scope and options"
				description={selectedType ? `What the ${info?.label.toLowerCase()} covers and how it is computed.` : 'Choose a report type first.'}
				done={scopeDone}
				invalid={!!selectedType && (daysInvalid || toolDaysInvalid || seedInvalid)}
			>
				{#if !selectedType}
					<p class="flex items-center gap-2 text-sm text-muted-foreground"><CircleDashed class="size-4" aria-hidden="true" />The options depend on the report type.</p>
				{:else if selectedType === 'model_analysis'}
					<div class="space-y-1.5">
						{@render field('rep-model', 'Model', 'One model; its runs, code size and findings are aggregated.')}
						<Select.Simple id="rep-model" bind:value={modelId} placeholder={listsLoaded ? 'Select a model' : 'Loading models…'} items={modelItems} />
					</div>
				{:else if selectedType === 'template_comparison'}
					<div class="grid gap-4 @xl:grid-cols-[minmax(0,1fr)_12rem]">
						<div class="space-y-1.5">
							{@render field('rep-task', 'Library task', '')}
							<Select.Simple
								id="rep-task"
								value={taskSlug}
								placeholder={listsLoaded ? 'Select a library task' : 'Loading tasks…'}
								items={tasks.map((t) => ({ value: t.slug, label: t.name || t.slug, description: `${t.slug} · latest v${t.version}` }))}
								onValueChange={(v) => {
									taskSlug = v;
									taskVersion = '';
								}}
							/>
						</div>
						<div class="space-y-1.5">
							{@render field('rep-task-version', 'Version', '')}
							<Select.Simple
								id="rep-task-version"
								bind:value={taskVersion}
								placeholder="Every version"
								clearLabel="Every version"
								items={versionItems}
								disabled={!selectedTask}
							/>
						</div>
					</div>
					<p class="text-xs text-muted-foreground">Versions of a task are different stimuli; pick one to compare like with like.</p>
					{@render excludeTruncatedOption('')}
				{:else if selectedType === 'tool_analysis'}
					<div class="grid gap-4 @xl:grid-cols-2">
						<div class="space-y-1.5">
							{@render field('rep-tool', 'Tool', 'Blank aggregates every tool.')}
							<Input id="rep-tool" bind:value={toolName} placeholder="bandit, eslint, zap, …" autocomplete="off" />
						</div>
						<NumberField
							id="rep-tool-days"
							label="Window (days)"
							bind:value={toolDays}
							bind:invalid={toolDaysInvalid}
							min={1}
							max={365}
							placeholder="all time"
							hint="Runs from the last N days; blank for all runs."
						/>
					</div>
				{:else if selectedType === 'generation_analytics' || selectedType === 'comprehensive'}
					<div class="space-y-2">
						<NumberField id="rep-days" label="Window (days)" bind:value={days} bind:invalid={daysInvalid} min={1} max={365} required hint="1–365 days." class="@xl:max-w-56" />
						<div class="flex flex-wrap gap-1.5" role="group" aria-label="Window presets">
							{#each [7, 30, 90, 365] as d (d)}
								<button
									type="button"
									class={cn(
										'h-7 cursor-pointer rounded-md border px-2.5 font-mono text-xs transition-colors',
										days === d ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground hover:bg-muted hover:text-foreground'
									)}
									aria-pressed={days === d}
									onclick={() => (days = d)}>{d}d</button
								>
							{/each}
						</div>
					</div>
				{:else if selectedType === 'experiment_report'}
					<div class="space-y-1.5">
						{@render field('rep-study', 'Experiment', 'Uses the current run of every main-stage cell; pilot, beyond-plan and superseded runs are left out and counted.')}
						<Select.Simple id="rep-study" bind:value={studyId} placeholder={listsLoaded ? (studyItems.length ? 'Select an experiment' : 'No launched experiments yet') : 'Loading experiments…'} items={studyItems} />
					</div>
					{@render excludeTruncatedOption("If the experiment's analysis plan is locked, its truncated-runs rule applies instead.")}
				{:else if selectedType === 'study_report'}
					<div class="space-y-1.5">
						{@render field('rep-study-scope', 'Experiment', 'Optional. With an experiment, its locked analysis plan decides which comparisons are confirmatory (and its exclusions win over the option below).')}
						<Select.Simple id="rep-study-scope" bind:value={studyId} placeholder="All my apps" items={[{ value: '', label: 'All my apps' }, ...studyItems]} />
					</div>
					{@render excludeTruncatedOption("With an experiment whose analysis plan is locked, the plan's truncated-runs rule applies; leave this off to follow it.")}
					<div class="grid gap-4 @xl:grid-cols-[12rem_minmax(0,1fr)] @xl:items-start">
						<NumberField id="rep-seed" label="Random seed" bind:value={seed} bind:invalid={seedInvalid} min={0} required hint="Fixes every bootstrap and permutation draw." />
						<div class="space-y-2 @xl:pt-6">
							<label class="flex cursor-pointer items-center gap-2 text-sm">
								<Checkbox bind:checked={bootstrap} />
								Bootstrap rank intervals over apps <span class="text-xs text-muted-foreground">(slower)</span>
							</label>
							<label class="flex cursor-pointer items-center gap-2 text-sm">
								<Checkbox bind:checked={includeAi} />
								Count AI-reviewer findings as issues
							</label>
							<label class="flex cursor-pointer items-center gap-2 text-sm">
								<Checkbox checked={countScratch || countTests} disabled={countTests} onCheckedChange={(v) => (countScratch = v === true)} />
								Count the agent's scratch and check scripts as app code
							</label>
							<label class="flex cursor-pointer items-center gap-2 text-sm">
								<Checkbox bind:checked={countTests} />
								Count test code as app code
							</label>
						</div>
					</div>
				{/if}
			</ComposerSection>

			<!-- 3 · Title -->
			<ComposerSection
				step={3}
				id="details"
				title="Title and note"
				description="Optional."
				collapsible
				bind:open={detailsOpen}
				summary={title.trim() ? `“${title.trim()}”${description.trim() ? ' · with a note' : ''}` : `Automatic title · ${description.trim() ? 'with a note' : 'no note'}`}
			>
				<div class="space-y-1.5">
					<Label for="rep-title">Title</Label>
					<Input id="rep-title" bind:value={title} placeholder={autoTitle} maxlength={200} />
				</div>
				<div class="space-y-1.5">
					<Label for="rep-desc">Note</Label>
					<Input id="rep-desc" bind:value={description} placeholder="Why this report exists (shown under the title)" maxlength={500} />
				</div>
			</ComposerSection>
		</div>

		<!-- Preview + generate -->
		<aside class="lg:sticky lg:top-16" aria-label="Report preview">
			<div class="overflow-clip rounded-md border border-border bg-card shadow-[var(--shadow-card)]">
				<div class="space-y-1.5 border-b border-border/60 px-4 pb-3 pt-3.5">
					<p class="page-eyebrow">
						{#if info}{@const Icon = info.icon}<Icon class="size-3.5" aria-hidden="true" />{:else}<FileText class="size-3.5" aria-hidden="true" />{/if}
						<span>Report</span>
						{#if info}<span aria-hidden="true">·</span><span>{info.label}</span>{/if}
					</p>
					<p class={cn('break-words font-display text-lg font-semibold leading-snug', !title.trim() && 'text-foreground/80')}>
						{title.trim() || autoTitle}
					</p>
				</div>
				{#if info}
					<dl class="space-y-1 border-b border-border/60 px-4 py-3 text-sm">
						{#each scopeLines as [k, v] (k)}
							<div class="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-2">
								<dt class="text-muted-foreground">{k}</dt>
								<dd class="truncate" title={v}>{v}</dd>
							</div>
						{/each}
					</dl>
					<div class="border-b border-border/60 px-4 py-3">
						<p class="section-label mb-1.5">Contains</p>
						<ol class="space-y-1 text-[13px] leading-snug">
							{#each info.contents as c, i (c)}
								<li class="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-1">
									<span class="font-mono text-[11px] text-muted-foreground tabular-nums">{i + 1}</span><span>{c}</span>
								</li>
							{/each}
						</ol>
					</div>
				{:else}
					<p class="border-b border-border/60 px-4 py-6 text-center text-sm text-muted-foreground">Choose a report type to see what it will contain.</p>
				{/if}
				<div class="space-y-2.5 px-4 py-3">
					{#if error}<ErrorAlert message={error} />{/if}
					<Button class="w-full" onclick={submit} disabled={submitting || !selectedType}>
						{#if submitting}<LoaderCircle class="size-4 animate-spin motion-reduce:animate-none" />{/if}
						Generate report
					</Button>
					<p class={cn('text-center text-xs', missing ? 'text-muted-foreground' : 'text-success-fg')}>
						{missing || 'Ready'} <span class="text-muted-foreground">· {EXPORT_FORMATS.map((f) => f.label).join(', ')}</span>
					</p>
				</div>
			</div>
		</aside>
	</div>
</div>
