<script lang="ts">
	/**
	 * ScaffoldingComposer — template scaffolding: prompt profiles fill a stack
	 * template in one pass per app spec × model × trial. The summary is a client
	 * tally, the profile/stack consistency check and the effective sampling
	 * params (`/jobs/llm-preview/`); success opens the batch page.
	 *
	 * Props: `initial` (draft), `pins` (sampling preset version from a job),
	 * `keyState` (OpenRouter key status: a missing key blocks the start).
	 */
	import { onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import {
		createScaffoldingBatch,
		getAgentConfig,
		getGenerationProfiles,
		type GenerationProfile,
		type LLMParams,
	} from '$lib/api/generation';
	import { apiFieldErrors, formatApiError } from '$lib/api/core';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import AppSpecPicker from '../AppSpecPicker.svelte';
	import ModelPicker from '../ModelPicker.svelte';
	import ProfilePicker from '../ProfilePicker.svelte';
	import StackSelect from '../StackSelect.svelte';
	import type { OpenRouterKeyState } from '../OpenRouterKeyBanner.svelte';
	import { cleanLLMParams } from '../llm-params';
	import ComposerSection from './ComposerSection.svelte';
	import NumberField from './NumberField.svelte';
	import SamplingSettings from './SamplingSettings.svelte';
	import SimpleSummaryPanel from './SimpleSummaryPanel.svelte';
	import StartBar from './StartBar.svelte';
	import {
		CONFIRM_RUNS_ABOVE,
		DEFAULT_TRIALS,
		MAX_TRIALS,
		saveDraft,
		sortedIds,
		type PresetPins,
		type ScaffoldingDraft,
	} from './draft';
	import type { ChecklistItem, IssueAction, SummaryIssue } from './issues';
	import { trackLLMPreview } from './llm-preview.svelte';
	import { trackPresets } from './preset-lookup.svelte';

	interface Props {
		initial: ScaffoldingDraft;
		pins: PresetPins;
		keyState: OpenRouterKeyState;
	}

	let { initial, pins, keyState }: Props = $props();

	const LARGE_BATCH = 50;

	const init = untrack(() => initial);
	let appIds = $state(new Set(init.appIds));
	let stackSlug = $state(init.stackSlug);
	let profileId = $state<number | null>(init.profileId);
	let modelIds = $state(new Set(init.modelIds));
	let samplingPresetId = $state<number | null>(init.samplingPresetId);
	let llmParams = $state<LLMParams>(init.llmParams);
	let trials = $state<number | null>(init.trials);
	let llmInvalid = $state(false);
	let trialsInvalid = $state(false);

	// ── supporting data ─────────────────────────────────────────────────
	let defaultStack = $state<string | undefined>(undefined);
	let profiles = $state<GenerationProfile[]>([]);
	let profilesLoaded = $state(false);
	let profilesError = $state('');

	async function loadProfiles() {
		profilesError = '';
		try {
			profiles = await getGenerationProfiles();
			profilesLoaded = true;
		} catch (e) {
			profilesError = formatApiError(e, 'Could not load prompt profiles.');
		}
	}

	onMount(() => {
		void loadProfiles();
		// Only the platform default stack is needed; a failure just leaves the choice to the user.
		getAgentConfig()
			.then((config) => (defaultStack = config.default_stack_slug))
			.catch(() => {});
	});

	const samplingPresets = trackPresets('sampling', () => [samplingPresetId]);
	const samplingPreset = $derived(samplingPresets.find(samplingPresetId) ?? null);
	$effect(() => {
		// A draft's preset that no longer exists: back to the mode default (no preset).
		if (samplingPresetId != null && samplingPresets.isMissing(samplingPresetId)) samplingPresetId = null;
	});

	const profile = $derived(profileId != null ? profiles.find((p) => p.id === profileId) : undefined);
	$effect(() => {
		// A draft's profile that no longer exists falls back to Auto.
		if (profilesLoaded && profileId != null && !profiles.some((p) => p.id === profileId)) profileId = null;
	});

	// ── effective params ────────────────────────────────────────────────
	const llm = trackLLMPreview(() => ({
		mode: 'scaffolding',
		profile_id: profileId,
		sampling_preset_id: samplingPresetId,
		llm_params: cleanLLMParams(llmParams),
	}));
	const inheritedLLM = $derived.by(() => {
		const result = llm.result;
		const values: Record<string, unknown> = {};
		const sources: Record<string, string> = {};
		for (const [key, value] of Object.entries(result?.effective ?? {})) {
			const source = result?.sources[key];
			if (value == null || source === 'inline') continue;
			values[key] = value;
			if (source) sources[key] = source;
		}
		return { values: values as LLMParams, sources };
	});

	// ── tally + checks ──────────────────────────────────────────────────
	const runCount = $derived(appIds.size * modelIds.size * (trials ?? 0));
	const stackMismatch = $derived(!!profile && !!stackSlug && profile.scaffolding_slug !== stackSlug);
	// Auto resolves per app: the app's pilot profile for the stack, else the
	// stack's default or system profile. Without any, the server falls back to
	// another stack's prompts and refuses the batch, so say so before Start.
	const autoUnavailable = $derived(
		profileId == null &&
			profilesLoaded &&
			!!stackSlug &&
			!profiles.some(
				(p) =>
					!p.is_archived &&
					p.scaffolding_slug === stackSlug &&
					(p.is_default || p.slug.startsWith('system-') || p.slug.startsWith('app-'))
			)
	);
	const ready = $derived(appIds.size > 0 && modelIds.size > 0 && !!stackSlug);
	const localInvalid = $derived(llmInvalid || trialsInvalid || trials == null);

	const issues = $derived.by((): SummaryIssue[] => {
		const out: SummaryIssue[] = [];
		if (stackMismatch && profile) {
			out.push({
				code: 'profile_stack_mismatch',
				severity: 'error',
				detail: `${profile.name} targets ${profile.scaffolding_slug}, but the stack is ${stackSlug}. Scaffolding rejects mismatched runs.`,
			});
		}
		if (autoUnavailable) {
			out.push({
				code: 'no_profile_for_stack',
				severity: 'error',
				detail: `No prompt profile targets ${stackSlug}, so Auto would use another stack's prompts. Pick a stack that has a profile, or create one for ${stackSlug} in the Library.`,
			});
		}
		if (keyState === 'missing') {
			out.push({ code: 'missing_api_key', severity: 'error', detail: 'Runs call models with your OpenRouter key and none is configured.' });
		} else if (keyState === 'invalid') {
			out.push({ code: 'missing_api_key', severity: 'warn', detail: 'Your OpenRouter key was rejected at its last validation; runs may fail.' });
		}
		if (runCount > LARGE_BATCH) {
			out.push({ code: 'large_batch', severity: 'warn', detail: `${runCount} runs in one batch.` });
		}
		if (profileId == null && appIds.size > 0 && !autoUnavailable) {
			out.push({
				code: 'auto_profile',
				severity: 'info',
				detail: 'Auto picks a profile per app (app pilot, else the stack default); each may add its own LLM defaults.',
			});
		}
		if (trials === 1) {
			out.push({ code: 'single_trial', severity: 'info', detail: 'One trial per cell: no estimate of run-to-run variance.' });
		}
		return out;
	});
	const errorCount = $derived(issues.filter((i) => i.severity === 'error').length);

	const actions = $derived.by((): Record<string, IssueAction> => {
		const out: Record<string, IssueAction> = {
			missing_api_key: { label: 'Open Settings', href: '/users/settings#credentials' },
			single_trial: { label: `Use ${DEFAULT_TRIALS} trials`, onclick: () => (trials = DEFAULT_TRIALS) },
		};
		if (profile) {
			const target = profile.scaffolding_slug;
			out.profile_stack_mismatch = { label: `Use the ${target} stack`, onclick: () => (stackSlug = target) };
		}
		if (defaultStack && defaultStack !== stackSlug) {
			const target = defaultStack;
			out.no_profile_for_stack = { label: `Use the ${target} stack`, onclick: () => (stackSlug = target) };
		}
		return out;
	});

	// ── start ───────────────────────────────────────────────────────────
	let submitting = $state(false);
	let submitError = $state('');
	let confirmOpen = $state(false);
	let serverFieldErrors = $state<Record<string, string>>({});

	const canStart = $derived(ready && !localInvalid && errorCount === 0 && !submitting);
	const blockedReason = $derived.by(() => {
		if (appIds.size === 0) return 'Pick at least one app spec.';
		if (!stackSlug) return 'Choose a stack.';
		if (modelIds.size === 0) return 'Pick at least one model.';
		if (localInvalid) return 'Fix the highlighted fields.';
		if (errorCount > 0) return `Resolve ${errorCount} error${errorCount === 1 ? '' : 's'} in the Run summary.`;
		return '';
	});
	const startLabel = $derived(ready && runCount > 0 ? `Start ${runCount} run${runCount === 1 ? '' : 's'}` : 'Start');

	function requestStart() {
		if (!canStart) return;
		if (runCount > CONFIRM_RUNS_ABOVE) {
			confirmOpen = true;
			return;
		}
		void start();
	}

	async function start() {
		if (!canStart || trials == null) return;
		submitting = true;
		submitError = '';
		serverFieldErrors = {};
		try {
			const llmOverrides = cleanLLMParams(llmParams);
			const result = await createScaffoldingBatch({
				stack_slug: stackSlug,
				app_requirement_ids: sortedIds(appIds),
				model_ids: sortedIds(modelIds),
				profile_id: profileId,
				sampling_preset_id: samplingPresetId,
				...(Object.keys(llmOverrides).length > 0 ? { llm_params: llmOverrides } : {}),
				trials,
			});
			confirmOpen = false;
			toast.success(`Batch started: ${result.job_count} run${result.job_count === 1 ? '' : 's'} queued`);
			await goto(`/sample-generator/batches/${result.batch_id}`);
		} catch (e) {
			confirmOpen = false;
			submitError = formatApiError(e, 'Could not start the batch.');
			serverFieldErrors = apiFieldErrors(e);
		} finally {
			submitting = false;
		}
	}

	const llmErrors = $derived(
		Object.fromEntries(
			Object.entries(serverFieldErrors)
				.filter(([key]) => key.startsWith('llm_params.'))
				.map(([key, message]) => [key.slice('llm_params.'.length), message])
		)
	);

	const checklist = $derived<ChecklistItem[]>([
		{ label: 'Pick at least one app spec', done: appIds.size > 0, detail: `${appIds.size} selected`, href: '#scaffolding-task' },
		{ label: 'Choose a stack', done: !!stackSlug, detail: stackSlug, href: '#scaffolding-task' },
		{ label: 'Pick at least one model', done: modelIds.size > 0, detail: `${modelIds.size} selected`, href: '#scaffolding-models' },
	]);

	$effect(() => {
		saveDraft('scaffolding', {
			appIds: sortedIds(appIds),
			stackSlug,
			profileId,
			modelIds: sortedIds(modelIds),
			samplingPresetId,
			llmParams: cleanLLMParams($state.snapshot(llmParams) as LLMParams),
			trials,
		});
	});
</script>

<div class="@container/composer">
	<div class="grid items-start gap-4 @4xl/composer:grid-cols-[minmax(0,1fr)_22rem] @6xl/composer:grid-cols-[minmax(0,1fr)_26rem]">
		<div class="min-w-0 space-y-4">
			<ComposerSection
				step={1}
				id="scaffolding-task"
				title="Task & prompts"
				description="App specs to build, the stack template, and the prompt profile that fills it."
			>
				<AppSpecPicker bind:selectedIds={appIds} idPrefix="scaffolding-apps" />
				<div class="grid items-start gap-4 @xl:grid-cols-2">
					<StackSelect bind:value={stackSlug} defaultSlug={defaultStack} id="scaffolding-stack" />
					{#if profilesLoaded}
						<ProfilePicker
							bind:selectedId={profileId}
							{profiles}
							appIds={[...appIds]}
							{stackSlug}
							idPrefix="scaffolding-profile"
							onchange={(picked) => {
								// A profile belongs to one stack; follow it so the pair stays valid.
								if (picked) stackSlug = picked.scaffolding_slug;
							}}
						/>
					{:else if profilesError}
						<ErrorAlert message={profilesError} onretry={loadProfiles} />
					{:else}
						<LoadingState label="loading profiles" class="py-6" />
					{/if}
				</div>
			</ComposerSection>

			<ComposerSection
				step={2}
				id="scaffolding-models"
				title="Models"
				description="Every selected model scaffolds every app spec."
			>
				<ModelPicker mode="multi" bind:selectedIds={modelIds} idPrefix="scaffolding-models" label="" aria-label="Models" maxHeight="max-h-96" />
			</ComposerSection>

			<ComposerSection
				step={3}
				id="scaffolding-sampling"
				title="Model settings"
				description="Sampling settings apply identically to every model."
			>
				<SamplingSettings
					mode="scaffolding"
					bind:presetId={samplingPresetId}
					pinned={pins.sampling}
					preset={samplingPreset}
					bind:params={llmParams}
					inherited={inheritedLLM}
					errors={llmErrors}
					bind:invalid={llmInvalid}
				/>
			</ComposerSection>

			<ComposerSection step={4} id="scaffolding-repeats" title="Repeats" description="How often every app spec × model cell runs.">
				<NumberField
					id="scaffolding-trials"
					label="Trials per cell"
					class="sm:max-w-xs"
					bind:value={trials}
					min={1}
					max={MAX_TRIALS}
					required
					bind:invalid={trialsInvalid}
					error={serverFieldErrors.trials}
					hint="Use 3 or more to estimate run-to-run variance."
				/>
			</ComposerSection>
		</div>

		<aside
			class="min-w-0 @4xl/composer:sticky @4xl/composer:top-[4.5rem] @4xl/composer:max-h-[calc(100dvh-5.5rem)] @4xl/composer:overflow-y-auto @4xl/composer:overscroll-contain"
			aria-label="Run summary"
		>
			<SimpleSummaryPanel
				{checklist}
				{ready}
				{llm}
				llmNote="Base layer: the mode default (temperature 0.3, output cap 32,000), then the profile, the sampling preset and your overrides."
				{issues}
				{actions}
				{canStart}
				{blockedReason}
				{submitting}
				{submitError}
				{startLabel}
				onstart={requestStart}
			>
				<div class="rounded-md border border-border bg-surface-2 p-3" aria-live="polite">
					<p class="flex items-baseline gap-2">
						<span class="font-display text-2xl font-semibold tabular-nums">{runCount}</span>
						<span class="text-sm text-muted-foreground">run{runCount === 1 ? '' : 's'}</span>
					</p>
					<p class="text-xs text-muted-foreground tabular-nums">
						{appIds.size} app spec{appIds.size === 1 ? '' : 's'} × {modelIds.size} model{modelIds.size === 1 ? '' : 's'} ×
						{trials ?? '—'} trial{trials === 1 ? '' : 's'}
					</p>
					<dl class="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
						<dt class="text-muted-foreground">Stack</dt>
						<dd class="font-mono">{stackSlug || '—'}</dd>
						<dt class="text-muted-foreground">Profile</dt>
						<dd class="min-w-0 break-words">
							{#if profile}{profile.name} <span class="font-mono text-muted-foreground">v{profile.version}</span>{:else}Auto (per app){/if}
						</dd>
					</dl>
				</div>
			</SimpleSummaryPanel>
		</aside>
	</div>

	<StartBar
		runs={ready ? runCount : null}
		errors={errorCount}
		label={startLabel}
		{canStart}
		busy={submitting}
		reason={blockedReason}
		summaryHref="#run-summary"
		onstart={requestStart}
	/>
</div>

<ConfirmDialog
	bind:open={confirmOpen}
	title="Start {runCount} runs?"
	description="This queues {runCount} scaffolding runs. They run in the background; the batch page shows progress."
	confirmLabel="Start {runCount} runs"
	busy={submitting}
	onConfirm={start}
/>
