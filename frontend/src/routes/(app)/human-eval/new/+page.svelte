<script lang="ts">
	/**
	 * New evaluation task: one page of numbered steps — 1 starting point
	 * (preset or scratch), 2 basics, 3 app pool, 4 design, 5 settings, 6 review —
	 * that creates a draft task (POST tasks/) and opens it. The design and
	 * settings are validated live (`validate-design/`, debounced) and errors land
	 * on their fields. The unsaved draft survives a reload (sessionStorage).
	 * `?preset=<slug>` preselects a preset.
	 */
	import { onDestroy, onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Segmented } from '$lib/components/ui/segmented';
	import { Textarea } from '$lib/components/ui/textarea';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import AppPoolPicker from '$lib/components/human-eval/AppPoolPicker.svelte';
	import SettingsEditor from '$lib/components/human-eval/SettingsEditor.svelte';
	import DesignEditor from '$lib/components/human-eval/design/DesignEditor.svelte';
	import MarkdownField from '$lib/components/human-eval/design/MarkdownField.svelte';
	import { BLOCK_TYPE_INFO } from '$lib/components/human-eval/design/factories';
	import { BLOCK_ICONS } from '$lib/components/human-eval/design/icons';
	import { classifyPath, describeDesignPath, splitErrors, type PathError } from '$lib/components/human-eval/design/errors';
	import { planSummary } from '$lib/components/human-eval/design/plan';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import Info from '@lucide/svelte/icons/info';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import SquareDashed from '@lucide/svelte/icons/square-dashed';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { getStudy, getStudyResults } from '$lib/api/v3/studies';
	import {
		createTask,
		getEnvironment,
		humanEvalErrorBody,
		listCandidateRuns,
		listPresets,
		validateDesign,
		type HumanEvalEnvironment,
		type Preset,
		type TaskMode,
	} from '$lib/api/human-eval';
	import {
		applyPreset,
		applyScratch,
		clearStoredDraft,
		createInput,
		designFingerprint,
		draftNotes,
		emptyDraft,
		hasContent,
		loadStoredDraft,
		storeDraft,
		type WizardDraft,
	} from './wizard';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// ── catalogue ─────────────────────────────────────────────────────
	let presets = $state<Preset[]>([]);
	let environment = $state<HumanEvalEnvironment | null>(null);
	let presetsError = $state('');
	let environmentError = $state('');
	let loading = $state(true);
	let ready = $state(false);

	// ── draft ─────────────────────────────────────────────────────────
	let draft = $state<WizardDraft>(emptyDraft(null));
	/** Fingerprint of mode/design/settings right after the last preset was applied ('' = unknown). */
	let appliedFingerprint = $state('');
	let restored = $state(false);
	let titleTouched = $state(false);

	async function loadCatalogue() {
		loading = true;
		presetsError = '';
		environmentError = '';
		const [p, env] = await Promise.allSettled([listPresets(), getEnvironment()]);
		if (p.status === 'fulfilled') presets = p.value;
		else presetsError = formatApiError(p.reason, 'Could not load the presets.');
		if (env.status === 'fulfilled') environment = env.value;
		else environmentError = formatApiError(env.reason, 'Could not load the evaluation environment.');
		loading = false;
	}

	onMount(async () => {
		await loadCatalogue();
		const stored = loadStoredDraft();
		if (stored && hasContent(stored)) {
			draft = stored;
			restored = true;
			appliedFingerprint = '';
		} else {
			draft = emptyDraft(environment);
			const preset = presets.find((x) => x.slug === data.preset);
			if (preset) draft = applyPreset(draft, preset);
			appliedFingerprint = designFingerprint(draft);
		}
		ready = true;
	});

	// Keep an unsaved copy for reloads.
	let persistTimer: ReturnType<typeof setTimeout> | undefined;
	let persistEnabled = true;
	const draftJson = $derived(JSON.stringify(draft));
	$effect(() => {
		void draftJson;
		if (!ready) return;
		untrack(() => {
			clearTimeout(persistTimer);
			persistTimer = setTimeout(() => {
				if (!persistEnabled) return;
				if (hasContent(draft)) storeDraft($state.snapshot(draft) as WizardDraft);
				else clearStoredDraft();
			}, 400);
		});
	});

	// ── starting point ────────────────────────────────────────────────
	type Start = { kind: 'preset'; preset: Preset } | { kind: 'scratch' };
	let pendingStart = $state<Start | null>(null);
	let replaceOpen = $state(false);

	function applyStart(start: Start) {
		draft = start.kind === 'preset' ? applyPreset(draft, start.preset) : applyScratch(draft, environment);
		appliedFingerprint = designFingerprint(draft);
		toast.success(start.kind === 'preset' ? `Design and settings from “${start.preset.name}”` : 'Started from scratch');
	}

	function choose(start: Start) {
		const edited = draft.start !== null && designFingerprint(draft) !== appliedFingerprint;
		if (edited) {
			pendingStart = start;
			replaceOpen = true;
			return;
		}
		applyStart(start);
	}

	function confirmReplace() {
		if (pendingStart) applyStart(pendingStart);
		pendingStart = null;
		replaceOpen = false;
	}

	// ── discard ───────────────────────────────────────────────────────
	let discardOpen = $state(false);
	function discard() {
		clearStoredDraft();
		draft = emptyDraft(environment);
		appliedFingerprint = designFingerprint(draft);
		restored = false;
		titleTouched = false;
		validation = { status: 'idle', errors: [] };
		discardOpen = false;
	}

	function setMode(value: string) {
		draft.mode = (value === 'pairwise' ? 'pairwise' : 'single') as TaskMode;
	}

	// ── linked study ──────────────────────────────────────────────────
	let studyName = $state('');
	let baselineOptions = $state<{ id: number; label: string }[]>([]);
	let studyApps = $state<number | null>(null);
	let studyFor = '';

	$effect(() => {
		const id = draft.studyId;
		untrack(() => {
			if ((id ?? '') === studyFor) return;
			studyFor = id ?? '';
			studyName = '';
			baselineOptions = [];
			studyApps = null;
			if (!id) return;
			getStudy(id)
				.then((study) => {
					if (studyFor === id) studyName = study.name;
				})
				.catch(() => {});
			// The StudyConditions of the active plan, with the pre-registered baseline marked.
			getStudyResults(id)
				.then((res) => {
					if (studyFor !== id) return;
					baselineOptions = [...res.leaderboard]
						.sort((x, y) => x.index - y.index)
						.map((c) => ({
							id: c.condition_id,
							label: `${c.label || `Condition ${c.condition_id}`}${c.is_baseline ? ' (baseline)' : ''}`,
						}));
				})
				.catch(() => {});
			listCandidateRuns({ study_id: id, current_only: true, per_page: 1 })
				.then((res) => {
					if (studyFor === id) studyApps = res.total;
				})
				.catch(() => {});
		});
	});

	// ── live validation ───────────────────────────────────────────────
	type Validation = { status: 'idle' | 'checking' | 'ok' | 'invalid' | 'failed'; errors: PathError[]; message?: string };
	let validation = $state<Validation>({ status: 'idle', errors: [] });
	let validateTimer: ReturnType<typeof setTimeout> | undefined;
	let validateCtl: AbortController | null = null;
	const validationKey = $derived(JSON.stringify([draft.mode, draft.design, draft.settings]));

	$effect(() => {
		void validationKey;
		if (!ready) return;
		untrack(() => {
			clearTimeout(validateTimer);
			validateTimer = setTimeout(() => void runValidation(), 600);
		});
	});

	async function runValidation() {
		validateCtl?.abort();
		const mine = new AbortController();
		validateCtl = mine;
		validation = { ...validation, status: 'checking' };
		try {
			const res = await validateDesign(
				{ mode: draft.mode, design: $state.snapshot(draft.design), settings: $state.snapshot(draft.settings) },
				{ signal: mine.signal }
			);
			if (validateCtl !== mine) return;
			validation = { status: res.ok && res.errors.length === 0 ? 'ok' : 'invalid', errors: res.errors ?? [] };
		} catch (e) {
			if (isAbortError(e) && mine.signal.aborted) return;
			const body = humanEvalErrorBody(e);
			if (body && body.errors.length) {
				validation = { status: 'invalid', errors: body.errors };
			} else {
				validation = { status: 'failed', errors: [], message: formatApiError(e, 'Could not check the design.') };
			}
		}
	}

	onDestroy(() => {
		clearTimeout(validateTimer);
		clearTimeout(persistTimer);
		validateCtl?.abort();
	});

	const errorsByArea = $derived(splitErrors(validation.errors));
	const generalErrors = $derived(
		validation.errors.filter((e) => {
			const c = classifyPath(e.path ?? '');
			return c.area === 'general' && c.segments.length > 0;
		})
	);
	const designErrorCount = $derived(errorsByArea.design.length);
	const settingsErrorCount = $derived(errorsByArea.settings.length);

	function generalError(field: string): string {
		return generalErrors.find((e) => classifyPath(e.path).segments[0] === field)?.message ?? '';
	}

	// ── create ────────────────────────────────────────────────────────
	let creating = $state(false);
	const titleError = $derived(titleTouched && !draft.title.trim() ? 'Give the task a title.' : generalError('title'));
	const notes = $derived(draftNotes(draft));
	const appCountText = $derived.by(() => {
		const parts: string[] = [];
		if (draft.studyId) {
			parts.push(`${studyApps != null ? studyApps : 'all'} current app${studyApps === 1 ? '' : 's'} of ${studyName ? `“${studyName}”` : 'the linked experiment'}`);
		}
		if (draft.runIds.length) parts.push(`${draft.runIds.length} picked app${draft.runIds.length === 1 ? '' : 's'}`);
		return parts.length ? parts.join(' + ') : 'None yet';
	});

	function scrollTo(id: string) {
		document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
	}

	async function create() {
		titleTouched = true;
		if (!draft.title.trim()) {
			scrollTo('step-basics');
			toast.error('Give the task a title first.');
			return;
		}
		if (validation.status === 'invalid') {
			scrollTo(designErrorCount ? 'step-design' : settingsErrorCount ? 'step-settings' : 'step-review');
			toast.error('Fix the problems marked in the design or settings first.');
			return;
		}
		creating = true;
		try {
			const task = await createTask(createInput($state.snapshot(draft) as WizardDraft));
			persistEnabled = false;
			clearTimeout(persistTimer);
			clearStoredDraft();
			toast.success('Draft task created');
			await goto(`/human-eval/${task.id}`);
		} catch (e) {
			const body = humanEvalErrorBody(e);
			if (body && body.errors.length) validation = { status: 'invalid', errors: body.errors };
			toast.error(formatApiError(e, 'Could not create the task.'));
		} finally {
			creating = false;
		}
	}

	const modeItems = [
		{ value: 'single', label: 'Single app', title: 'Participants evaluate one app at a time' },
		{ value: 'pairwise', label: 'Pairwise (A/B)', title: 'Participants compare two apps built from the same spec' },
	];
</script>

{#snippet validationBadge()}
	{#if validation.status === 'checking'}
		<span class="inline-flex items-center gap-1 text-[11px] text-muted-foreground"><LoaderCircle class="size-3 animate-spin motion-reduce:animate-none" />Checking…</span>
	{:else if validation.status === 'ok'}
		<span class="inline-flex items-center gap-1 text-[11px] text-success-fg"><CircleCheck class="size-3" />Valid</span>
	{:else if validation.status === 'invalid'}
		<span class="inline-flex items-center gap-1 text-[11px] text-danger-fg"><CircleAlert class="size-3" />{validation.errors.length} problem{validation.errors.length === 1 ? '' : 's'}</span>
	{:else if validation.status === 'failed'}
		<span class="inline-flex items-center gap-1 text-[11px] text-warning-fg"><TriangleAlert class="size-3" />Not checked</span>
	{/if}
{/snippet}

<div class="space-y-4">
	<PageHeader
		title="New evaluation task"
		description="Pick a starting point, the apps and what participants do; the task is saved as a draft you can still change."
		back={{ href: '/human-eval', label: 'Human evaluation' }}
	>
		{#snippet meta()}{@render validationBadge()}{/snippet}
		{#snippet actions()}
			{#if ready && hasContent(draft)}
				<Button variant="outline" size="sm" onclick={() => (discardOpen = true)}><RotateCcw />Start over</Button>
			{/if}
			<Button size="sm" onclick={create} disabled={!ready || creating} aria-busy={creating}>
				{#if creating}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}Create draft
			</Button>
		{/snippet}
		<div class="header-stats">
			<span class="header-stat">{draft.mode === 'pairwise' ? 'Pairwise' : 'Single app'}</span>
			<span class="header-stat"><b>{draft.design.blocks.length}</b>block{draft.design.blocks.length === 1 ? '' : 's'}</span>
			<span class="header-stat"><b>{draft.runIds.length}</b>picked app{draft.runIds.length === 1 ? '' : 's'}{draft.studyId ? ' + linked experiment' : ''}</span>
			{#if restored}<span class="header-stat text-muted-foreground">Restored from this browser session</span>{/if}
		</div>
	</PageHeader>

	{#if loading && !ready}
		<LoadingState label="loading presets" />
	{:else}
		{#if environmentError}
			<ErrorAlert title="Environment unavailable" message="{environmentError} Default checklists and instrument wording are missing until it loads." onretry={loadCatalogue} />
		{/if}

		<div class="space-y-10 pt-2">
			<!-- 1 · Start -->
			<section id="step-start" class="scroll-mt-20 space-y-3" aria-labelledby="step-start-title">
				<SectionHeading id="step-start-title" number={1} title="Start from" lead="A preset fills the design and settings with a sensible study layout; everything stays editable." />
				{#if presetsError}
					<ErrorAlert message={presetsError} onretry={loadCatalogue} />
				{/if}
				<div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" role="group" aria-label="Starting point">
					{#each presets as preset (preset.slug)}
						{@const active = draft.start === preset.slug}
						<button
							type="button"
							class="card-lift flex cursor-pointer flex-col gap-2 rounded-md border p-3.5 text-left transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none motion-reduce:transition-none {active
								? 'border-primary bg-primary/10'
								: 'border-border bg-card hover:border-primary/40'}"
							aria-pressed={active}
							onclick={() => choose({ kind: 'preset', preset })}
						>
							<span class="flex items-start justify-between gap-2">
								<span class="font-display text-base leading-tight">{preset.name}</span>
								<span class="shrink-0 rounded border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground">{preset.mode === 'pairwise' ? 'Pairwise' : 'Single app'}</span>
							</span>
							<span class="text-xs text-muted-foreground">{preset.description}</span>
							<span class="mt-auto flex flex-wrap gap-1.5 pt-1">
								{#each preset.design.blocks as block (block.id)}
									{@const Icon = BLOCK_ICONS[block.type]}
									<span class="inline-flex items-center gap-1 rounded border border-border bg-surface-2 px-1.5 py-0.5 text-[11px]" title={BLOCK_TYPE_INFO[block.type]?.label}>
										{#if Icon}<Icon class="size-3 text-muted-foreground" aria-hidden="true" />{/if}{block.title}
									</span>
								{/each}
							</span>
						</button>
					{/each}
					<button
						type="button"
						class="card-lift flex cursor-pointer flex-col gap-2 rounded-md border border-dashed p-3.5 text-left transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none motion-reduce:transition-none {draft.start === ''
							? 'border-primary bg-primary/10'
							: 'border-border bg-card hover:border-primary/40'}"
						aria-pressed={draft.start === ''}
						onclick={() => choose({ kind: 'scratch' })}
					>
						<span class="flex items-center gap-2 font-display text-base leading-tight"><SquareDashed class="size-4 text-muted-foreground" aria-hidden="true" />From scratch</span>
						<span class="text-xs text-muted-foreground">No blocks; default settings. Add blocks in step 4.</span>
					</button>
				</div>
			</section>

			<!-- 2 · Basics -->
			<section id="step-basics" class="scroll-mt-20 space-y-3" aria-labelledby="step-basics-title">
				<SectionHeading id="step-basics-title" number={2} title="Basics" lead="What participants see first." />
				<Card.Root>
					<Card.Content class="space-y-4">
						<div class="grid gap-4 md:grid-cols-[minmax(0,1fr)_auto]">
							<div class="space-y-1.5">
								<Label class="text-xs" for="task-title">Title</Label>
								<Input
									id="task-title"
									bind:value={draft.title}
									maxlength={200}
									placeholder="e.g. Usability of generated todo apps"
									aria-invalid={titleError ? true : undefined}
									aria-describedby={titleError ? 'task-title-error' : undefined}
									onblur={() => (titleTouched = true)}
								/>
								{#if titleError}<p id="task-title-error" class="text-[11px] text-danger-fg">{titleError}</p>{/if}
							</div>
							<div class="space-y-1.5">
								<p class="text-xs font-medium" id="task-mode-label">Mode</p>
								<Segmented aria-labelledby="task-mode-label" value={draft.mode} onValueChange={setMode} items={modeItems} />
								{#if generalError('mode')}<p class="text-[11px] text-danger-fg">{generalError('mode')}</p>{/if}
							</div>
						</div>
						<div class="space-y-1.5">
							<Label class="text-xs" for="task-description">Short description <span class="text-muted-foreground">(shown on the join page)</span></Label>
							<Textarea id="task-description" rows={2} bind:value={draft.description} maxlength={2000} />
							{#if generalError('description')}<p class="text-[11px] text-danger-fg">{generalError('description')}</p>{/if}
						</div>
						<MarkdownField
							id="task-instructions"
							label="Instructions for participants (Markdown)"
							bind:value={draft.instructions}
							rows={7}
							placeholder="What the study is about, how to test the apps, how long it takes and whom to contact."
							hint="Shown on the task page and in the workbench. Changes after opening are recorded in the protocol log."
							error={generalError('instructions')}
						/>
					</Card.Content>
				</Card.Root>
			</section>

			<!-- 3 · Apps -->
			<section id="step-apps" class="scroll-mt-20 space-y-3" aria-labelledby="step-apps-title">
				<SectionHeading
					id="step-apps-title"
					number={3}
					title="App pool"
					count={appCountText}
					lead={draft.mode === 'pairwise'
						? 'Pairs are formed between apps built from the same spec. Participants only ever see blinded labels such as “App 07”.'
						: 'Completed app runs to evaluate. Participants only ever see blinded labels such as “App 07”.'}
				/>
				<AppPoolPicker bind:selected={draft.runIds} bind:studyId={draft.studyId} />
				{#if generalError('run_ids') || generalError('study_id')}
					<p class="text-[11px] text-danger-fg">{generalError('run_ids') || generalError('study_id')}</p>
				{/if}
			</section>

			<!-- 4 · Design -->
			<section id="step-design" class="scroll-mt-20 space-y-3" aria-labelledby="step-design-title">
				<SectionHeading id="step-design-title" number={4} title="Design" lead="The blocks, consent, questionnaires and the pre-registered analysis plan. Frozen when the task first opens.">
					{#snippet aside()}{@render validationBadge()}{/snippet}
				</SectionHeading>
				{#if ready}
					<DesignEditor
						mode={draft.mode}
						bind:design={draft.design}
						{environment}
						errors={validation.errors}
						allocationStrategy={draft.settings.allocation.strategy}
					/>
				{/if}
			</section>

			<!-- 5 · Settings -->
			<section id="step-settings" class="scroll-mt-20 space-y-3" aria-labelledby="step-settings-title">
				<SectionHeading id="step-settings-title" number={5} title="Settings" lead="Blinding, allocation, access, hosting and data quality." />
				{#if ready}
					<SettingsEditor mode={draft.mode} bind:settings={draft.settings} {baselineOptions} errors={validation.errors} />
				{/if}
			</section>

			<!-- 6 · Review -->
			<section id="step-review" class="scroll-mt-20 space-y-3" aria-labelledby="step-review-title">
				<SectionHeading id="step-review-title" number={6} title="Review and create" lead="The draft is private until you open it; you can still change everything." />
				<Card.Root>
					<Card.Content class="space-y-5">
						<dl class="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[10rem_minmax(0,1fr)]">
							<dt class="section-label pt-0.5">Task</dt>
							<dd>
								{#if draft.title.trim()}<span class="font-medium">{draft.title}</span>{:else}<span class="text-danger-fg">No title yet</span>{/if}
								<span class="text-muted-foreground"> · {draft.mode === 'pairwise' ? 'pairwise (A/B)' : 'single app'}</span>
							</dd>
							<dt class="section-label pt-0.5">Apps</dt>
							<dd>{appCountText}</dd>
							<dt class="section-label pt-0.5">Blocks</dt>
							<dd>
								{#if draft.design.blocks.length === 0}
									<span class="text-muted-foreground">None</span>
								{:else}
									<ol class="space-y-0.5">
										{#each draft.design.blocks as block, i (block.id)}
											{@const Icon = BLOCK_ICONS[block.type]}
											<li class="flex items-center gap-2">
												<span class="w-4 text-right font-mono text-[11px] tabular-nums text-muted-foreground">{i + 1}</span>
												{#if Icon}<Icon class="size-3.5 text-muted-foreground" aria-hidden="true" />{/if}
												<span>{block.title}</span>
												{#if BLOCK_TYPE_INFO[block.type]?.label !== block.title}<span class="text-xs text-muted-foreground">{BLOCK_TYPE_INFO[block.type]?.label}</span>{/if}
											</li>
										{/each}
									</ol>
								{/if}
								<p class="mt-1 text-xs text-muted-foreground">
									{draft.design.consent.enabled ? 'Consent required' : 'No consent step'} · {draft.design.pre_questions.length} question{draft.design.pre_questions.length === 1 ? '' : 's'} before · {draft.design.post_questions.length} after
								</p>
							</dd>
							<dt class="section-label pt-0.5">Analysis plan</dt>
							<dd>
								<ul class="space-y-0.5">
									{#each planSummary(draft.design, draft.settings.allocation.strategy) as line (line)}<li>{line}</li>{/each}
								</ul>
							</dd>
							<dt class="section-label pt-0.5">Allocation</dt>
							<dd>
								{draft.settings.allocation.strategy === 'balanced'
									? `Balanced, ${draft.settings.allocation.ratings_per_item} rating${draft.settings.allocation.ratings_per_item === 1 ? '' : 's'} per item`
									: 'Every participant rates every item'}
								{#if draft.settings.allocation.max_items_per_participant != null} · at most {draft.settings.allocation.max_items_per_participant} per participant{/if}
							</dd>
							<dt class="section-label pt-0.5">Access</dt>
							<dd>
								{[
									draft.settings.access.allow_invite_links && 'invite links',
									draft.settings.access.allow_access_codes && 'access codes',
									draft.settings.access.allow_accounts && 'evaluator accounts',
								]
									.filter(Boolean)
									.join(', ') || 'none'}
							</dd>
							<dt class="section-label pt-0.5">Hosting</dt>
							<dd>
								{draft.settings.hosting.isolation === 'exclusive' ? 'One participant per app at a time' : 'Shared apps'}
								· {draft.settings.hosting.reset === 'per_assignment' ? 'reset before each assignment' : 'state kept between participants'}
								· {draft.settings.hosting.on_demand ? 'started on demand' : 'started by you'}
							</dd>
						</dl>

						{#if validation.status === 'invalid'}
							<div class="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs" role="alert">
								<p class="flex items-center gap-1.5 font-medium text-danger-fg"><CircleAlert class="size-3.5" />Fix these before creating the draft</p>
								<ul class="mt-1 space-y-0.5">
									{#each validation.errors.slice(0, 12) as e, i (i)}
										{@const c = classifyPath(e.path ?? '')}
										<li>
											<span class="text-muted-foreground">
												{c.area === 'design' ? describeDesignPath(c.segments, draft.design) : c.area === 'settings' ? `Settings › ${c.segments.join(' › ').replace(/_/g, ' ')}` : c.segments.join(' › ') || 'Task'}:
											</span>
											{e.message}
										</li>
									{/each}
									{#if validation.errors.length > 12}<li class="text-muted-foreground">… and {validation.errors.length - 12} more</li>{/if}
								</ul>
								<div class="mt-2 flex flex-wrap gap-1.5">
									{#if designErrorCount}<Button variant="outline" size="sm" class="h-7 text-xs" onclick={() => scrollTo('step-design')}>Go to design</Button>{/if}
									{#if settingsErrorCount}<Button variant="outline" size="sm" class="h-7 text-xs" onclick={() => scrollTo('step-settings')}>Go to settings</Button>{/if}
								</div>
							</div>
						{:else if validation.status === 'failed'}
							<ErrorAlert title="Could not check the design" message={validation.message ?? ''} onretry={() => void runValidation()} />
						{/if}

						{#if notes.length > 0}
							<ul class="space-y-1.5 text-xs">
								{#each notes as n (n.text)}
									<li class="flex items-start gap-2 {n.level === 'warning' ? 'text-warning-fg' : 'text-muted-foreground'}">
										{#if n.level === 'warning'}<TriangleAlert class="mt-px size-3.5 shrink-0" aria-hidden="true" />{:else}<Info class="mt-px size-3.5 shrink-0" aria-hidden="true" />{/if}
										<span>{n.text}</span>
									</li>
								{/each}
							</ul>
						{/if}

						<div class="flex flex-wrap items-center justify-end gap-2 border-t border-border pt-4">
							{@render validationBadge()}
							<Button variant="outline" size="sm" href="/human-eval">Cancel</Button>
							<Button size="sm" onclick={create} disabled={!ready || creating} aria-busy={creating}>
								{#if creating}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}Create draft
							</Button>
						</div>
					</Card.Content>
				</Card.Root>
			</section>
		</div>
	{/if}
</div>

<ConfirmDialog
	bind:open={replaceOpen}
	title="Replace the design and settings?"
	description="Your changes to the blocks, questions, analysis plan and settings are replaced by the new starting point. Title, instructions and picked apps stay."
	confirmLabel="Replace"
	destructive
	onConfirm={confirmReplace}
/>

<ConfirmDialog
	bind:open={discardOpen}
	title="Start over?"
	description="The unsaved draft — title, instructions, apps, design and settings — is discarded."
	confirmLabel="Discard draft"
	destructive
	onConfirm={discard}
/>
