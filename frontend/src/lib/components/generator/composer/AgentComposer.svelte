<script lang="ts">
	/**
	 * AgentComposer — the primary mode: tasks × models × harness presets ×
	 * trials, each run by an agent harness in a sandbox. Sections on the left
	 * and the sticky Run summary (server preview) on the right once the composer
	 * is ≥ 56rem wide (a container query: the sidebar state matters, not just the
	 * viewport); narrower, the summary follows the form and a StartBar sticks
	 * to the bottom.
	 *
	 * Props:
	 * - `initial`: the draft to start from (stored draft, a job, or defaults).
	 * - `pins`: exact sampling / budget preset versions a job used (shown pinned).
	 * Every edit is persisted as the mode's session draft.
	 */
	import { onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import {
		createAgentBatch,
		getAgentConfig,
		previewAgentBatch,
		type AgentBatchRequest,
		type AgentConfigInfo,
		type AgentConfigOverrides,
		type AgentPreview,
		type BudgetPresetConfig,
		type HarnessOptionField,
		type HarnessPresetConfig,
		type LLMParams,
		type RunPreset,
		type SamplingPolicy,
	} from '$lib/api/generation';
	import { apiFieldErrors, formatApiError, isAbortError } from '$lib/api/core';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import { Segmented } from '$lib/components/ui/segmented';
	import { Textarea } from '$lib/components/ui/textarea';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import AppSpecPicker from '../AppSpecPicker.svelte';
	import ModelPicker from '../ModelPicker.svelte';
	import PresetPicker from '../PresetPicker.svelte';
	import StackSelect from '../StackSelect.svelte';
	import { cleanLLMParams } from '../llm-params';
	import AgentPreviewPanel from './AgentPreviewPanel.svelte';
	import BudgetFields from './BudgetFields.svelte';
	import ComposerSection from './ComposerSection.svelte';
	import HarnessPresetPicker from './HarnessPresetPicker.svelte';
	import NumberField from './NumberField.svelte';
	import SamplingSettings from './SamplingSettings.svelte';
	import SavePresetDialog from './SavePresetDialog.svelte';
	import StartBar from './StartBar.svelte';
	import {
		CONFIRM_RUNS_ABOVE,
		DEFAULT_TRIALS,
		MAX_SEED,
		MAX_TRIALS,
		cleanAgentOverrides,
		randomSeed,
		saveDraft,
		sortedIds,
		stableKey,
		type AgentDraft,
		type PresetPins,
		type TaskKind,
	} from './draft';
	import { previewIssues, type ChecklistItem, type IssueAction } from './issues';
	import { trackPresets } from './preset-lookup.svelte';
	import Dices from '@lucide/svelte/icons/dices';
	import Save from '@lucide/svelte/icons/save';

	interface Props {
		initial: AgentDraft;
		pins: PresetPins;
	}

	let { initial, pins }: Props = $props();

	const BUDGET_KEYS = ['wall_clock_seconds', 'max_llm_requests', 'max_total_tokens', 'max_cost_usd', 'network_policy'] as const;
	const PREVIEW_DEBOUNCE_MS = 400;

	// ── form state (seeded once from the initial draft) ─────────────────
	const init = untrack(() => initial);
	let taskKind = $state<TaskKind>(init.taskKind);
	let appIds = $state(new Set(init.appIds));
	let description = $state(init.description);
	let stackSlug = $state(init.stackSlug);
	let modelIds = $state(new Set(init.modelIds));
	let harnessPresetIds = $state<number[]>(init.harnessPresetIds);
	let samplingPresetId = $state<number | null>(init.samplingPresetId);
	let llmParams = $state<LLMParams>(init.llmParams);
	let samplingPolicy = $state<SamplingPolicy | undefined>(init.agentConfig.sampling_policy);
	let budgetPresetId = $state<number | null>(init.budgetPresetId);
	let budgetOverrides = $state<AgentConfigOverrides>(budgetOnly(init.agentConfig));
	let trials = $state<number | null>(init.trials);
	let baseSeed = $state<number | null>(init.baseSeed);

	let llmInvalid = $state(false);
	let budgetInvalid = $state(false);
	let trialsInvalid = $state(false);
	let seedInvalid = $state(false);
	let saveBudgetOpen = $state(false);

	function budgetOnly(config: AgentConfigOverrides): AgentConfigOverrides {
		const out: Record<string, unknown> = {};
		for (const key of BUDGET_KEYS) if (config[key] != null) out[key] = config[key];
		return out as AgentConfigOverrides;
	}

	// ── platform config + presets ───────────────────────────────────────
	let config = $state<AgentConfigInfo | null>(null);
	let configError = $state('');

	async function loadConfig() {
		configError = '';
		try {
			config = await getAgentConfig();
		} catch (e) {
			configError = formatApiError(e, 'Could not load the agent configuration.');
		}
	}

	onMount(() => {
		void loadConfig();
	});

	const harnessPresets = trackPresets('harness', () => harnessPresetIds);
	const samplingPresets = trackPresets('sampling', () => [samplingPresetId]);
	const budgetPresets = trackPresets('budget', () => [budgetPresetId]);

	const samplingPreset = $derived(samplingPresets.find(samplingPresetId) ?? null);
	const budgetPreset = $derived(budgetPresets.find(budgetPresetId) ?? null);
	const selectedHarnessPresets = $derived(
		harnessPresetIds.map((id) => harnessPresets.find(id)).filter((p): p is RunPreset<'harness'> => !!p)
	);
	const harnessSchemas = $derived(
		Object.fromEntries((config?.harnesses ?? []).map((h) => [h.name, h.option_schema])) as Record<
			string,
			HarnessOptionField[]
		>
	);
	const requireTools = $derived(
		selectedHarnessPresets.some(
			(p) => config?.harnesses.find((h) => h.name === (p.config as HarnessPresetConfig).harness)?.requires_tool_calling
		)
	);

	// Default harness preset: the default harness's system preset (at least one is always selected).
	$effect(() => {
		if (!config || !harnessPresets.loaded || harnessPresetIds.length > 0) return;
		const fallback = harnessPresets.systemDefault(config.defaults.harness) ?? harnessPresets.systemDefault();
		if (fallback) harnessPresetIds = [fallback.id];
	});

	// Draft ids that no longer exist (deleted, or not visible) are dropped.
	$effect(() => {
		const gone = harnessPresetIds.filter((id) => harnessPresets.isMissing(id));
		if (gone.length > 0) harnessPresetIds = harnessPresetIds.filter((id) => !gone.includes(id));
	});
	$effect(() => {
		if (samplingPresetId != null && samplingPresets.isMissing(samplingPresetId)) {
			samplingPresetId = samplingPresets.systemDefault()?.id ?? null;
		}
	});
	$effect(() => {
		if (budgetPresetId != null && budgetPresets.isMissing(budgetPresetId)) {
			budgetPresetId = budgetPresets.systemDefault()?.id ?? null;
		}
	});

	// ── inherited values (the layers below the inline overrides) ─────────
	const inheritedLLM = $derived.by(() => {
		const values: Record<string, unknown> = {};
		const sources: Record<string, string> = {};
		if (config) {
			values.max_tokens = config.default_max_output_tokens;
			sources.max_tokens = 'max_tokens';
		}
		for (const [key, value] of Object.entries(samplingPreset?.config.llm_params ?? {})) {
			if (value == null) continue;
			values[key] = value;
			sources[key] = 'preset';
		}
		return { values: values as LLMParams, sources };
	});

	const inheritedBudget = $derived.by(() => {
		const values: Record<string, unknown> = {};
		const sources: Record<string, string> = {};
		if (config) {
			for (const key of BUDGET_KEYS) {
				values[key] = config.defaults[key];
				sources[key] = 'default';
			}
		}
		for (const [key, value] of Object.entries(budgetPreset?.config ?? {})) {
			if (value == null) continue;
			values[key] = value;
			sources[key] = 'preset';
		}
		return { values, sources };
	});

	const inheritedPolicy = $derived(
		samplingPreset?.config.sampling_policy
			? { value: samplingPreset.config.sampling_policy, source: 'preset' }
			: config
				? { value: config.defaults.sampling_policy, source: 'default' }
				: null
	);

	const budgetSaveConfig = $derived.by((): BudgetPresetConfig => ({
		...((budgetPreset?.config ?? {}) as BudgetPresetConfig),
		...(budgetOnly(budgetOverrides) as BudgetPresetConfig),
	}));

	// ── request + readiness ─────────────────────────────────────────────
	const descriptionText = $derived(description.trim());
	const taskReady = $derived(taskKind === 'freeform' ? descriptionText.length > 0 : appIds.size > 0);
	const ready = $derived(taskReady && modelIds.size > 0 && harnessPresetIds.length > 0);
	const localInvalid = $derived(llmInvalid || budgetInvalid || trialsInvalid || seedInvalid || trials == null);

	const request = $derived.by((): AgentBatchRequest => {
		const body: AgentBatchRequest = {
			stack_slug: stackSlug || null,
			app_requirement_ids: taskKind === 'specs' ? sortedIds(appIds) : [],
			// With app specs the description is extra context; without any it would
			// silently become a free-form task, so it is only sent when meant.
			description: taskKind === 'freeform' || appIds.size > 0 ? descriptionText : '',
			model_ids: sortedIds(modelIds),
			harness_preset_ids: sortedIds(harnessPresetIds),
			sampling_preset_id: samplingPresetId,
			budget_preset_id: budgetPresetId,
			trials: trials ?? DEFAULT_TRIALS,
			base_seed: baseSeed,
		};
		const llm = cleanLLMParams(llmParams);
		if (Object.keys(llm).length > 0) body.llm_params = llm;
		const agent = cleanAgentOverrides({ ...budgetOverrides, sampling_policy: samplingPolicy });
		if (Object.keys(agent).length > 0) body.agent_config = agent;
		return body;
	});
	const requestKey = $derived(stableKey(request));

	// ── preview (debounced; superseded requests are aborted) ────────────
	let preview = $state<{ key: string; data: AgentPreview } | null>(null);
	let previewFailure = $state<{ key: string; message: string; fields: Record<string, string> } | null>(null);
	let previewLoading = $state(false);
	let previewNonce = $state(0);
	let fetchedNonce = 0;

	$effect(() => {
		const key = requestKey;
		const nonce = previewNonce;
		if (!ready || localInvalid) {
			previewLoading = false;
			return;
		}
		if (nonce === fetchedNonce && untrack(() => preview?.key === key)) {
			previewLoading = false;
			return;
		}
		const body = JSON.parse(key) as AgentBatchRequest;
		const controller = new AbortController();
		previewLoading = true;
		const timer = setTimeout(() => {
			fetchedNonce = nonce;
			previewAgentBatch(body, { signal: controller.signal })
				.then((data) => {
					preview = { key, data };
					previewFailure = null;
				})
				.catch((e: unknown) => {
					if (controller.signal.aborted || isAbortError(e)) return;
					previewFailure = { key, message: formatApiError(e, 'Preview failed.'), fields: apiFieldErrors(e) };
				})
				.finally(() => {
					if (!controller.signal.aborted) previewLoading = false;
				});
		}, PREVIEW_DEBOUNCE_MS);
		return () => {
			clearTimeout(timer);
			controller.abort();
		};
	});

	const currentPreview = $derived(preview?.key === requestKey ? preview.data : null);
	const stale = $derived(!!preview && preview.key !== requestKey);
	const failure = $derived(previewFailure?.key === requestKey ? previewFailure : null);
	const fieldErrors = $derived(failure?.fields ?? {});

	function scoped(prefix: string): Record<string, string> {
		return Object.fromEntries(
			Object.entries(fieldErrors)
				.filter(([key]) => key.startsWith(prefix))
				.map(([key, message]) => [key.slice(prefix.length), message])
		);
	}
	const llmErrors = $derived(scoped('llm_params.'));
	const budgetErrors = $derived(scoped('agent_config.'));

	const shownPreview = $derived(ready ? (preview?.data ?? null) : null);
	const modelNames = $derived(new Map((shownPreview?.models ?? []).map((m) => [m.model_id, m.name])));
	const issues = $derived(shownPreview ? previewIssues(shownPreview.warnings, (id) => modelNames.get(id)) : []);
	const errorCount = $derived(issues.filter((i) => i.severity === 'error').length);

	// ── start ───────────────────────────────────────────────────────────
	let submitting = $state(false);
	let submitError = $state('');
	let confirmOpen = $state(false);

	const canStart = $derived(
		ready && !localInvalid && !!currentPreview && !previewLoading && errorCount === 0 && !submitting
	);
	const blockedReason = $derived.by(() => {
		if (!taskReady) return taskKind === 'freeform' ? 'Describe the task first.' : 'Pick at least one app spec.';
		if (modelIds.size === 0) return 'Pick at least one model.';
		if (harnessPresetIds.length === 0) return 'Pick a harness preset.';
		if (localInvalid) return 'Fix the highlighted fields.';
		if (failure) return 'The preview failed — retry it in the Run summary.';
		if (!currentPreview || previewLoading) return 'Resolving the runs…';
		if (errorCount > 0) return `Resolve ${errorCount} error${errorCount === 1 ? '' : 's'} in the Run summary.`;
		return '';
	});
	const runCount = $derived(currentPreview?.run_count ?? null);
	const startLabel = $derived(runCount != null ? `Start ${runCount} run${runCount === 1 ? '' : 's'}` : 'Start');

	const confirmText = $derived(
		`This queues ${runCount ?? 0} agent runs (at most $${(currentPreview?.max_cost_usd_total ?? 0).toFixed(2)} in total). ` +
			'They run in the background; the batch page shows progress.'
	);

	function requestStart() {
		if (!canStart || !currentPreview) return;
		if (currentPreview.run_count > CONFIRM_RUNS_ABOVE) {
			confirmOpen = true;
			return;
		}
		void start();
	}

	async function start() {
		if (!canStart) return;
		const body = JSON.parse(requestKey) as AgentBatchRequest;
		submitting = true;
		submitError = '';
		try {
			const result = await createAgentBatch(body);
			confirmOpen = false;
			// A resubmitted form must not reuse the seed matrix of this batch.
			if (baseSeed != null) baseSeed = randomSeed();
			toast.success(`Batch started: ${result.job_count} run${result.job_count === 1 ? '' : 's'} queued`);
			await goto(`/sample-generator/batches/${result.batch_id}`);
		} catch (e) {
			confirmOpen = false;
			submitError = formatApiError(e, 'Could not start the batch.');
		} finally {
			submitting = false;
		}
	}

	const issueActions = $derived.by((): Record<string, IssueAction> => {
		const actions: Record<string, IssueAction> = {
			missing_api_key: { label: 'Add a key in Settings', href: '/users/settings#credentials' },
			single_trial: { label: `Use ${DEFAULT_TRIALS} trials`, onclick: () => (trials = DEFAULT_TRIALS) },
			random_seeds: { label: 'Use a base seed', onclick: () => (baseSeed = randomSeed()) },
		};
		const withoutTools = (shownPreview?.models ?? []).filter((m) => !m.tool_harness_ok).map((m) => m.model_id);
		if (withoutTools.length > 0) {
			actions.models_without_tools = {
				label: `Remove ${withoutTools.length} model${withoutTools.length === 1 ? '' : 's'} without tool calling`,
				onclick: () => (modelIds = new Set([...modelIds].filter((id) => !withoutTools.includes(id)))),
			};
		}
		return actions;
	});

	// A key added in another tab: re-check when the window regains focus.
	function onWindowFocus() {
		if (currentPreview?.warnings.some((w) => w.code === 'missing_api_key')) previewNonce += 1;
	}

	// ── checklist ───────────────────────────────────────────────────────
	const checklist = $derived<ChecklistItem[]>([
		{
			label: taskKind === 'freeform' ? 'Describe the free-form task' : 'Pick at least one app spec',
			done: taskReady,
			detail: taskKind === 'freeform' ? 'free-form (not graded)' : `${appIds.size} selected`,
			href: '#composer-task',
		},
		{ label: 'Pick at least one model', done: modelIds.size > 0, detail: `${modelIds.size} selected`, href: '#composer-models' },
		{
			label: 'Choose harness presets',
			done: harnessPresetIds.length > 0,
			detail: `${harnessPresetIds.length} selected`,
			href: '#composer-harnesses',
		},
	]);

	// ── draft persistence ───────────────────────────────────────────────
	$effect(() => {
		saveDraft('agent', {
			taskKind,
			appIds: sortedIds(appIds),
			description,
			stackSlug,
			modelIds: sortedIds(modelIds),
			harnessPresetIds: [...harnessPresetIds],
			samplingPresetId,
			llmParams: cleanLLMParams($state.snapshot(llmParams) as LLMParams),
			budgetPresetId,
			agentConfig: cleanAgentOverrides({ ...$state.snapshot(budgetOverrides), sampling_policy: samplingPolicy }),
			trials,
			baseSeed,
		});
	});

	const taskItems = $derived([
		{ value: 'specs', label: 'App specs', badge: appIds.size > 0 ? String(appIds.size) : undefined },
		{ value: 'freeform', label: 'Free-form task (not graded)' },
	]);
</script>

<svelte:window onfocus={onWindowFocus} />

<div class="@container/composer">
	<div class="grid items-start gap-4 @4xl/composer:grid-cols-[minmax(0,1fr)_22rem] @6xl/composer:grid-cols-[minmax(0,1fr)_26rem]">
		<div class="min-w-0 space-y-4">
			<ComposerSection
				step={1}
				id="composer-task"
				title="Task"
				description="What every run builds. App specs carry a hidden conformance suite, so their runs are graded."
			>
				<Segmented
					aria-label="Task type"
					size="sm"
					value={taskKind}
					items={taskItems}
					onValueChange={(v) => (taskKind = v as TaskKind)}
				/>
				{#if taskKind === 'specs'}
					<AppSpecPicker bind:selectedIds={appIds} idPrefix="agent-apps" label="" aria-label="App specs" />
					<div class="space-y-1.5">
						<Label for="agent-context">
							Additional context <span class="font-normal text-muted-foreground">(optional)</span>
						</Label>
						<Textarea
							id="agent-context"
							rows={2}
							bind:value={description}
							placeholder="Added to every app spec’s brief, e.g. constraints or conventions to follow."
							class="min-h-0"
							aria-describedby="agent-context-note"
						/>
						<p id="agent-context-note" class="text-[11px] text-muted-foreground">
							Every run sees the same text; the hidden test suite is unchanged.
						</p>
					</div>
				{:else}
					<div class="space-y-1.5">
						<Label for="agent-freeform">Task description</Label>
						<Textarea
							id="agent-freeform"
							rows={5}
							bind:value={description}
							placeholder="Describe the app to build: features, pages, data, API…"
							aria-describedby="agent-freeform-note"
						/>
						<p id="agent-freeform-note" class="text-[11px] text-warning-fg">
							Not graded: a free-form task has no hidden conformance suite, so runs report build and start-up status
							only. Use app specs for comparable scores.
						</p>
					</div>
				{/if}
				<StackSelect bind:value={stackSlug} defaultSlug={config?.default_stack_slug} id="agent-stack" />
			</ComposerSection>

			<ComposerSection
				step={2}
				id="composer-models"
				title="Models"
				description={requireTools
					? 'A selected harness needs native tool calling, so models without it are hidden.'
					: 'Every selected model runs every task with every harness preset.'}
			>
				<ModelPicker mode="multi" bind:selectedIds={modelIds} {requireTools} idPrefix="agent-models" label="" aria-label="Models" maxHeight="max-h-96" />
			</ComposerSection>

			<ComposerSection
				step={3}
				id="composer-harnesses"
				title="Harnesses"
				description="The comparison axis: each selected harness preset runs every model on every task."
			>
				{#if config}
					<HarnessPresetPicker harnesses={config.harnesses} presets={harnessPresets} bind:selectedIds={harnessPresetIds} />
				{:else if configError}
					<ErrorAlert message={configError} onretry={loadConfig} />
				{:else}
					<LoadingState label="loading harnesses" class="py-8" />
				{/if}
			</ComposerSection>

			<ComposerSection
				step={4}
				id="composer-sampling"
				title="Model settings"
				description="Sampling settings apply identically to every model; per-model support is shown in the Run summary."
			>
				<SamplingSettings
					mode="agent"
					bind:presetId={samplingPresetId}
					pinned={pins.sampling}
					preset={samplingPreset}
					bind:params={llmParams}
					inherited={inheritedLLM}
					bind:policy={samplingPolicy}
					{inheritedPolicy}
					errors={llmErrors}
					bind:invalid={llmInvalid}
				/>
			</ComposerSection>

			<ComposerSection
				step={5}
				id="composer-budget"
				title="Budget & repeats"
				description="Limits for each run, how often every cell repeats, and the seed shared across conditions."
			>
				<PresetPicker kind="budget" bind:selectedId={budgetPresetId} pinned={pins.budget} idPrefix="agent-budget-preset" />
				{#if config}
					<div class="space-y-2">
						<div class="flex flex-wrap items-center justify-between gap-2">
							<p class="section-label">Overrides for this batch</p>
							<Button
								variant="ghost"
								size="sm"
								class="h-7 px-2"
								disabled={Object.keys(budgetOnly(budgetOverrides)).length === 0 || budgetInvalid}
								onclick={() => (saveBudgetOpen = true)}
							>
								<Save />Save as preset…
							</Button>
						</div>
						<BudgetFields
							bind:value={budgetOverrides}
							inherited={inheritedBudget}
							limits={config.limits}
							networkPolicies={config.network_policies}
							idPrefix="agent-budget"
							errors={budgetErrors}
							bind:invalid={budgetInvalid}
						/>
					</div>
				{:else if configError}
					<ErrorAlert message={configError} onretry={loadConfig} />
				{/if}
				<div class="grid gap-3 border-t border-border/60 pt-4 @lg:grid-cols-2">
					<NumberField
						id="agent-trials"
						label="Trials per cell"
						bind:value={trials}
						min={1}
						max={MAX_TRIALS}
						required
						bind:invalid={trialsInvalid}
						error={fieldErrors.trials}
						hint="Repeats of every task × model × harness cell. Use 3 or more to estimate run-to-run variance."
					/>
					<NumberField
						id="agent-seed"
						label="Base seed"
						bind:value={baseSeed}
						min={0}
						max={MAX_SEED}
						placeholder="random per run"
						bind:invalid={seedInvalid}
						error={fieldErrors.base_seed}
						hint="One derived seed per task × trial, shared by every model and harness (paired comparison). Blank = random per run. Re-rolled after each start."
					>
						<Button
							variant="outline"
							size="icon"
							class="size-8 shrink-0"
							aria-label="Re-roll the base seed"
							title="Re-roll"
							onclick={() => (baseSeed = randomSeed())}
						>
							<Dices />
						</Button>
					</NumberField>
				</div>
			</ComposerSection>
		</div>

		<aside
			class="min-w-0 @4xl/composer:sticky @4xl/composer:top-[4.5rem] @4xl/composer:max-h-[calc(100dvh-5.5rem)] @4xl/composer:overflow-y-auto @4xl/composer:overscroll-contain"
			aria-label="Run summary"
		>
			<AgentPreviewPanel
				{checklist}
				{ready}
				preview={shownPreview}
				{stale}
				loading={previewLoading}
				error={failure?.message ?? ''}
				onretry={() => (previewNonce += 1)}
				{localInvalid}
				{baseSeed}
				{harnessSchemas}
				{issues}
				actions={issueActions}
				{canStart}
				{blockedReason}
				{submitting}
				{submitError}
				{startLabel}
				onstart={requestStart}
			/>
		</aside>
	</div>

	<StartBar
		runs={runCount}
		maxCost={currentPreview?.max_cost_usd_total ?? null}
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
	title="Start {runCount ?? 0} runs?"
	description={confirmText}
	confirmLabel="Start {runCount ?? 0} runs"
	busy={submitting}
	onConfirm={start}
/>

<SavePresetDialog
	bind:open={saveBudgetOpen}
	kind="budget"
	config={budgetSaveConfig}
	basedOn={budgetPreset}
	onsaved={(saved) => {
		budgetPresetId = saved.id;
		budgetOverrides = {};
	}}
/>
