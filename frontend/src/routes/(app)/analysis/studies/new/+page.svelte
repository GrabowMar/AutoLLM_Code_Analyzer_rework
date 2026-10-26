<script lang="ts">
	/**
	 * New study wizard: Question → Targets → Method → Preflight → Lock & launch.
	 *
	 * The draft is created after step 1 and every later step is saved to it
	 * (PATCH), so the wizard is resumable: `?id=<draft>&step=<step>`.
	 * `?experiment=<id>` / `?batch=<id>` preselect the targets of a new draft.
	 */
	import { onMount } from 'svelte';
	import { beforeNavigate, goto } from '$app/navigation';
	import { page } from '$app/state';
	import {
		createStudy,
		getStudy,
		getTargetOptions,
		hasTargetSpec,
		isPreflightReport,
		runStudyPreflight,
		updateStudy,
		type AnalysisDesign,
		type ExclusionRules,
		type PreflightReport,
		type ProfileDefaults,
		type ResearchQuestionInput,
		type StudyDetail,
		type StudyUpdatePayload,
		type TargetOptions,
		type TargetResolveResult,
		type TargetSpec
	} from '$lib/api/analysis';
	import { apiFieldErrors, formatApiError } from '$lib/api/core';
	import { loadProfileDefaults } from '$lib/components/analysis/profile/index';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Save from '@lucide/svelte/icons/save';
	import Lock from '@lucide/svelte/icons/lock';
	import StudyStepper, { type StepDef } from '$lib/components/analysis/study/StudyStepper.svelte';
	import QuestionEditor from '$lib/components/analysis/study/QuestionEditor.svelte';
	import TargetPicker from '$lib/components/analysis/study/TargetPicker.svelte';
	import MethodEditor from '$lib/components/analysis/study/MethodEditor.svelte';
	import PreflightView from '$lib/components/analysis/study/PreflightView.svelte';
	import LaunchPanel from '$lib/components/analysis/study/LaunchPanel.svelte';
	import { targetSpecSummary } from '$lib/components/analysis/study/format';

	type Step = 'question' | 'targets' | 'method' | 'preflight' | 'launch';
	const STEPS: Step[] = ['question', 'targets', 'method', 'preflight', 'launch'];
	const STEP_LABELS: Record<Step, string> = {
		question: 'Question',
		targets: 'Targets',
		method: 'Method',
		preflight: 'Preflight',
		launch: 'Lock & launch'
	};
	const FALLBACK_RULES: ExclusionRules = {
		exclude_invalid: true,
		exclude_empty_output: true,
		exclude_truncated: true,
		min_measured_sloc: 0,
		require_functional: false
	};
	// Mirrors the backend DEFAULT_DESIGN until /profiles/defaults/ answers.
	const FALLBACK_DESIGN: AnalysisDesign = {
		group_by: ['model'],
		block_by: 'app',
		alpha: 0.05,
		correction: 'holm',
		exploratory_correction: 'bh',
		bootstrap_resamples: 10000,
		effect_ci_resamples: 2000,
		ci_level: 0.95,
		seed: 0,
		primary_outcome: 'static_issues_per_kloc',
		outcomes: null,
		cell_aggregate: 'mean'
	};

	// ── URL state ─────────────────────────────────────────────────────────
	const studyId = $derived(page.url.searchParams.get('id') ?? '');
	const step = $derived.by((): Step => {
		const raw = page.url.searchParams.get('step') as Step | null;
		return raw && STEPS.includes(raw) ? raw : 'question';
	});

	function navigate(next: { step?: Step; id?: string }, replace = false) {
		const url = new URL(page.url);
		if (next.id) url.searchParams.set('id', next.id);
		url.searchParams.set('step', next.step ?? step);
		url.searchParams.delete('experiment');
		url.searchParams.delete('batch');
		void goto(url, { replaceState: replace, keepFocus: true, noScroll: false });
	}

	// ── Data ──────────────────────────────────────────────────────────────
	let loading = $state(true);
	let loadError = $state('');
	let options = $state<TargetOptions | null>(null);
	let defaults = $state<ProfileDefaults | null>(null);
	let study = $state<StudyDetail | null>(null);

	// Form (hydrated from the draft; saved with PATCH on step changes)
	let name = $state('');
	let description = $state('');
	let questions = $state<ResearchQuestionInput[]>([]);
	let spec = $state<TargetSpec | null>(null);
	let profileId = $state<string | null>(null);
	let rules = $state<ExclusionRules>({ ...FALLBACK_RULES });
	let design = $state<AnalysisDesign>({
		...FALLBACK_DESIGN,
		group_by: [...FALLBACK_DESIGN.group_by]
	});
	let dirty = $state({ question: false, targets: false, method: false });
	let pickerKey = $state(0);
	let resolved = $state<TargetResolveResult | null>(null);
	let profileBusy = $state(false);

	let saving = $state(false);
	let saveError = $state('');
	let fieldErrors = $state<Record<string, string>>({});

	let preflight = $state<PreflightReport | null>(null);
	let preflightBusy = $state(false);
	let preflightError = $state('');

	const readonly = $derived(!!study && study.status !== 'draft');
	const anyDirty = $derived(dirty.question || dirty.targets || dirty.method);

	function hydrate(s: StudyDetail) {
		study = s;
		name = s.name;
		description = s.description;
		questions = s.research_questions.map((q) => ({ ...q }));
		spec = hasTargetSpec(s.target_spec) ? s.target_spec : null;
		profileId = s.profile?.id ?? null;
		rules = { ...s.exclusion_rules };
		design = {
			...s.analysis_design,
			group_by: [...s.analysis_design.group_by]
		};
		preflight = isPreflightReport(s.preflight) ? s.preflight : null;
		dirty = { question: false, targets: false, method: false };
		pickerKey += 1;
	}

	async function load() {
		loading = true;
		loadError = '';
		try {
			const [opts, defs] = await Promise.all([getTargetOptions(), loadProfileDefaults()]);
			options = opts;
			defaults = defs;
			rules = { ...defs.exclusion_rules };
			design = { ...defs.design, group_by: [...defs.design.group_by] };
			if (studyId) {
				const s = await getStudy(studyId);
				if (s.status !== 'draft' && s.status !== 'locked') {
					await goto(`/analysis/studies/${s.id}`, { replaceState: true });
					return;
				}
				hydrate(s);
			} else {
				const experiment = page.url.searchParams.get('experiment');
				const batch = page.url.searchParams.get('batch');
				if (experiment) spec = { kind: 'experiment', experiment_id: experiment };
				else if (batch) spec = { kind: 'batch', batch_id: batch };
				if (spec) pickerKey += 1;
			}
		} catch (e) {
			loadError = formatApiError(e, 'Could not load the study setup.');
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		void load();
	});

	// ── Saving ────────────────────────────────────────────────────────────
	const STEP_OF_FIELD: [RegExp, Step][] = [
		[/^(name|description|research_questions)/, 'question'],
		[/^target_spec/, 'targets'],
		[/^(profile_id|exclusion_rules|analysis_design)/, 'method']
	];

	function questionPayload(): ResearchQuestionInput[] {
		return questions
			.filter((q) => q.text.trim())
			.map((q, i) => ({
				id: q.id || `RQ${i + 1}`,
				text: q.text.trim(),
				kind: q.kind ?? 'exploratory',
				...(q.hypothesis?.trim() ? { hypothesis: q.hypothesis.trim() } : {})
			}));
	}

	/** Create the draft or PATCH every dirty part; false (and errors shown) on failure. */
	async function save(): Promise<boolean> {
		if (readonly) return true;
		if (!name.trim()) {
			fieldErrors = { name: 'A study needs a name.' };
			if (step !== 'question') navigate({ step: 'question' });
			return false;
		}
		saving = true;
		saveError = '';
		fieldErrors = {};
		try {
			if (!study) {
				const created = await createStudy({
					name: name.trim(),
					description: description.trim(),
					research_questions: questionPayload(),
					...(spec ? { target_spec: spec } : {})
				});
				hydrate(created);
				return true;
			}
			const patch: StudyUpdatePayload = {};
			if (dirty.question) {
				patch.name = name.trim();
				patch.description = description.trim();
				patch.research_questions = questionPayload();
			}
			if (dirty.targets) patch.target_spec = spec ?? {};
			if (dirty.method) {
				patch.profile_id = profileId;
				patch.exclusion_rules = rules;
				patch.analysis_design = design;
			}
			if (Object.keys(patch).length === 0) return true;
			hydrate(await updateStudy(study.id, patch));
			return true;
		} catch (e) {
			fieldErrors = apiFieldErrors(e);
			saveError = formatApiError(e, 'Could not save the study.');
			const first = Object.keys(fieldErrors)[0];
			const target = first ? STEP_OF_FIELD.find(([re]) => re.test(first))?.[1] : undefined;
			if (target && target !== step) navigate({ step: target });
			return false;
		} finally {
			saving = false;
		}
	}

	async function goToStep(next: Step) {
		if (next === step) return;
		if (anyDirty || !study) {
			const ok = await save();
			if (!ok) return;
		}
		navigate({ step: next, id: study?.id });
	}

	const stepIndex = $derived(STEPS.indexOf(step));

	async function next() {
		const target = STEPS[stepIndex + 1];
		if (target) await goToStep(target);
	}

	async function back() {
		const target = STEPS[stepIndex - 1];
		if (target) await goToStep(target);
	}

	async function saveAndExit() {
		if (await save()) {
			skipGuard = true;
			await goto(study ? `/analysis/studies/${study.id}` : '/analysis/studies');
		}
	}

	// ── Preflight ─────────────────────────────────────────────────────────
	async function runPreflight() {
		if (!study) return;
		preflightBusy = true;
		preflightError = '';
		try {
			preflight = await runStudyPreflight(study.id);
			// The study's preflight summary feeds the stepper and the launch step.
			const fresh = await getStudy(study.id);
			study = fresh;
		} catch (e) {
			preflightError = formatApiError(e, 'The preflight failed.');
		} finally {
			preflightBusy = false;
		}
	}

	// Entering the preflight step runs it when there is no fresh report.
	$effect(() => {
		if (step !== 'preflight' || !study || loading || readonly) return;
		if (!preflight && !preflightBusy && !preflightError) void runPreflight();
	});

	// ── Step definitions ──────────────────────────────────────────────────
	const summary = $derived(study?.preflight_summary ?? null);
	const steps = $derived.by((): StepDef[] => {
		const created = !!study;
		const hasTargets = !!study && hasTargetSpec(study.target_spec);
		const hasProfile = !!study?.profile;
		const preflightClean = !!summary && summary.blocking === 0;
		return [
			{
				key: 'question',
				label: STEP_LABELS.question,
				enabled: true,
				done: created,
				hint: created ? 'saved' : 'name, questions'
			},
			{
				key: 'targets',
				label: STEP_LABELS.targets,
				enabled: created,
				done: hasTargets,
				hint: hasTargets
					? targetSpecSummary(study?.target_spec, options).split(' · ')[0]
					: 'what to analyse'
			},
			{
				key: 'method',
				label: STEP_LABELS.method,
				enabled: created,
				done: hasProfile && hasTargets,
				hint: study?.profile ? `${study.profile.name} v${study.profile.version}` : 'profile, design'
			},
			{
				key: 'preflight',
				label: STEP_LABELS.preflight,
				enabled: hasTargets && hasProfile,
				done: preflightClean,
				warn: !!summary && summary.blocking > 0,
				hint: summary ? `${summary.blocking} blocking · ${summary.warnings} warn` : 'pre-run report'
			},
			{
				key: 'launch',
				label: STEP_LABELS.launch,
				enabled: study?.status === 'locked' || (hasTargets && hasProfile && preflightClean),
				done: study?.status === 'locked',
				hint: study?.status === 'locked' ? 'locked' : 'protocol hash'
			}
		];
	});
	const nextEnabled = $derived(steps[stepIndex + 1]?.enabled || step === 'question');

	const outcomeLabels = $derived(
		Object.fromEntries(Object.entries(defaults?.outcomes ?? {}).map(([k, m]) => [k, m.label]))
	);

	// ── Unsaved-changes guard ─────────────────────────────────────────────
	let skipGuard = false;
	beforeNavigate((navigation) => {
		if (skipGuard) {
			skipGuard = false;
			return;
		}
		if (!anyDirty || saving) return;
		if (navigation.to?.url.pathname === page.url.pathname) return;
		if (navigation.type === 'leave') {
			navigation.cancel();
			return;
		}
		if (!confirm('Discard your unsaved changes to this study?')) navigation.cancel();
	});
</script>

<div class="space-y-6">
	<PageHeader
		title={study ? study.name : 'New study'}
		documentTitle={study ? `Set up ${study.name}` : 'New study'}
		description="Define the research question, choose targets and method, check the pre-run report, then lock the protocol and launch."
		back={{ href: '/analysis/studies', label: 'Studies' }}
	>
		{#snippet meta()}
			{#if study}<StatusBadge status={study.status} />{/if}
		{/snippet}
		{#snippet actions()}
			{#if study}
				<Button variant="outline" size="sm" onclick={saveAndExit} disabled={saving}>
					{#if saving}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Save
						/>{/if}
					{anyDirty ? 'Save & exit' : 'Open study'}
				</Button>
			{/if}
		{/snippet}
	</PageHeader>

	{#if loadError}
		<ErrorAlert message={loadError} onretry={load} />
	{:else if loading}
		<LoadingState label="loading study setup" />
	{:else}
		<StudyStepper {steps} current={step} onselect={(k) => goToStep(k as Step)} />

		{#if readonly && step !== 'launch'}
			<div
				class="flex flex-wrap items-center gap-2 rounded-md border border-info/30 bg-info/10 px-3 py-2 text-sm text-info-fg"
			>
				<Lock class="h-4 w-4" aria-hidden="true" />
				The protocol is locked: these settings are frozen. Unlock it in the last step to edit.
			</div>
		{/if}

		{#if saveError}<ErrorAlert message={saveError} />{/if}

		<section aria-labelledby="step-title" class="space-y-4">
			<h2 id="step-title" class="sr-only">{STEP_LABELS[step]}</h2>
			{#if step === 'question'}
				<Card.Root>
					<Card.Header>
						<Card.Title>Research question</Card.Title>
						<Card.Description
							>The draft is saved when you continue; you can come back to it from the studies list.</Card.Description
						>
					</Card.Header>
					<Card.Content>
						{#if !study && spec}
							<p
								class="mb-4 rounded-md border border-info/30 bg-info/10 px-3 py-2 text-xs text-info-fg"
							>
								Targets preselected: {targetSpecSummary(spec, options)}. You can change them in the
								next step.
							</p>
						{/if}
						<QuestionEditor
							bind:name
							bind:description
							bind:questions
							errors={fieldErrors}
							disabled={readonly}
							onchange={() => (dirty.question = true)}
						/>
					</Card.Content>
				</Card.Root>
			{:else if step === 'targets'}
				<Card.Root>
					<Card.Header>
						<Card.Title>Targets</Card.Title>
						<Card.Description>
							Which generated apps to analyse. Only completed jobs are targets; the exclusion rules
							(Method) decide eligibility.
						</Card.Description>
					</Card.Header>
					<Card.Content>
						{#key pickerKey}
							<TargetPicker
								bind:spec
								bind:resolved
								{options}
								exclusionRules={rules}
								{profileId}
								errors={fieldErrors}
								disabled={readonly}
								onchange={() => (dirty.targets = true)}
							/>
						{/key}
					</Card.Content>
				</Card.Root>
			{:else if step === 'method'}
				<MethodEditor
					bind:profileId
					bind:exclusionRules={rules}
					bind:design
					bind:busy={profileBusy}
					{defaults}
					errors={fieldErrors}
					disabled={readonly}
					onchange={() => (dirty.method = true)}
				/>
			{:else if step === 'preflight'}
				{#if preflightError}
					<ErrorAlert title="Preflight" message={preflightError} onretry={runPreflight} />
				{/if}
				{#if preflight}
					<PreflightView
						report={preflight}
						onrerun={readonly ? undefined : runPreflight}
						rerunning={preflightBusy}
						{outcomeLabels}
					/>
				{:else if preflightBusy}
					<LoadingState
						label="running preflight: resolving targets, checking tools and estimating cost"
					/>
				{/if}
			{:else if step === 'launch' && study}
				<LaunchPanel
					{study}
					onchange={(s) => hydrate(s)}
					onrefresh={async () => {
						if (study) hydrate(await getStudy(study.id));
					}}
					onpreflight={() => navigate({ step: 'preflight' })}
					onlaunched={(s) => {
						skipGuard = true;
						void goto(`/analysis/studies/${s.id}?tab=overview`);
					}}
				/>
			{/if}
		</section>

		<div class="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-4">
			<Button variant="outline" size="sm" onclick={back} disabled={stepIndex === 0 || saving}>
				<ArrowLeft />Back
			</Button>
			<div class="flex items-center gap-3">
				{#if anyDirty}<span class="text-xs text-muted-foreground">Unsaved changes</span>{/if}
				{#if step === 'targets' && resolved && resolved.total === 0}
					<span class="text-xs text-warning-fg">No target matches yet</span>
				{/if}
				{#if stepIndex < STEPS.length - 1}
					<Button
						size="sm"
						onclick={next}
						disabled={saving || profileBusy || !nextEnabled || (step === 'targets' && !spec)}
					>
						{#if saving}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}
						{step === 'question' && !study
							? 'Create draft'
							: `Continue: ${STEP_LABELS[STEPS[stepIndex + 1]]}`}
						<ArrowRight />
					</Button>
				{/if}
			</div>
		</div>
	{/if}
</div>
