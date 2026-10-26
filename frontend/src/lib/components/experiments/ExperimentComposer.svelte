<script lang="ts">
	/**
	 * ExperimentComposer — the whole experiment design on one page: basics,
	 * method, models (grouped by endpoint) × harness presets (agent) or prompt
	 * profiles (scaffolding), a pre-registered baseline, apps, replication,
	 * sampling and budget, ablations, execution and automation. The sticky
	 * PlanRail shows the server's plan (per-endpoint load, est. hours, issues)
	 * once the composer is ≥ 56rem wide; narrower, it follows the form and a
	 * StartBar sticks to the bottom.
	 *
	 * Props:
	 * - `mode`: 'create' (POST with conditions, optionally launching) |
	 *   'edit' (a draft: PATCH + PUT conditions).
	 * - `initial`: the draft to start from (session draft, clone, edit, defaults).
	 * - `experimentId`: the draft being edited (edit mode).
	 * Create mode persists every edit as the session draft (`experiment-composer:v2`).
	 */
	import { onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import {
		getAgentConfig,
		getGenerationProfiles,
		type AgentConfigInfo,
		type AgentConfigOverrides,
		type GenerationProfile,
		type HarnessPresetConfig,
		type LLMParams,
		type RunPreset,
	} from '$lib/api/generation';
	import type { LLMModelSummary } from '$lib/api/models';
	import { listEndpoints, type EndpointSummary } from '$lib/api/inference';
	import {
		createExperiment,
		createSchedule,
		experimentErrorBody,
		launchExperiment,
		previewDraft,
		putConditions,
		updateExperiment,
		type ExperimentCreateV2,
		type ExperimentPreviewV2,
		type Issue,
	} from '$lib/api/experiments';
	import { apiFieldErrors, formatApiError, isAbortError } from '$lib/api/core';
	import { getAuth } from '$lib/stores/auth.svelte';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Segmented } from '$lib/components/ui/segmented';
	import * as Select from '$lib/components/ui/select';
	import * as Sheet from '$lib/components/ui/sheet';
	import { Textarea } from '$lib/components/ui/textarea';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import {
		AppSpecPicker,
		LLMParamsEditor,
		ModelPicker,
		PresetPicker,
		ProfilePicker,
		StackSelect,
		cleanLLMParams,
		summarizeLLMParams,
		watchCatalog,
	} from '$lib/components/generator';
	import BudgetFields from '$lib/components/generator/composer/BudgetFields.svelte';
	import ComposerSection from '$lib/components/generator/composer/ComposerSection.svelte';
	import HarnessPresetPicker from '$lib/components/generator/composer/HarnessPresetPicker.svelte';
	import NumberField from '$lib/components/generator/composer/NumberField.svelte';
	import SamplingSettings, { isNoopSamplingPreset } from '$lib/components/generator/composer/SamplingSettings.svelte';
	import StartBar from '$lib/components/generator/composer/StartBar.svelte';
	import { stableKey } from '$lib/components/generator/composer/draft';
	import { trackPresets } from '$lib/components/generator/composer/preset-lookup.svelte';
	import { trackLLMPreview } from '$lib/components/generator/composer/llm-preview.svelte';
	import {
		presetLookupIssue,
		type ChecklistItem,
		type IssueAction,
		type SummaryIssue,
	} from '$lib/components/generator/composer/issues';
	import Bot from '@lucide/svelte/icons/bot';
	import Dices from '@lucide/svelte/icons/dices';
	import FileCode from '@lucide/svelte/icons/file-code';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Plus from '@lucide/svelte/icons/plus';
	import Save from '@lucide/svelte/icons/save';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import AutomationSettings from './AutomationSettings.svelte';
	import ComposerSectionNav, { type ComposerNavSection } from './ComposerSectionNav.svelte';
	import ExecutionSettings from './ExecutionSettings.svelte';
	import PlanRail from './PlanRail.svelte';
	import SelectionSummary from './SelectionSummary.svelte';
	import {
		MAX_REPEATS,
		MAX_SEED,
		ablationReady,
		balanceAcrossEndpoints,
		buildCreateBody,
		clearComposerDraft,
		crossConditions,
		defaultComposerDraft,
		defaultPostRun,
		draftSlug,
		effectiveBaseline,
		endpointKeyOf,
		isLocal,
		loadInfoFromPreview,
		randomSeed,
		saveComposerDraft,
		type AblationDraft,
		type ComposerMode,
		type ExecutionDraft,
		type ExperimentDraft,
	} from './composer';
	import { formatHours, plural, policyLabel } from './format';

	interface Props {
		mode?: 'create' | 'edit';
		initial?: ExperimentDraft;
		experimentId?: string;
	}

	let { mode: composerMode = 'create', initial, experimentId }: Props = $props();

	const PREVIEW_DEBOUNCE_MS = 600;
	const INFO_CODES = new Set(['block_grouped', 'same_digest_multiple_endpoints', 'digest_unknown']);

	// ── form state (seeded once) ────────────────────────────────────────
	const init = untrack(() => initial ?? defaultComposerDraft());
	let name = $state(init.name);
	let slugInput = $state(init.slug);
	let slugTouched = $state(init.slugTouched);
	let hypothesis = $state(init.hypothesis);
	let description = $state(init.description);
	let method = $state<ComposerMode>(init.mode);
	let stackSlug = $state(init.stackSlug);
	let modelIds = $state(new Set(init.modelIds));
	let harnessPresetIds = $state<number[]>(init.harnessPresetIds);
	let profileIds = $state<number[]>(init.profileIds);
	let baseline = $state<string | null>(init.baseline);
	let appIds = $state(new Set(init.appIds));
	let repeats = $state<number | null>(init.repeats);
	let baseSeed = $state<number | null>(init.baseSeed);
	let samplingPresetId = $state<number | null>(init.samplingPresetId);
	let llmParams = $state<LLMParams>(init.llmParams);
	let budgetPresetId = $state<number | null>(init.budgetPresetId);
	let budgetOverrides = $state<AgentConfigOverrides>(init.budgetOverrides);
	let ablations = $state<AblationDraft[]>(init.ablations);
	let execution = $state<ExecutionDraft>(init.execution);
	let postRun = $state(init.postRun);
	let schedule = $state(init.schedule);

	let llmInvalid = $state(false);
	let budgetInvalid = $state(false);
	let repeatsInvalid = $state(false);
	let seedInvalid = $state(false);
	let executionInvalid = $state(false);
	let scheduleInvalid = $state(false);

	const slug = $derived(draftSlug({ name, slug: slugInput, slugTouched }));
	const isAgent = $derived(method === 'agent');

	function setMethod(next: ComposerMode) {
		if (next === method) return;
		method = next;
		// Evaluation defaults on for scaffolding (agent runs are graded inline).
		postRun = { ...postRun, evaluate: next === 'scaffolding' ? true : defaultPostRun(next).evaluate };
		baseline = null;
	}

	// ── catalogs ────────────────────────────────────────────────────────
	let config = $state<AgentConfigInfo | null>(null);
	let configError = $state('');
	let catalog = $state<LLMModelSummary[]>([]);
	let profiles = $state<GenerationProfile[]>([]);
	let endpoints = $state<EndpointSummary[]>([]);
	const auth = getAuth();

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
		getGenerationProfiles()
			.then((list) => (profiles = list))
			.catch(() => {});
		listEndpoints()
			.then((list) => (endpoints = list))
			.catch(() => {});
		return watchCatalog('models', (list) => (catalog = list));
	});

	const byId = $derived(new Map(catalog.map((m) => [m.id, m])));
	const harnessPresets = trackPresets('harness', () => [...harnessPresetIds, ...ablations.map((a) => a.harness_preset_id)]);
	const samplingPresets = trackPresets('sampling', () => [samplingPresetId]);
	const budgetPresets = trackPresets('budget', () => [budgetPresetId]);
	const samplingPreset = $derived(samplingPresets.find(samplingPresetId) ?? null);
	const budgetPreset = $derived(budgetPresets.find(budgetPresetId) ?? null);

	const requireTools = $derived(
		isAgent &&
			harnessPresetIds.some((id) => {
				const preset = harnessPresets.find(id);
				if (!preset) return false;
				const harness = (preset.config as HarnessPresetConfig).harness;
				return config?.harnesses.find((h) => h.name === harness)?.requires_tool_calling ?? true;
			})
	);

	// Default harness preset in a fresh agent draft: the default harness's system preset.
	$effect(() => {
		if (!isAgent || !config || !harnessPresets.loaded || harnessPresetIds.length > 0) return;
		if (composerMode === 'edit') return;
		const fallback = harnessPresets.systemDefault(config.defaults.harness) ?? harnessPresets.systemDefault();
		if (fallback) harnessPresetIds = [fallback.id];
	});
	$effect(() => {
		const gone = harnessPresetIds.filter((id) => harnessPresets.isMissing(id));
		if (gone.length > 0) harnessPresetIds = harnessPresetIds.filter((id) => !gone.includes(id));
	});

	// ── labels ──────────────────────────────────────────────────────────
	function modelName(id: number | null | undefined): string {
		if (id == null) return '—';
		return byId.get(id)?.model_name ?? `model #${id}`;
	}
	function presetName(id: number | null | undefined): string {
		if (id == null) return '—';
		const p = harnessPresets.find(id);
		return p ? `${p.name}` : `preset #${id}`;
	}
	function profileName(id: number | null | undefined): string {
		if (id == null) return '—';
		const p = profiles.find((x) => x.id === id);
		return p ? `${p.name} v${p.version}` : `profile #${id}`;
	}
	function axisName(c: { harness_preset_id?: number; profile_id?: number }): string {
		return c.harness_preset_id != null ? presetName(c.harness_preset_id) : profileName(c.profile_id);
	}

	// ── conditions ──────────────────────────────────────────────────────
	const axisIds = $derived(isAgent ? harnessPresetIds : profileIds);
	const cross = $derived(
		crossConditions({ mode: method, modelIds: [...modelIds], harnessPresetIds, profileIds })
	);
	const readyAblations = $derived(ablations.filter((a) => ablationReady(a, method)));
	const conditionCount = $derived(cross.length + readyAblations.length);
	const baselineChoice = $derived(
		effectiveBaseline({ mode: method, modelIds: [...modelIds], harnessPresetIds, profileIds, baseline })
	);
	const baselineItems = $derived(
		cross.map((c) => ({ value: c.key, label: `${modelName(c.model_id)} · ${axisName(c)}` }))
	);

	// ── selection by endpoint, ownership, balance ───────────────────────
	const endpointBySlug = $derived(new Map(endpoints.map((e) => [e.slug, e])));
	const selectedModels = $derived([...modelIds].map((id) => byId.get(id)).filter((m): m is LLMModelSummary => !!m));
	const allLocal = $derived(selectedModels.length > 0 && selectedModels.every((m) => isLocal(m)));
	const anyOpenRouter = $derived(selectedModels.some((m) => !isLocal(m)));
	const hasOllama = $derived(selectedModels.some((m) => isLocal(m) && m.endpoint?.kind === 'ollama'));

	const foreignOwners = $derived.by(() => {
		const me = auth.user?.id;
		const owners = new Map<string, number>();
		for (const m of selectedModels) {
			if (!isLocal(m)) continue;
			const ep = endpointBySlug.get(endpointKeyOf(m));
			if (!ep || ep.is_system || !ep.owner || ep.owner.id === me) continue;
			owners.set(ep.owner.username, (owners.get(ep.owner.username) ?? 0) + 1);
		}
		return [...owners.entries()];
	});
	const foreignKey = $derived(foreignOwners.map(([o, n]) => `${o}:${n}`).join(','));
	let consent = $state(false);
	$effect(() => {
		void foreignKey;
		consent = false;
	});

	const canBalance = $derived.by(() => {
		const digests = new Map<string, Set<string>>();
		for (const m of catalog) {
			if (!isLocal(m) || !m.digest || m.is_available === false || !m.endpoint) continue;
			const set = digests.get(m.digest) ?? new Set();
			set.add(m.endpoint.slug);
			digests.set(m.digest, set);
		}
		return selectedModels.some((m) => isLocal(m) && !!m.digest && (digests.get(m.digest)?.size ?? 0) > 1);
	});

	function balance() {
		const capacities = new Map(
			endpoints
				.filter((e) => !e.is_system)
				.map((e) => [e.slug, Math.max(1, Math.min(e.max_concurrency, e.max_active_jobs_effective?.[isAgent ? 'sandbox' : 'llm'] ?? e.max_concurrency))])
		);
		const result = balanceAcrossEndpoints([...modelIds], catalog, loadInfoFromPreview(shownPreview, capacities));
		if (result.changed === 0) {
			toast.info('Already balanced: no model would finish sooner elsewhere.');
			return;
		}
		modelIds = new Set(result.ids);
		const load = Object.entries(result.load)
			.filter(([slug]) => slug !== 'openrouter')
			.map(([slug, h]) => `${slug} ${h.toFixed(1)}`)
			.join(' · ');
		toast.success(`Moved ${plural(result.changed, 'model')} to balance the load (${load}).`);
	}

	const toolHint = $derived.by(() => {
		if (!requireTools) return '';
		const hidden = catalog.filter((m) => isLocal(m) && !m.supports_function_calling).length;
		if (hidden === 0) return '';
		return `${plural(hidden, 'local model')} without tool calling ${hidden === 1 ? 'is' : 'are'} hidden by the selected harness. Use a harness that drives models without tools (e.g. aider), or mark tool support on the endpoint’s Models tab.`;
	});

	// ── inherited LLM values ────────────────────────────────────────────
	const scaffoldingLLM = trackLLMPreview(() =>
		method === 'scaffolding'
			? {
					mode: 'scaffolding',
					profile_id: profileIds.length === 1 ? profileIds[0] : null,
					sampling_preset_id: samplingPresetId,
					llm_params: cleanLLMParams(llmParams),
				}
			: null
	);
	const inheritedLLM = $derived.by(() => {
		const values: Record<string, unknown> = {};
		const sources: Record<string, string> = {};
		if (method === 'scaffolding') {
			const result = scaffoldingLLM.result;
			for (const [key, value] of Object.entries(result?.effective ?? {})) {
				const source = result?.sources[key];
				if (value == null || source === 'inline') continue;
				values[key] = value;
				if (source) sources[key] = source;
			}
		} else {
			if (config) {
				values.max_tokens = config.default_max_output_tokens;
				sources.max_tokens = 'max_tokens';
			}
			for (const [key, value] of Object.entries(samplingPreset?.config.llm_params ?? {})) {
				if (value == null) continue;
				values[key] = value;
				sources[key] = 'preset';
			}
		}
		return { values: values as LLMParams, sources };
	});
	const inheritedBudget = $derived.by(() => {
		const values: Record<string, unknown> = {};
		const sources: Record<string, string> = {};
		if (config) {
			for (const key of ['wall_clock_seconds', 'max_llm_requests', 'max_total_tokens', 'max_cost_usd', 'network_policy'] as const) {
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

	// ── draft + body ────────────────────────────────────────────────────
	const draft = $derived<ExperimentDraft>({
		name,
		slug: slugInput,
		slugTouched,
		hypothesis,
		description,
		mode: method,
		stackSlug,
		modelIds: [...modelIds],
		harnessPresetIds: [...harnessPresetIds],
		profileIds: [...profileIds],
		baseline,
		appIds: [...appIds],
		repeats,
		baseSeed,
		samplingPresetId,
		llmParams: cleanLLMParams($state.snapshot(llmParams) as LLMParams),
		budgetPresetId,
		budgetOverrides: $state.snapshot(budgetOverrides) as AgentConfigOverrides,
		ablations: $state.snapshot(ablations) as AblationDraft[],
		execution: $state.snapshot(execution) as ExecutionDraft,
		postRun: $state.snapshot(postRun),
		schedule: schedule ? $state.snapshot(schedule) : null,
	});
	const samplingNoop = $derived(method === 'scaffolding' && isNoopSamplingPreset(samplingPreset as RunPreset | null));
	const body = $derived(buildCreateBody(draft, { samplingNoop }));

	$effect(() => {
		if (composerMode === 'create') saveComposerDraft(draft);
	});

	// ── preview (debounced; superseded requests aborted) ─────────────────
	const designReady = $derived(modelIds.size > 0 && axisIds.length > 0 && appIds.size > 0);
	const localInvalid = $derived(
		llmInvalid || (isAgent && budgetInvalid) || repeatsInvalid || seedInvalid || repeats == null || executionInvalid || scheduleInvalid
	);
	const previewBody = $derived<ExperimentCreateV2>({
		...body,
		name: body.name || 'Untitled experiment',
		slug: body.slug || 'untitled-experiment',
		schedules: [],
	});
	const previewKey = $derived(stableKey(previewBody));

	let preview = $state.raw<{ key: string; data: ExperimentPreviewV2 } | null>(null);
	let previewFailure = $state<{ key: string; message: string } | null>(null);
	let previewLoading = $state(false);
	let previewNonce = $state(0);
	let fetchedNonce = 0;

	$effect(() => {
		const key = previewKey;
		const nonce = previewNonce;
		if (!designReady || localInvalid) {
			previewLoading = false;
			return;
		}
		if (nonce === fetchedNonce && untrack(() => preview?.key === key)) {
			previewLoading = false;
			return;
		}
		const requestBody = JSON.parse(key) as ExperimentCreateV2;
		const controller = new AbortController();
		previewLoading = true;
		const timer = setTimeout(() => {
			fetchedNonce = nonce;
			previewDraft(requestBody, controller.signal)
				.then((data) => {
					preview = { key, data };
					previewFailure = null;
				})
				.catch((e: unknown) => {
					if (controller.signal.aborted || isAbortError(e)) return;
					previewFailure = { key, message: formatApiError(e, 'Planning failed.') };
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

	const currentPreview = $derived(preview?.key === previewKey ? preview.data : null);
	const stale = $derived(!!preview && preview.key !== previewKey);
	const failure = $derived(previewFailure?.key === previewKey ? previewFailure : null);
	const shownPreview = $derived(designReady ? (preview?.data ?? null) : null);

	// ── issues ──────────────────────────────────────────────────────────
	function toSummary(issue: Issue): SummaryIssue {
		return {
			code: issue.code,
			severity: issue.blocking ? 'error' : INFO_CODES.has(issue.code) ? 'info' : 'warn',
			detail: issue.detail,
			subject: issue.model_id != null ? modelName(issue.model_id) : (issue.endpoint ?? undefined),
		};
	}
	const serverIssues = $derived<Issue[]>(
		shownPreview ? [...(shownPreview.issues ?? []), ...(shownPreview.endpoints ?? []).flatMap((e) => e.issues)] : []
	);
	const issues = $derived.by((): SummaryIssue[] => {
		const seen = new Set<string>();
		const list: SummaryIssue[] = [];
		const lookup = presetLookupIssue([harnessPresets, samplingPresets, budgetPresets].map((t) => t.lookupError));
		if (lookup) list.push(lookup);
		for (const issue of serverIssues) {
			const key = `${issue.code}|${issue.model_id ?? ''}|${issue.endpoint ?? ''}|${issue.detail}`;
			if (seen.has(key)) continue;
			seen.add(key);
			list.push(toSummary(issue));
		}
		for (const err of shownPreview?.launch_errors ?? []) {
			list.push({ code: err.code, severity: 'error', detail: err.detail });
		}
		return list;
	});
	const blockingCount = $derived(issues.filter((i) => i.severity === 'error').length);

	const issueActions = $derived.by((): Record<string, IssueAction> => {
		const actions: Record<string, IssueAction> = {
			missing_api_key: { label: 'Add a key in Settings', href: '/users/settings#credentials' },
			foreign_endpoint: { label: 'Review the consent note', href: '#composer-models' },
			endpoint_offline: { label: 'Open Endpoints', href: '/endpoints' },
			preset_lookup_failed: {
				label: 'Retry',
				onclick: () => [harnessPresets, samplingPresets, budgetPresets].filter((t) => t.lookupError).forEach((t) => t.retry()),
			},
		};
		const withoutTools = serverIssues.filter((i) => i.code === 'models_without_tools' && i.model_id != null).map((i) => i.model_id!);
		if (withoutTools.length > 0) {
			actions.models_without_tools = {
				label: `Remove ${plural(withoutTools.length, 'model')} without tool calling`,
				onclick: () => (modelIds = new Set([...modelIds].filter((id) => !withoutTools.includes(id)))),
			};
		}
		if (canBalance) actions.same_digest_multiple_endpoints = { label: 'Balance across endpoints', onclick: balance };
		return actions;
	});

	// ── readiness ───────────────────────────────────────────────────────
	const checklist = $derived<ChecklistItem[]>([
		{ label: 'Name the experiment', done: !!name.trim() && !!slug, href: '#composer-basics' },
		{ label: 'Pick models', done: modelIds.size > 0, detail: `${modelIds.size} selected`, href: '#composer-models' },
		{
			label: isAgent ? 'Pick harness presets' : 'Pick prompt profiles',
			done: axisIds.length > 0,
			detail: `${axisIds.length} selected`,
			href: '#composer-conditions',
		},
		{ label: 'Pick apps', done: appIds.size > 0, detail: `${appIds.size} selected`, href: '#composer-apps' },
	]);

	const needsConsent = $derived(foreignOwners.length > 0 && !consent);
	const canSave = $derived(!!name.trim() && !!slug && !localInvalid);
	const canLaunch = $derived(
		canSave && designReady && !!currentPreview && !previewLoading && blockingCount === 0 && !needsConsent && !!stackSlug
	);
	const blockedReason = $derived.by(() => {
		if (!name.trim() || !slug) return 'Name the experiment first.';
		if (modelIds.size === 0) return 'Pick at least one model.';
		if (axisIds.length === 0) return isAgent ? 'Pick a harness preset.' : 'Pick a prompt profile.';
		if (appIds.size === 0) return 'Pick at least one app.';
		if (!stackSlug) return 'Pick a stack.';
		if (localInvalid) return 'Fix the highlighted fields.';
		if (failure) return 'Planning failed — retry it in the Plan.';
		if (!currentPreview || previewLoading) return 'Planning…';
		if (blockingCount > 0) return `Resolve ${plural(blockingCount, 'blocking issue')} in the Plan.`;
		if (needsConsent) return 'Confirm the shared-endpoint note under Models.';
		return '';
	});

	// ── submit ──────────────────────────────────────────────────────────
	let submitting = $state<'save' | 'launch' | null>(null);
	let submitError = $state('');
	let fieldErrors = $state<Record<string, string>>({});
	let confirmOpen = $state(false);

	// ── section index (rail) ────────────────────────────────────────────
	const navSections = $derived<ComposerNavSection[]>([
		{ id: 'composer-basics', step: 1, label: 'Basics', done: checklist[0].done, invalid: !!(fieldErrors.name || fieldErrors.slug) },
		{ id: 'composer-method', step: 2, label: 'Method' },
		{ id: 'composer-models', step: 3, label: 'Models', done: checklist[1].done },
		{ id: 'composer-conditions', step: 4, label: isAgent ? 'Harness presets' : 'Prompt profiles', done: checklist[2].done },
		{ id: 'composer-apps', step: 5, label: 'Apps', done: checklist[3].done },
		{
			id: 'composer-replication',
			step: 6,
			label: 'Replication',
			invalid: repeatsInvalid || seedInvalid || repeats == null || !!(fieldErrors.repeats || fieldErrors.base_seed),
		},
		{ id: 'composer-sampling', step: 7, label: 'Sampling & budget', invalid: llmInvalid || (isAgent && budgetInvalid) },
		{ id: 'composer-ablations', step: 8, label: 'Ablations' },
		{ id: 'composer-execution', step: 9, label: 'Execution', invalid: executionInvalid },
		{ id: 'composer-automation', step: 10, label: 'Automation', invalid: scheduleInvalid },
	]);

	/** The section that clears `blockedReason`, for its link in the Plan. */
	const blockedHref = $derived.by(() => {
		if (!name.trim() || !slug) return '#composer-basics';
		if (modelIds.size === 0) return '#composer-models';
		if (axisIds.length === 0) return '#composer-conditions';
		if (appIds.size === 0) return '#composer-apps';
		if (!stackSlug) return '#composer-method';
		if (localInvalid) return `#${navSections.find((x) => x.invalid)?.id ?? 'composer-basics'}`;
		if (needsConsent) return '#composer-models';
		return '';
	});

	function finalBody(): ExperimentCreateV2 {
		return JSON.parse(JSON.stringify(body)) as ExperimentCreateV2;
	}

	async function saveEdit(b: ExperimentCreateV2): Promise<string> {
		const id = experimentId!;
		const { conditions, execution: exec, schedules, ...fields } = b;
		await updateExperiment(id, {
			...fields,
			scheduling_policy: exec?.scheduling_policy,
			priority: exec?.priority,
			max_concurrency: exec?.max_concurrency ?? null,
			infra_retries: exec?.infra_retries,
			execution_options: exec?.options,
		});
		await putConditions(id, conditions);
		// The edit draft starts with no schedule (existing ones live on the
		// Automation tab), so one switched on here is new: add it.
		for (const spec of schedules ?? []) await createSchedule(id, spec);
		return id;
	}

	async function save() {
		if (!canSave || submitting) return;
		submitting = 'save';
		submitError = '';
		fieldErrors = {};
		try {
			const b = finalBody();
			const id = composerMode === 'edit' ? await saveEdit(b) : (await createExperiment(b)).id;
			if (composerMode === 'create') clearComposerDraft();
			toast.success('Draft saved');
			await goto(`/experiments/${id}?tab=design`);
		} catch (e) {
			fieldErrors = apiFieldErrors(e);
			submitError = formatApiError(e, 'Could not save the experiment.');
		} finally {
			submitting = null;
		}
	}

	function requestLaunch() {
		if (!canLaunch) return;
		confirmOpen = true;
	}

	async function launch() {
		if (!canLaunch || submitting) return;
		submitting = 'launch';
		submitError = '';
		fieldErrors = {};
		try {
			const b = finalBody();
			let id: string;
			if (composerMode === 'edit') {
				id = await saveEdit(b);
				await launchExperiment(id, { mode: 'launch' });
			} else {
				id = (await createExperiment(b, { launch: true })).id;
				clearComposerDraft();
			}
			confirmOpen = false;
			toast.success('Experiment launched');
			await goto(`/experiments/${id}`);
		} catch (e) {
			confirmOpen = false;
			const refused = experimentErrorBody(e);
			if (refused?.experiment_id) {
				// The draft was saved; the launch was refused. Continue from the draft.
				if (composerMode === 'create') clearComposerDraft();
				toast.error(`Saved as a draft, but the launch was refused: ${refused.detail}`);
				await goto(`/experiments/${refused.experiment_id}?tab=design`);
				return;
			}
			fieldErrors = apiFieldErrors(e);
			submitError = refused
				? `${refused.detail}${refused.errors.length ? ` — ${refused.errors.map((x) => x.detail).join('; ')}` : ''}`
				: formatApiError(e, 'Could not launch the experiment.');
		} finally {
			submitting = null;
		}
	}

	const cells = $derived(conditionCount * appIds.size * (repeats ?? 0));
	const offlineEndpoints = $derived((currentPreview?.endpoints ?? []).filter((e) => !e.online && e.kind !== 'openrouter'));
	const confirmText = $derived.by(() => {
		const p = currentPreview;
		const lines = [
			`${plural(conditionCount, 'condition')} × ${plural(appIds.size, 'app')} × ${plural(repeats ?? 0, 'repeat')} = ${plural(cells, 'cell')}`,
		];
		if (p) {
			const eps = p.endpoints.map((e) => e.name || e.slug).join(', ');
			if (eps) lines.push(`on ${eps}`);
			lines.push(`estimated ${formatHours(p.estimate.total_hours)}`);
			const policies = Object.entries(p.schedule.policy_effective_by_endpoint ?? {});
			if (policies.length) lines.push(`order: ${[...new Set(policies.map(([, v]) => policyLabel(v)))].join(' / ')}`);
		}
		let text = lines.join(' · ') + '.';
		if (offlineEndpoints.length) {
			text += ` ${offlineEndpoints.map((e) => e.name || e.slug).join(', ')} ${offlineEndpoints.length === 1 ? 'is' : 'are'} offline: those runs wait until the relay connects.`;
		}
		return text + ' The design is frozen once it launches.';
	});

	// ── ablations ───────────────────────────────────────────────────────
	let ablationOpen = $state(false);
	let ablationEdit = $state<AblationDraft | null>(null);
	let ablationInvalid = $state(false);
	let ablationModel = $state<number | null>(null);
	let ablationAxis = $state<number | null>(null);

	function openAblation(existing?: AblationDraft) {
		const base: AblationDraft = existing
			? $state.snapshot(existing)
			: {
					key: `ablation-${Date.now()}`,
					model_id: [...modelIds][0] ?? null,
					harness_preset_id: harnessPresetIds[0] ?? null,
					profile_id: profileIds[0] ?? null,
					label: '',
					param_overrides: {},
				};
		ablationEdit = base;
		ablationModel = base.model_id;
		ablationAxis = isAgent ? base.harness_preset_id : base.profile_id;
		ablationOpen = true;
	}

	function saveAblation() {
		const edit = ablationEdit;
		if (!edit) return;
		const next: AblationDraft = {
			...edit,
			model_id: ablationModel,
			harness_preset_id: isAgent ? ablationAxis : null,
			profile_id: isAgent ? null : ablationAxis,
			param_overrides: cleanLLMParams(edit.param_overrides),
		};
		const i = ablations.findIndex((a) => a.key === next.key);
		ablations = i >= 0 ? ablations.map((a, j) => (j === i ? next : a)) : [...ablations, next];
		ablationOpen = false;
	}

	const ablationCanSave = $derived(
		!!ablationEdit &&
			ablationModel != null &&
			ablationAxis != null &&
			!ablationInvalid &&
			Object.keys(cleanLLMParams(ablationEdit.param_overrides)).length > 0
	);

	// ── misc ────────────────────────────────────────────────────────────
	const methodItems = [
		{ value: 'agent', label: 'Agent', icon: Bot, badge: 'Recommended', title: 'A sandboxed coding agent builds each app' },
		{ value: 'scaffolding', label: 'Template scaffolding', icon: FileCode, title: 'Template prompts fill a fixed skeleton' },
	];
	const maxResidentLimit = $derived.by(() => {
		const limits = (shownPreview?.endpoints ?? []).map((e) => e.resident_limit).filter((n): n is number => n != null);
		return limits.length ? Math.min(...limits) : null;
	});
	const seedHint = $derived(
		baseSeed == null
			? 'Blank: each run draws its own seed — no common random numbers across conditions.'
			: isAgent
				? 'Every condition shares the seed per app × repeat (common random numbers); it also fixes the run order.'
				: 'Seeds are fixed per condition × app × repeat (reproducible, not shared across conditions).'
	);
</script>

<div class="@container/composer">
	<div class="grid items-start gap-4 @4xl/composer:grid-cols-[minmax(0,1fr)_22rem] @6xl/composer:grid-cols-[minmax(0,1fr)_380px]">
		<div class="min-w-0 space-y-4">
			<ComposerSection step={1} id="composer-basics" title="Basics" description="Name the experiment and record what it should show.">
				<div class="grid gap-4 @lg:grid-cols-2">
					<div class="space-y-1.5">
						<Label for="exp-name">Name <span class="text-destructive" aria-hidden="true">*</span></Label>
						<Input id="exp-name" bind:value={name} required placeholder="Local coders vs GPT on CRUD apps" aria-invalid={fieldErrors.name ? true : undefined} />
						{#if fieldErrors.name}<p class="text-[11px] text-danger-fg">{fieldErrors.name}</p>{/if}
					</div>
					<div class="space-y-1.5">
						<Label for="exp-slug">Slug</Label>
						<Input
							id="exp-slug"
							value={slug}
							class="font-mono"
							placeholder="local-coders-vs-gpt"
							disabled={composerMode === 'edit'}
							aria-invalid={fieldErrors.slug ? true : undefined}
							oninput={(e: Event) => {
								slugTouched = true;
								slugInput = (e.currentTarget as HTMLInputElement).value;
							}}
						/>
						{#if fieldErrors.slug}<p class="text-[11px] text-danger-fg">{fieldErrors.slug}</p>{/if}
					</div>
				</div>
				<div class="space-y-1.5">
					<Label for="exp-hypothesis">Hypothesis <span class="font-normal text-muted-foreground">(optional)</span></Label>
					<Textarea id="exp-hypothesis" bind:value={hypothesis} rows={2} placeholder="What this experiment should show, for the eventual report." />
				</div>
				<div class="space-y-1.5">
					<Label for="exp-description">Description <span class="font-normal text-muted-foreground">(optional)</span></Label>
					<Textarea id="exp-description" bind:value={description} rows={2} />
				</div>
			</ComposerSection>

			<ComposerSection step={2} id="composer-method" title="Method" description="How apps are generated, and the stack every condition runs on.">
				<Segmented aria-label="Generation method" value={method} items={methodItems} onValueChange={(v) => setMethod(v as ComposerMode)} />
				<p class="text-xs text-muted-foreground">
					{#if isAgent}
						Each condition is a model × harness preset. Every condition gets the same brief, stack, budget and sampling settings, so
						differences come from the model and the harness.
					{:else}
						Each condition is a model × prompt profile; prompts are rendered from the profile's blocks.
					{/if}
				</p>
				<StackSelect bind:value={stackSlug} defaultSlug={config?.default_stack_slug} id="exp-stack" />
			</ComposerSection>

			<ComposerSection
				step={3}
				id="composer-models"
				title="Models"
				description="Grouped by where they run. Local endpoints first; OpenRouter by vendor."
			>
				<ModelPicker
					mode="multi"
					bind:selectedIds={modelIds}
					{requireTools}
					groupBy="endpoint"
					showEndpointStatus
					endpointFilter="all"
					maxHeight="max-h-[28rem]"
					label=""
					aria-label="Models"
					idPrefix="exp-models"
				/>
				{#if toolHint}
					<p class="flex items-start gap-1.5 text-[11px] text-muted-foreground">
						<TriangleAlert class="mt-px size-3.5 shrink-0 text-warning-fg" aria-hidden="true" />{toolHint}
					</p>
				{/if}
				<SelectionSummary bind:selectedIds={modelIds} {byId} {canBalance} onbalance={balance} />
				{#if foreignOwners.length > 0}
					<Alert class="border-warning/40 bg-warning/5">
						<TriangleAlert class="text-warning-fg" />
						<div class="space-y-2">
							<AlertDescription>
								{#each foreignOwners as [owner, count], i (owner)}
									{i > 0 ? ' ' : ''}Prompts and generated code for {plural(count, 'model')} pass through machines run by
									<span class="font-medium">{owner}</span>.
								{/each}
							</AlertDescription>
							<label class="flex items-center gap-2 text-xs font-medium">
								<Checkbox bind:checked={consent} />
								I understand and want to use these endpoints
							</label>
						</div>
					</Alert>
				{/if}
			</ComposerSection>

			<ComposerSection
				step={4}
				id="composer-conditions"
				title={isAgent ? 'Harness presets' : 'Prompt profiles'}
				description={isAgent
					? 'The comparison axis: every selected model runs with every selected harness preset.'
					: 'Every selected model runs with every selected profile (an exact version each; no “Auto”).'}
			>
				{#if isAgent}
					{#if config}
						<HarnessPresetPicker harnesses={config.harnesses} presets={harnessPresets} bind:selectedIds={harnessPresetIds} />
					{:else if configError}
						<ErrorAlert message={configError} onretry={loadConfig} />
					{:else}
						<LoadingState label="loading harnesses" class="py-8" />
					{/if}
				{:else}
					<ProfilePicker
						multiple
						bind:selectedIds={profileIds}
						allowAuto={false}
						profiles={profiles.length ? profiles : undefined}
						appIds={[...appIds]}
						{stackSlug}
						idPrefix="exp-profiles"
						label=""
					/>
				{/if}
				<p class="rounded-md border border-border bg-muted/30 px-3 py-2 font-mono text-xs tabular-nums" aria-live="polite">
					{plural(modelIds.size, 'model')} × {plural(axisIds.length, isAgent ? 'preset' : 'profile')} =
					<span class="font-semibold">{plural(cross.length, 'condition')}</span>
					{#if readyAblations.length}<span class="text-muted-foreground"> + {plural(readyAblations.length, 'ablation')}</span>{/if}
				</p>
				{#if cross.length > 0}
					<div class="space-y-1.5">
						<Label class="text-xs" for="exp-baseline">Pre-registered baseline</Label>
						<Select.Simple
							id="exp-baseline"
							size="sm"
							value={baselineChoice?.key ?? ''}
							items={baselineItems}
							onValueChange={(v) => (baseline = v)}
						/>
						<p class="text-[11px] text-muted-foreground">
							Comparisons in Results are made against it. Choosing another one after the results are in is labelled exploratory.
						</p>
					</div>
				{/if}
			</ComposerSection>

			<ComposerSection step={5} id="composer-apps" title="Apps" description="The app specs every condition builds; runs are graded against their hidden suites.">
				<AppSpecPicker bind:selectedIds={appIds} label="" aria-label="App specs" idPrefix="exp-apps" />
			</ComposerSection>

			<ComposerSection step={6} id="composer-replication" title="Replication" description="Independent repeats per cell and the seed that makes them reproducible.">
				<div class="grid gap-3 @lg:grid-cols-2">
					<NumberField
						id="exp-repeats"
						label="Repeats per cell"
						bind:value={repeats}
						min={1}
						max={MAX_REPEATS}
						required
						bind:invalid={repeatsInvalid}
						error={fieldErrors.repeats}
						hint="3 or more lets you report a spread. For paired tests, more apps beat more repeats."
					/>
					<NumberField
						id="exp-seed"
						label="Base seed"
						bind:value={baseSeed}
						min={0}
						max={MAX_SEED}
						placeholder="random per run"
						bind:invalid={seedInvalid}
						error={fieldErrors.base_seed}
						hint={seedHint}
					>
						<Button variant="outline" size="icon" class="size-8 shrink-0" aria-label="Pick a random base seed" title="Random" onclick={() => (baseSeed = randomSeed())}>
							<Dices />
						</Button>
					</NumberField>
				</div>
				{#if baseSeed == null}
					<p class="flex items-start gap-1.5 text-[11px] text-warning-fg">
						<TriangleAlert class="mt-px size-3.5 shrink-0" aria-hidden="true" />Each run draws its own seed; no common random numbers.
					</p>
				{/if}
			</ComposerSection>

			<ComposerSection
				step={7}
				id="composer-sampling"
				title="Sampling & budget"
				description="Apply identically to every model; per-model support is shown in the comparability check."
			>
				<SamplingSettings
					mode={isAgent ? 'agent' : 'scaffolding'}
					bind:presetId={samplingPresetId}
					preset={samplingPreset}
					bind:params={llmParams}
					inherited={inheritedLLM}
					bind:invalid={llmInvalid}
					hideProviderRouting={selectedModels.length > 0 && !anyOpenRouter}
					localModels={selectedModels.some((m) => isLocal(m))}
					hint="Blank fields inherit the value shown in grey. What each model actually receives is in the Plan's comparability check."
				/>
				{#if isAgent}
					<div class="space-y-3 border-t border-border/60 pt-4">
						<PresetPicker kind="budget" bind:selectedId={budgetPresetId} idPrefix="exp-budget-preset" />
						{#if config}
							<div class="space-y-2">
								<p class="section-label">Overrides for this experiment</p>
								<BudgetFields
									bind:value={budgetOverrides}
									inherited={inheritedBudget}
									limits={config.limits}
									networkPolicies={config.network_policies}
									idPrefix="exp-budget"
									bind:invalid={budgetInvalid}
								/>
								{#if allLocal}
									<p class="text-[11px] text-muted-foreground">
										The cost cap does not bind here: local models cost nothing, so only requests, wall clock and tokens end a run.
									</p>
								{/if}
							</div>
						{:else if configError}
							<ErrorAlert message={configError} onretry={loadConfig} />
						{/if}
					</div>
				{/if}
			</ComposerSection>

			<ComposerSection
				step={8}
				id="composer-ablations"
				title="Ablations"
				description="Optional extra conditions that change sampling for one model on purpose; listed apart and marked in every comparison."
			>
				{#snippet actions()}
					<Button variant="outline" size="sm" disabled={modelIds.size === 0 || axisIds.length === 0} onclick={() => openAblation()}>
						<Plus />Add ablation
					</Button>
				{/snippet}
				{#if ablations.length === 0}
					<p class="text-xs text-muted-foreground">None. The main cross-product never tunes parameters per model.</p>
				{:else}
					<ul class="space-y-1.5">
						{#each ablations as ab (ab.key)}
							<li class="flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-sm">
								<Badge variant="outline" class="border-warning/30 bg-warning/10 text-warning-fg">ablation</Badge>
								<span class="font-medium">{modelName(ab.model_id)}</span>
								<span class="text-muted-foreground">· {isAgent ? presetName(ab.harness_preset_id) : profileName(ab.profile_id)}</span>
								<span class="font-mono text-xs text-muted-foreground">{summarizeLLMParams(ab.param_overrides) || 'no overrides'}</span>
								{#if ab.label}<span class="text-xs">“{ab.label}”</span>{/if}
								{#if !ablationReady(ab, method)}<span class="text-[11px] text-warning-fg">incomplete: not sent</span>{/if}
								<span class="ml-auto flex gap-1">
									<Button variant="ghost" size="icon" class="size-7" aria-label="Edit ablation" onclick={() => openAblation(ab)}><Pencil class="size-3.5" /></Button>
									<Button
										variant="ghost"
										size="icon"
										class="size-7 text-muted-foreground hover:text-destructive"
										aria-label="Remove ablation"
										onclick={() => (ablations = ablations.filter((a) => a.key !== ab.key))}
									>
										<Trash2 class="size-3.5" />
									</Button>
								</span>
							</li>
						{/each}
					</ul>
				{/if}
			</ComposerSection>

			<ComposerSection step={9} id="composer-execution" title="Execution" description="Run order, priority and limits. The order is frozen at launch, so it is reproducible.">
				<ExecutionSettings
					bind:value={execution}
					swaps={shownPreview?.schedule.estimated_swaps ?? null}
					effective={shownPreview?.schedule.policy_effective_by_endpoint ?? null}
					{hasOllama}
					{maxResidentLimit}
					bind:invalid={executionInvalid}
				/>
			</ComposerSection>

			<ComposerSection step={10} id="composer-automation" title="Automation" description="What happens after the runs, and optional repeats on a schedule.">
				<AutomationSettings bind:postRun bind:schedule mode={method} bind:scheduleInvalid />
			</ComposerSection>

			<div class="flex justify-end @4xl/composer:hidden">
				<Button variant="outline" size="sm" disabled={!canSave || submitting !== null} onclick={save}>
					<Save />{composerMode === 'edit' ? 'Save changes' : 'Save draft'}
				</Button>
			</div>
		</div>

		<aside
			class="min-w-0 @4xl/composer:sticky @4xl/composer:top-[4.5rem] @4xl/composer:max-h-[calc(100dvh-5.5rem)] @4xl/composer:overflow-y-auto @4xl/composer:overscroll-contain"
			aria-label="Experiment plan"
		>
			<div class="mb-3 hidden @4xl/composer:block">
				<ComposerSectionNav sections={navSections} />
			</div>
			<PlanRail
				{checklist}
				ready={designReady}
				preview={shownPreview}
				{stale}
				loading={previewLoading}
				error={failure?.message ?? ''}
				onretry={() => (previewNonce += 1)}
				{localInvalid}
				conditions={conditionCount}
				apps={appIds.size}
				repeats={repeats ?? 0}
				{issues}
				actions={issueActions}
				{canLaunch}
				{canSave}
				{blockedReason}
				{blockedHref}
				{submitting}
				{submitError}
				saveLabel={composerMode === 'edit' ? 'Save changes' : 'Save draft'}
				onsave={save}
				onlaunch={requestLaunch}
				experimentStack={stackSlug || null}
			/>
		</aside>
	</div>

	<StartBar
		runs={cells || null}
		errors={blockingCount}
		label="Launch"
		canStart={canLaunch}
		busy={submitting === 'launch'}
		reason={blockedReason}
		summaryHref="#experiment-plan"
		onstart={requestLaunch}
	/>
</div>

<ConfirmDialog
	bind:open={confirmOpen}
	title="Launch {plural(cells, 'cell')}?"
	description={confirmText}
	confirmLabel="Launch"
	busy={submitting === 'launch'}
	onConfirm={launch}
/>

<Sheet.Root bind:open={ablationOpen}>
	<Sheet.Content side="right" class="w-full max-w-none overflow-y-auto p-4 sm:max-w-xl">
		<Sheet.Header class="p-0">
			<Sheet.Title class="flex items-center gap-2"><FlaskConical class="size-4" />Ablation</Sheet.Title>
			<Sheet.Description>
				One model with deliberately different sampling. It is compared like any condition and always listed as differing.
			</Sheet.Description>
		</Sheet.Header>
		{#if ablationEdit}
			<div class="mt-4 space-y-4">
				<div class="space-y-1.5">
					<Label class="text-xs" for="ablation-model">Model</Label>
					<Select.Simple
						id="ablation-model"
						size="sm"
						value={ablationModel != null ? String(ablationModel) : ''}
						items={[...modelIds].map((id) => ({ value: String(id), label: modelName(id), description: endpointKeyOf(byId.get(id)) }))}
						placeholder="One of the selected models"
						onValueChange={(v) => (ablationModel = v ? Number(v) : null)}
					/>
				</div>
				{#if isAgent}
					<PresetPicker kind="harness" bind:selectedId={ablationAxis} idPrefix="ablation-harness" label="Harness preset" />
				{:else}
					<ProfilePicker
						bind:selectedId={ablationAxis}
						allowAuto={false}
						profiles={profiles.length ? profiles : undefined}
						{stackSlug}
						showPreview={false}
						idPrefix="ablation-profile"
					/>
				{/if}
				<div class="space-y-1.5">
					<Label class="text-xs" for="ablation-label">Label <span class="font-normal text-muted-foreground">(optional)</span></Label>
					<Input id="ablation-label" class="h-8 text-sm" bind:value={ablationEdit.label} placeholder="e.g. hot sampling" />
				</div>
				<div class="space-y-1.5">
					<p class="section-label">Parameter overrides</p>
					<LLMParamsEditor
						bind:params={ablationEdit.param_overrides}
						inherited={inheritedLLM}
						mode={isAgent ? 'agent' : 'scaffolding'}
						idPrefix="ablation-params"
						bind:invalid={ablationInvalid}
						hideProviderRouting={ablationModel != null ? isLocal(byId.get(ablationModel)) : !anyOpenRouter}
						hint="Set at least one parameter; blank fields keep the experiment's value."
					/>
				</div>
				<div class="flex justify-end gap-2 border-t border-border pt-3">
					<Button variant="outline" size="sm" onclick={() => (ablationOpen = false)}>Cancel</Button>
					<Button size="sm" disabled={!ablationCanSave} onclick={saveAblation}>Save ablation</Button>
				</div>
			</div>
		{/if}
	</Sheet.Content>
</Sheet.Root>
